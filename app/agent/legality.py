# -*- coding: utf-8 -*-
"""legality.py —— 输入合法性判定（方案《输入合法性分类总表》A~I 的实现）。

判定依据只有一条：能否无歧义地填满 gvf_core.Params 的最小参数集
（Q、reaches[b,m,i,n,L]、下游边界）。

  A 合法·完全齐备      B 合法·口语单位混用   C 合法·边界条件   D 合法·需换算推导
  E 不完整·可追问      F 非法·主题越界       G 非法·参数越界   H 非法·自相矛盾
  I 非法·无法解析

判定结果里 verdict 只会取三值：
  ok          —— 可直接算
  incomplete  —— 缺参数，追问（不是错）
  reject      —— 不该算，必须拒答或改参数
"""
import re

# ---- F 类：主题越界（内核能力之外，必须拒答）----
OUT_OF_SCOPE = [
    (r"水锤|有压|管道瞬变|压力管道", "属有压瞬变流，本系统只算明渠恒定渐变流"),
    (r"泥沙|淤积|冲淤|含沙量", "属泥沙运动，不在本系统范围"),
    (r"洪水演进|非恒定流|过程线|圣维南", "属非恒定流（圣维南方程组），不在本系统范围"),
    (r"冲刷|绕流|桥墩|局部冲坑", "属局部冲刷问题，不在本系统范围"),
    (r"圆管|满管|圆形断面|排水管|涵管", "圆形断面不支持（同一水深可能双解），请改用矩形或梯形"),
    (r"弯道|横向超高|弯段", "弯段水流不支持，本系统只算顺直棱柱体渠道"),
    (r"非棱柱|收缩段|扩散段|渐变段", "非棱柱体渠道不支持"),
    (r"复式断面|滩地|主槽.*滩", "复式断面不支持"),
    (r"消能|挑流|溢洪道|跌坎.*消能", "属急变流与消能工，不在本系统范围"),
    (r"边坡稳定|抗滑|稳定分析", "属岩土工程问题，不在本系统范围"),
]

# ---- E 类：追问话术 ----
FIELD_ASK = {
    "section": "断面型式（矩形还是梯形）",
    "b": "底宽 b（m）",
    "m": "边坡系数 m（梯形必需，如 1:1.5 填 1.5）",
    "n": "糙率 n（0.008~0.30，如混凝土 0.014）",
    "i": "底坡 i（如千分之一填 0.001）",
    "Q": "流量 Q（m³/s）",
    "h0": "控制水深 h0（m），或说明渠末是自由跌水/给定水位",
    "L": "渠长 L（m）",
    "zw": "下游水位（m），同时请给渠底高程",
}

FIELD_CN = {
    "section": "断面型式", "b": "底宽", "m": "边坡系数", "n": "糙率", "i": "底坡",
    "Q": "流量", "h0": "控制水深", "L": "渠长", "zw": "下游水位",
}

CLASS_LABEL = {
    "A": "合法 · 完全齐备",
    "B": "合法 · 口语与单位混用（已换算）",
    "C": "合法 · 边界条件已推定",
    "D": "合法 · 需换算或推导",
    "E": "不完整 · 可追问",
    "F": "非法 · 主题越界",
    "G": "非法 · 参数越界",
    "H": "非法 · 自相矛盾",
    "I": "非法 · 无法解析",
}

# 需要换算的特征。
# 注意：分数词（千分之一 / 万分之五 / 百分之五）一律算 B——它们是实打实的换算，
# 与 ‰、中文数字、公分/升/公里 同类。文档把含「千分之一」的 A1 归到 A 类，
# 与它自己的 B1/B7 口径不一致，这里按自洽口径处理（A1 实际判为 B）。
_CONV_PATTERNS = [
    r"[零〇一二三四五六七八九两十百千万]",   # 中文数字（作数量用）
    r"[万千百十]分之",                        # 千分之一 / 万分之五 / 百分之五
    r"‰|公分|厘米|毫米|公里|千米|升每秒",     # 需换算的单位
    r"\d\s*/\s*\d+",                          # 分数式 1/1000
]


