# -*- coding: utf-8 -*-
"""V4.5.0：枚举寻优 + Pareto + 不可行放宽 + 鲁棒性 + 建议书。

沿用 V4.4.0 的求解与解释管线：AI 只读结果表，数字必须可追溯。
"""
import copy
import math

from agent import llm
from unsteady import solver as us
from unsteady import chat
from unsteady import schedule_ai as ai


def _grid(intent, steps):
    plans = (intent or {}).get("plans_a") or []
    if plans:
        vals = [round(min(max(float(x), 0.2), 2.0), 2) for x in plans]
    else:
        import numpy as np
        vals = [round(float(v), 2) for v in np.linspace(0.3, 3.0, max(3, min(int(steps), 15)))]
    out = []
    for v in vals:
        if v not in out:
            out.append(v)
    return out


def _pareto(rows):
    out = []
    for a in rows:
        dominated = False
        for b in rows:
            if a is b:
                continue
            if b["peak"] <= a["peak"] and b["hmax"] <= a["hmax"] and (
                    b["peak"] < a["peak"] or b["hmax"] < a["hmax"]):
                dominated = True
                break
        out.append(not dominated)
    return out


def _robust(base, gate_a, max_stage):
    """糙率 +10%、流量系数 −10% 两个扰动，看闸上水位是否仍满足约束。"""
    out = []
    for label, nb, ng in (("n+10%", 1.10, 1.0), ("μ−10%", 1.0, 0.90)):
        b = copy.deepcopy(base)
        b["n"] = float(b["n"]) * nb
        g = ai._gate_for(b, gate_a)
        g["mu"] = float(g.get("mu", 0.62)) * ng
        try:
            res = ai._solve(b, g)
            hmax = max((max(st["hu"]) for st in res.get("structures") or []), default=0.0)
            peak = res["stations"][-1]["peak_Q"]
            out.append({"case": label, "hmax": round(hmax, 3), "peak": round(peak, 3),
                        "ok": bool(hmax <= max_stage + 1e-9)})
        except Exception as e:
            out.append({"case": label, "error": "%s: %s" % (type(e).__name__, e)})
    return out


def _relax(rows, max_stage):
    """不额外计算：用已有网格重新按放宽后的限值筛选。"""
    out = []
    for extra in (0.3, 0.6):
        lim = round(max_stage + extra, 2)
        feas = [r for r in rows if r["hmax"] <= lim]
        best = min(feas, key=lambda r: r["peak"]) if feas else None
        out.append({"limit": lim, "feasible_any": bool(feas),
                    "best": ({"name": best["name"], "a_end": best["a_end"],
                              "peak": best["peak"], "hmax": best["hmax"]} if best else None)})
    return out


def _md_table(rows):
    lines = ["| 方案 | 开度 (m) | 下游峰值 | 削峰率 | 闸上最高 (m) | 可行 |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %.2f | %.2f | %.1f%% | %.2f | %s |" % (
            r["name"], r["a_end"], r["peak"], r["cut"] * 100, r["hmax"],
            "是" if r["feasible"] else "否"))
    return "\n".join(lines)


def _template(rows, max_stage, recommend, relax, robust, pareto_flags):
    if recommend is None:
        if relax and all(not x["feasible_any"] for x in relax):
            min_h = min(r["hmax"] for r in rows)
            _txt = ("即使闸门全开，闸上最低也有约 %.2f m，仍高于“≤%.1f m”。"
                    "建议把约束放宽到 %.1f m 以上" % (
                        min_h, max_stage, math.ceil(min_h * 10.0) / 10.0))
            if suggest_gate_b:
                _txt += ("，或把闸宽从 %.1f m 加宽到 %.1f m（全开时闸上约 %.2f m）"
                         % (suggest_gate_b["from"], suggest_gate_b["to"],
                            suggest_gate_b["hmax"]))
            return _txt + "。"
        msg = "在 0.3–3.0 m 的 %d 个候选里，没有满足“闸上≤%.1f m”的方案。" % (len(rows), max_stage)
        feas = [x for x in relax if x["feasible_any"]]
        if feas:
            r = feas[0]
            msg += "若把闸上允许水位放宽到 %.1f m，推荐 %s（开度 %.2f m、峰值 %.2f、闸上 %.2f）。" % (
                r["limit"], r["best"]["name"], r["best"]["a_end"], r["best"]["peak"], r["best"]["hmax"])
        else:
            msg += "把限值放宽 0.6 m 仍无可行解，建议提前关闸或加宽闸门。"
        return msg
    t = ("推荐【%s】：开度 %.2f m，下游峰值 %.2f m³/s，削峰 %.1f%%，闸上最高 %.2f m（约束 %.1f m）。"
         % (recommend["name"], recommend["a_end"], recommend["peak"],
            recommend["cut"] * 100, recommend["hmax"], max_stage))
    if robust:
        t += "扰动校核：" + "；".join(
            "%s 时闸上 %.2f m（%s）" % (r["case"], r.get("hmax", 0),
                                        "仍满足" if r.get("ok") else "会超限")
            for r in robust if "hmax" in r) + "。"
    t += "操作建议：按候选表中的开度过程关闭，关注闸上水位与下游退水时间。"
    return t


