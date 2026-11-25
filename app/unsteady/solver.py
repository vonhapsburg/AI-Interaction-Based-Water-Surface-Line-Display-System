# -*- coding: utf-8 -*-
"""明渠非恒定渐变流 · 扩散波内核（方案 A，教材 8.10.3「瞬时流态法」）

与恒定流内核 gvf_core 完全隔离：本模块不导入、不修改它。

    连续方程：  B·∂h/∂t + ∂Q/∂s = 0
    运动方程：  忽略惯性项（当地加速度、迁移加速度）
                Q = K(h)·sqrt(i0 − ∂h/∂s),   K = A·R^(2/3)/n

稳态时 dQ/ds=0，退化为 GVF 方程 dh/ds = i0 − Q²/K²，
因此"边界恒定 → 收敛到恒定解"就是与 gvf_core 的一致性检验。

数值：节点中心差分算水面坡度，通量散度推进水深，显式、步长按扩散稳定性自适应。
不支持溃坝、涌潮、闸门急变（属强非恒定，需全圣维南/激波捕捉）。
"""
import math

import numpy as np


# ---------------------------------------------------------------- 断面
def geom(b, m, h):
    h = np.maximum(np.asarray(h, dtype=float), 1e-6)
    A = h * (b + m * h)
    B = b + 2.0 * m * h
    P = b + 2.0 * h * math.sqrt(1.0 + m * m)
    R = A / P
    return A, B, P, R


def conveyance(b, m, n, h):
    A, B, P, R = geom(b, m, h)
    return A * np.power(R, 2.0 / 3.0) / n


def normal_depth(b, m, n, i0, Q):
    """均匀流水深 K(h)=Q/√i0（二分）。"""
    target = max(Q, 1e-9) / math.sqrt(max(i0, 1e-12))
    lo, hi = 1e-4, 1.0
    while float(conveyance(b, m, n, hi)) < target and hi < 1e5:
        hi *= 2.0
    for _ in range(90):
        mid = 0.5 * (lo + hi)
        if float(conveyance(b, m, n, mid)) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def kinematic_speed(b, m, n, i0, h):
    """运动波波速 c_k = (1/B)·dQ/dh（数值求导）。"""
    h = np.maximum(np.asarray(h, dtype=float), 1e-6)
    dh = np.maximum(1e-4 * h, 1e-5)
    Q1 = conveyance(b, m, n, h) * math.sqrt(i0)
    Q2 = conveyance(b, m, n, h + dh) * math.sqrt(i0)
    _, B, _, _ = geom(b, m, h)
    return (Q2 - Q1) / dh / B


