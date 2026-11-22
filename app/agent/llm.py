# -*- coding: utf-8 -*-
"""第二通道：调用本地 Ollama 做参数抽取。只吐数值，不做换算。"""
import json
import urllib.request

OLLAMA_URL = "http://127.0.0.1:11434"
MODEL = "qwen3:4b-nothink"

# 关键：缺失值填 -1，不用 null。
# 因为 -1 是合法数字，语法约束永远不会失败；null 在某些版本上会报错。
SCHEMA = {
    "type": "object",
    "properties": {
        "section": {"type": "string", "enum": ["rectangular", "trapezoidal", "unknown"]},
        "b": {"type": "number"},
        "m": {"type": "number"},
        "n": {"type": "number"},
        "i": {"type": "number"},
        "Q": {"type": "number"},
        "h0": {"type": "number"},
        "L": {"type": "number"},
        "downstream": {"type": "string",
                       "enum": ["free_outfall", "given_zw", "normal_depth", "unknown"]},
        "reason": {"type": "string"},
    },
    "required": ["section", "b", "m", "n", "i", "Q", "h0", "L", "downstream"]
}

SYSTEM = """你是水力参数抽取器。把用户的中文描述转成 JSON。

规则（必须遵守）：
1. 只输出数值，不要带单位。长度一律米，流量一律立方米每秒。
2. 底坡 i 用小数表示：千分之一 = 0.001，百分之五 = 0.05。
3. 矩形断面 m 填 0；梯形断面填边坡系数（1:1.5 表示 m=1.5）。
4. 用户没说或说不清的字段，填 -1，绝不能自己编造。
5. section 只能是 rectangular / trapezoidal / unknown。只有原句明确出现「梯形」
   或给出「边坡系数」时才填 trapezoidal；出现「矩形」才填 rectangular；
   两样都没提就填 unknown，不要猜。
   （原句只写「五米宽的渠」时要填 unknown。）
6. downstream 只能取四个值之一：自由跌水 = free_outfall，给定水位 = given_zw，
   正常水深 = normal_depth，没说 = unknown。
7. 不做任何计算，不解释，只输出 JSON。
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


def ask(text, current=None, timeout=90):
    """抽取这一句话里的字段。current 只用于消解指代，不参与输出。"""
    cur = json.dumps(
        {k: v for k, v in (current or {}).items()
         if k in ("section", "b", "m", "n", "i", "Q", "h0", "L", "downstream", "zw")},
        ensure_ascii=False)
    system = SYSTEM.replace("{current}", cur)

    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "format": SCHEMA,          # 强制输出符合 schema 的 JSON
        "keep_alive": "30m",       # 30 分钟内不卸载，避免每次冷启动 11 秒
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 240},
    }
    req = urllib.request.Request(
        OLLAMA_URL + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        content = data["message"]["content"]
        obj = json.loads(content)
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}

    # -1 还原成缺失
    clean = {}
    for k in ["b", "m", "n", "i", "Q", "h0", "L"]:
        v = obj.get(k, -1)
        if isinstance(v, (int, float)) and v != -1:
            clean[k] = float(v)
    sec = obj.get("section", "unknown")
    clean["section"] = None if sec == "unknown" else sec
    down = obj.get("downstream", "unknown")
    if down != "unknown":
        clean["downstream"] = down

    return {"source": "llm", "slots": clean,
            "reason": obj.get("reason", ""), "raw": obj}


def preheat():
    """启动时打一条空请求，把模型装进内存。"""
    try:
        ask("预热", timeout=120)
        return True
    except Exception:
        return False


def health():
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False
