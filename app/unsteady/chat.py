# -*- coding: utf-8 -*-
"""非恒定流的对话通道：自然语言 → 工况参数；以及解释/出题。

与恒定流的 chat 分开，因为要解析的对象不同（过程线参数而不是单点水深）。
所有数值只来自参数表与内核输出，模型不产生水力数值。
"""
import json

from agent import llm

UNSTEADY_KEYS = ("L", "b", "m", "n", "i0", "Q0", "Qpeak",
                 "t_peak_h", "t_base_h", "t_end_h", "h_dn", "Nx")

EXTRACT_SYS = """你是«明渠非恒定流»教学系统的助手。把用户这句话转成工况参数，输出严格 JSON。

字段（缺的填 -1）：
  L        渠长 m
  b        底宽 m
  m        边坡系数（矩形填 0）
  n        糙率
  i0       底坡
  Q0       初始流量 m³/s
  Qpeak    峰值流量 m³/s
  t_peak_h 涨水历时 小时
  t_base_h 回落到初始流量 小时
  t_end_h  模拟总时长 小时
  h_dn     下游水位 m（没提到就填 -1，表示按正常水深）
  Nx       网格数（没提到填 -1）

规则：
1. 只转换用户明确说过的量；没说的一律 -1，禁止猜测。
2. 时间统一换算成小时，长度统一成 m，流量成 m³/s。
3. 中文数字要换算（“两小时”→2，“十方”→10）。
4. 如果用户是在提问（为什么/怎么/是什么/能不能解释）而不是给参数，返回 {"act":"ask"}。
5. 如果是要求出题/练习，返回 {"act":"quiz"}。
6. 否则返回 {"act":"set","params":{...},"reply":"一句话确认"}。
只输出 JSON，不要解释。"""

ANSWER_SYS = """你是«明渠非恒定流»教学系统的助手，回答与当前工况相关的问题。

要求：
1. 口语化、简洁（3–6 句）。
2. 只能使用【当前工况】与【最近一次结果】里出现的数字，不编造。
3. 解释要用力学语言：连续方程、动量方程、特征线 v±c、运动波速 c_k=dQ/dA、
   洪峰坦化来自扩散项、绳套来自附加比降、回水逆传速度 c−v。
4. 如果当前用的是扩散波（忽略惯性项），要说明它算不了回水顶托与急流下游扰动；
   工程模式用全方程时则可以。
5. 不要输出 JSON。"""


def _fmt_params(p):
    names = {"L": "渠长", "b": "底宽", "m": "边坡", "n": "糙率", "i0": "底坡",
             "Q0": "初始流量", "Qpeak": "峰值流量",
             "t_peak_h": "涨水历时(h)", "t_base_h": "回落历时(h)",
             "t_end_h": "模拟时长(h)", "h_dn": "下游水位", "Nx": "网格数"}
    out = []
    for k, v in (p or {}).items():
        if k in names and v not in (None, "", -1):
            out.append("%s=%s" % (names[k], v))
    return "，".join(out) if out else "（尚未设置）"


def extract(text, params):
    """返回 {'act': 'set'|'ask'|'quiz', 'params': {...}, 'reply': str}。"""
    user = "当前工况：%s\n\n用户：%s" % (_fmt_params(params), text)
    r = llm.chat([{"role": "system", "content": EXTRACT_SYS},
                  {"role": "user", "content": user}],
                 max_tokens=500, temperature=0.1)
    if "error" in r:
        return {"act": "error", "reply": "模型通道不可用：%s" % r["error"]}
    try:
        data = llm._extract_json(r["text"])
    except Exception:
        data = {}
    act = (data.get("act") or "").lower()
    if act in ("ask", "quiz"):
        return {"act": act}
    if act != "set":
        return {"act": "ask"}          # 解析失败时按提问处理，不瞎改参数
    patch = {}
    for k, v in (data.get("params") or {}).items():
        if k not in UNSTEADY_KEYS:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv == -1 or fv is None:
            continue
        patch[k] = fv
    return {"act": "set", "params": patch,
            "reply": data.get("reply") or "已按你的描述更新工况。"}


def answer(text, params, summary):
    user = ("【当前工况】%s\n\n【最近一次结果】%s\n\n用户问题：%s"
            % (_fmt_params(params), summary or "（还没有计算）", text))
    r = llm.chat([{"role": "system", "content": ANSWER_SYS},
                  {"role": "user", "content": user}],
                 max_tokens=700, temperature=0.4)
    if "error" in r:
        return {"reply": "模型通道不可用：%s" % r["error"]}
    return {"reply": r.get("text", "").strip()}


def quiz(params, summary):
    sys_p = ("你是水力学习题老师。依据下面的非恒定流算例，出 3 道题并给答案与解析。"
             "只能使用给定数字，不得编造。输出严格 JSON："
             '{"items":[{"q":"题面","a":"答案","why":"解析"}]}')
    user = "【当前工况】%s\n\n【结果】%s" % (_fmt_params(params), summary or "（还没有计算）")
    r = llm.chat([{"role": "system", "content": sys_p},
                  {"role": "user", "content": user}],
                 max_tokens=900, temperature=0.3)
    if "error" in r:
        return {"items": [], "error": r["error"]}
    try:
        data = llm._extract_json(r["text"])
        items = data.get("items") or []
    except Exception:
        items = []
    return {"items": items}
