# -*- coding: utf-8 -*-
"""V4.3.1：AI 调度参谋（规则粗扫 + AI 精修 + 可追溯解释 + 澄清/降级）。

原则：
- 数值只来自求解器；AI 只提候选与写解释；
- 解释里的每个数字必须能在结果表中找到，否则重写或退回模板；
- 模型不可用时用规则网格 + 模板解释，功能不中断。
"""
import re

from agent import llm
from unsteady import solver as us
from unsteady import preissmann as up
from unsteady import chat


def _num(v, d):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _base_defaults(base):
    b = dict(base or {})
    b.setdefault("L", 2000.0)
    b.setdefault("b", 6.0)
    b.setdefault("m", 0.0)
    b.setdefault("n", 0.015)
    b.setdefault("i0", 0.001)
    b.setdefault("Q0", 10.0)
    b.setdefault("Qpeak", 30.0)
    b.setdefault("t_peak", 3600.0)
    b.setdefault("t_base", 7200.0)
    b.setdefault("t_end", 8 * 3600.0)
    b.setdefault("Nx", 61)
    return b


def _gate_for(base, a_end):
    g = dict(base.get("gate") or {})
    L = _num(base.get("L"), 2000.0)
    b = _num(base.get("b"), 6.0)
    return {"s": _num(g.get("s"), 0.5 * L), "b_g": _num(g.get("b_g"), b),
            "z_s": _num(g.get("z_s"), 0.0), "mu": _num(g.get("mu"), 0.62),
            "a0": _num(g.get("a0"), 1.0), "a_end": float(a_end),
            "t_close": _num(g.get("t_close"), 0.8 * 3600.0),
            "t_ramp": _num(g.get("t_ramp"), 0.5 * 3600.0)}


def _solve(base, gate):
    L, b, m = base["L"], base["b"], base["m"]
    n, i0 = base["n"], base["i0"]
    hn = us.normal_depth(b, m, n, i0, base["Q0"])
    hd = base.get("h_dn")
    hd = hn if hd is None or hd == "auto" or _num(hd, -1) < 0 else _num(hd, hn)
    res = up.run(L=L, b=b, m=m, n=n, i0=i0, Q0=base["Q0"], Qpeak=base["Qpeak"],
                 t_peak=base["t_peak"], t_base=base["t_base"], t_end=base["t_end"],
                 h_dn=hd, h_init=None, Nx=int(base.get("Nx") or 61),
                 snap_hours=[base["t_end"] / 3600.0], gate=gate)
    return res


def _metric(res, ref_peak):
    peak = res["stations"][-1]["peak_Q"]
    hmax = max((max(st["hu"]) for st in res.get("structures") or []), default=0.0)
    return {"peak": round(peak, 3),
            "cut": round((ref_peak - peak) / max(ref_peak, 1e-9), 4),
            "hmax": round(hmax, 3),
            "mass_err": res["diagnostics"]["mass_err"]}


def _coarse_values(intent):
    plans = (intent or {}).get("plans_a") or []
    if plans:
        vals = [float(x) for x in plans]
    else:
        vals = [0.45, 0.6, 0.75, 0.9]
    out = []
    for v in vals:
        v = round(min(max(v, 0.2), 2.0), 2)
        if v not in out:
            out.append(v)
    return out[:5]


def _table_line(m):
    return "%s：开度 %.2f m，下游峰值 %.2f m³/s，削峰率 %.1f%%，闸上最高 %.2f m，可行=%s" % (
        m["name"], m["a_end"], m["peak"], m["cut"] * 100, m["hmax"],
        "是" if m.get("feasible") else "否")


