# -*- coding: utf-8 -*-
"""V4.0 闸门内边界验收：稳态过闸、关闸壅水、削峰、守恒与诊断字段。"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unsteady import preissmann as up
from unsteady import solver as us

OK = TOTAL = 0
LINES = []


def check(label, cond, detail=""):
    global OK, TOTAL
    TOTAL += 1
    if cond:
        OK += 1
    LINES.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def base(**kw):
    L, b, m, n, i0 = 2000.0, 5.0, 0.0, 0.015, 0.001
    hn = us.normal_depth(b, m, n, i0, kw.pop("Q0", 10.0))
    d = dict(L=L, b=b, m=m, n=n, i0=i0, Q0=10.0, Qpeak=10.0,
             t_peak=600.0, t_base=1200.0,
             h_dn=1.4, h_init=np.full(81, hn), Nx=81, dt=30.0,
             station_fracs=[0.0, 0.25, 0.5, 0.75, 1.0])
    d.update(kw)
    return d


def main():
    # 1) 不开闸门：与旧接口完全一致
    r0 = up.run(**base(t_end=3 * 3600))
    r1 = up.run(**base(t_end=3 * 3600, gate=None))
    diff = max(abs(a - b) for a, b in zip(r0["h_snap"][-1], r1["h_snap"][-1]))
    check("无闸门时结果与旧版一致", diff == 0.0, "max diff %.2e" % diff)
    check("无闸门诊断 gate_on=False", r1["diagnostics"]["gate_on"] is False)

    # 2) 淹没出流稳态：Q_gate 应等于上游来流，闸上水头满足闸孔公式
    gate = dict(s=1000, b_g=5, z_s=0, mu=0.62, a0=0.5, a_end=0.5,
                t_close=0, t_ramp=0)
    rs = up.run(**base(t_end=12 * 3600, gate=gate))
    g = rs["gate"]
    qf = g["Q"][-1]
    check("稳态过闸流量=上游来流（±1%）", abs(qf - 10.0) / 10.0 < 0.01,
          "Q=%.3f" % qf)
    Hu_th = g["hd"][-1] + (10.0 / (0.62 * 5 * 0.5)) ** 2 / (2 * 9.81)
    err = abs(g["hu"][-1] - Hu_th) / Hu_th
    check("闸上水深符合淹没闸孔公式（±3%）", err < 0.03,
          "模拟 %.3f / 理论 %.3f，偏差 %.2f%%" % (g["hu"][-1], Hu_th, err * 100))
    check("稳态质量守恒 <0.5%", rs["diagnostics"]["mass_err"] < 0.005,
          "%.4f%%" % (rs["diagnostics"]["mass_err"] * 100))
    check("稳态 Newton 无失败", rs["diagnostics"]["newton_failed"] == 0)

    # 3) 洪水过程 + 关闸：削峰、上游壅水、单调上升
    kw = base(Qpeak=30.0, t_peak=3600.0, t_base=7200.0, t_end=8 * 3600)
    rA = up.run(**kw)
    rB = up.run(**kw, gate=dict(s=1000, b_g=5, z_s=0, mu=0.62,
                                a0=1.0, a_end=0.5, t_close=3000, t_ramp=1800))
    pk0 = rA["stations"][-1]["peak_Q"]
    pk1 = rB["stations"][-1]["peak_Q"]
    check("关闸削峰（下游峰值下降 >5%）", pk1 < pk0 * 0.95,
          "%.2f → %.2f（%.1f%%）" % (pk0, pk1, (pk0 - pk1) / pk0 * 100))
    hmaxB = max(rB["gate"]["hu"])
    check("关闸后闸上水位明显抬升", hmaxB > 3.0, "%.2f m" % hmaxB)
    check("有闸门质量守恒 <0.5%", rB["diagnostics"]["mass_err"] < 0.005,
          "%.4f%%" % (rB["diagnostics"]["mass_err"] * 100))
    check("有闸门 Newton 无失败", rB["diagnostics"]["newton_failed"] == 0)

    # 4) 开度越小，闸上水位越高
    rS = up.run(**base(t_end=10 * 3600,
                       gate=dict(s=1000, b_g=5, z_s=0, mu=0.62,
                                 a0=0.4, a_end=0.4, t_close=0, t_ramp=0)))
    check("开度 0.4 的闸上水位高于开度 0.5", rS["gate"]["hu"][-1] > g["hu"][-1],
          "%.3f vs %.3f" % (rS["gate"]["hu"][-1], g["hu"][-1]))

    # 5) 诊断字段
    d = rB["diagnostics"]
    check("诊断含闸门位置", abs(d["gate_x"] - 1000.0) < 1e-6, d["gate_x"])
    check("诊断含最终过闸流量", abs(d["gate_Q_final"] - 10.0) < 0.5,
          d["gate_Q_final"])
    gt = rB["gate"]
    n = len(gt["t"])
    check("闸门过程数组等长", all(len(gt[k]) == n for k in ("a", "Q", "hu", "hd")), n)
    check("开度按过程线变化", gt["a"][0] > gt["a"][-1],
          "%.2f → %.2f" % (gt["a"][0], gt["a"][-1]))

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
