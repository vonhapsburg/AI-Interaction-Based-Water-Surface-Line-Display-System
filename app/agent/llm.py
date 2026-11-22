# -*- coding: utf-8 -*-
"""第二通道：把「一句话」抽成结构化参数。

支持两个后端，由 app/config.ini 或环境变量选择：

  provider = deepseek   调用 DeepSeek 官方 API（默认）
  provider = ollama     调用本地 Ollama（离线可用）
  provider = none       只用规则解析

约束与本地模型时完全一致：只抽本句提到的字段、缺失填 -1、不抄历史参数。
DeepSeek 不支持 JSON Schema 强约束，所以这里用 json_object 模式 + 解析后校验，
解析失败会自动重试一次，仍失败则退回规则通道（由 merge 处理）。
"""
import configparser
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- 配置
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.ini")

_DEFAULTS = {
    "provider": "deepseek",
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen3:4b-nothink",
    "timeout": "30",
}

_lock = threading.Lock()
_cache = {"cfg": None, "mtime": None}


def _read_config():
    """读 config.ini；文件变化时自动重载（改完不用重启服务）。"""
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        mtime = None
    with _lock:
        if _cache["cfg"] is not None and _cache["mtime"] == mtime:
            return _cache["cfg"]

    cfg = dict(_DEFAULTS)
    if mtime is not None:
        cp = configparser.ConfigParser()
        try:
            # utf-8-sig：兼容记事本/Set-Content 写出的带 BOM 文件
            cp.read(CONFIG_PATH, encoding="utf-8-sig")
            if cp.has_section("llm"):
                for k in _DEFAULTS:
                    if cp.has_option("llm", k):
                        v = cp.get("llm", k).strip()
                        if v:
                            cfg[k] = v
            else:
                cfg["_config_error"] = "config.ini 里没有 [llm] 段"
        except Exception as e:
            cfg["_config_error"] = "config.ini 解析失败：%s" % e

    # 环境变量优先
    env_map = {
        "provider": "GVF_LLM_PROVIDER",
        "api_key": "DEEPSEEK_API_KEY",
        "model": "DEEPSEEK_MODEL",
        "base_url": "DEEPSEEK_BASE_URL",
        "ollama_url": "OLLAMA_URL",
        "ollama_model": "OLLAMA_MODEL",
    }
    for k, env in env_map.items():
        v = os.environ.get(env)
        if v:
            cfg[k] = v.strip()

    cfg["provider"] = (cfg.get("provider") or "").lower().strip()
    if cfg["provider"] not in ("deepseek", "ollama", "none"):
        cfg["provider"] = "deepseek"
    cfg["base_url"] = (cfg.get("base_url") or _DEFAULTS["base_url"]).rstrip("/")

    with _lock:
        _cache["cfg"] = cfg
        _cache["mtime"] = mtime
    # 换 Key / 改配置后，健康检查的缓存必须失效，否则会沿用旧的 401 结果
    _health_cache["ok"] = None
    _health_cache["ts"] = 0.0
    return cfg


def config():
    return dict(_read_config())


def provider():
    return _read_config()["provider"]


def describe():
    """给 /api/health 用：绝不返回 key 本身。"""
    cfg = _read_config()
    p = cfg["provider"]
    if p == "deepseek":
        model = cfg["model"]
        has_key = bool(cfg.get("api_key"))
        note = ""
        if not has_key:
            note = "未配置 DEEPSEEK_API_KEY，当前仅规则解析"
        else:
            cached = _health_cache.get("note")
            if cached and _health_cache.get("ok") is False:
                note = cached
        return {"provider": "deepseek", "model": model, "ready": has_key,
                "note": note}
    if p == "ollama":
        return {"provider": "ollama", "model": cfg["ollama_model"],
                "ready": _ollama_alive(), "note": ""}
    return {"provider": "none", "model": "", "ready": False,
            "note": "已关闭模型通道，仅规则解析"}


