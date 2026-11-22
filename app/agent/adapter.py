# -*- coding: utf-8 -*-
"""adapter.py —— 槽位 → gvf_core.Params。所有换算与校验都在这里。

相对方案文档的改动：
  1. 越界项不再只丢进 warnings —— 单独记到 violations，
     这样合法性判定能区分「缺参数（可追问）」和「参数越界（非法）」。
  2. given_zw 缺渠底高程时明确提示（内核要的是水位不是水深）。
  3. 流量量级做合理性拦截（方案表 G9）。
"""
import re

from gvf_core import Params, Reach, Boundary

N_MIN, N_MAX = 0.008, 0.30
M_MIN, M_MAX = 0.0, 3.0
I_ABS_MAX = 0.1
Q_MAX = 1e6          # 量级异常阈值，方案表 G9

N_NAMES = {"section": "断面型式", "b": "底宽", "m": "边坡系数", "n": "糙率",
           "i": "底坡", "Q": "流量", "h0": "控制水深", "L": "渠长",
           "downstream": "渠末边界", "zw": "下游水位", "z_b0": "渠底高程"}


def _num(v):
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _extract(text, pattern, cast=float):
    m = re.search(pattern, text or "")
    if not m:
        return None
    try:
        return cast(m.group(1))
    except (TypeError, ValueError):
        return None


