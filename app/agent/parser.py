# -*- coding: utf-8 -*-
"""规则解析器：把中文口语转成结构化参数。第一通道，不联网，毫秒级。"""
import re

# ---------- 中文数字 ----------
CN = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
      "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def to_number(text):
    """把 '二十五' '零点零一五' 这类中文数字转成数字字符串。"""
    s = text.strip()
    if not s:
        return s
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return s

    total = 0
    num = 0
    for i, ch in enumerate(s):
        if ch == "点":
            frac = "".join(str(CN.get(c, 0)) for c in s[i + 1:])
            return "%g" % float("%d.%s" % (total + num, frac or "0"))
        if ch in CN:
            num = CN[ch]
        elif ch == "十":
            total += (num if num else 1) * 10
            num = 0
        elif ch == "百":
            total += (num if num else 1) * 100
            num = 0
        elif ch == "千":
            total += (num if num else 1) * 1000
            num = 0
    return str(total + num)


def normalize(text):
    """把口语里的分数词、中文数字统一成阿拉伯数字。"""
    t = text

    # 千分之一 / 百分之一点五 → 小数字符串
    def _frac(m):
        v = float(to_number(m.group(2)))
        d = {"万": 10000, "千": 1000, "百": 100, "十": 10}[m.group(1)]
        return "%g" % (v / d)

    # 先把「零点一」这类小数口语换成 0.1，否则「百分之零点一」会匹配不上
    t = re.sub(r"零点([零一二三四五六七八九\d]+)",
               lambda m: "0." + "".join(str(CN.get(c, 0)) for c in m.group(1)), t)

    # 万分之一 / 千分之一 / 百分之一点五 → 小数字符串
    t = re.sub(r"([万千百十])分之([零一二三四五六七八九十两\d.]+)", _frac, t)
    t = re.sub(r"百分之([零一二三四五六七八九十两\d.]+)",
               lambda m: "%g" % (float(to_number(m.group(1))) / 100), t)

    # 一米二 → 1.2（口语省略「分米」的读法，必须先于下面的单位归一化）
    t = re.sub(r"([一二三四五六七八九两])米([一二三四五六七八九])",
               lambda m: "%s.%s" % (CN[m.group(1)], CN[m.group(2)]), t)

    # 三百 / 一千二百（含百、千的中文大数；必须先于「十」处理）
    t = re.sub(r"[零一二三四五六七八九两][百千][零一二三四五六七八九两十百千]*",
               lambda m: to_number(m.group(0)), t)

    # 十五 / 二十五 / 二十
    t = re.sub(r"([一二三四五六七八九])?十([一二三四五六七八九])?",
               lambda m: str((CN.get(m.group(1), 1) if m.group(1) else 1) * 10
                             + (CN.get(m.group(2), 0) if m.group(2) else 0)), t)

    # 五米 / 三个 / 四条
    t = re.sub(r"([一二三四五六七八九两])(?=[个米分寸支根条次倍号元吨度方])",
               lambda m: str(CN[m.group(1)]), t)

    t = t.replace("‰", "/1000").replace("：", ":")
    return t


# ---------- 单位换算表 ----------
LEN = {"毫米": 0.001, "mm": 0.001, "厘米": 0.01, "公分": 0.01, "cm": 0.01,
       "米": 1.0, "m": 1.0, "公里": 1000.0, "千米": 1000.0, "km": 1000.0}
FLOW = {"立方米每秒": 1.0, "立方每秒": 1.0, "方每秒": 1.0, "m3/s": 1.0,
        "m³/s": 1.0, "秒立方": 1.0, "方": 1.0, "立方": 1.0,
        "升每秒": 0.001, "l/s": 0.001}

NUM = r"(\d+(?:\.\d+)?)"
U_LEN = r"(毫米|厘米|公分|公里|千米|米|m|mm|cm|km)?"
U_FLOW = r"(立方米每秒|立方每秒|方每秒|m3/s|m³/s|秒立方|升每秒|l/s|方|立方)?"
# 槽位与数值之间的连接词。多轮场景里大量出现「改成」「换成」「改为」，
# 不认这些词的话，跨轮修改会解析不出来（表现为"模型失忆"）。
CONN = r"(?:为|是|=|:|取|按|依|用|改成|换成|改为|调到|调整到|调成|变为|变成)?"
# 符号写法（i=、n取）用这个：连接词是必需的
CONN2 = r"(?:为|是|[=:]|取|按|依|用|改成|换成|改为|调到|调整到|调成|变为|变成)"


def _search(patterns, text):
    """按顺序试每个正则，返回第一个命中的 (数值, 后缀字符串)。"""
    for p in patterns:
        m = re.search(p, text)
        if m:
            try:
                return float(m.group(1)), (m.group(2) if m.lastindex and m.lastindex >= 2 else "")
            except (ValueError, TypeError):
                continue
    return None, ""


