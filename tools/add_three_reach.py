# -*- coding: utf-8 -*-
"""把"三段：水跃 + 坡折跌水"算例加入预设库（矩形、梯形各一条）。

单独成脚本，避免为了加一条预设而重跑整套 48 条的搜索。
"""
import io
import json
import os
import sys

ROOT = r"C:\Users\26287\Documents\deepseek-harness\default-workspace\gvf-web"
sys.path.insert(0, ROOT)
from agent import adapter          # noqa: E402
import gvf_core                    # noqa: E402

CAT = os.path.join(ROOT, "presets_catalog.json")


def solve(params):
    a = adapter.build({"slots": dict(params), "warnings": [], "normalized": ""})
    if a["params"] is None:
        return None
    try:
        return gvf_core.solve(a["params"])
    except Exception:
        return None


def make(m):
    return {"Q": 10, "mode": "teaching", "jump_len_formula": "wu",
            "reaches": [{"L": 300, "i": 0.001, "b": 5, "m": m, "n": 0.015},
                        {"L": 200, "i": 0.050, "b": 5, "m": m, "n": 0.015},
                        {"L": 300, "i": 0.001, "b": 5, "m": m, "n": 0.015}],
            "downstream": "given_h", "h0": 1.2,
            "upstream": "none", "up_h": None}


def info(res):
    return [{"L": round(r.L, 1), "i": r.i, "b": r.b, "m": r.m, "n": r.n,
             "slope": r.slope_class,
             "h0": None if r.h0 is None else round(r.h0, 4),
             "hc": round(r.hc, 4), "ic": round(r.ic, 6)} for r in res.reaches]


def summary(res):
    jump = next((t for t in res.transitions if t.kind == "jump"), None)
    return {"types": [s.curve_type for s in res.segments],
            "transitions": [{"kind": t.kind, "x": round(t.x, 1)} for t in res.transitions],
            "jump_x": round(jump.x, 1) if jump else None}


def main():
    data = json.load(io.open(CAT, encoding="utf-8"))
    items = data["items"]
    new = []
    for suffix, m, tag in (("", 0.0, "矩形"), ("_T", 1.5, "梯形")):
        p = make(m)
        res = solve(p)
        if res is None or res.status != "ok":
            print("  [跳过] %s 计算失败" % tag)
            continue
        kinds = [t.kind for t in res.transitions]
        if "jump" not in kinds or "critical" not in kinds:
            print("  [跳过] %s 现象不全：%s" % (tag, kinds))
            continue
        sm = summary(res)
        item = {"key": "combo_three" + suffix, "group": "坡度组合",
                "label": "三段 · 缓→陡→缓（水跃 + 坡折跌水·%s）" % tag,
                "desc": "%s；水跃在 %.0f m，坡折跌水在 %.0f m"
                        % (" → ".join(sm["types"]), sm["jump_x"] or 0,
                           next(t["x"] for t in sm["transitions"] if t["kind"] == "critical")),
                "params": p, "expect_types": sm["types"],
                "expect_jump": True, "expect_transition": "critical",
                "reaches": info(res)}
        new.append(item)
        print("  [OK] %-16s %s  衔接=%s" % (item["key"], sm["types"], sm["transitions"]))

    keys = {it["key"] for it in items}
    for it in new:
        if it["key"] in keys:
            idx = next(i for i, x in enumerate(items) if x["key"] == it["key"])
            items[idx] = it
        else:
            items.append(it)

    json.dump(data, io.open(CAT, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    print("写入 %s（%d 条）" % (CAT, len(items)))


if __name__ == "__main__":
    main()
