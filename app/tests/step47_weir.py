# -*- coding: utf-8 -*-
"""V4.13.0 宽顶堰物理内核验收（自由/淹没/混排/剖面初始场）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unsteady import preissmann as up  # noqa: E402

OK = TOTAL = 0
LINES = []


def check(label, cond, detail=""):
    global OK, TOTAL
    TOTAL += 1
    if cond:
        OK += 1
    LINES.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def run(Q, hd=1.5, zs=0.5, bw=5.0, C=0.35, gate=None):
    base = dict(L=2000, b=5, m=0, n=0.015, i0=0.001,
                Q0=Q, Qpeak=Q, t_peak=3600, t_base=7200,
                t_end=6 * 3600, Nx=61, h_dn=hd)
    w = dict(type="weir", s=1000, b_w=bw, z_s=zs, C=C)
    structs = [w] if gate is None else gate + [w]
    return up.run(**base, gate={"structures": structs})


def main():
    # 1) 自由出流：Q=5/10/20 与堰流公式对照
    for Q in (5, 10, 20):
        r = run(Q)
        st = r["structures"][-1]
        H = max(st["hu"][-1] - 0.5, 1e-6)
        qf = 0.35 * 5 * H ** 1.5
        d = r["diagnostics"]
        check("自由出流 Q=%d 可算" % Q, d.get("status") == "ok", d.get("status"))
        check("Q=%d 无 Newton 失败" % Q, d.get("newton_failed") == 0,
              d.get("newton_failed"))
        check("Q=%d 与公式偏差 <1%%" % Q,
              abs(st["Q"][-1] - Q) / Q < 0.01, st["Q"][-1])
        check("Q=%d 堰上水头关系 <2%%" % Q,
              abs(qf - Q) / Q < 0.02, qf)
        check("Q=%d 水量守恒 <0.5%%" % Q,
              d.get("mass_err", 1) < 0.005, d.get("mass_err"))

    # 2) 堰顶抬高：上游水位应上升
    lo = run(10, zs=0.2)["structures"][-1]["hu"][-1]
    hi = run(10, zs=0.8)["structures"][-1]["hu"][-1]
    check("堰顶抬高后上游水位上升", hi > lo + 0.3, (lo, hi))

    # 3) 淹没出流：下游高水位 → 堰上水位抬高、淹没系数 <1
    free_hu = run(10, hd=1.5)["structures"][-1]["hu"][-1]
    r = run(10, hd=3.8)
    st = r["structures"][-1]
    check("淹没时堰上水位抬高", st["hu"][-1] > free_hu + 0.1, (free_hu, st["hu"][-1]))
    check("流态判为 submerged", st.get("regime") == "submerged", st.get("regime"))
    check("淹没系数 <1", (st.get("sigma") or 1.0) < 0.99, st.get("sigma"))
    check("淹没工况仍收敛", r["diagnostics"].get("status") == "ok",
          r["diagnostics"].get("status"))
    check("淹没工况水量", r["diagnostics"].get("mass_err", 1) < 0.005,
          r["diagnostics"].get("mass_err"))

    # 4) 闸 + 堰混排
    g = dict(type="gate", s=500, b_g=5, z_s=0, mu=0.62, a0=1.0, a_end=0.6,
             t_close=2880, t_ramp=1800)
    r = run(20, gate=[g])
    d = r["diagnostics"]
    check("闸+堰混排可算", d.get("status") == "ok", d.get("status"))
    check("混排闸/堰计数", d.get("gate_count") == 1 and d.get("weir_count") == 1,
          (d.get("gate_count"), d.get("weir_count")))
    check("混排水量 <0.5%", d.get("mass_err", 1) < 0.005, d.get("mass_err"))

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