def parse(text):
    """主入口。返回 dict：slots / missing / warnings / normalized。"""
    raw = text or ""
    t = normalize(raw)
    slots = {}
    warnings = []

    # 先摘掉会干扰的短语
    t_h = t.replace("正常水深", "")

    # ---- 断面型式 ----
    section = None
    if re.search(r"梯形", t):
        section = "trapezoidal"
    elif re.search(r"矩形|长方形|方形渠", t):
        section = "rectangular"

    # ---- 底宽 b ----
    b, u = _search([
        r"底宽" + CONN + r"\s*" + NUM + r"\s*" + U_LEN,
        r"渠宽" + CONN + r"\s*" + NUM + r"\s*" + U_LEN,
        r"(?<![a-z])b\s*[=:]\s*" + NUM + r"\s*" + U_LEN,
        NUM + r"\s*(毫米|厘米|公分|公里|千米|米|m|mm|cm|km)\s*(?:宽的|宽)",
        r"(?<!底)宽(?:度)?" + CONN + r"\s*" + NUM + r"\s*" + U_LEN,
    ], t)
    if b is not None:
        slots["b"] = b * LEN.get(u, 1.0)

    # ---- 边坡系数 m ----
    # 注意：'边坡1比1.5' 里的 1 是比值前项，不能当成边坡系数。
    # 用负向先行断言挡住「1比/1:」这种写法，交给第二条正则取后项。
    m_val, _ = _search([
        r"边坡(?:系数)?" + CONN + r"\s*" + NUM + r"(?!\s*[:比]\s*\d)",
        r"1\s*[:比]\s*" + NUM,
        r"(?<![a-z])m\s*" + CONN2 + r"\s*" + NUM,
    ], t)
    if m_val is not None:
        slots["m"] = m_val
        if section is None:
            section = "trapezoidal"

    # ---- 糙率 n ----
    n_val, _ = _search([
        r"(?:糙率|粗糙系数|曼宁系数)" + CONN + r"\s*" + NUM,
        r"(?<![a-z])n\s*" + CONN2 + r"\s*" + NUM,
    ], t)
    if n_val is not None:
        slots["n"] = n_val

    # ---- 底坡 i ----
    # 先处理 1/n 这种比值写法：1/500 表示 0.002，不能把分母 500 当成坡度
    ratio = re.search(r"(?:底坡|坡度|坡降|比降)" + CONN + r"\s*1\s*/\s*(\d+)", t)
    if ratio:
        i_val = 1.0 / float(ratio.group(1))
        slots["i"] = i_val
    else:
        i_val, iu = _search([
            r"(?:底坡|坡度|坡降|比降)" + CONN + r"\s*" + NUM + r"\s*(%|/1000)?",
            r"(?<![a-z])i\s*" + CONN2 + r"\s*" + NUM,
        ], t)
        if i_val is not None:
            if iu == "%":
                i_val = i_val / 100.0
            elif iu == "/1000":
                i_val = i_val / 1000.0
            slots["i"] = i_val

    # ---- 流量 Q ----
    q_val, qu = _search([
        r"(?:流量|过流量)" + CONN + r"\s*" + NUM + r"\s*" + U_FLOW,
        r"Q\s*" + CONN2 + r"\s*" + NUM + r"\s*" + U_FLOW,
        r"(?:过|通过|走)\s*" + NUM + r"\s*个?\s*(?:方|立方)",
        NUM + r"\s*(?:立方米每秒|立方每秒|方每秒|m3/s|m³/s|秒立方)的?流量",
    ], t)
    if q_val is not None:
        slots["Q"] = q_val * FLOW.get(qu, 1.0)

    # ---- 控制水深 h0 ----
    h_val, hu = _search([
        r"(?:渠末水深|控制水深|初始水深|水深)" + CONN + r"\s*" + NUM + r"\s*" + U_LEN,
        r"(?<![a-z])h0?\s*" + CONN2 + r"\s*" + NUM,
    ], t_h)
    if h_val is not None:
        slots["h0"] = h_val * LEN.get(hu, 1.0)

    # ---- 渠长 L ----
    l_val, lu = _search([
        r"(?:渠长|渠道长度|计算长度|长度|渠身长)" + CONN + r"\s*" + NUM + r"\s*(公里|千米|km|米|m)?",
        r"(?:全长|总长|长)" + CONN + r"\s*" + NUM + r"\s*(公里|千米|km|米|m)?",
        r"(?<![a-z])L\s*" + CONN2 + r"\s*" + NUM + r"\s*(公里|千米|km|米|m)?",
        r"(?:算|计算)" + CONN + r"\s*" + NUM + r"\s*(公里|千米|km|米|m)?",
        NUM + r"\s*(公里|千米|km|米|m)\s*长",
    ], t)
    if l_val is not None:
        slots["L"] = l_val * LEN.get(lu, 1.0)

    # ---- 渠末边界 ----
    if re.search(r"自由跌水|自由出流|跌水|跌坎", t):
        slots["downstream"] = "free_outfall"
    elif re.search(r"正常水深", t):
        slots["downstream"] = "normal_depth"
    elif re.search(r"水位", t):
        slots["downstream"] = "given_zw"

    # 记录边界是"渠首"还是"渠末"，跨轮次保留（顶层设计 C4 类）
    if re.search(r"(渠首|上游|入口|进口).{0,4}(水深|水位)", t):
        slots["bc_up"] = "given_h"

    slots["section"] = section

    # ---- 校验与提示 ----
    if n_val is not None:
        if n_val > 0.30:
            warnings.append("糙率 %.3f 超出合理范围（0.008~0.30），请确认" % n_val)
        elif n_val > 0.05:
            warnings.append("糙率 %.3f 属教学夸张取值，非工程取值" % n_val)
    if i_val is not None and abs(slots.get("i", 0)) > 0.1:
        warnings.append("底坡绝对值大于 0.1，请确认是否填写正确")
    if q_val is not None and slots.get("Q", 1) <= 0:
        warnings.append("流量必须大于 0")
    if re.search(r"变坡|两段|二段|三段|多段|坡折", t):
        warnings.append("检测到变坡描述，但本版仅支持单段渠道，请确认或拆分后再算")

    # ---- 缺失项 ----
    required = ["section", "b", "n", "i", "Q", "h0", "L"]
    if slots.get("section") == "trapezoidal":
        required.append("m")
    missing = [k for k in required if slots.get(k) in (None, "")]

    return {
        "source": "rule",
        "slots": slots,
        "missing": missing,
        "warnings": warnings,
        "normalized": t,
    }