def _ai_refine(table, max_stage, use_ai):
    """AI 在最优可行方案附近提 2 个候选；失败则给确定性中点。"""
    feas = [r for r in table if r["feasible"]]
    pool = feas or table
    best = min(pool, key=lambda r: r["peak"])
    if use_ai:
        sys = ("你是水力调度助手。只能输出 JSON，不要解释。"
               "字段：{\"candidates\":[{\"a_end\":0.55},{\"a_end\":0.5}]}。"
               "a_end 单位 m，范围 0.2–2.0；在最优方案附近细化，不要重复已有开度。")
        user = "约束：闸上≤%.2f m。\n已有方案：\n%s\n请给 2 个更细的开度候选。" % (
            max_stage, "\n".join(_table_line(r) for r in table))
        r = llm.chat([{"role": "system", "content": sys},
                      {"role": "user", "content": user}],
                     max_tokens=300, temperature=0.2)
        if "text" in r:
            try:
                data = llm._extract_json(r["text"])
                cand = [float(c["a_end"]) for c in (data.get("candidates") or [])
                        if isinstance(c, dict)]
            except Exception:
                cand = []
        else:
            cand = []
        good = []
        for v in cand:
            if 0.2 <= v <= 2.0 and all(abs(v - r["a_end"]) > 0.03 for r in table):
                if v not in good:
                    good.append(round(v, 2))
        if good:
            return good[:2], True
    # 确定性兜底：最优点与相邻方案之间取中点
    others = [r for r in table if abs(r["a_end"] - best["a_end"]) > 0.05]
    out = []
    for r in sorted(others, key=lambda x: abs(x["a_end"] - best["a_end"]))[:2]:
        v = round((best["a_end"] + r["a_end"]) / 2.0, 2)
        if all(abs(v - x["a_end"]) > 0.03 for x in table) and v not in out:
            out.append(v)
    return out, False


def _fmt_table(table, max_stage):
    lines = ["约束：闸上最高水位 ≤ %.2f m" % max_stage]
    for r in table:
        lines.append("- " + _table_line(r))
    return "\n".join(lines)


def _numbers_in(text):
    t = text or ""
    # 去掉“1. / 2、 / (3)”这类序号，避免被当成数值
    t = re.sub(r"(?m)^\s*\d+[.、)]\s*", "", t)
    t = re.sub(r"(?m)^\s*[（(]\d+[)）]\s*", "", t)
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", t)]


def _table_numbers(table, max_stage):
    vals = {round(max_stage, 2), round(max_stage, 1)}
    for r in table:
        vals.update({round(r.get("a_end", 0), 2), round(r.get("a_end", 0), 1),
                     round(r.get("peak", 0), 2), round(r.get("peak", 0), 1),
                     round(r.get("cut", 0) * 100, 1), round(r.get("hmax", 0), 2),
                     round(r.get("hmax", 0), 1)})
    return vals


def _trace_ok(text, table, max_stage):
    vals = _table_numbers(table, max_stage)
    bad = []
    for tok in re.finditer(r"-?\d+(?:\.\d+)?", text or ""):
        raw = tok.group(0)
        x = float(raw)
        # 不带小数点的 1–10 视为序号/条数（“3 段理由”），不作为数值追溯
        if "." not in raw and abs(x) <= 10:
            continue
        if any(abs(x - v) <= 0.051 for v in vals):
            continue
        bad.append(x)
    return bad


def _template_explain(table, max_stage, feasible_any, recommend):
    if not feasible_any:
        best = min(table, key=lambda r: r["peak"])
        return ("没有满足“闸上≤%.1f m”的方案。%s 的闸上最高 %.2f m、削峰 %.1f%%，"
                "可作为放宽约束后的参考；也可以提前关闸或加宽闸门再试。" %
                (max_stage, best["name"], best["hmax"], best["cut"] * 100))
    rec = recommend
    lines = ["推荐【%s】：开度 %.2f m，下游峰值 %.2f m³/s，削峰 %.1f%%，闸上最高 %.2f m。" %
             (rec["name"], rec["a_end"], rec["peak"], rec["cut"] * 100, rec["hmax"])]
    others = [r for r in table if r is not rec and r["feasible"]]
    if others:
        alt = min(others, key=lambda r: r["peak"])
        lines.append("与【%s】相比：闸上最高 %.2f m 对 %.2f m，削峰 %.1f%% 对 %.1f%%。" %
                     (alt["name"], rec["hmax"], alt["hmax"],
                      rec["cut"] * 100, alt["cut"] * 100))
    lines.append("风险：约束为 %.1f m，推荐方案闸上最高 %.2f m；建议关注闸上水位与下游退水时间。" %
                 (max_stage, rec["hmax"]))
    return " ".join(lines)