# ---------------------------------------------------------------- 提示词
# 缺失值填 -1 而不是 null：-1 是合法数字，解析与校验都不会因为 null 失败。
SCHEMA_HINT = ('{"section":..,"b":..,"m":..,"n":..,"i":..,"Q":..,"h0":..,"L":..,'
               '"downstream":..,"reason":".."}')

SYSTEM = """你是水力参数抽取器。把用户这一句中文描述转成 JSON。

规则（必须遵守）：
1. 只输出数值，不要带单位。长度一律米，流量一律立方米每秒。
2. 底坡 i 用小数表示：千分之一 = 0.001，百分之五 = 0.05。
3. 矩形断面 m 填 0；梯形断面填边坡系数（1:1.5 表示 m=1.5）。
4. 用户没说或说不清的字段，填 -1，绝不能自己编造。
5. section 只能是 rectangular / trapezoidal / unknown。只有原句明确出现「梯形」
   或给出「边坡系数」时才填 trapezoidal；出现「矩形」才填 rectangular；
   两样都没提就填 unknown，不要猜。（原句只写「五米宽的渠」时要填 unknown。）
6. downstream 只能取四个值之一：自由跌水 = free_outfall，给定水位 = given_zw，
   正常水深 = normal_depth，没说 = unknown。
7. 不做任何计算，不解释，只输出 JSON 对象（不要 markdown 代码块）。
8. 只有当糙率用中文数字口述时才按工程量级换算：「糙率十五」= 0.015，
   「糙率二十」= 0.020，「糙率二十五」= 0.025。
   如果用户直接写了阿拉伯数字（如「糙率0.3」「糙率0.025」「糙率0.015」），
   必须原样输出，绝对不能改动数量级。
9. 「渠末正常水深」「正常水深」只是边界条件，不代表控制水深已知，h0 必须填 -1，
   绝不能把渠长的数字抄给 h0。
10. 中文大数要换算：三百 = 300，一千二 = 1200，五百 = 500。
11. b（底宽）只取「底宽」后面的数；「渠宽」「矩形渠宽」也算底宽。
    同一句里出现两个宽度时，以「底宽」为准。
12. 多轮场景：用户说「改成」「换成」「调整到」「改为」时，只输出该字段的新值。
    你只负责「这一句话里说了什么」；没提到的字段一律 -1，历史值由程序合并，
    绝不能把下面的【当前已记录参数】抄进输出。
13. 输出必须是合法 JSON 对象，字段固定为：section, b, m, n, i, Q, h0, L, downstream, reason。

【当前已记录参数】
{current}
（这份参数只用于理解「改成」「那个」「它」指的是什么，不要复制到输出里）

示例输入：矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10个方，水深2米，算500米
示例输出：{"section":"rectangular","b":5,"m":0,"n":0.015,"i":0.001,"Q":10,"h0":2,"L":500,"downstream":"unknown","reason":"直接给出"}

示例输入：五米宽的梯形渠，边坡1比1.5，流量十个方
示例输出：{"section":"trapezoidal","b":5,"m":1.5,"n":-1,"i":-1,"Q":10,"h0":-1,"L":-1,"downstream":"unknown","reason":"糙率、底坡、水深、长度未提及"}

示例输入：五米宽的梯形渠，边坡1比1.5，糙率十五，坡度千分之一，流量十个方，水深两米，算三百米
示例输出：{"section":"trapezoidal","b":5,"m":1.5,"n":0.015,"i":0.001,"Q":10,"h0":2,"L":300,"downstream":"unknown","reason":"糙率十五按 0.015 处理，三百米为 300 米"}

示例输入：梯形渠道，底宽2.5米，边坡1:2，糙率0.025，底坡0.0015，流量1.2立方米每秒，渠末正常水深，渠长1.2公里
示例输出：{"section":"trapezoidal","b":2.5,"m":2,"n":0.025,"i":0.0015,"Q":1.2,"h0":-1,"L":1200,"downstream":"normal_depth","reason":"正常水深只是边界条件，h0 未知；1.2 公里 = 1200 米"}

示例输入：帮我算个水面线
示例输出：{"section":"unknown","b":-1,"m":-1,"n":-1,"i":-1,"Q":-1,"h0":-1,"L":-1,"downstream":"unknown","reason":"信息不足，需追问"}
"""