def _upstream_h(b, m, n, i0, Qup, h1, dx):
    """已知下一节点 h1 与来流 Qup，反解边界水深：Q=K(h0)√(i0−(h1−h0)/dx)。"""
    def f(h0):
        s = i0 - (h1 - h0) / dx
        if s <= 1e-9:
            return -Qup
        return float(conveyance(b, m, n, h0)) * math.sqrt(s) - Qup

    lo = 1e-4
    hi = max(h1 * 3.0, 1.0)
    for _ in range(40):
        if f(hi) >= 0:
            break
        hi *= 2.0
    if f(lo) >= 0:                       # 极浅水即可通过
        return normal_depth(b, m, n, i0, Qup)
    for _ in range(70):
        mid = 0.5 * (lo + hi)
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------- 主求解
def run(L=10000.0, b=5.0, m=0.0, n=0.015, i0=0.001,
        Q0=10.0, Qpeak=30.0, t_peak=3600.0, t_base=7200.0, t_end=None,
        h_dn=None, Nx=121, snap_hours=None, station_fracs=None,
        safety=0.7, max_steps=60000, steady_tol=None):
    i0 = max(float(i0), 1e-6)
    L = float(L)
    Q0 = float(max(Q0, 1e-6))
    Qpeak = float(max(Qpeak, Q0))
    t_peak = float(max(t_peak, 60.0))
    t_base = float(max(t_base, t_peak + 60.0))
    t_end = float(t_end or max(t_base * 2.0, 6 * 3600.0))
    snap_hours = list(snap_hours or (0, 0.5, 1, 2, 4, 8))
    station_fracs = list(station_fracs or (0.0, 0.25, 0.5, 0.75, 1.0))

    def Q_up(t):
        if t <= t_peak:
            return Q0 + (Qpeak - Q0) * (t / t_peak)
        if t <= t_base:
            return Qpeak - (Qpeak - Q0) * ((t - t_peak) / max(t_base - t_peak, 1.0))
        return Q0

    h_n0 = normal_depth(b, m, n, i0, Q0)
    h_dn = float(h_n0 if h_dn is None else h_dn)

    # 网格与步长：扩散稳定条件 dt ≤ dx²/(2D)，D ≈ Q/(2 B i0)
    B_n = b + 2.0 * m * h_n0
    D = max(Qpeak, 1.0) / (2.0 * max(B_n, 1e-3) * i0)
    Nx_req = int(Nx)
    Nx = int(max(60, min(int(Nx), 601)))
    _cap = max_steps if steady_tol is None else max(max_steps, 150000)
    for _ in range(12):
        dx = L / (Nx - 1)
        c_max = float(np.max(kinematic_speed(b, m, n, i0, np.array([h_n0, h_dn, 3 * h_n0]))))
        dt = min(safety * dx * dx / (2.0 * D), safety * dx / max(c_max, 1e-6), 30.0)
        steps = int(math.ceil(t_end / dt))
        if steps <= _cap:
            break
        Nx = max(60, int(Nx * 0.75))
    else:
        dt = t_end / _cap
        steps = _cap
    auto_coarsened = (Nx < Nx_req)

    x = np.linspace(0.0, L, Nx)
    stations = sorted({int(round(f * (Nx - 1))) for f in station_fracs})

    # 初始场：与下游水位相容的平滑过渡，避免 t=0 的虚假冲击波
    h = np.full(Nx, h_n0)
    if abs(h_dn - h_n0) > 1e-9:
        w = np.linspace(0.0, 1.0, Nx) ** 2
        h = h_n0 + (h_dn - h_n0) * w

    snap_t = [hr * 3600.0 for hr in snap_hours if hr * 3600.0 <= t_end]
    h_snap, t_snap = [], []
    si = 0
    while si < len(snap_t) and snap_t[si] <= 1e-9:
        h_snap.append(h.copy()); t_snap.append(0.0); si += 1

    t_hist, h_st, Q_st, Qup_hist, Qout_hist = [], [], [], [], []
    storage0 = float(np.sum(h * (b + m * h)) * dx)
    inflow = outflow = 0.0
    max_c = 0.0
    t = 0.0
    c_ref = float(np.max(kinematic_speed(b, m, n, i0, np.array([h_n0, h_dn, 2 * h_n0]))))
    rec_every = max(1, steps // 800)

    def node_Q(hh):
        Sf = i0 - (hh[2:] - hh[:-2]) / (2.0 * dx)
        return conveyance(b, m, n, hh[1:-1]) * np.sqrt(np.maximum(Sf, 1e-9))

    def snap_now(t_now):
        h_here = np.interp([x[i] for i in stations], x, h)
        q_here = np.interp([x[i] for i in stations], x[1:-1], node_Q(h))
        t_hist.append(t_now)
        h_st.append([float(v) for v in h_here])
        Q_st.append([float(v) for v in q_here])

    # 上游边界：预先算好 h_n(Q) 查表，避免每步做二分（否则慢一个量级）
    Q_tab = np.linspace(Q0 * 0.5, max(Qpeak * 1.6, Q0 * 2.0), 160)
    h_tab = np.array([normal_depth(b, m, n, i0, q) for q in Q_tab])

    def h_up_of(Qup):
        return float(np.interp(Qup, Q_tab, h_tab))

    h[0] = h_up_of(Q_up(0.0))
    h[-1] = h_dn
    snap_now(0.0)

    h_check = h.copy()
    for step in range(steps):
        h_prev = h.copy()
        Qup = Q_up(t)
        h[0] = h_up_of(Qup)
        h[-1] = h_dn

        q = node_Q(h)
        # 节点流量 → 界面通量（相邻取平均），再算单元的净流入
        q_node = np.empty(Nx)
        q_node[1:-1] = q
        q_node[0] = Qup
        q_node[-1] = q[-1]
        flux = 0.5 * (q_node[1:] + q_node[:-1])
        _, B, _, _ = geom(b, m, h)
        dhdt = -(flux[1:] - flux[:-1]) / (B[1:-1] * dx)
        h[1:-1] = h[1:-1] + dt * dhdt
        h = np.maximum(h, 1e-5)

        h[0] = h_up_of(Q_up(t + dt))
        h[-1] = h_dn

        # 库朗数按参考波速估算（不必每步重算）
        max_c = max(max_c, c_ref * dt / dx)
        inflow += Qup * dt
        outflow += float(q[-1]) * dt
        t += dt
        # 只求稳态时：与 500 步前比较，变化足够小才停（避免 dt 小造成假收敛）
        if steady_tol is not None and step % 500 == 0:
            if float(np.max(np.abs(h - h_check))) < steady_tol:
                break
            h_check = h.copy()
        if step % rec_every == 0 or step == steps - 1:
            snap_now(t)

        while si < len(snap_t) and snap_t[si] <= t + 1e-9:
            h_snap.append(h.copy()); t_snap.append(float(snap_t[si])); si += 1

    storage1 = float(np.sum(h * (b + m * h)) * dx)
    net_in = inflow - outflow
    d_store = storage1 - storage0
    total_in = max(abs(inflow), 1e-9)
    mass_err = abs(d_store - net_in) / total_in

    # 特征量：c_k 在 h0 / 峰值水深处的值，以及实测等效波速
    h_peak0 = normal_depth(b, m, n, i0, Qpeak)
    ck_h0 = float(kinematic_speed(b, m, n, i0, np.array([h_n0]))[0])
    ck_pk = float(kinematic_speed(b, m, n, i0, np.array([h_peak0]))[0])

    keep = max(1, len(t_hist) // 500)
    T = [round(v, 1) for v in t_hist[::keep]]
    stations_out = []
    for k, idx in enumerate(stations):
        qs = [row[k] for row in Q_st]
        hs = [row[k] for row in h_st]
        ipk = int(np.argmax(qs))
        stations_out.append({
            "x": round(float(x[idx]), 1),
            "Q": [round(v, 4) for v in qs[::keep]],
            "h": [round(v, 4) for v in hs[::keep]],
            "peak_Q": round(float(qs[ipk]), 4),
            "peak_t": round(float(t_hist[ipk]), 1),
        })

    st0, st1 = stations_out[0], stations_out[-1]
    travel = st1["peak_t"] - st0["peak_t"]
    dx_meas = x[stations[-1]] - x[stations[0]]
    ck_meas = (dx_meas / travel) if travel > 1e-6 else 0.0
    bnd_peak_err = abs(st0["peak_Q"] - Qpeak) / max(Qpeak, 1e-9)

    return {
        "model": "diffusion",
        "x": [round(float(v), 2) for v in x],
        "t": T,
        "t_snap_hours": [round(v / 3600.0, 3) for v in t_snap],
        "h_snap": [[round(float(v), 4) for v in hs] for hs in h_snap],
        "h_final": [round(float(v), 5) for v in h],
        "t_final": round(float(t), 1),
        "stations": stations_out,
        "diagnostics": {
            "dt": round(dt, 4), "steps": steps, "dx": round(dx, 2),
            "nx": Nx, "auto_coarsened": bool(auto_coarsened),
            "max_courant": round(max_c, 3),
            "mass_err": round(float(mass_err), 6),
            "storage_change": round(d_store, 2),
            "net_inflow": round(net_in, 2),
            "boundary_peak_err": round(float(bnd_peak_err), 5),
            "c_k_h0": round(ck_h0, 4),
            "c_k_peak": round(ck_pk, 4),
            "c_k_measured": round(float(ck_meas), 4),
            "h_peak_normal": round(float(h_peak0), 4),
            "h_dn_used": round(float(h_dn), 4),
            "h_dn_is_normal": bool(h_dn is not None and abs(h_dn - h_n0) < 1e-9),
        },
        "meta": {
            "L": L, "b": b, "m": m, "n": n, "i0": i0,
            "Q0": Q0, "Qpeak": Qpeak, "t_peak": t_peak, "t_base": t_base,
            "h_n": round(float(h_n0), 5),
            "h_dn": round(float(h_dn), 5),
            "c_k": round(float(kinematic_speed(b, m, n, i0, h_n0)), 5),
        },
    }


def steady(b=5.0, m=0.0, n=0.015, i0=0.001, Q=10.0, h_dn=None,
           L=10000.0, Nx=201, hours=30.0):
    """跑到稳态，返回 (x, h)。用于与恒定流内核对账。"""
    res = run(L=L, b=b, m=m, n=n, i0=i0, Q0=Q, Qpeak=Q,
              t_peak=3600.0, t_base=3601.0, t_end=hours * 3600.0,
              h_dn=h_dn, Nx=Nx, snap_hours=(), station_fracs=(0.5,), steady_tol=1e-4)
    return np.array(res["x"]), np.array(res["h_final"])
