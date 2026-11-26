# -*- coding: utf-8 -*-
"""V4.3.0 验收：多闸门结构 + 方案对比接口。"""
import json
import os
import sys
import time
import urllib.request

import numpy as np

BASE = "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from unsteady import solver as us          # noqa: E402
from unsteady import preissmann as up      # noqa: E402

OK = TOTAL = 0
LINES = []


def check(label, cond, detail=""):
    global OK, TOTAL
    TOTAL += 1
    if cond:
        OK += 1
    LINES.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def post(path, obj, timeout=240):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    hn = us.normal_depth(5, 0, 0.015, 0.001, 10)
    kw = dict(L=2000, b=5, m=0, n=0.015, i0=0.001, Q0=10, Qpeak=30,
              t_peak=3600, t_base=7200, t_end=8 * 3600, h_dn=1.4,
              h_init=np.full(81, hn), Nx=81, snap_hours=[1, 4, 8])
    g = dict(s=1000, b_g=5, z_s=0, mu=0.62, a0=1.0, a_end=0.6,
             t_close=2880, t_ramp=1800)
    r1 = up.run(**kw, gate=g)
    r2 = up.run(**kw, gate={"structures": [g]})
    d = max(abs(a - b) for a, b in zip(r1["h_snap"][-1], r2["h_snap"][-1]))
    check("单闸与 structures 单元素零差异", d == 0.0, "max diff %.1e" % d)
    check("单闸诊断 gate_count=1", r1["diagnostics"].get("gate_count") == 1)
    check("单闸输出结构不变",
          set(r1["gate"].keys()) >= {"on", "x", "b_g", "t", "a", "Q", "hu", "hd"})

    g2 = dict(s=700, b_g=5, z_s=0, mu=0.62, a0=1.0, a_end=0.7,
              t_close=2880, t_ramp=1800)
    g3 = dict(s=1400, b_g=5, z_s=0, mu=0.62, a0=0.9, a_end=0.5,
              t_close=3600, t_ramp=1800)
    r3 = up.run(**kw, gate={"structures": [g2, g3]})
    check("双闸识别两座", r3["diagnostics"].get("gate_count") == 2,
          [s["x"] for s in r3["structures"]])
    check("双闸质量守恒 <0.5%", r3["diagnostics"]["mass_err"] < 0.005,
          "%.4f%%" % (r3["diagnostics"]["mass_err"] * 100))
    check("双闸 Newton 无失败", r3["diagnostics"]["newton_failed"] == 0)
    check("双闸削峰不弱于单闸",
          r3["stations"][-1]["peak_Q"] <= r1["stations"][-1]["peak_Q"] * 1.01,
          "%.2f vs %.2f" % (r3["stations"][-1]["peak_Q"], r1["stations"][-1]["peak_Q"]))
    check("每座闸都有过程", all(len(s["t"]) == len(s["a"]) for s in r3["structures"]))

    base = dict(L=2000, b=6, m=0, n=0.015, i0=0.001, Q0=10, Qpeak=30,
                t_peak=3600, t_base=7200, t_end=8 * 3600, Nx=81)
    t0 = time.time()
    r = post("/api/unsteady/compare", dict(base, text="闸上不能超过6米，尽量削峰"))
    el = time.time() - t0
    check("对比接口可用", r.get("ok"), r.get("error"))
    check("对比总耗时 <10 s", el < 10, "%.1f s" % el)
    check("意图解析出约束", (r.get("intent") or {}).get("constraints", {}).get("max_stage") == 6.0,
          r.get("intent"))
    plans = r.get("plans") or []
    check("默认两个方案", len(plans) == 2, len(plans))
    check("基准峰值高于方案峰值",
          all(p["peak"] < r["reference"]["peak"] for p in plans if p.get("ok")))
    feas = {round(p["peak"], 1): p.get("feasible") for p in plans if "feasible" in p}
    check("可行标记与闸上水位一致",
          all((p["hmax"] <= 6.0) == p["feasible"] for p in plans if p.get("feasible") is not None))
    check("方案质量守恒 <0.5%",
          all(p.get("mass_err", 1) < 0.005 for p in plans if p.get("ok")))

    # 多闸门方案也能进对比
    r2c = post("/api/unsteady/compare", dict(base, plans=[
        {"name": "单闸", "gate": g},
        {"name": "双闸", "gate": {"structures": [g2, g3]}},
    ]))
    check("对比支持多闸门方案", r2c.get("ok") and
          any(p.get("gate_count") == 2 for p in r2c.get("plans", []) if p.get("ok")))

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