FIELDS = ("section", "b", "m", "n", "i", "Q", "h0", "L", "downstream")
NUM_FIELDS = ("b", "m", "n", "i", "Q", "h0", "L")


# ---------------------------------------------------------------- 工具
def _system_prompt(current):
    cur = json.dumps({k: v for k, v in (current or {}).items()
                      if k in FIELDS or k == "zw"}, ensure_ascii=False)
    return SYSTEM.replace("{current}", cur)


def _extract_json(text):
    """从可能带 ```json 围栏的回复里取出第一个 JSON 对象。"""
    if not text:
        raise ValueError("空回复")
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    try:
        return json.loads(s)
    except Exception:
        pass
    # 退一步：找配对的第一个 { ... }
    start = s.find("{")
    if start < 0:
        raise ValueError("回复中没有 JSON 对象")
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start:i + 1])
    raise ValueError("JSON 括号不匹配")


def _clean(obj):
    """把 -1 / unknown 还原成缺失，并做类型与枚举校验。"""
    if not isinstance(obj, dict):
        raise ValueError("JSON 顶层不是对象")
    clean = {}
    for k in NUM_FIELDS:
        v = obj.get(k, -1)
        if isinstance(v, bool):
            continue
        if isinstance(v, str):
            try:
                v = float(v.strip())
            except ValueError:
                continue
        if isinstance(v, (int, float)) and v != -1:
            clean[k] = float(v)
    sec = obj.get("section", "unknown")
    clean["section"] = sec if sec in ("rectangular", "trapezoidal") else None
    down = obj.get("downstream", "unknown")
    if down in ("free_outfall", "given_zw", "normal_depth"):
        clean["downstream"] = down
    return clean, obj.get("reason", "")


