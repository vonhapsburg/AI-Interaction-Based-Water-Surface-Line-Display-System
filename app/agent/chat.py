# -*- coding: utf-8 -*-
"""对话通道：回答与系统相关的一切问题，而不是每次都追问参数。

与「参数抽取」分开：
  · 抽取器（llm.ask）只负责把一句话变成槽位，输出 JSON；
  · 这里（chat.answer）是自由问答，输出自然语言，可以解释概念、用法、当前结果。
两条路都不产生水力数值；数值一律来自 gvf_core 的计算结果。
"""
import re

from . import llm

CAPABILITIES = """你能做的事情（可以据此回答"你会什么"）：
1. 把一句话变成计算参数（底宽、边坡、糙率、底坡、流量、控制水深、渠长、渠末条件）；
2. 用自建内核计算水面线：判别十二型（M1/M2/M3、S1/S2/S3、C1/C3、H2/H3、A2/A3），
   推算沿程水深，定位水跃与水跌，算共轭水深、跃长、消能系数；
3. 画纵剖面与横断面，显示正常水深线、临界水深线、流区、坡折衔接；
4. 解释结果：为什么是这个型号、水跃在哪、哪个参数最敏感；
5. 解释概念：正常水深、临界水深、临界底坡、弗劳德数、断面比能、共轭水深等；
6. 支持多段变坡、双模式（教学 α=1.0 / 工程 α=1.05）、历史回放、CSV 导出。"""

SYSTEM = """你是「基于AI交互的水面线展示系统」的助手，可以自由回答与本系统相关的一切问题：
系统能力与用法、水力学概念、参数含义、当前算例的结果解释、报错与提示。

""" + CAPABILITIES + """

回答要求：
1. 口语化、直接、简洁。一般 3–6 句；列举时可用短横线，不要长篇大论。
2. 不要每句话都反问用户补参数。只有在用户明确想"算"、且确实缺关键参数时，
   才用一句话说明还差什么（最多提一次），然后给出最简单的补法。
3. 涉及具体数值时，只能使用【当前算例】与【最近一次结果】里出现的数字。
   没有的数字不要编，可以说"需要先算一次才能给出具体数值"。
4. 与明渠水面线无关的问题（管道水锤、泥沙、结构设计等），用一两句话说明超出范围，
   再说明本系统能做什么。不要生硬拒答。
5. 不要输出 JSON，不要提到提示词，不要自称"模型"。

【当前算例参数】
{slots}

【最近一次结果】
{result}"""

_QUESTION_RE = re.compile(
    r"[？?]|什么|为什么|为啥|怎么|如何|哪些|哪个|多少|是否|能否|可以吗|吗$|呢$|"
    r"你会|你能|介绍|解释|讲讲|说说|区别|原理|含义|意思|在哪|哪里")


def looks_like_question(text):
    """粗判：这是提问，还是在下达计算指令/补充参数。"""
    t = (text or "").strip()
    if not t:
        return False
    if re.search(r"帮我算|计算一下|算一下|算个|出图|重新算", t) and not re.search(r"[？?]", t):
        return False
    return bool(_QUESTION_RE.search(t))


def _slots_brief(slots):
    if not slots:
        return "（还没有输入任何参数）"
    names = {"section": "断面型式", "b": "底宽 m", "m": "边坡系数", "n": "糙率",
             "i": "底坡", "Q": "流量 m³/s", "h0": "控制水深 m", "L": "渠长 m",
             "downstream": "渠末条件", "zw": "下游水位 m"}
    out = []
    for k, v in slots.items():
        if k in ("reaches",) or v in (None, ""):
            continue
        out.append("%s=%s" % (names.get(k, k), v))
    rs = slots.get("reaches")
    if isinstance(rs, list) and rs:
        seg = "；".join("第%d段 L=%s i=%s b=%s m=%s n=%s"
                        % (i + 1, r.get("L"), r.get("i"), r.get("b"),
                           r.get("m"), r.get("n")) for i, r in enumerate(rs))
        out.append("分段：" + seg)
    return "，".join(out) if out else "（还没有输入任何参数）"


def result_brief(res):
    """把内核结果压成给模型看的事实摘要（不含编造空间）。"""
    if res is None:
        return "（还没有计算过）"
    try:
        facts = res.facts or {}
        seq = " → ".join(x["label"] for x in (facts.get("type_sequence") or []))
        kn = facts.get("key_numbers") or {}
        rs = res.reaches or []
        parts = ["状态：%s" % res.status]
        if seq:
            parts.append("型序列：%s" % seq)
        for r in rs:
            parts.append("第%d段：i=%s，i_c=%s，坡型=%s，h0=%s，h_c=%s"
                         % (r.index + 1, _num(r.i), _num(r.ic), r.slope_class,
                            _num(r.h0), _num(r.hc)))
        if kn.get("max_h") is not None:
            parts.append("最大水深 %s m" % _num(kn["max_h"]))
        if kn.get("max_zw") is not None:
            parts.append("最高水位 %s m（里程 %s m）"
                         % (_num(kn["max_zw"]), _num(kn.get("max_zw_x"))))
        for t in (res.transitions or []):
            if t.kind == "jump":
                d = t.data or {}
                parts.append("水跃：里程 %s m，h1=%s，h2=%s，Fr1=%s，跃长 %s m，消能系数 %s"
                             % (_num(t.x), _num(d.get("h1")), _num(d.get("h2")),
                                _num(d.get("Fr1")), _num(d.get("L_j")), _num(d.get("Kj"))))
            elif t.kind == "outfall":
                parts.append("渠末自由跌水（水跌），控制水深 %s m"
                             % _num((t.data or {}).get("hc")))
            elif t.kind == "critical":
                parts.append("里程 %s m 处临界衔接" % _num(t.x))
        reasons = facts.get("reasons") or []
        if reasons:
            parts.append("判别依据：" + "；".join(reasons[:6]))
        cautions = facts.get("cautions") or []
        if cautions:
            parts.append("注意事项：" + "；".join(cautions[:3]))
        return "\n".join(parts)
    except Exception:
        return "（结果摘要生成失败）"


def _num(v):
    if v is None:
        return "—"
    try:
        return ("%.4g" % float(v))
    except (TypeError, ValueError):
        return str(v)


def answer(text, slots=None, result=None, history=None, timeout=None,
           hypothetical=False):
    """自由问答。返回 {'text': ...} 或 {'error': ...}。

    hypothetical=True 表示 result 是按用户这句里的参数"试算"出来的，
    当前参数并没有改动——回答里必须说清楚，否则用户会以为已经改好了。
    """
    system = SYSTEM.replace("{slots}", _slots_brief(slots or {})) \
                   .replace("{result}", result_brief(result))
    if hypothetical:
        system += ("\n\n【重要】用户这句话里带了参数，下面这份结果是按这些参数"
                   "试算出来的，当前参数并未改动。回答时要说明这是假设情形，"
                   "并提示用户：想采用的话点「应用到表单」。")
    messages = [{"role": "system", "content": system}]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": text})
    return llm.chat(messages, timeout=timeout, max_tokens=700, temperature=0.4)
