# -*- coding: utf-8 -*-
"""恒定流内核算术基线：记录 solve() 的全部数值结果，用于改动前后逐字段对比。

用法：
  python tests/golden_snapshot.py before   # 改动前生成基线
  python tests/golden_snapshot.py after    # 改动后生成
  python tests/golden_snapshot.py diff     # 比较两份，输出差异
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import adapter          # noqa: E402
import gvf_core                    # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "golden_before.json")
AFTR = os.path.join(HERE, "golden_after.json")
CAT = os.path.join(ROOT, "presets_catalog.json")


def hand_cases():
    cases = {}
    cases["two_reach_mild_steep"] = {
        "Q": 10, "reaches": [{"L": 300, "i": 0.001, "b": 5, "m": 0, "n": 0.015},
                             {"L": 300, "i": 0.02, "b": 5, "m": 0, "n": 0.015}],
        "downstream": "free_outfall", "upstream": "none"}
    cases["jump_steep_mild"] = {
        "Q": 10, "reaches": [{"L": 150, "i": 0.05, "b": 5, "m": 0, "n": 0.015},
                             {"L": 450, "i": 0.001, "b": 5, "m": 0, "n": 0.015}],
        "downstream": "given_h", "h0": 1.1, "upstream": "given_h", "up_h": 0.30}
    cases["M1_backwater"] = {
        "Q": 10, "reaches": [{"L": 600, "i": 0.001, "b": 5, "m": 0, "n": 0.015}],
        "downstream": "given_h", "h0": 2.0, "upstream": "none"}
    cases["trapezoid_trap"] = {
        "Q": 12, "reaches": [{"L": 500, "i": 0.0015, "b": 2.5, "m": 2.0, "n": 0.025}],
        "downstream": "free_outfall", "upstream": "none"}
    cases["flat"] = {
        "Q": 10, "reaches": [{"L": 500, "i": 0.0, "b": 5, "m": 0, "n": 0.015}],
        "downstream": "given_h", "h0": 1.2, "upstream": "none"}
    cases["adverse"] = {
        "Q": 10, "reaches": [{"L": 500, "i": -0.0005, "b": 5, "m": 0, "n": 0.015}],
        "downstream": "given_h", "h0": 1.2, "upstream": "none"}
    cases["three_flat_to_steep"] = {
        "Q": 10, "reaches": [{"L": 300, "i": 0.0, "b": 5, "m": 0, "n": 0.015},
                             {"L": 300, "i": 0.02, "b": 5, "m": 0, "n": 0.015}],
        "downstream": "free_outfall", "upstream": "none"}
    cases["four_alt"] = {
        "Q": 10, "reaches": [{"L": 150, "i": 0.05, "b": 5, "m": 0, "n": 0.015},
                             {"L": 200, "i": 0.001, "b": 5, "m": 0, "n": 0.015},
                             {"L": 150, "i": 0.05, "b": 5, "m": 0, "n": 0.015},
                             {"L": 200, "i": 0.001, "b": 5, "m": 0, "n": 0.015}],
        "downstream": "given_h", "h0": 1.2, "upstream": "given_h", "up_h": 0.32}
    return cases


def summarize(res):
    out = {"status": res.status,
           "reaches": [{"h0": None if r.h0 is None else round(r.h0, 10),
                        "hc": round(r.hc, 10), "ic": round(r.ic, 10),
                        "slope": r.slope_class} for r in res.reaches],
           "segments": [], "transitions": [], "warnings": [],
           "key_numbers": {}}
    for s in res.segments:
        out["segments"].append({
            "reach": s.reach_index, "type": s.curve_type, "dir": s.direction,
            "x0": round(float(min(s.x)), 8), "x1": round(float(max(s.x)), 8),
            "h0": round(float(s.h[0]), 10), "h1": round(float(s.h[-1]), 10),
            "n": int(len(s.x))})
    for t in res.transitions:
        out["transitions"].append({"kind": t.kind, "x": round(float(t.x), 8),
                                   "h1": None if not t.data else t.data.get("h1"),
                                   "h2": None if not t.data else t.data.get("h2")})
    out["warnings"] = sorted(w.code for w in res.warnings)
    kn = (res.facts or {}).get("key_numbers") or {}
    out["key_numbers"] = {k: (round(v, 10) if isinstance(v, float) else v)
                          for k, v in kn.items() if isinstance(v, (int, float))}
    return out


def collect():
    out = {}
    cases = hand_cases()
    try:
        data = json.load(open(CAT, encoding="utf-8"))
        for it in data.get("items", []):
            cases["preset:" + it["key"]] = it["params"]
    except Exception as e:
        print("预设库读取失败:", e)
    for name, slots in cases.items():
        try:
            a = adapter.build({"slots": dict(slots), "warnings": [], "normalized": ""})
            if a["params"] is None:
                out[name] = {"error": "adapter 拒绝", "missing": a.get("missing")}
                continue
            out[name] = summarize(gvf_core.solve(a["params"]))
        except Exception as e:
            out[name] = {"error": "%s: %s" % (type(e).__name__, e)}
    return out


EXPECTED = {"four_alt"}   # 已知的预期改进：多跃位（step 2）


def diff(a, b):
    keys = sorted(set(a) | set(b))
    bad = 0
    for k in keys:
        if k not in a or k not in b:
            print("  [缺失] %s" % k); bad += 1; continue
        sa, sb = a[k], b[k]
        if k in EXPECTED:
            print("  [预期变化] %s（多跃位支持，已核对）" % k)
            continue
        # 允许 warnings 增加
        wa = set(sa.get("warnings", []))
        wb = set(sb.get("warnings", []))
        if not wa <= wb:
            print("  [警告减少] %s: %s → %s" % (k, wa, wb)); bad += 1
        sa2 = dict(sa); sb2 = dict(sb)
        sa2.pop("warnings", None); sb2.pop("warnings", None)
        if sa2 != sb2:
            bad += 1
            print("  [数值变化] %s" % k)
            for f in ("status", "reaches", "segments", "transitions", "key_numbers"):
                if sa2.get(f) != sb2.get(f):
                    print("    字段 %s 不同" % f)
                    if f in ("segments", "transitions"):
                        print("      before:", str(sa2.get(f))[:160])
                        print("      after :", str(sb2.get(f))[:160])
    print()
    print("对比 %d 个算例，%d 处不一致" % (len(keys), bad))
    return bad


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "before"
    if mode == "diff":
        a = json.load(open(BASE, encoding="utf-8"))
        b = json.load(open(AFTR, encoding="utf-8"))
        sys.exit(1 if diff(a, b) else 0)
    data = collect()
    path = BASE if mode == "before" else AFTR
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("%s：%d 个算例 -> %s" % (mode, len(data), path))