def build(merged, overrides=None):
    """返回 {params, missing, warnings, rows, violations, facts}。

    params 为 None 表示还不能算。violations 非空表示参数非法（不是缺项）。
    """
    slots = dict(merged.get("slots") or {})
    if overrides:
        for k, v in overrides.items():
            if v is not None and v != "":
                slots[k] = v
    text = merged.get("normalized") or ""

    warns = list(merged.get("warnings") or [])
    rows = []
    violations = []
    facts = {"section_declared": slots.get("section")}

    # 表单可以直接给分段：给定了 reaches 就不该再要求单段的 b/n/i/L
    raw_reaches = slots.get("reaches")
    if not (isinstance(raw_reaches, list) and raw_reaches):
        raw_reaches = None
    facts["multi_reach"] = bool(raw_reaches)

    def rec(slot, core, value, note=""):
        rows.append({"slot": slot, "core": core, "value": value, "note": note})

    # ---- 断面与边坡：section 只是给人看的，内核只认 m ----
    section = slots.get("section")
    m = _num(slots.get("m"))
    if raw_reaches:
        # 分段模式：断面型式由各段 m 决定
        if section is None:
            section = "trapezoidal" if any(
                (_num(r.get("m")) or 0) > 0 for r in raw_reaches) else "rectangular"
            m = 0.0
    elif section == "rectangular":
        if m not in (None, 0.0):
            warns.append("声明为矩形但给出边坡 m=%g，已按矩形处理（m=0）" % m)
            facts["contradiction"] = "H1"
        m = 0.0
    elif section == "trapezoidal":
        if m is not None and m <= 0:
            warns.append("梯形断面但边坡系数为 0，请补充有效边坡")
            m = None
        # 护栏：模型有时会把没提断面的句子判成梯形。原句里根本没有
        # 「梯形/边坡」字样时不要采信，否则会凭空要求边坡系数。
        if m is None and not re.search(r"梯形|边坡", text):
            warns.append("模型判为梯形，但原句没有边坡描述，已按矩形处理")
            section, m = "rectangular", 0.0
    elif m is not None and m > 0:
        section = "trapezoidal"
        warns.append("未声明断面型式，但给出边坡 m=%g，已判为梯形" % m)
    elif m == 0:
        section = "rectangular"
    else:
        # 既没声明断面、也没给边坡：内核只需要 b 和 m，m=0 是中性取值。
        # 按矩形处理并明确提示，避免因为「少一句话」把可算的输入判成缺项。
        section = "rectangular"
        m = 0.0
        warns.append("未声明断面型式，已按矩形处理（m=0）；若为梯形请补充边坡系数")

    rec("section", "Reach.m 的取值依据", section or "缺失")

    # ---- 逐项取数并校验 ----
    def take(key, lo=None, hi=None, positive=False, name=None):
        v = _num(slots.get(key))
        if v is None:
            return None
        if positive and v <= 0:
            violations.append({"field": key, "value": v,
                               "rule": "%s 必须大于 0" % (name or key),
                               "message": "%s 必须大于 0，当前为 %g" % (name or key, v)})
            return None
        if lo is not None and v < lo:
            violations.append({"field": key, "value": v,
                               "rule": "%s ≥ %g" % (name or key, lo),
                               "message": "%s=%g 小于下限 %g" % (name or key, v, lo)})
            return None
        if hi is not None and v > hi:
            violations.append({"field": key, "value": v,
                               "rule": "%s ≤ %g" % (name or key, hi),
                               "message": "%s=%g 超出上限 %g" % (name or key, v, hi)})
            return None
        return v

    b = None if raw_reaches else take("b", positive=True, name="底宽")
    n = None if raw_reaches else take("n", N_MIN, N_MAX, name="糙率")
    i = None if raw_reaches else take("i", -I_ABS_MAX, I_ABS_MAX, name="底坡")
    Q = take("Q", positive=True, name="流量")
    h0 = take("h0", positive=True, name="控制水深")
    L = None if raw_reaches else take("L", positive=True, name="渠长")

    # 分段模式下逐段校验（内核 _build_infos 也会抛 ValueError，这里先给人话）
    if raw_reaches:
        for k, r in enumerate(raw_reaches):
            tag = "第%d段" % (k + 1)
            for key, lo, hi, pos, cn in (
                    ("L", None, None, True, "渠长"),
                    ("b", None, None, True, "底宽"),
                    ("n", N_MIN, N_MAX, False, "糙率"),
                    ("i", -I_ABS_MAX, I_ABS_MAX, False, "底坡"),
                    ("m", M_MIN, M_MAX, False, "边坡系数")):
                v = _num(r.get(key)) if key != "m" else (_num(r.get(key)) or 0.0)
                if v is None:
                    violations.append({"field": "%s.%s" % (tag, key),
                                       "value": r.get(key), "rule": "%s必填" % cn,
                                       "message": "%s %s 缺失" % (tag, cn)})
                    continue
                if pos and v <= 0:
                    violations.append({"field": "%s.%s" % (tag, key), "value": v,
                                       "rule": "%s 必须大于 0" % cn,
                                       "message": "%s %s=%g 必须大于 0" % (tag, cn, v)})
                elif lo is not None and v < lo:
                    violations.append({"field": "%s.%s" % (tag, key), "value": v,
                                       "rule": "%s ≥ %g" % (cn, lo),
                                       "message": "%s %s=%g 小于下限 %g" % (tag, cn, v, lo)})
                elif hi is not None and v > hi:
                    violations.append({"field": "%s.%s" % (tag, key), "value": v,
                                       "rule": "%s ≤ %g" % (cn, hi),
                                       "message": "%s %s=%g 超出上限 %g" % (tag, cn, v, hi)})
        # 展示用：取首段
        r0 = raw_reaches[0]
        b, n, i, L = _num(r0.get("b")), _num(r0.get("n")), _num(r0.get("i")), _num(r0.get("L"))
        m = _num(r0.get("m")) or 0.0

    if Q is not None and Q > Q_MAX:
        violations.append({"field": "Q", "value": Q, "rule": "流量量级合理",
                           "message": "流量 %g 量级异常（超过 %g），请确认单位是否写错" % (Q, Q_MAX)})
        Q = None

    if m is not None and not (M_MIN <= m <= M_MAX):
        violations.append({"field": "m", "value": m, "rule": "0 ≤ m ≤ 3",
                           "message": "边坡系数 %g 超出 [0, 3]" % m})
        m = None

    if n is not None and n > 0.05:
        warns.append("糙率 %.3f 属教学夸张取值（真实明渠一般 ≤0.05～0.07）" % n)
    if i is not None and abs(i) < 1e-12:
        warns.append("底坡为 0，判为平坡：无正常水深，只可能出现 H2 / H3")
        facts["flat_slope"] = True

    rec("b", "Reach.b", b, "m")
    rec("m", "Reach.m", m, "矩形取 0")
    rec("n", "Reach.n", n, "0.008~0.30")
    rec("i", "Reach.i", i, "平坡填 0，负坡填负值")
    rec("Q", "Params.Q", Q, "m³/s")
    rec("h0", "Boundary.h", h0, "m")
    rec("L", "Reach.x_end - x_start", L, "m")

    # ---- 补充量：水位、渠底高程 ----
    zw = _num(slots.get("zw")) or _extract(
        text, r"(?:水位|水面高程|水面标高)(?:为|是|=|:)?\s*(-?\d+(?:\.\d+)?)")
    zb_raw = _num(slots.get("z_b0")) or _extract(
        text, r"渠底(?:高程|标高)(?:为|是|=|:)?\s*(-?\d+(?:\.\d+)?)")
    z_b0 = 0.0 if zb_raw is None else zb_raw
    rec("z_b0", "Reach.z_b_start", z_b0, "默认 0（未给时取 0）")

    # ---- 下游边界（优先级见方案 2.3）----
    dtype = slots.get("downstream")
    if dtype == "normal_depth":
        down = Boundary("normal_depth")
        if i is not None and abs(i) < 1e-12:
            warns.append("平坡渠道没有正常水深，normal_depth 边界无效，请改给水深或水位")
            facts["contradiction"] = facts.get("contradiction") or "H3"
    elif dtype == "given_zw":
        if zw is None:
            down = None
        else:
            down = Boundary("given_zw", zw=zw)
            if zb_raw is None:
                warns.append("给的是下游水位 %.3f m，但没给渠底高程，已按渠底高程 0 处理"
                             "（此时下游水深 = %.3f m）" % (zw, zw))
    elif dtype == "free_outfall":
        down = Boundary("free_outfall")
        facts["boundary_explicit"] = True
    elif h0 is not None:
        # 普通的「水深 2 米」是最常见的 A 类控制水深，不算边界推定；
        # 只有「下游/渠末水深」「水位」「正常水深」「渠首水深」才归 C 类。
        down = Boundary("given_h", h=h0)
        dtype = "given_h"
    else:
        down = Boundary("free_outfall")
        dtype = "free_outfall"
        # 既没给水深、也没说渠末条件：可以先用自由跌水试算，
        # 但按方案 E4 的口径，必须把「控制水深」作为缺项追问出来。
        facts["need_boundary"] = True
        warns.append("未给定下游条件，暂按渠末自由跌水处理（建议补充控制水深或渠末水位）")

    if dtype == "given_zw":
        rec("zw", "Boundary.zw", zw, "需配合渠底高程换算水深")

    # ---- 上游边界：表单显式给定优先，其次看原句「渠首/上游/进口水深」----
    up = Boundary("none")
    up_type = slots.get("upstream")
    if up_type == "given_h":
        up_h = _num(slots.get("up_h"))
        if up_h is None:
            up_h = h0
        if up_h is not None:
            up = Boundary("given_h", h=up_h)
            rec("up_h", "Boundary.h（上游）", up_h, "表单指定的渠首水深")
            facts["upstream_control"] = True
            # 上游给定时，下游按自由跌水（除非已经是水位控制）
            if dtype != "given_zw" or not zw:
                down = Boundary("free_outfall")
                dtype = "free_outfall"
    elif up_type == "none":
        up = Boundary("none")
    elif re.search(r"(渠首|上游|入口|进口)水深", text) and h0 is not None:
        up = Boundary("given_h", h=h0)
        down = Boundary("free_outfall") if dtype != "given_zw" or not zw else Boundary(
            "given_zw", zw=zw)
        rec("h0", "Boundary.h（上游）", h0, "原句指明渠首水深")
        warns.append("原句指明渠首水深，已改作上游控制，下游按自由跌水")
        facts["upstream_control"] = True

    rec("downstream", "Boundary.type", dtype,
        "free_outfall / given_h / given_zw / normal_depth")

    # ---- 缺失项 ----
    missing = []
    if section is None:
        missing.append("section")
    if b is None:
        missing.append("b")
    if n is None:
        missing.append("n")
    if i is None:
        missing.append("i")
    if Q is None:
        missing.append("Q")
    if L is None:
        missing.append("L")
    if section == "trapezoidal" and m is None:
        missing.append("m")
    if facts.get("need_boundary"):
        missing.append("h0")
    if dtype == "given_zw" and zw is None:
        missing.append("zw")
    if dtype == "given_h" and h0 is None:
        missing.append("h0")
    if down is None:
        down = Boundary("free_outfall")

    facts["boundary"] = dtype
    facts["section_resolved"] = section

    if missing or violations:
        return {"params": None, "missing": missing, "warnings": warns,
                "rows": rows, "violations": violations, "facts": facts}

    # ---- 组 Params（支持多段覆盖）----
    mode = slots.get("mode") or "teaching"
    alpha = 1.05 if mode == "engineering" else 1.0
    beta = alpha

    raw_reaches2 = (overrides or {}).get("reaches") or raw_reaches
    reaches = []
    if raw_reaches2:
        x = 0.0
        for k, r in enumerate(raw_reaches2):
            rr = Reach(index=k, name=r.get("name") or "第%d段" % (k + 1),
                       x_start=x, x_end=x + float(r["L"]), i=float(r["i"]),
                       b=float(r["b"]), m=float(r.get("m") or 0.0),
                       n=float(r["n"]), n_tag=r.get("n_tag") or "engineering",
                       z_b_start=float(r.get("z_b_start") if r.get("z_b_start") is not None else z_b0))
            reaches.append(rr)
            x = rr.x_end
    else:
        reaches.append(Reach(index=0, name="单段", x_start=0.0, x_end=float(L),
                             i=float(i), b=float(b), m=float(m or 0.0),
                             n=float(n), n_tag="teaching" if n > 0.05 else "engineering",
                             z_b_start=float(z_b0)))

    pr = Params(Q=float(Q), reaches=reaches, upstream=up, downstream=down,
                mode=mode, alpha=alpha, beta=beta,
                jump_len_formula=slots.get("jump_len_formula") or "wu",
                outfall_correction=bool(slots.get("outfall_correction") or False))

    return {"params": pr, "missing": [], "warnings": warns, "rows": rows,
            "violations": violations, "facts": facts}