# ---------------------------------------------------------------- DeepSeek
def _ask_deepseek(text, current, cfg, timeout):
    key = (cfg.get("api_key") or "").strip()
    if not key:
        return {"error": "未配置 DEEPSEEK_API_KEY"}

    url = cfg["base_url"] + "/chat/completions"
    last_err = None
    for attempt in (1, 2):
        system = _system_prompt(current)
        if attempt == 2:
            system += ("\n\n上一次输出不是合法 JSON。这次只输出一个 JSON 对象，"
                       "不要任何解释、不要代码块，字段必须是 " + SCHEMA_HINT)
        body = {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": 320,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            obj = _extract_json(content)
            clean, reason = _clean(obj)
            return {"source": "deepseek", "slots": clean, "reason": reason, "raw": obj,
                    "usage": data.get("usage")}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            if e.code == 401:
                return {"error": "DeepSeek API Key 无效（401）"}
            if e.code == 402:
                return {"error": "DeepSeek 账户余额不足（402）"}
            if e.code == 429:
                last_err = "DeepSeek 触发限流（429）"
            else:
                last_err = "DeepSeek HTTP %s %s" % (e.code, detail)
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
    return {"error": last_err or "DeepSeek 调用失败"}


# ---------------------------------------------------------------- Ollama
def _ollama_alive(cfg=None):
    cfg = cfg or _read_config()
    try:
        with urllib.request.urlopen(cfg["ollama_url"] + "/", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _ask_ollama(text, current, cfg, timeout):
    body = {
        "model": cfg["ollama_model"],
        "messages": [
            {"role": "system", "content": _system_prompt(current)},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "format": {
            "type": "object",
            "properties": {
                "section": {"type": "string",
                            "enum": ["rectangular", "trapezoidal", "unknown"]},
                "b": {"type": "number"}, "m": {"type": "number"},
                "n": {"type": "number"}, "i": {"type": "number"},
                "Q": {"type": "number"}, "h0": {"type": "number"},
                "L": {"type": "number"},
                "downstream": {"type": "string",
                               "enum": ["free_outfall", "given_zw",
                                        "normal_depth", "unknown"]},
                "reason": {"type": "string"},
            },
            "required": ["section", "b", "m", "n", "i", "Q", "h0", "L", "downstream"],
        },
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 240},
    }
    req = urllib.request.Request(
        cfg["ollama_url"] + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        obj = _extract_json(data["message"]["content"])
        clean, reason = _clean(obj)
        return {"source": "ollama", "slots": clean, "reason": reason, "raw": obj}
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- 自由对话
def _chat_deepseek(messages, cfg, timeout, max_tokens, temperature):
    key = (cfg.get("api_key") or "").strip()
    if not key:
        return {"error": "未配置 DEEPSEEK_API_KEY"}
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    req = urllib.request.Request(
        cfg["base_url"] + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = (data["choices"][0]["message"].get("content") or "").strip()
        return {"text": text, "usage": data.get("usage")}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"error": "DeepSeek API Key 无效（401）"}
        if e.code == 402:
            return {"error": "DeepSeek 账户余额不足（402）"}
        if e.code == 429:
            return {"error": "DeepSeek 触发限流（429），请稍后再问"}
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        return {"error": "DeepSeek HTTP %s %s" % (e.code, detail)}
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}


def _chat_ollama(messages, cfg, timeout, max_tokens, temperature):
    body = {
        "model": cfg["ollama_model"],
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": 4096,
                    "num_predict": max_tokens},
    }
    req = urllib.request.Request(
        cfg["ollama_url"] + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = (data.get("message", {}).get("content") or "").strip()
        return {"text": text}
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}


def chat(messages, timeout=None, max_tokens=700, temperature=0.4):
    """自由问答（不强制 JSON）。给 chat.py 用。"""
    cfg = _read_config()
    if timeout is None:
        try:
            timeout = float(cfg.get("timeout") or 30)
        except ValueError:
            timeout = 30
    p = cfg["provider"]
    if p == "deepseek":
        return _chat_deepseek(messages, cfg, timeout, max_tokens, temperature)
    if p == "ollama":
        return _chat_ollama(messages, cfg, timeout, max_tokens, temperature)
    return {"error": "模型通道已关闭"}


# ---------------------------------------------------------------- 对外
def ask(text, current=None, timeout=None):
    """抽取这一句话里的字段。失败返回 {'error': ...}，调用方退回规则通道。"""
    cfg = _read_config()
    if timeout is None:
        try:
            timeout = float(cfg.get("timeout") or 30)
        except ValueError:
            timeout = 30
    p = cfg["provider"]
    if p == "deepseek":
        return _ask_deepseek(text, current, cfg, timeout)
    if p == "ollama":
        return _ask_ollama(text, current, cfg, timeout)
    return {"error": "模型通道已关闭"}


def preheat():
    """只有本地模型需要预热；API 调用不需要，也不该为此花钱。"""
    if provider() != "ollama":
        return True
    try:
        ask("预热", timeout=120)
        return True
    except Exception:
        return False


_health_cache = {"ts": 0.0, "ok": None}


def health():
    p = provider()
    if p == "none":
        return False
    if p == "ollama":
        return _ollama_alive()

    # deepseek：带 30 秒缓存，避免每次 /api/health 都去请求一次，
    # 也避免偶发网络抖动把界面误判成"未就绪"。
    cfg = _read_config()
    if not cfg.get("api_key"):
        return False
    now = time.time()
    if _health_cache["ok"] is not None and now - _health_cache["ts"] < 30:
        return bool(_health_cache["ok"])
    ok = False
    try:
        req = urllib.request.Request(
            cfg["base_url"] + "/models",
            headers={"Authorization": "Bearer " + cfg["api_key"]})
        with urllib.request.urlopen(req, timeout=8) as r:
            ok = (r.status == 200)
    except urllib.error.HTTPError as e:
        ok = False
        if e.code == 401:
            _health_cache["note"] = "API Key 无效（401）"
    except Exception:
        ok = False
    _health_cache["ts"] = now
    _health_cache["ok"] = ok
    return ok