def _ai_explain(table, max_stage, recommend, feasible_any):
    rec_name = recommend["name"] if recommend else "无"
    sys = ("你是水力调度参谋。只能用下面表格中的数字，不得计算、不得编造；"
           "不要出现“实测”“全局最优”“保证安全”。"
           "输出 3 段：推荐方案；两条理由；代价与操作建议。总字数≤220。")
    user = (_fmt_table(table, max_stage) + "\n\n候选推荐：%s\n请按 3 段写。" % rec_name)
    r = llm.chat([{"role": "system", "content": sys},
                  {"role": "user", "content": user}],
                 max_tokens=500, temperature=0.3)
    if "text" in r:
        return r["text"].strip()
    return ""


def run(base, text="", max_stage=None, use_ai=True):
    """返回调度参谋结果。max_stage 为 None 时从 text 解析；仍无则要求澄清。"""
    base = _base_defaults(base)
    intent = chat.parse_schedule_intent(text or "") or {}
    if max_stage is None:
        max_stage = (intent.get("constraints") or {}).get("max_stage")
    if max_stage is None:
        return {"ok": True, "need_clarify": True,
                "question": "闸上最高水位允许到多少？",
                "options": [5.0, 5.5, 6.0],
                "intent": intent}
    max_stage = float(max_stage)

    ref = _solve(base, None)
    ref_peak = ref["stations"][-1]["peak_Q"]

    rows = []
    for i, a in enumerate(_coarse_values(intent)):
        res = _solve(base, _gate_for(base, a))
        m = _metric(res, ref_peak)
        m.update({"name": "粗扫%d" % (i + 1), "a_end": a,
                  "feasible": bool(m["hmax"] <= max_stage), "source": "coarse"})
        rows.append(m)

    refine, ai_used = _ai_refine(rows, max_stage, use_ai)
    for i, a in enumerate(refine):
        res = _solve(base, _gate_for(base, a))
        m = _metric(res, ref_peak)
        m.update({"name": "精修%d" % (i + 1), "a_end": a,
                  "feasible": bool(m["hmax"] <= max_stage), "source": "ai"})
        rows.append(m)

    feas = [r for r in rows if r["feasible"]]
    feasible_any = bool(feas)
    recommend = min(feas, key=lambda r: r["peak"]) if feas else None

    fallback = not ai_used or not use_ai
    explanation = ""
    if use_ai:
        explanation = _ai_explain(rows, max_stage, recommend, feasible_any)
    bad = _trace_ok(explanation, rows, max_stage) if explanation else ["(空)"]
    if bad and use_ai:
        # 重写一次，明确列出不允许的数字
        sys2 = ("你上次写了解释里没有出现在表格中的数字：%s。"
                "请只使用表格中的数字重写，≤200 字。" % ", ".join(str(x) for x in bad[:5]))
        r2 = llm.chat([{"role": "system", "content": sys2},
                       {"role": "user", "content": _fmt_table(rows, max_stage)}],
                      max_tokens=450, temperature=0.2)
        if "text" in r2:
            explanation = r2["text"].strip()
        bad = _trace_ok(explanation, rows, max_stage)
    if (not explanation) or bad:
        explanation = _template_explain(rows, max_stage, feasible_any, recommend)
        fallback = True

    return {
        "ok": True, "need_clarify": False,
        "intent": intent, "max_stage": max_stage,
        "reference_peak": round(ref_peak, 3),
        "candidates": rows,
        "recommend": recommend,
        "feasible_any": feasible_any,
        "explanation": explanation,
        "fallback_used": bool(fallback),
        "trace_bad": bad if bad else [],
    }
