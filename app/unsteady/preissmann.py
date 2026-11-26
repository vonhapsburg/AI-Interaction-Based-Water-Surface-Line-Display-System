# -*- coding: utf-8 -*-
"""方案 B1：圣维南全方程 · Preissmann 四点隐式（缓流）

与 A（扩散波）并存，互不影响：
  · 本文件不修改 solver.py，也不导入 gvf_core（初场由调用方传入）；
  · 保留全部惯性项，能算回水顶托；无条件稳定，Δt 可到几十秒；
  · 不支持溃坝/涌潮/干床（隐式格式会抹平陡波前）。

离散（每个渠段 i~i+1，两个方程）：
  连续： [ψ(A_R^{n+1}-A_R^n) + (1-ψ)(A_L^{n+1}-A_L^n)]/Δt
        + [θ(Q_R-Q_L)^{n+1} + (1-θ)(Q_R-Q_L)^n]/Δx = 0
  动量： [ψ(Q_R^{n+1}-Q_R^n) + (1-ψ)(Q_L^{n+1}-Q_L^n)]/Δt
        + θ·∂(Q²/A)/∂x|^{n+1} + (1-θ)·∂(Q²/A)/∂x|^n
        + g·Ā·[θ(h_R-h_L)^{n+1}+(1-θ)(h_R-h_L)^n]/Δx
        + g·Ā·(S_f - i0) = 0

未知量 U=[h_0,Q_0,h_1,Q_1,...]，边界：上游 Q(t)、下游 h(t)。
非线性用 Newton 迭代；Jacobian 用"逐段局部数值微分"（4 次向量化残差求值），
不手推解析导数，避免符号错误。
"""
import math

import numpy as np

from .solver import geom, normal_depth

G = 9.81


def _area(b, m, h):
    return h * (b + m * h)


def _radius(b, m, h):
    A = _area(b, m, h)
    P = b + 2.0 * np.asarray(h) * math.sqrt(1.0 + m * m)
    return A / np.maximum(P, 1e-9)


def _sf(n, Q, A, R):
    return n * n * Q * np.abs(Q) / (np.maximum(A, 1e-9) ** 2 * np.maximum(R, 1e-9) ** (4.0 / 3.0))


