# -*- coding: utf-8 -*-
"""V4.8.0 代码审查整改验收：初始相容、收敛上报、快照插值、底坎 z_s、诊断字段。"""
import os
import sys
import time

import numpy as np

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


def main():
    hn = us.normal_depth(5, 0, 0.015, 0.001, 10)
    base = dict(L=2000, b=5, m=0, n=0.015, i0=0.001, Q0=10, Qpeak=30,
                t_peak=3600, t_base=7200, t_end=4 * 3600, h_dn=hn,
                h_init=np.full(81, hn), Nx=81, snap_hours=[0, 0.5, 1, 2, 4])

    # 1) 合法性：i0<=0 与近全关必须报错，而不是静默改
    try:
        up.run(**dict(base, i0=0.0))
        check("i0=0 报错", False)
    except ValueError as e:
        check("i0=0 报错", "i0" in str(e), str(e)[:40])
    try:
        g = dict(s=1000, b_g=5, z_s=0, mu=0.62, a0=1.0, a_end=0.01,
                 t_close=0, t_ramp=0)
        up.run(**dict(base, gate=g))
        check("开度<0.02 报错", False)
    except ValueError as e:
        check("开度<0.02 报错", "0.02" in str(e), str(e)[:40])

    # 2) 正常算例：收敛上报与诊断字段
    t0 = time.time()
    r = up.run(**base)
    el = time.time() - t0
    d = r["diagnostics"]
    check("正常算例可用", r["stations"][-1]["peak_Q"] > 0)
    check("NaN/无失败", d["newton_failed"] >= 0 and np.isfinite(d["max_residual"]))
    check("上报最终残差", d["max_residual"] < 1e-5, d["max_residual"])
    check("状态字段", d.get("status") in ("ok", "partial"), d.get("status"))
    check("h_final 长度与 Nx 一致", len(d.get("h_final") or []) == d["nx"])
    check("max_courant 已填", d.get("max_courant") is not None and
          d["max_courant"] > 0, d.get("max_courant"))
    check("c_k_peak / h_peak_normal 已填",
          d.get("c_k_peak", 0) > 0 and d.get("h_peak_normal", 0) > 0,
          (d.get("c_k_peak"), d.get("h_peak_normal")))
    check("Courant 明显小于旧口径 7",
          d.get("max_courant", 99) < 5.0, d.get("max_courant"))

    # 3) 快照按时刻插值
    check("快照时刻精确", [round(x, 3) for x in r["t_snap_hours"]] == [0, 0.5, 1, 2, 4],
          r["t_snap_hours"])
    check("快照帧数一致", len(r["h_snap"]) == 5)
    check("末帧与 h_final 一致",
          abs(r["h_snap"][-1][-1] - d["h_final"][-1]) < 0.02,
          (r["h_snap"][-1][-1], d["h_final"][-1]))

    # 4) 初始场与下游相容（h_dn 不等于正常水深）
    r2 = up.run(**dict(base, h_dn=2.4))
    first = r2["h_snap"][0]
    check("t=0 下游边界相容", abs(first[-1] - 2.4) < 1e-6, first[-1])
    check("t=0 不是均匀场（有过渡）", max(first) - min(first) > 0.2,
          (min(first), max(first)))
    check("不相容工况仍收敛", r2["diagnostics"]["status"] == "ok",
          r2["diagnostics"].get("max_residual"))

    # 5) 底坎 z_s 真正参与
    g0 = dict(s=1000, b_g=5, z_s=0.0, mu=0.62, a0=1.0, a_end=0.6,
              t_close=0, t_ramp=0)
    r0 = up.run(**dict(base, gate=g0))
    g1 = dict(g0, z_s=1.0)
    r1 = up.run(**dict(base, gate=g1))
    h0 = max(r0["gate"]["hu"])
    h1 = max(r1["gate"]["hu"])
    check("z_s 改变闸上水位", abs(h1 - h0) > 0.1, (round(h0, 2), round(h1, 2)))
    check("z_s 抬高后过流受限（闸上更高）", h1 > h0, (round(h0, 2), round(h1, 2)))

    # 6) Newton 不收敛不再静默
    r3 = up.run(**dict(base, max_newton=1))
    d3 = r3["diagnostics"]
    check("单次 Newton 被标记为 partial", d3.get("status") == "partial",
          (d3.get("status"), d3.get("max_residual")))
    check("失败步数被计数", d3.get("newton_failed", 0) > 0, d3.get("newton_failed"))

    # 7) 摩阻时间层统一（诊断保留 theta/psi）
    check("保留 theta/psi 诊断", "theta" in d and "psi" in d)

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d，耗时 %.1f s" % (OK, TOTAL, el))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