def _has_conversion(text):
    return any(re.search(p, text or "") for p in _CONV_PATTERNS)


def _out_of_scope(text):
    for pattern, reason in OUT_OF_SCOPE:
        m = re.search(pattern, text or "")
        if m:
            return m.group(0), reason
    return None, None


def classify(text, merged, ad):
    """返回合法性判定。merged 为 merge() 结果，ad 为 adapter.build() 结果。"""
    text = (text or "").strip()
    missing = list(ad.get("missing") or [])
    violations = list(ad.get("violations") or [])
    facts = ad.get("facts") or {}
    warns = list(ad.get("warnings") or [])

    def out(code, verdict, reasons, hints=None, **extra):
        d = {
            "code": code,
            "label": CLASS_LABEL.get(code, code),
            "verdict": verdict,
            "reasons": [r for r in reasons if r],
            "hints": hints or [],
            "missing": missing,
            "violations": violations,
        }
        d.update(extra)
        return d

    # ---------- I 类：无法解析 ----------
    if not text:
        return out("I", "reject", ["输入为空"])
    if len(text) > 500:
        return out("I", "reject",
                   ["输入超过 500 字（%d 字），请精简后重试" % len(text)])
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text):
        return out("I", "incomplete", ["输入里没有可识别的文字或数字，请用中文描述渠道参数"])

    # ---------- F 类：主题越界 ----------
    hit, reason = _out_of_scope(text)
    if hit:
        return out("F", "reject", ["检测到「%s」：%s" % (hit, reason)],
                   hints=["本系统只支持顺直棱柱体矩形/梯形明渠的恒定渐变流水面线计算"])

    # ---------- G 类：参数越界（内核实为 ValueError，这里先挡）----------
    if violations:
        return out("G", "reject",
                   [v["message"] for v in violations],
                   hints=[v["rule"] for v in violations])

    # ---------- H 类：自相矛盾 ----------
    contradiction = facts.get("contradiction")
    if contradiction == "H1":
        # 矩形 + 边坡：已按矩形处理，仍可算
        return out("H", "ok",
                   ["声明为矩形却给出边坡，已按矩形处理（m=0）"],
                   notes=["H1"])
    if contradiction == "H3":
        return out("H", "incomplete",
                   ["平坡渠道不存在正常水深，不能以 normal_depth 作下游边界"],
                   hints=["请改给下游水深，或指定渠末自由跌水"])

    # ---------- E 类：不完整 ----------
    if missing:
        asks = [FIELD_ASK.get(k, k) for k in missing]
        return out("E", "incomplete",
                   ["缺少：%s" % "、".join(FIELD_CN.get(k, k) for k in missing)],
                   hints=["还需要：%s" % "；".join(asks)],
                   asks=asks)

    # ---------- A/B/C/D 类：可以算 ----------
    if ad.get("params") is None:
        return out("E", "incomplete", ["参数尚未齐备"], hints=["请补充缺失参数"])

    # D：反问题口吻（求正常水深 / 恰好临界 / 查表取糙率 / 区间取值）
    if re.search(r"要求.*正常水深|刚好临界|恰好临界|求.*坡度|查参数库|土渠|杂草", text):
        return out("D", "ok",
                   ["这句话是反问题或需要查表取值，当前按正问题输入直接计算"],
                   hints=["若要反求参数，请改用反问题接口（solve_inverse）"])

    # C：边界条件被显式描述或推定
    boundary = facts.get("boundary")
    if (boundary in ("given_zw", "normal_depth") or facts.get("upstream_control")
            or facts.get("boundary_explicit")):
        return out("C", "ok",
                   ["边界条件：%s" % boundary],
                   hints=[w for w in warns if "水位" in w or "正常水深" in w or "渠首" in w])

    # B：发生了数字/单位换算
    if _has_conversion(text):
        return out("B", "ok", ["已把中文数字/分数/单位换算成标准数值"])

    return out("A", "ok", ["参数齐备"])
