# -*- coding: utf-8 -*-
"""方案 B1（圣维南全方程 · Preissmann 隐式）验收。

三条硬判据（与 A 同口径，便于对照）：
  1. 恒定极限：与 gvf_core 恒定解逐点对比，平均 <1%、最大 <2%
  2. 质量守恒：<0.5%
  3. 回水逆传：下游水位阶跃的传播速度 ≈ (c − v)，偏差 <20%

另外确认：A（扩散波）结果不受影响；HTTP 两个模型都能跑。
"""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from unsteady import preissmann as UP     # noqa: E402
from unsteady import solver as US         # noqa: E402
from agent import adapter                 # noqa: E402
import gvf_core                           # noqa: E402

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step22-preissmann.txt")
lines = []
ok = total = 0

B, M, N, I0, Q = 5.0, 0.0, 0.015, 0.001, 10.0


def check(label, cond, detail=""):
    global ok, total
    total += 1
    if cond:
        ok += 1
    lines.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def req(path, payload=None, timeout=240):
    if payload is None:
        r = urllib.request.Request(BASE + path)
    else:
        r = urllib.request.Request(
            BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")


def gvf_profile(L, h_dn, Nx):
    slots = {"Q": Q, "mode": "teaching",
             "reaches": [{"L": L, "i": I0, "b": B, "m": M, "n": N}],
             "downstream": "given_h", "h0": h_dn, "upstream": "none"}
    a = adapter.build({"slots": slots, "warnings": [], "normalized": ""})
    res = gvf_core.solve(a["params"])
    sg = res.segments[0]
    xs = np.asarray(sg.x, float)
    hs = np.asarray(sg.h, float)
    o = np.argsort(xs)
    return xs[o], hs[o]


def main():
    hn = UP.normal_depth(B, M, N, I0, Q)
    lines.append("=== 基础 ===")
    check("正常水深为正", hn > 0, round(hn, 4))

    # ---------- 1. 恒定极限 ----------
    lines.append("")
    lines.append("=== 判据一：恒定极限 vs gvf_core ===")
    L = 5000.0
    Nx = 61
    h_dn = 1.4 * hn
    xs, hs = gvf_profile(L, h_dn, Nx)
    x_uni = np.linspace(0.0, L, Nx)
    h_init = np.interp(x_uni, xs, hs)
    t0 = time.time()
    r = UP.run(L=L, b=B, m=M, n=N, i0=I0, Q0=Q, Qpeak=Q, t_peak=3600, t_base=3601,
               t_end=6 * 3600, h_dn=h_dn, h_init=h_init, Nx=Nx, dt=30.0)
    cost = time.time() - t0
    h_end = np.array(r["h_snap"][-1])
    hg = np.interp(x_uni, xs, hs)
    dev = np.abs(h_end - hg) / hg
    check("稳态平均偏差 < 1%", dev.mean() < 0.01, "%.2f%%" % (dev.mean() * 100))
    check("稳态最大偏差 < 2%", dev.max() < 0.02, "%.2f%%" % (dev.max() * 100))
    check("Newton 无失败", r["diagnostics"]["newton_failed"] == 0,
          r["diagnostics"]["newton_failed"])
    check("单次 6 h 模拟 < 10 s", cost < 10, "%.1f s" % cost)
    lines.append("  平均 %.2f%%、最大 %.2f%%、耗时 %.1f s、Newton 合计 %d 次"
                 % (dev.mean() * 100, dev.max() * 100, cost, r["diagnostics"]["newton_iters"]))

    # ---------- 2. 质量守恒 ----------
    lines.append("")
    lines.append("=== 判据二：质量守恒 ===")
    check("相对误差 < 0.5%", r["diagnostics"]["mass_err"] < 0.005,
          "%.4f%%" % (r["diagnostics"]["mass_err"] * 100))

    # ---------- 3. 回水逆传 ----------
    lines.append("")
    lines.append("=== 判据三：回水逆传速度 ===")
    L2 = 2000.0
    Nx2 = 41
    v = Q / (B * hn)
    c = math.sqrt(9.81 * hn)
    cw = c - v
    t0 = time.time()
    r2 = UP.run(L=L2, b=B, m=M, n=N, i0=I0, Q0=Q, Qpeak=Q, t_peak=3600, t_base=3601,
                t_end=3 * 3600, h_dn=1.3 * hn, h_init=np.full(Nx2, hn),
                Nx=Nx2, dt=10.0)
    T = np.array(r2["t"])

    def arrival(idx, frac=0.1):
        H = np.array(r2["stations"][idx]["h"])
        if abs(H[-1] - H[0]) < 1e-6:
            return None
        th = H[0] + frac * (H[-1] - H[0])
        return float(T[int(np.argmax(H >= th))])

    ta, tb = arrival(2), arrival(3)
    check("下游水位阶跃能传到上游（有响应）", ta is not None and tb is not None,
          (ta, tb))
    if ta is not None and tb is not None and tb > ta:
        dx = 0.25 * L2
        c_meas = dx / (tb - ta)
        err = abs(c_meas - cw) / cw
        check("逆传速度与 (c−v) 偏差 < 20%", err < 0.20,
              "实测 %.2f / 理论 %.2f m/s（%.1f%%）" % (c_meas, cw, err * 100))
        lines.append("  实测逆传 %.2f m/s，理论 c−v=%.2f m/s" % (c_meas, cw))
    check("回水算例质量守恒 < 0.5%", r2["diagnostics"]["mass_err"] < 0.005,
          "%.4f%%" % (r2["diagnostics"]["mass_err"] * 100))
    lines.append("  回水算例耗时 %.1f s" % (time.time() - t0))

    # ---------- 4. A 未被影响 ----------
    lines.append("")
    lines.append("=== 回归：A（扩散波）===")
    r3 = US.run(L=3000, b=B, m=M, n=N, i0=I0, Q0=Q, Qpeak=20,
                t_peak=1800, t_base=5400, t_end=3 * 3600, Nx=41)
    check("A 仍可计算", r3["diagnostics"]["mass_err"] < 0.01,
          "%.4f%%" % (r3["diagnostics"]["mass_err"] * 100))
    peaks = [s["peak_Q"] for s in r3["stations"]]
    check("A 仍体现削峰", peaks[0] > peaks[-1], [round(p, 2) for p in peaks])

    # ---------- 5. HTTP ----------
    lines.append("")
    lines.append("=== HTTP：两个模型 ===")
    common = {"L": 3000, "b": B, "m": M, "n": N, "i0": I0, "Q0": Q, "Qpeak": 20,
              "t_peak": 1800, "t_base": 5400, "t_end": 3 * 3600, "Nx": 41}
    code, j1 = req("/api/unsteady/run", dict(common, model="diffusion"))
    check("扩散波接口 ok", code == 200 and j1.get("ok"), j1.get("error"))
    code, j2 = req("/api/unsteady/run", dict(common, model="preissmann"))
    check("全方程接口 ok", code == 200 and j2.get("ok"), j2.get("error"))
    if j2.get("ok"):
        rr = j2["result"]
        check("返回模型标记", rr.get("model") == "preissmann", rr.get("model"))
        check("返回快照与断面", len(rr["t_snap_hours"]) >= 4 and len(rr["stations"]) == 5,
              (len(rr["t_snap_hours"]), len(rr["stations"])))
        check("诊断含 Newton 信息", "newton_iters" in rr["diagnostics"],
              list(rr["diagnostics"])[:5])
    lines.append("")

    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
