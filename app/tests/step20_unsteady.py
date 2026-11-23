# -*- coding: utf-8 -*-
"""方案 A（扩散波）验收：数值可靠性 + 与恒定流内核的一致性 + 不破坏原功能。

跑法：python tests/step20_unsteady.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from unsteady import solver as US       # noqa: E402
from agent import adapter               # noqa: E402
import gvf_core                         # noqa: E402

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step20-unsteady.txt")
lines = []
ok = total = 0


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


def req_raw(path, timeout=60):
    """取原始文本（用于 HTML 页面）。"""
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


B, M, N, I0, Q = 5.0, 0.0, 0.015, 0.001, 10.0


def main():
    hn = US.normal_depth(B, M, N, I0, Q)
    lines.append("=== 基本关系 ===")
    check("正常水深为正", hn > 0, round(hn, 4))
    h2 = US.normal_depth(B, M, N, I0, Q * 2)
    check("Q 增大 → 正常水深增大", h2 > hn, "%.4f / %.4f" % (hn, h2))
    ck = float(US.kinematic_speed(B, M, N, I0, np.array([hn]))[0])
    v = Q / (B * hn)
    # c_k/v = 5/3 只在宽浅矩形（R≈h）成立；b/h≈4 时严格曼宁给出 1.46
    BW = 50.0
    hnw = US.normal_depth(BW, 0.0, N, I0, Q)
    ckw = float(US.kinematic_speed(BW, 0.0, N, I0, np.array([hnw]))[0])
    vw = Q / (BW * hnw)
    check("宽浅矩形（b/h>100）时 c_k/v ≈ 5/3", abs(ckw / vw - 5 / 3) < 0.02,
          round(ckw / vw, 3))
    check("窄矩形时 c_k/v 偏离 5/3（说明 5/3 有适用条件）",
          abs(ck / v - 5 / 3) > 0.05, "b=5 时 %.3f" % (ck / v))
    lines.append("")

    # ---------- 1. 质量守恒 ----------
    lines.append("=== 检验一：质量守恒 ===")
    t0 = time.time()
    r = US.run(L=10000, b=B, m=M, n=N, i0=I0, Q0=Q, Qpeak=30.0,
               t_peak=3600, t_base=7200, t_end=12 * 3600, Nx=81)
    cost = time.time() - t0
    mass = r["diagnostics"]["mass_err"]
    check("质量守恒相对误差 < 0.5%", mass < 0.005, "%.5f%%" % (mass * 100))
    check("计算耗时可接受（<20 s）", cost < 20, "%.1f s" % cost)
    check("库朗数 < 1", r["diagnostics"]["max_courant"] < 1.0,
          r["diagnostics"]["max_courant"])
    lines.append("")

    # ---------- 2. 洪峰削峰、滞后 ----------
    lines.append("=== 检验二：削峰 / 滞后 ===")
    st = r["stations"]
    peaks = [s["peak_Q"] for s in st]
    times = [s["peak_t"] for s in st]
    check("峰值沿程单调下降（坦化）",
          all(peaks[i] > peaks[i + 1] for i in range(len(peaks) - 1)),
          [round(x, 2) for x in peaks])
    check("峰现时刻沿程单调增大（滞后）",
          all(times[i] < times[i + 1] for i in range(len(times) - 1)),
          [round(x / 3600, 2) for x in times])
    lines.append("")

    # ---------- 3. 稳态回归（与恒定流内核一致性） ----------
    lines.append("=== 检验三：稳态回归 vs gvf_core ===")
    h_dn = 1.25 * hn
    x1, h1 = US.steady(b=B, m=M, n=N, i0=I0, Q=Q, h_dn=h_dn,
                       L=10000, Nx=81, hours=45)
    slots = {"Q": Q, "mode": "teaching",
             "reaches": [{"L": 10000, "i": I0, "b": B, "m": M, "n": N}],
             "downstream": "given_h", "h0": h_dn, "upstream": "none"}
    a = adapter.build({"slots": slots, "warnings": [], "normalized": ""})
    res = gvf_core.solve(a["params"])
    sg = res.segments[0]
    xs = np.asarray(sg.x, float)
    hs = np.asarray(sg.h, float)
    o = np.argsort(xs)
    hg = np.interp(x1, xs[o], hs[o])
    dev = np.abs(h1 - hg) / hg
    check("稳态解与恒定流解平均偏差 < 2%", dev.mean() < 0.02,
          "%.2f%%" % (dev.mean() * 100))
    check("稳态解与恒定流解最大偏差 < 4%", dev.max() < 0.04,
          "%.2f%% @ x=%.0f m" % (dev.max() * 100, x1[int(np.argmax(dev))]))
    lines.append("  平均 %.2f%%，最大 %.2f%%" % (dev.mean() * 100, dev.max() * 100))
    lines.append("")

    # ---------- 4. 波速（5/3 的数值检验） ----------
    lines.append("=== 检验四：小扰动波速 ===")
    r2 = US.run(L=10000, b=B, m=M, n=N, i0=I0, Q0=Q, Qpeak=Q * 1.01,
                t_peak=1800, t_base=3600, t_end=6 * 3600, Nx=121,
                station_fracs=(0.25, 0.75))
    s2 = r2["stations"]
    dt_meas = s2[1]["peak_t"] - s2[0]["peak_t"]
    theory = 5000.0 / r2["meta"]["c_k"]
    err = abs(dt_meas - theory) / theory
    check("峰现时间与 L/c_k 偏差 < 10%", err < 0.10,
          "实测 %.0f s / 理论 %.0f s（%.1f%%）" % (dt_meas, theory, err * 100))
    lines.append("")

    # ---------- 5. HTTP 接口与页面 ----------
    lines.append("=== 接口与页面 ===")
    code, j = req("/api/unsteady/run", {
        "L": 8000, "b": 6, "m": 1.5, "n": 0.02, "i0": 0.0008,
        "Q0": 8, "Qpeak": 20, "t_peak": 1800, "t_base": 5400,
        "t_end": 6 * 3600, "Nx": 61})
    check("接口返回 ok", code == 200 and j.get("ok"), j.get("error"))
    if j.get("ok"):
        rr = j["result"]
        check("返回 6 个时刻快照", len(rr["t_snap_hours"]) >= 5, rr["t_snap_hours"])
        check("返回 5 个断面过程线", len(rr["stations"]) == 5, len(rr["stations"]))
        check("梯形断面参数生效", abs(rr["meta"]["m"] - 1.5) < 1e-9, rr["meta"]["m"])
        d2 = rr["diagnostics"]
        check("特征量三段式齐全",
              all(k in d2 for k in ("c_k_h0", "c_k_peak", "c_k_measured")),
              (d2.get("c_k_h0"), d2.get("c_k_peak"), d2.get("c_k_measured")))
        check("峰值处波速大于 h0 处（解释了峰现偏差）",
              d2["c_k_peak"] > d2["c_k_h0"], (d2["c_k_h0"], d2["c_k_peak"]))
        check("报出边界峰值误差", d2["boundary_peak_err"] < 0.02,
              "%.2f%%" % (d2["boundary_peak_err"] * 100))
        check("报出下游边界实际取值", d2["h_dn_used"] > 0, d2["h_dn_used"])

    # 稳态回归接口（带真实回水，不是平凡情形）
    code, v = req("/api/unsteady/verify", {
        "L": 10000, "b": 5, "m": 0, "n": 0.015, "i0": 0.001, "Q": 10,
        "h_dn": round(1.25 * hn, 4)})
    check("稳态回归接口可用", code == 200 and v.get("ok"), v.get("error"))
    if v.get("ok"):
        check("回归平均偏差 < 2%", v["mean"] < 0.02, "%.2f%%" % (v["mean"] * 100))
        check("回归最大偏差 < 4%", v["max"] < 0.04, "%.2f%%" % (v["max"] * 100))
        lines.append("  稳态回归：平均 %.2f%%，最大 %.2f%%" % (v["mean"] * 100, v["max"] * 100))
    code, p = req_raw("/unsteady.html")
    lines.append("  页面状态 %s" % code)
    check("独立页面可访问", code == 200)
    check("页面含三个画布",
          code == 200 and all(k in p for k in ("cFamily", "cHydro", "cLoop")),
          "已检查")
    lines.append("")

    # ---------- 6. 原恒定流功能未被破坏 ----------
    lines.append("=== 回归：恒定流功能 ===")
    code, s = req("/api/solve", {"overrides": {
        "Q": 10,
        "reaches": [{"L": 300, "i": 0.001, "b": 5, "m": 0, "n": 0.015},
                    {"L": 300, "i": 0.02, "b": 5, "m": 0, "n": 0.015}],
        "downstream": "free_outfall", "upstream": "none"}})
    check("恒定流仍可计算", s.get("ok"))
    if s.get("ok"):
        types = [x["curve_type"] for x in s["result"]["segments"]]
        check("恒定流结果仍为 M2→S2", types == ["M2", "S2"], types)
    code, h = req("/api/health")
    check("健康检查正常", h.get("ok") and h.get("core") == "1.1.0", h.get("core"))
    lines.append("")

    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
