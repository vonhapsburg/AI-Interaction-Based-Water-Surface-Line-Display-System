# -*- coding: utf-8 -*-
"""在真实内核上搜索参数，构造"12 型水面线 + 组合"预设库。

原则：每一条预设都必须先 solve() 验证确实出现目标现象，才写进 catalog。
跑法：python tools/build_catalog.py
产物：presets_catalog.json（供 /api/presets 与前端使用）
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import adapter          # noqa: E402
import gvf_core                    # noqa: E402

OUT = os.path.join(ROOT, "presets_catalog.json")

B, N, Q = 5.0, 0.015, 10.0


def make(reaches, down="free_outfall", h0=None, up="none", up_h=None):
    return {"Q": Q, "mode": "teaching", "jump_len_formula": "wu",
            "reaches": reaches, "downstream": down, "h0": h0,
            "upstream": up, "up_h": up_h}


def run(params):
    a = adapter.build({"slots": dict(params), "warnings": [], "normalized": ""})
    if a["params"] is None:
        return None
    try:
        return gvf_core.solve(a["params"])
    except Exception:
        return None


def types_of(res):
    return [s.curve_type for s in res.segments]


def reach_info(res):
    out = []
    for r in res.reaches:
        out.append({"L": round(r.L, 1), "i": r.i, "b": r.b, "m": r.m, "n": r.n,
                    "slope": r.slope_class,
                    "h0": None if r.h0 is None else round(r.h0, 4),
                    "hc": round(r.hc, 4), "ic": round(r.ic, 6)})
    return out


def has_kind(res, kind):
    return any(t.kind == kind for t in res.transitions)


def single(i, down, h0=None, up="none", up_h=None, b=B, n=N, q=Q, L=600.0, m=0.0):
    return make([{"L": L, "i": i, "b": b, "m": m, "n": n}], down, h0, up, up_h)


def find_single_type(target, m=0.0):
    """在若干候选里找能复现目标型号的参数。m=0 矩形，m=1.5 梯形。"""
    # 候选：坡度 × 控制方式 × 控制水深系数
    slopes = {
        "M": [0.0010, 0.0008, 0.0015, 0.0005],
        "S": [0.0200, 0.0500, 0.0100, 0.0300],
        "C": [], "H": [0.0], "A": [-0.0005, -0.0010],
    }
    letter = target[0]
    zone = int(target[1])

    # 临界坡：先用公式算出来再逼近
    if letter == "C":
        probe = run(single(0.001, "free_outfall", m=m))
        if probe is None:
            return None
        ic = probe.reaches[0].ic
        slopes["C"] = [ic * 1.0, ic * 1.0005, ic * 0.9995]

    found = []            # (score, params, result)
    for i in slopes[letter]:
        base = run(single(i, "free_outfall", m=m))
        if base is None:
            continue
        r = base.reaches[0]
        hn, hc = r.h0, r.hc
        if letter in ("H", "A"):
            hn = None
        # 由区间反推控制水深
        if zone == 1:
            ref = max([x for x in (hn, hc) if x], default=hc)
            controls = [("given_h", ref * k) for k in (1.4, 1.6, 2.0, 2.6, 3.2)]
        elif zone == 2:
            lo = min([x for x in (hn, hc) if x], default=hc)
            hi = max([x for x in (hn, hc) if x], default=hc)
            controls = [("given_h", (lo + hi) / 2)]
            if letter not in ("H", "A", "C"):
                controls.append(("free_outfall", None))
        else:
            ref = min([x for x in (hn, hc) if x], default=hc)
            controls = [("up", ref * k) for k in (0.5, 0.6, 0.7, 0.8, 0.9)]

        for how, h in controls:
            # 先短后长：短渠段通常能在"跨到临界"之前停下，呈现单一型号
            for L in (150.0, 200.0, 300.0, 400.0, 600.0, 900.0):
                if how == "up":
                    p = single(i, "free_outfall", None, "given_h", h, L=L, m=m)
                elif how == "given_h":
                    p = single(i, "given_h", h, L=L, m=m)
                else:
                    p = single(i, "free_outfall", L=L, m=m)
                res = run(p)
                if res is None or res.status != "ok":
                    continue
                ts = types_of(res)
                if target not in ts:
                    continue
                score = (0 if ts == [target] else (1 if ts[0] == target else 2), len(ts), L)
                found.append((score, p, res))

    if not found:
        return None
    found.sort(key=lambda x: x[0])
    return found[0][1], found[0][2]


def build_catalog(m=0.0, sfx="", tag=""):
    catalog = []
    order = ["M1", "M2", "M3", "S1", "S2", "S3", "C1", "C3",
             "H2", "H3", "A2", "A3"]
    labels = {"M": "缓坡", "S": "陡坡", "C": "临界坡", "H": "平坡", "A": "负坡"}
    zone_cn = {1: "Ⅰ区", 2: "Ⅱ区", 3: "Ⅲ区"}
    for t in order:
        got = find_single_type(t, m=m)
        if got is None:
            print("  [未找到] %s%s" % (t, tag))
            continue
        p, res = got
        catalog.append({
            "key": "type_" + t + sfx, "group": "12 型", "label": "%s · %s%s%s" % (
                t, labels[t[0]], zone_cn[int(t[1])], tag),
            "desc": "单段「%s」（%s断面），%s" % (" → ".join(types_of(res)),
                                           tag.strip("· ") or "矩形", t),
            "params": p, "expect_types": types_of(res),
            "reaches": reach_info(res),
        })
        print("  [OK] %s%s  %s" % (t, tag, types_of(res)))
    return catalog


def build_class_cases(cat, m=0.0, sfx="", tag="矩形"):
    """五类综合算例：照教材图 (a)(c)(e)(g)(i) 的分支关系组织。

    单段渠道一次只能呈现一条曲线，所以每类挑"能同时出现两条分支"的算例，
    并在说明里写清 N-N 线（正常水深）与 C-C 线（临界水深）如何划分三条支线。
    """
    by_key = {it["key"]: it for it in cat}
    spec = [
        ("class_M" + sfx, "type_M3" + sfx, "M 类 · 缓坡（%s）" % tag,
         "对照教材图 (a)：N-N 线以上为 M1，N-N 与 C-C 之间为 M2，C-C 以下为 M3。"
         "本算例从Ⅲ区起算，先走 M3，越过后形成水跃转入 M2；把渠末水深调大可得 M1。"),
        ("class_S" + sfx, "type_S1" + sfx, "S 类 · 陡坡（%s）" % tag,
         "对照教材图 (c)：C-C 线以上为 S1，C-C 与 N-N 之间为 S2，N-N 以下为 S3。"
         "本算例由下游控制向上游推算，可看到 S2 过渡到 S1。"),
        ("class_C" + sfx, "type_C3" + sfx, "C 类 · 临界坡（%s）" % tag,
         "对照教材图 (e)：C-C 与 N-N 重合，只有 C1（上方）与 C3（下方）两支。"
         "临界坡对参数极敏感，i 必须正好等于 i_c。"),
        ("class_H" + sfx, "type_H3" + sfx, "H 类 · 平坡（%s）" % tag,
         "对照教材图 (g)：平坡没有 N-N 线，C-C 线以上为 H2、以下为 H3。"),
        ("class_A" + sfx, "type_A3" + sfx, "A 类 · 负坡（%s）" % tag,
         "对照教材图 (i)：负坡同样没有 N-N 线，C-C 线以上为 A2、以下为 A3。"),
    ]
    out = []
    for key, src, label, desc in spec:
        it = by_key.get(src)
        if it is None:
            print("  [综合算例缺源] %s ← %s" % (key, src))
            continue
        out.append({"key": key, "group": "五类综合", "label": label, "desc": desc,
                    "params": it["params"], "expect_types": it["expect_types"],
                    "reaches": it["reaches"]})
        print("  [OK] %-12s ← %-10s %s" % (key, src, it["expect_types"]))
    return out


def build_combos_and_cases(m=0.0, sfx="", tag="矩形"):
    out = []
    # 缓→缓
    p = make([{"L": 400, "i": 0.0010, "b": B, "m": m, "n": N},
              {"L": 400, "i": 0.0006, "b": B, "m": m, "n": N}],
             down="given_h", h0=2.0)
    r = run(p)
    if r and r.status == "ok":
        out.append({"key": "combo_mild_mild" + sfx, "group": "坡度组合",
                    "label": "缓坡 → 缓坡（" + tag + "）", "desc": "两种缓坡拼接，%s" % " → ".join(types_of(r)),
                    "params": p, "expect_types": types_of(r), "reaches": reach_info(r)})
    # 陡→陡
    p = make([{"L": 300, "i": 0.020, "b": B, "m": m, "n": N},
              {"L": 300, "i": 0.050, "b": B, "m": m, "n": N}],
             up="given_h", up_h=0.35)
    r = run(p)
    if r and r.status == "ok":
        out.append({"key": "combo_steep_steep" + sfx, "group": "坡度组合",
                    "label": "陡坡 → 陡坡（" + tag + "）", "desc": "%s" % " → ".join(types_of(r)),
                    "params": p, "expect_types": types_of(r), "reaches": reach_info(r)})
    # 缓→陡
    p = make([{"L": 300, "i": 0.001, "b": B, "m": m, "n": N},
              {"L": 300, "i": 0.020, "b": B, "m": m, "n": N}])
    r = run(p)
    if r and r.status == "ok":
        out.append({"key": "combo_mild_steep" + sfx, "group": "坡度组合",
                    "label": "缓坡 → 陡坡（坡折临界·" + tag + "）",
                    "desc": "%s" % " → ".join(types_of(r)),
                    "params": p, "expect_types": types_of(r), "reaches": reach_info(r)})
    # 陡→缓（含水跃）
    p = make(
        [{"L": 200, "i": 0.050, "b": B, "m": m, "n": N},
         {"L": 400, "i": 0.001, "b": B, "m": m, "n": N}],
        down="given_h", h0=1.0, up="given_h", up_h=0.30)
    r = run(p)
    if r and r.status == "ok":
        out.append({"key": "combo_steep_mild" + sfx, "group": "坡度组合",
                    "label": "陡坡 → 缓坡（含水跃·" + tag + "）",
                    "desc": "%s" % " → ".join(types_of(r)),
                    "params": p, "expect_types": types_of(r),
                    "expect_jump": has_kind(r, "jump"), "reaches": reach_info(r)})
    # 水跌：缓坡转陡坡，坡折处临界控制形成跌水（不是只有渠末自由跌水）
    p = make([{"L": 400, "i": 0.001, "b": B, "m": m, "n": N},
              {"L": 400, "i": 0.020, "b": B, "m": m, "n": N}], down="free_outfall")
    r = run(p)
    if r and r.status == "ok" and any(t.kind == "critical" for t in r.transitions):
        out.append({"key": "case_drop" + sfx, "group": "典型现象",
                    "label": "水跌 · 坡折跌水（" + tag + "）",
                    "desc": "%s，坡折处临界控制、水面跌落" % " → ".join(types_of(r)),
                    "params": p, "expect_types": types_of(r),
                    "expect_transition": "critical", "reaches": reach_info(r)})
    # 水跌：缓坡 + 渠末自由跌水
    p = make([{"L": 600, "i": 0.001, "b": B, "m": m, "n": N}], down="free_outfall")
    r = run(p)
    if r and r.status == "ok":
        out.append({"key": "case_outfall" + sfx, "group": "典型现象",
                    "label": "水跌 · 渠末跌水（" + tag + "）",
                    "desc": "%s，渠末水跌" % " → ".join(types_of(r)),
                    "params": p, "expect_types": types_of(r),
                    "expect_transition": "outfall", "reaches": reach_info(r)})
    # 水跃：陡→缓 + 上游给定
    p = make([{"L": 150, "i": 0.050, "b": B, "m": m, "n": N},
              {"L": 450, "i": 0.001, "b": B, "m": m, "n": N}],
             down="given_h", h0=1.1, up="given_h", up_h=0.30)
    r = run(p)
    if r and r.status == "ok":
        out.append({"key": "case_jump" + sfx, "group": "典型现象",
                    "label": "水跃 · 陡转缓（" + tag + "）",
                    "desc": "%s，跃位 %.1f m" % (" → ".join(types_of(r)),
                                               next((t.x for t in r.transitions
                                                     if t.kind == "jump"), 0)),
                    "params": p, "expect_types": types_of(r),
                    "expect_transition": "jump", "reaches": reach_info(r)})
    return out


if __name__ == "__main__":
    all_items = []
    # 矩形（m=0）与梯形（m=1.5）两套，同一分组里并列展示
    for m, sfx, tag in ((0.0, "", "矩形"), (1.5, "_T", "梯形")):
        print("=== 断面：%s ===" % tag)
        print("搜索 12 型 ...")
        cat = build_catalog(m=m, sfx=sfx, tag="（%s）" % tag)
        print("构建五类综合算例 ...")
        classes = build_class_cases(cat, m=m, sfx=sfx, tag=tag)
        print("构建组合与典型现象 ...")
        rest = build_combos_and_cases(m=m, sfx=sfx, tag=tag)
        all_items += classes + cat + rest
    cat = all_items

    data = {"generated_by": "tools/build_catalog.py",
            "note": "每条都经 gvf_core.solve() 验证",
            "items": cat}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("写入 %s（%d 条）" % (OUT, len(cat)))
    for it in cat:
        print("  %-22s %-28s %s" % (it["key"], it["label"], it["expect_types"]))