def _ai_text(rows, max_stage, recommend, relax, robust):
    sys = ("你是水力调度参谋。只能使用表格中的数字，不得计算、不得编造；"
           "不要出现“实测”“全局最优”“保证安全”。"
           "输出 3 段：推荐方案；两条理由；风险与操作建议。总字数≤240。")
    rec = recommend["name"] if recommend else "无"
    user = ("约束：闸上≤%.2f m。\n候选：\n%s\n推荐：%s\n扰动：%s\n请写。" % (
        max_stage,
        "\n".join("- %s 开度%.2f 峰值%.2f 削峰%.1f%% 闸上%.2f 可行=%s" % (
            r["name"], r["a_end"], r["peak"], r["cut"] * 100, r["hmax"],
            "是" if r["feasible"] else "否") for r in rows),
        rec,
        "无"))
    if rec == "无":
        user += "\n注意：没有可行方案，要明确说明。"
    r = llm.chat([{"role": "system", "content": sys},
                  {"role": "user", "content": user}],
                 max_tokens=550, temperature=0.3)
    return r.get("text", "").strip() if "text" in r else ""


def _report_md(base, max_stage, rows, recommend, relax, robust, explanation, pareto):
    lines = ["# 闸门调度建议书", "",
             "- 渠道：L=%.0f m，b=%.1f m，Q₀=%.1f m³/s，Q_peak=%.1f m³/s" % (
                 float(base.get("L", 0)), float(base.get("b", 0)),
                 float(base.get("Q0", 0)), float(base.get("Qpeak", 0))),
             "- 约束：闸上最高水位 ≤ %.1f m" % max_stage,
             "- 目标：下游峰值最小", "", "## 候选方案", _md_table(rows), ""]
    if recommend:
        lines += ["## 推荐方案", "**%s**：开度 %.2f m，下游峰值 %.2f m³/s，削峰 %.1f%%，闸上最高 %.2f m。" % (
            recommend["name"], recommend["a_end"], recommend["peak"],
            recommend["cut"] * 100, recommend["hmax"]), ""]
    else:
        lines += ["## 推荐方案", "没有满足约束的方案。", ""]
        for r in relax:
            if r["feasible_any"]:
                lines.append("- 放宽到闸上≤%.1f m：可推荐 %s（开度 %.2f m、峰值 %.2f、闸上 %.2f）" % (
                    r["limit"], r["best"]["name"], r["best"]["a_end"],
                    r["best"]["peak"], r["best"]["hmax"]))
        lines.append("")
    if robust:
        lines += ["## 扰动校核", "| 扰动 | 闸上最高 (m) | 下游峰值 | 是否满足 |", "|---|---|---|---|"]
        for r in robust:
            if "hmax" in r:
                lines.append("| %s | %.2f | %.2f | %s |" % (
                    r["case"], r["hmax"], r["peak"], "是" if r["ok"] else "否"))
        lines.append("")
    pareto_rows = [r for r, p in zip(rows, pareto) if p]
    if pareto_rows:
        lines += ["## Pareto 前沿", "| 方案 | 开度 | 下游峰值 | 闸上最高 |", "|---|---|---|---|"]
        for r in pareto_rows:
            lines.append("| %s | %.2f | %.2f | %.2f |" % (
                r["name"], r["a_end"], r["peak"], r["hmax"]))
        lines.append("")
    lines += ["## 说明", explanation or "（无）", "",
              "> 本建议由模型参数枚举得到，不是全局最优；数值均来自求解器。"]
    return "\n".join(lines)


