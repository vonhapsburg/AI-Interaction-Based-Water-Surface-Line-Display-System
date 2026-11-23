# -*- coding: utf-8 -*-
"""内核：多跃位（渐进实现）与告警验收。

覆盖：
  1. 四段交替（陡-缓-陡-缓）：必须无缺口、status=ok、含临界衔接与水跃
  2. 单水跃工况不受影响（跃位与基线一致）
  3. 无重叠区间给出 W_NO_OVERLAP
  4. 平坡接陡坡的事件命名不再是 mild_to_steep
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import adapter          # noqa: E402
import gvf_core                    # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step21-kernel.txt")
lines = []
ok = total = 0


def check(label, cond, detail=""):
    global ok, total
    total += 1
    if cond:
        ok += 1
    lines.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def solve(slots):
    a = adapter.build({"slots": dict(slots), "warnings": [], "normalized": ""})
    return gvf_core.solve(a["params"])


FOUR = {"Q": 10, "mode": "teaching",
        "reaches": [{"L": 150, "i": 0.05, "b": 5, "m": 0, "n": 0.015},
                    {"L": 200, "i": 0.001, "b": 5, "m": 0, "n": 0.015},
                    {"L": 150, "i": 0.05, "b": 5, "m": 0, "n": 0.015},
                    {"L": 200, "i": 0.001, "b": 5, "m": 0, "n": 0.015}],
        "downstream": "given_h", "h0": 1.2,
        "upstream": "given_h", "up_h": 0.32}

JUMP = {"Q": 10, "mode": "teaching",
        "reaches": [{"L": 150, "i": 0.05, "b": 5, "m": 0, "n": 0.015},
                    {"L": 450, "i": 0.001, "b": 5, "m": 0, "n": 0.015}],
        "downstream": "given_h", "h0": 1.1,
        "upstream": "given_h", "up_h": 0.30}


def main():
    # ---------- 1. 多跃位 ----------
    lines.append("=== 四段交替（陡-缓-陡-缓）===")
    r = solve(FOUR)
    codes = [w.code for w in r.warnings]
    check("解算状态为 ok", r.status == "ok", r.status)
    check("不再报 W_GAP（缺口已补）", "W_GAP" not in codes, codes)
    gaps = gvf_core._coverage_gaps(r.segments, 0.0, 700.0)
    check("全程无未覆盖区间", not gaps, gaps)
    kinds = [t.kind for t in r.transitions]
    check("出现坡折临界衔接", "critical" in kinds, kinds)
    check("出现水跃", "jump" in kinds, kinds)
    jump = next((t for t in r.transitions if t.kind == "jump"), None)
    check("跃位落在渠内", jump is not None and 0 < jump.x < 700,
          None if jump is None else round(jump.x, 1))
    lines.append("  段：%s" % [(s.curve_type, round(float(min(s.x))), round(float(max(s.x))))
                              for s in r.segments])
    lines.append("  衔接：%s" % [(t.kind, round(t.x, 1)) for t in r.transitions])
    lines.append("")

    # ---------- 2. 单水跃不受影响 ----------
    lines.append("=== 单水跃工况（回归）===")
    r2 = solve(JUMP)
    jumps = [t for t in r2.transitions if t.kind == "jump"]
    check("仍只有一个水跃", len(jumps) == 1, len(jumps))
    if jumps:
        check("跃位与基线一致（189.0 ± 0.5）", abs(jumps[0].x - 189.0) < 0.5,
              round(jumps[0].x, 2))
        check("跃长仍为正", (jumps[0].data or {}).get("L_j", 0) > 0,
              (jumps[0].data or {}).get("L_j"))
    check("单跃工况不误报 W_MULTI_JUMP", "W_MULTI_JUMP" not in [w.code for w in r2.warnings],
          [w.code for w in r2.warnings])
    lines.append("")

    # ---------- 3. 无重叠告警 ----------
    lines.append("=== 无重叠告警 ===")
    r3 = solve({"Q": 10, "mode": "teaching",
                "reaches": [{"L": 300, "i": 0.001, "b": 5, "m": 0, "n": 0.015}],
                "downstream": "given_h", "h0": 0.3,
                "upstream": "given_h", "up_h": 3.0})
    codes3 = [w.code for w in r3.warnings]
    check("给出可读告警（不静默）",
          any(c in codes3 for c in ("W_NO_OVERLAP", "W_OVER_CONSTRAINED", "W_INLET_MISMATCH")),
          codes3)
    lines.append("")

    # ---------- 4. 平坡接陡坡的命名 ----------
    lines.append("=== 节点命名 ===")
    r4 = solve({"Q": 10, "mode": "teaching",
                "reaches": [{"L": 300, "i": 0.0, "b": 5, "m": 0, "n": 0.015},
                            {"L": 300, "i": 0.02, "b": 5, "m": 0, "n": 0.015}],
                "downstream": "free_outfall", "upstream": "none"})
    infos = gvf_core._build_infos(
        adapter.build({"slots": {"Q": 10, "mode": "teaching",
                                 "reaches": [{"L": 300, "i": 0.0, "b": 5, "m": 0, "n": 0.015},
                                             {"L": 300, "i": 0.02, "b": 5, "m": 0, "n": 0.015}],
                                 "downstream": "free_outfall", "upstream": "none"},
                       "warnings": [], "normalized": ""})["params"])
    nodes = gvf_core._build_nodes(None, infos)
    check("平坡→陡坡命名为 flat_to_steep", nodes and nodes[0]["join_type"] == "flat_to_steep",
          nodes[0]["join_type"] if nodes else None)
    check("该节点仍按临界控制", nodes and nodes[0]["control_h"] is not None,
          nodes[0]["control_h"] if nodes else None)
    lines.append("")

    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