def run(L=10000.0, b=5.0, m=0.0, n=0.015, i0=0.001,
        Q0=10.0, Qpeak=30.0, t_peak=3600.0, t_base=7200.0, t_end=12 * 3600.0,
        h_dn=None, h_init=None, Nx=81, theta=0.7, psi=0.5, dt=None,
        snap_hours=None, station_fracs=None, max_newton=6, tol=1e-7,
        cost_budget=1.2e11, gate=None):
    """跑一次全方程非恒定演进（缓流）。

    返回结构与扩散波一致，便于同一页面共用绘图。
    """
    i0 = max(float(i0), 1e-6)
    L = float(L)
    Q0 = float(max(Q0, 1e-3))
    Qpeak = float(max(Qpeak, Q0))
    t_peak = float(max(t_peak, 60.0))
    t_base = float(max(t_base, t_peak + 60.0))
    t_end = float(t_end)
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
    Nx_req = int(Nx)
    Nx = int(max(30, min(int(Nx), 401)))
    dx = L / (Nx - 1)
    x = np.linspace(0.0, L, Nx)

    h_start = (np.array(h_init, dtype=float) if h_init is not None else np.full(Nx, h_n0))
    h = h_start.copy()
    if len(h) != Nx:
        h = np.interp(x, np.linspace(0.0, L, len(h)), h)
    Q = np.full(Nx, Q0)

    # 时间步：Preissmann 无条件稳定，但精度要求 Δt 小于 ~dx/c
    c_ref = math.sqrt(G * max(h_n0, 0.05))
    dt_given = dt is not None
    if dt is None:
        dt = float(min(max(3.0 * dx / max(c_ref, 0.1), 15.0), 90.0))
    steps = int(math.ceil(t_end / dt))

    # 耗时控制：每次 Newton 要解 (2N)³ 的方程组，N 大时极慢。
    # 用 steps×(2N)³ 估代价，超过预算就自动加粗网格（不低于 81）。
    _auto = False
    _budget = cost_budget
    while Nx > 81:
        _dx = L / (Nx - 1)
        _dt = dt if dt is not None else float(min(max(3.0 * _dx / max(c_ref, 0.1), 15.0), 90.0))
        _steps = int(math.ceil(t_end / _dt))
        if _steps * (2 * Nx) ** 3 <= _budget:
            break
        Nx = max(81, int(Nx * 0.72))
        _auto = True
    if _auto:
        dx = L / (Nx - 1)
        x = np.linspace(0.0, L, Nx)
        h = (np.array(h_init, dtype=float) if h_init is not None else np.full(Nx, h_n0))
        if len(h) != Nx:
            h = np.interp(x, np.linspace(0.0, L, len(h)), h)
        Q = np.full(Nx, Q0)
        if not dt_given:
            dt = float(min(max(3.0 * dx / max(c_ref, 0.1), 15.0), 90.0))
        steps = int(math.ceil(t_end / dt))

    auto_coarsened = bool(_auto or Nx < Nx_req)

    # ---------- 闸门内边界（可选）----------
    gp = dict(gate or {})
    gate_on = bool(gp.get("on", False)) or (gp.get("s") is not None)
    if gate_on:
        s_g = float(gp.get("s", 0.5 * L))
        b_g = float(gp.get("b_g", b))
        z_s = float(gp.get("z_s", 0.0))
        mu_g = float(gp.get("mu", 0.62))
        a0 = float(gp.get("a0", 1.0))
        a_end = float(gp.get("a_end", a0))
        t_close = float(gp.get("t_close", 0.0))
        t_ramp = float(gp.get("t_ramp", 0.0))
        sched = []
        try:
            for _it in (gp.get("sched") or []):
                if isinstance(_it, (list, tuple)) and len(_it) >= 2:
                    sched.append((float(_it[0]), float(_it[1])))
            sched.sort()
        except Exception:
            sched = []
        g_gate = int(round(s_g / L * (Nx - 1)))
        g_gate = max(1, min(Nx - 2, g_gate))
    else:
        s_g = b_g = z_s = mu_g = a0 = a_end = t_close = t_ramp = 0.0
        sched = []
        g_gate = -1

    def gate_a(t):
        if not gate_on:
            return 0.0
        if sched:
            th = t / 3600.0
            if th <= sched[0][0]:
                return sched[0][1]
            if th >= sched[-1][0]:
                return sched[-1][1]
            return float(np.interp(th, [p[0] for p in sched], [p[1] for p in sched]))
        if t <= t_close:
            return a0
        if t_ramp <= 1e-9:
            return a_end
        if t < t_close + t_ramp:
            return a0 + (a_end - a0) * (t - t_close) / t_ramp
        return a_end

    def gate_Q(hu, hd, a):
        hu = np.asarray(hu, dtype=float)
        hd = np.asarray(hd, dtype=float)
        if (not gate_on) or a <= 1e-9:
            return np.zeros_like(hu)
        Hu = np.maximum(hu - z_s, 1e-4)
        Hd = np.maximum(hd - z_s, 1e-4)
        free = mu_g * b_g * a * np.sqrt(2.0 * G * Hu)
        sub = mu_g * b_g * a * np.sqrt(2.0 * G * np.maximum(Hu - Hd, 1e-4))
        return np.where(Hd < 0.65 * a, free, sub)

    gate_hist = {"t": [], "a": [], "Q": [], "hu": [], "hd": []}
    a_now = gate_a(0.0)

    snap_t = [hr * 3600.0 for hr in snap_hours if hr * 3600.0 <= t_end]
    h_snap, t_snap = [], []
    si = 0
    while si < len(snap_t) and snap_t[si] <= 1e-9:
        h_snap.append(h.copy()); t_snap.append(0.0); si += 1

    stations = {int(round(f * (Nx - 1))) for f in station_fracs}
    if gate_on:
        stations.add(g_gate)
    stations = sorted(stations)
    t_hist, h_st, Q_st = [], [], []
    rec_every = max(1, steps // 600)

    def record(t_now):
        t_hist.append(t_now)
        h_st.append([float(h[i]) for i in stations])
        Q_st.append([float(Q[i]) for i in stations])
        if gate_on:
            aa = gate_a(t_now)
            qg = 0.5 * (float(Q[g_gate]) + float(Q[g_gate + 1]))
            gate_hist["t"].append(t_now)
            gate_hist["a"].append(aa)
            gate_hist["Q"].append(qg)
            gate_hist["hu"].append(float(h[g_gate]))
            gate_hist["hd"].append(float(h[g_gate + 1]))

    record(0.0)
    inflow = outflow = 0.0
    iters_total = 0
    failed = 0
    t = 0.0

    for step in range(steps):
        t_new = t + dt
        Qu = Q_up(t_new)
        a_now = gate_a(t_new)
        h_down = h_dn
        h_old, Q_old = h.copy(), Q.copy()
        hLo, hRo = h_old[:-1], h_old[1:]
        qLo, qRo = Q_old[:-1], Q_old[1:]

        def seg_res(hL, qL, hR, qR):
            """一个渠段的（连续, 动量）残差；左右节点显式分开传入。"""
            AL, AR = _area(b, m, hL), _area(b, m, hR)
            RL, RR = _radius(b, m, hL), _radius(b, m, hR)
            ALo, ARo = _area(b, m, hLo), _area(b, m, hRo)
            C = (psi * (AR - ARo) + (1 - psi) * (AL - ALo)) / dt \
                + (theta * (qR - qL) + (1 - theta) * (qRo - qLo)) / dx
            conv_new = (qR * qR / np.maximum(AR, 1e-9) - qL * qL / np.maximum(AL, 1e-9)) / dx
            conv_old = (qRo * qRo / np.maximum(ARo, 1e-9) - qLo * qLo / np.maximum(ALo, 1e-9)) / dx
            Abar = psi * AR + (1 - psi) * AL
            Qbar = psi * qR + (1 - psi) * qL
            Rbar = psi * RR + (1 - psi) * RL
            Sf = _sf(n, Qbar, Abar, Rbar)
            dh_new = (hR - hL) / dx
            dh_old = (hRo - hLo) / dx
            M = (psi * (qR - qRo) + (1 - psi) * (qL - qLo)) / dt \
                + theta * conv_new + (1 - theta) * conv_old \
                + G * Abar * (theta * dh_new + (1 - theta) * dh_old) \
                + G * Abar * (Sf - i0)
            if gate_on:
                # 闸门按局部水头损失处理：Δh = Q|Q| / (2g(μ·b·a)²)
                # 折算到单位长度即 g·A·S_gate，避免“闸前一个网格塌成干床”的假解
                M = M.copy()
                a_eff = max(a_now, 1e-3)
                qq = qL[g_gate] * abs(qL[g_gate])
                M[g_gate] = M[g_gate] + Abar[g_gate] * qq / \
                    (2.0 * (mu_g * b_g * a_eff) ** 2 * dx)
            return C, M

        def residual(U):
            hh = U[:Nx]
            qq = U[Nx:]
            C, M = seg_res(hh[:-1], qq[:-1], hh[1:], qq[1:])
            # 按 (C_i, M_i) 交错排列，与 Jacobian 的块结构一致
            seg = np.empty(2 * (Nx - 1))
            seg[0::2] = C
            seg[1::2] = M
            return np.concatenate([seg, [qq[0] - Qu, hh[-1] - h_down]])

        U = np.concatenate([h, Q])
        F = residual(U)
        it = 0
        for it in range(max_newton):
            if np.max(np.abs(F)) < tol * max(1.0, Qpeak):
                break
            # 逐段局部数值 Jacobian（按配对索引写入，避免二维切片广播）
            J = np.zeros((2 * Nx, 2 * Nx))
            d = 1e-6
            base = residual(U)
            rowsC = np.arange(0, 2 * (Nx - 1), 2)
            rowsM = rowsC + 1
            idxL = np.arange(0, Nx - 1)
            idxR = np.arange(1, Nx)
            hh, qq = U[:Nx], U[Nx:]
            hL, qL, hR, qR = hh[:-1], qq[:-1], hh[1:], qq[1:]
            C0, M0 = seg_res(hL, qL, hR, qR)
            for k in range(4):
                if k == 0:
                    C, M = seg_res(hL + d, qL, hR, qR); cols = idxL
                elif k == 1:
                    C, M = seg_res(hL, qL + d, hR, qR); cols = Nx + idxL
                elif k == 2:
                    C, M = seg_res(hL, qL, hR + d, qR); cols = idxR
                else:
                    C, M = seg_res(hL, qL, hR, qR + d); cols = Nx + idxR
                J[rowsC, cols] = (C - C0) / d
                J[rowsM, cols] = (M - M0) / d
            J[2 * (Nx - 1), Nx] = 1.0          # 上游 Q
            J[2 * (Nx - 1) + 1, Nx - 1] = 1.0  # 下游 h
            try:
                dU = np.linalg.solve(J, -base)
            except np.linalg.LinAlgError:
                failed += 1
                dU = np.zeros_like(U)
            U = U + dU
            # 保正
            U[:Nx] = np.maximum(U[:Nx], 1e-4)
            F = residual(U)
        iters_total += (it + 1)

        h = U[:Nx]
        Q = U[Nx:]
        inflow += Qu * dt
        outflow += float(Q[-1]) * dt
        t = t_new
        if step % rec_every == 0 or step == steps - 1:
            record(t)
        while si < len(snap_t) and snap_t[si] <= t + 1e-9:
            h_snap.append(h.copy()); t_snap.append(float(snap_t[si])); si += 1

    # 质量守恒（与扩散波同口径）
    storage1 = float(np.sum(_area(b, m, h)) * dx)
    storage0 = float(np.sum(_area(b, m, np.interp(x, np.linspace(0.0, L, len(h_start)), h_start) if len(h_start) != Nx else h_start)) * dx)
    net_in = inflow - outflow
    mass_err = abs((storage1 - storage0) - net_in) / max(abs(inflow), 1e-9)

    qg_final = (0.5 * (float(Q[g_gate]) + float(Q[g_gate + 1])) if gate_on else 0.0)
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
    ck_meas = ((x[stations[-1]] - x[stations[0]]) / travel) if travel > 1e-6 else 0.0
    from .solver import kinematic_speed
    ck_h0 = float(kinematic_speed(b, m, n, i0, np.array([h_n0]))[0])

    return {
        "model": "preissmann",
        "x": [round(float(v), 2) for v in x],
        "t": T,
        "t_snap_hours": [round(v / 3600.0, 3) for v in t_snap],
        "h_snap": [[round(float(v), 4) for v in hs] for hs in h_snap],
        "stations": stations_out,
        "diagnostics": {
            "dt": round(dt, 3), "steps": steps, "dx": round(dx, 2), "nx": Nx, "auto_coarsened": auto_coarsened,
            "max_courant": None,
            "mass_err": round(float(mass_err), 6),
            "newton_iters": iters_total,
            "newton_failed": failed,
            "theta": theta, "psi": psi,
            "c_k_h0": round(ck_h0, 4),
            "c_k_peak": None,
            "c_k_measured": round(float(ck_meas), 4),
            "boundary_peak_err": round(abs(st0["peak_Q"] - Qpeak) / max(Qpeak, 1e-9), 5),
            "h_peak_normal": None,
            "h_dn_used": round(float(h_dn), 4),
            "h_dn_is_normal": bool(abs(h_dn - h_n0) < 1e-9),
            "gate_on": bool(gate_on),
            "gate_x": (round(float(x[g_gate]), 2) if gate_on else None),
            "gate_Q_final": round(qg_final, 4),
        },
        "gate": ({
            "on": True, "x": round(float(x[g_gate]), 2),
            "b_g": b_g, "z_s": z_s, "mu": mu_g,
            "t": [round(v, 1) for v in gate_hist["t"][::keep]],
            "a": [round(v, 4) for v in gate_hist["a"][::keep]],
            "Q": [round(v, 4) for v in gate_hist["Q"][::keep]],
            "hu": [round(v, 4) for v in gate_hist["hu"][::keep]],
            "hd": [round(v, 4) for v in gate_hist["hd"][::keep]],
        } if gate_on else {"on": False}),
        "meta": {"L": L, "b": b, "m": m, "n": n, "i0": i0,
                 "Q0": Q0, "Qpeak": Qpeak, "t_peak": t_peak, "t_base": t_base,
                 "h_n": round(float(h_n0), 5), "h_dn": round(float(h_dn), 5),
                 "c_k": round(ck_h0, 5), "model": "preissmann"},
    }