def optimize(base, text="", max_stage=None, steps=7, use_ai=False):
    base = ai._base_defaults(base)
    intent = chat.parse_schedule_intent(text or "") or {}
    if max_stage is None:
        max_stage = (intent.get("constraints") or {}).get("max_stage")
    if max_stage is None:
        return {"ok": True, "need_clarify": True,
                "question": "闸上最高水位允许到多少？",
                "options": [5.0, 5.5, 6.0], "intent": intent}
    max_stage = float(max_stage)

    ref = ai._solve(base, None)
    ref_peak = ref["stations"][-1]["peak_Q"]

    rows = []
    for i, a in enumerate(_grid(intent, steps)):
        res = ai._solve(base, ai._gate_for(base, a))
        m = ai._metric(res, ref_peak)
        m.update({"name": "方案%02d" % (i + 1), "a_end": a,
                  "feasible": bool(m["hmax"] <= max_stage)})
        rows.append(m)

    # 若初始网格都不可行，先试“全开”：确认是不是约束本身不可达
    if not any(r["feasible"] for r in rows):
        a_open = 4.0
        res = ai._solve(base, ai._gate_for(base, a_open))
        m = ai._metric(res, ref_peak)
        m.update({"name": "全开", "a_end": a_open,
                  "feasible": bool(m["hmax"] <= max_stage)})
        rows.append(m)

    pareto = _pareto(rows)
    feas = [r for r in rows if r["feasible"]]
    recommend = min(feas, key=lambda r: r["peak"]) if feas else None

    # 可行边界二分：在最后一个不可行与第一个可行之间细化，兼顾精度与耗时
    if feas:
        feas_sorted = sorted(feas, key=lambda r: r["a_end"])
        first = feas_sorted[0]
        lower = max([r for r in rows if r["a_end"] < first["a_end"] and not r["feasible"]],
                    key=lambda r: r["a_end"], default=None)
        if lower is not None:
            lo, hi = lower["a_end"], first["a_end"]
            for k in range(3):
                mid = round((lo + hi) / 2.0, 3)
                if any(abs(r["a_end"] - mid) < 1e-6 for r in rows):
                    break
                res = ai._solve(base, ai._gate_for(base, mid))
                m = ai._metric(res, ref_peak)
                m.update({"name": "细化%d" % (k + 1), "a_end": mid,
                          "feasible": bool(m["hmax"] <= max_stage)})
                rows.append(m)
                if m["feasible"]:
                    hi = mid
                else:
                    lo = mid
            pareto = _pareto(rows)
            feas = [r for r in rows if r["feasible"]]
            recommend = min(feas, key=lambda r: r["peak"]) if feas else None

    no_feasible_even_open = (not feas and any(
        r["name"] == "全开" and not r["feasible"] for r in rows))
    open_row = next((r for r in rows if r["name"] == "全开"), None)
    min_hmax_possible = min((r["hmax"] for r in rows), default=None)
    suggest_stage = (math.ceil(min_hmax_possible * 10.0) / 10.0
                     if min_hmax_possible is not None else None)

    # 加宽闸门：约束太紧时，给一个可执行的补救方向
    suggest_gate_b = None
    if not feas:
        _g = dict(base.get("gate") or {})
        _structs = _g.get("structures") or []
        _b0 = float((_structs[0].get("b_g") if _structs else _g.get("b_g")) or base.get("b") or 6.0)
        for _extra in (2.0, 4.0):
            _b = _b0 + _extra
            _b2 = copy.deepcopy(base)
            _g2 = dict(_b2.get("gate") or {})
            if _g2.get("structures"):
                _g2["structures"] = [dict(x, b_g=_b) for x in _g2["structures"]]
            else:
                _g2["b_g"] = _b
            _b2["gate"] = _g2
            try:
                _res = ai._solve(_b2, ai._gate_for(_b2, 4.0))
                _hm = max((max(st["hu"]) for st in _res.get("structures") or []),
                          default=0.0)
                if _hm <= max_stage:
                    suggest_gate_b = {"from": round(_b0, 2), "to": round(_b, 2),
                                      "hmax": round(float(_hm), 3)}
                    break
            except Exception:
                continue
    relax = [] if feas else _relax(rows, max_stage)
    robust = _robust(base, recommend["a_end"], max_stage) if recommend else []

    explanation = ""
    fallback = True
    if use_ai:
        explanation = _ai_text(rows, max_stage, recommend, relax, robust)
        trace_rows = rows + [{"a_end": 0, "cut": 0, "peak": x.get("peak", 0),
                              "hmax": x.get("hmax", 0)} for x in robust if "hmax" in x]
        tb = ai._trace_ok(explanation, trace_rows, max_stage) if explanation else ["(空)"]
        if not tb:
            fallback = False
        else:
            explanation = _template(rows, max_stage, recommend, relax, robust, pareto)
    else:
        explanation = _template(rows, max_stage, recommend, relax, robust, pareto)
    if recommend is None and suggest_gate_b:
        explanation += (" 也可把闸宽从 %.1f m 加宽到 %.1f m（全开时闸上约 %.2f m）。"
                        % (suggest_gate_b["from"], suggest_gate_b["to"],
                           suggest_gate_b["hmax"]))

    report = _report_md(base, max_stage, rows, recommend, relax, robust,
                        explanation, pareto)
    return {
        "ok": True, "need_clarify": False, "intent": intent,
        "max_stage": max_stage, "reference_peak": round(ref_peak, 3),
        "candidates": rows, "pareto": pareto,
        "recommend": recommend, "feasible_any": bool(feas),
        "no_feasible_even_open": bool(no_feasible_even_open),
        "min_hmax_possible": (None if min_hmax_possible is None
                              else round(float(min_hmax_possible), 3)),
        "suggest_stage": suggest_stage,
        "suggest_gate_b": suggest_gate_b,
        "relax": relax, "robust": robust,
        "explanation": explanation, "fallback_used": bool(fallback),
        "report_md": report,
    }


def counterfactual(base, a_end, max_stage):
    base = ai._base_defaults(base)
    ref = ai._solve(base, None)
    ref_peak = ref["stations"][-1]["peak_Q"]
    res = ai._solve(base, ai._gate_for(base, float(a_end)))
    m = ai._metric(res, ref_peak)
    m.update({"a_end": round(float(a_end), 2),
              "feasible": bool(m["hmax"] <= max_stage) if max_stage else None})
    return {"ok": True, "result": m, "reference_peak": round(ref_peak, 3)}
