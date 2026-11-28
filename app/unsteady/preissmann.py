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

from .solver import kinematic_speed, normal_depth

G = 9.81


def _area(b, m, h):
    return h * (b + m * h)


def _radius(b, m, h):
    A = _area(b, m, h)
    P = b + 2.0 * np.asarray(h) * math.sqrt(1.0 + m * m)
    return A / np.maximum(P, 1e-9)


def _sigma_weir(r):
    """宽顶堰淹没系数（按教材表，0.80–1.00 线性插值）。"""
    xs = [0.80, 0.84, 0.88, 0.92, 0.96, 1.00]
    ys = [1.00, 0.97, 0.92, 0.85, 0.75, 0.62]
    if r <= xs[0]:
        return 1.0
    if r >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if r <= xs[i + 1]:
            w = (r - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + w * (ys[i + 1] - ys[i])
    return ys[-1]


def _steady_weir_h(Q, b, m, n, i0, L, Nx, seg, z_s, b_w, C, h_dn, h_n0):
    """给宽顶堰算一个近似稳态回水剖面，作为非恒定初场。"""
    import math as _m
    g = 9.81
    H0 = (Q / max(C * b_w, 1e-9)) ** (2.0 / 3.0)
    hw = z_s + H0
    dx = L / max(Nx - 1, 1)
    h = np.full(Nx, max(h_dn, 0.05))

    def f(hh):
        A = hh * (b + m * hh)
        B = b + 2.0 * m * hh
        P = b + 2.0 * hh * _m.sqrt(1.0 + m * m)
        R = A / max(P, 1e-9)
        Sf = n * n * Q * abs(Q) / (max(A, 1e-9) ** 2 * max(R, 1e-9) ** (4.0 / 3.0))
        Fr2 = Q * Q * B / (g * max(A, 1e-9) ** 3)
        den = max(1.0 - Fr2, 0.05)
        return (i0 - Sf) / den

    h[seg] = max(hw, 0.05)
    for i in range(seg - 1, -1, -1):
        hi = h[i + 1]
        k1 = f(hi)
        k2 = f(max(hi - 0.5 * dx * k1, 0.02))
        k3 = f(max(hi - 0.5 * dx * k2, 0.02))
        k4 = f(max(hi - dx * k3, 0.02))
        h[i] = max(hi - dx * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0, 0.02)
    for i in range(seg + 1, Nx):
        w = (i - seg) / max(Nx - 1 - seg, 1)
        h[i] = max(hw + (h_dn - hw) * w, 0.02)
    return h


def _sf(n, Q, A, R):
    return n * n * Q * np.abs(Q) / (np.maximum(A, 1e-9) ** 2 * np.maximum(R, 1e-9) ** (4.0 / 3.0))


def run(L=10000.0, b=5.0, m=0.0, n=0.015, i0=0.001,
        Q0=10.0, Qpeak=30.0, t_peak=3600.0, t_base=7200.0, t_end=12 * 3600.0,
        h_dn=None, h_init=None, Nx=81, theta=0.7, psi=0.5, dt=None,
        snap_hours=None, station_fracs=None, max_newton=6, tol=1e-7,
        cost_budget=1.2e11, gate=None, compat_init=True):
    """跑一次全方程非恒定演进（缓流）。

    返回结构与扩散波一致，便于同一页面共用绘图。
    """
    i0 = float(i0)
    if i0 <= 0:
        raise ValueError("底坡 i0 必须大于 0；本模块不支持平坡/负坡，请改用恒定流模块或调整坡度")
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
    if len(h_start) != Nx:
        h_start = np.interp(x, np.linspace(0.0, L, len(h_start)), h_start)
    if compat_init and abs(h_start[-1] - h_dn) > 1e-9:
        # 下游给定水深与初始场不一致时，用二次权重平滑过渡，避免 t=0 的阶跃假波
        _w = np.linspace(0.0, 1.0, Nx) ** 2
        h_start = h_start + (h_dn - h_start[-1]) * _w
    h = h_start.copy()
    Q = np.full(Nx, Q0)

    # 时间步：Preissmann 无条件稳定；取 Courant≈1 以控制数值耗散，
    # 用时由 cost_budget 的自动加粗网格兜底（不再用 3dx/c 的粗步长）。
    c_ref = math.sqrt(G * max(h_n0, 0.05))
    dt_given = dt is not None

    def _default_dt(dx_):
        return float(min(max(1.0 * dx_ / max(c_ref, 0.1), 10.0), 60.0))

    if dt is None:
        dt = _default_dt(dx)
    steps = int(math.ceil(t_end / dt))

    # 耗时控制：每次 Newton 要解 (2N)³ 的方程组，N 大时极慢。
    # 用 steps×(2N)³ 估代价，超过预算就自动加粗网格（不低于 81）。
    _auto = False
    _budget = cost_budget
    while Nx > 81:
        _dx = L / (Nx - 1)
        _dt = dt if dt is not None else _default_dt(_dx)
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
        if compat_init and abs(h[-1] - h_dn) > 1e-9:
            _w = np.linspace(0.0, 1.0, Nx) ** 2
            h = h + (h_dn - h[-1]) * _w
        Q = np.full(Nx, Q0)
        if not dt_given:
            dt = _default_dt(dx)
        steps = int(math.ceil(t_end / dt))

    auto_coarsened = bool(_auto or Nx < Nx_req)

    # ---------- 结构物内边界（闸门，可多座）----------
    gp = dict(gate or {})
    raw_list = gp.get("structures")
    if isinstance(raw_list, list) and raw_list:
        gate_list = [dict(x) for x in raw_list if isinstance(x, dict)]
    elif gp.get("on", False) or gp.get("s") is not None:
        gate_list = [gp]
    else:
        gate_list = []
    gate_on = bool(gate_list)

    def _mk_struct(g):
        _s = g.get("s")
        if _s is None:
            _s = float(g.get("s_pct", 50.0)) / 100.0 * L
        s_g = float(_s)
        z_s = float(g.get("z_s", 0.0))
        typ = str(g.get("type", "gate")).lower()
        seg = int(round(s_g / L * (Nx - 1)))
        seg = max(1, min(Nx - 2, seg))
        hist = {"t": [], "a": [], "Q": [], "hu": [], "hd": [],
                "H0": [], "sig": [], "reg": []}
        if typ == "weir":
            b_w = float(g.get("b_w", g.get("b_g", b)))
            C_w = float(g.get("C", 0.35))
            if b_w <= 0 or C_w <= 0:
                raise ValueError("宽顶堰参数不合法：堰宽与流量系数必须为正")
            return {"type": "weir", "seg": seg, "x": float(x[seg]), "s": s_g,
                    "b_w": b_w, "b_g": b_w, "C": C_w, "z_s": z_s,
                    "mu": None, "a0": 0.0, "a_end": 0.0,
                    "t_close": 0.0, "t_ramp": 0.0, "sched": [],
                    "a_now": 0.0, "hist": hist,
                    "_reg": "free", "_sig": 1.0}
        b_g = float(g.get("b_g", b))
        mu_g = float(g.get("mu", 0.62))
        a0 = float(g.get("a0", 1.0))
        a_end = float(g.get("a_end", a0))
        t_close = float(g.get("t_close", 0.0))
        t_ramp = float(g.get("t_ramp", 0.0))
        sched = []
        try:
            for _it in (g.get("sched") or []):
                if isinstance(_it, (list, tuple)) and len(_it) >= 2:
                    sched.append((float(_it[0]), float(_it[1])))
            sched.sort()
        except Exception:
            sched = []
        return {"type": "gate", "seg": seg, "x": float(x[seg]), "s": s_g,
                "b_g": b_g, "z_s": z_s, "mu": mu_g,
                "a0": a0, "a_end": a_end, "t_close": t_close,
                "t_ramp": t_ramp, "sched": sched, "a_now": a0,
                "hist": hist}

    structs = [_mk_struct(g) for g in gate_list]
    _segs = [st["seg"] for st in structs]
    if len(_segs) != len(set(_segs)):
        raise ValueError("两座结构物落在同一网格段，请把位置错开")
    for _st in structs:
        if _st["type"] != "gate":
            continue
        _amins = [_st["a0"], _st["a_end"]] + [float(p[1]) for p in _st["sched"]]
        if min(_amins) < 0.02:
            raise ValueError("闸门开度低于 0.02 m：本模块不支持全关断流，请把最小开度设在 0.02 m 以上")

    def struct_a(st, t):
        if not st:
            return 0.0
        if st["sched"]:
            th = t / 3600.0
            ts = [p[0] for p in st["sched"]]
            as_ = [p[1] for p in st["sched"]]
            if th <= ts[0]:
                return as_[0]
            if th >= ts[-1]:
                return as_[-1]
            return float(np.interp(th, ts, as_))
        if t <= st["t_close"]:
            return st["a0"]
        if st["t_ramp"] <= 1e-9:
            return st["a_end"]
        if t < st["t_close"] + st["t_ramp"]:
            return st["a0"] + (st["a_end"] - st["a0"]) * (t - st["t_close"]) / st["t_ramp"]
        return st["a_end"]

    for _st in structs:
        _st["a_now"] = float(struct_a(_st, 0.0))

    # 宽顶堰：用稳态回水剖面做初场，避免高水头 t=0 突加
    for _st in structs:
        if _st.get("type") != "weir":
            continue
        _h0 = _steady_weir_h(Q0, b, m, n, i0, L, Nx, _st["seg"],
                             _st["z_s"], _st["b_w"], _st["C"], h_dn, h_n0)
        h[:] = _h0
        Q[:] = Q0
        h_start = h.copy()

    snap_t = [hr * 3600.0 for hr in snap_hours if hr * 3600.0 <= t_end]
    h_snap, t_snap = [], []
    si = 0
    while si < len(snap_t) and snap_t[si] <= 1e-9:
        h_snap.append(h.copy()); t_snap.append(0.0); si += 1

    stations = {int(round(f * (Nx - 1))) for f in station_fracs}
    if gate_on:
        for _st in structs:
            stations.add(_st["seg"])
    stations = sorted(stations)
    t_hist, h_st, Q_st = [], [], []
    max_courant = 0.0
    rec_every = max(1, steps // 600)

    def record(t_now):
        nonlocal max_courant
        _A = _area(b, m, h)
        _c = np.sqrt(G * np.maximum(h, 1e-6))
        _cour = float(np.max((np.abs(Q) / np.maximum(_A, 1e-9) + _c) * dt / dx))
        max_courant = max(max_courant, _cour)
        t_hist.append(t_now)
        h_st.append([float(h[i]) for i in stations])
        Q_st.append([float(Q[i]) for i in stations])
        for _st in structs:
            _h = _st["hist"]
            _h["t"].append(t_now)
            _h["a"].append(float(struct_a(_st, t_now)))
            _h["Q"].append(0.5 * (float(Q[_st["seg"]]) + float(Q[_st["seg"] + 1])))
            _h["hu"].append(float(h[_st["seg"]]))
            _h["hd"].append(float(h[_st["seg"] + 1]))
            if _st.get("type") == "weir":
                _h1 = max(float(h[_st["seg"]]) - _st["z_s"], 0.0)
                _st["_H0_last"] = _h1
                _st["_sig_last"] = float(_st.get("_sig", 1.0))
                _st["_reg_last"] = _st.get("_reg", "free")
                _h["H0"].append(_h1)
                _h["sig"].append(_st["_sig_last"])
                _h["reg"].append(_st["_reg_last"])
            else:
                _h["H0"].append(0.0)
                _h["sig"].append(1.0)
                _h["reg"].append("gate")

    record(0.0)
    inflow = outflow = 0.0
    iters_total = 0
    failed = 0
    max_residual = 0.0
    t = 0.0
    t_prev = 0.0

    for step in range(steps):
        t_new = t + dt
        Qu = Q_up(t_new)
        for _st in structs:
            _st["a_now"] = float(struct_a(_st, t_new))
        h_down = h_dn
        # 冻结堰流态（每步只判一次，Newton 内不切换）
        for _st in structs:
            if _st.get("type") != "weir":
                continue
            _h1 = max(float(h[_st["seg"]]) - _st["z_s"], 0.0)
            _h2 = max(float(h_dn) - _st["z_s"],
                      float(h[_st["seg"] + 1]) - _st["z_s"], 0.0)
            if _h1 <= 1e-6:
                _st["_reg"], _st["_sig"] = "dry", 0.0
            else:
                _r = _h2 / _h1
                if _r < 0.80:
                    _st["_reg"], _st["_sig"] = "free", 1.0
                else:
                    _st["_reg"], _st["_sig"] = "submerged", _sigma_weir(_r)
        h_old, Q_old = h.copy(), Q.copy()
        hLo, hRo = h_old[:-1], h_old[1:]
        qLo, qRo = Q_old[:-1], Q_old[1:]
        _Abar_o = psi * _area(b, m, hRo) + (1 - psi) * _area(b, m, hLo)
        _Qbar_o = psi * qRo + (1 - psi) * qLo
        _Rbar_o = psi * _radius(b, m, hRo) + (1 - psi) * _radius(b, m, hLo)
        Sf_old_seg = _sf(n, _Qbar_o, _Abar_o, _Rbar_o)

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
            Sf = (theta * _sf(n, Qbar, Abar, Rbar)
                  + (1 - theta) * Sf_old_seg)
            dh_new = (hR - hL) / dx
            dh_old = (hRo - hLo) / dx
            M = (psi * (qR - qRo) + (1 - psi) * (qL - qLo)) / dt \
                + theta * conv_new + (1 - theta) * conv_old \
                + G * Abar * (theta * dh_new + (1 - theta) * dh_old) \
                + G * Abar * (Sf - i0)
            if gate_on:
                M = M.copy()
                for _st in structs:
                    _seg = _st["seg"]
                    if _st["type"] == "weir":
                        qbar = 0.5 * (qL[_seg] + qR[_seg])
                        if _st.get("_reg") == "dry":
                            M[_seg] = qbar
                            continue
                        h1 = max(float(hL[_seg]) - _st["z_s"], 0.0)
                        Qw = _st["C"] * _st["b_w"] * h1 ** 1.5 * _st.get("_sig", 1.0)
                        M[_seg] = qbar - Qw
                        continue
                    a_eff = max(_st["a_now"], 1e-3)
                    qq = qL[_seg] * abs(qL[_seg])
                    head = qq / (2.0 * (_st["mu"] * _st["b_g"] * a_eff) ** 2)
                    if _st["z_s"] > 0:
                        avail = max(float(hL[_seg]) - _st["z_s"], 1e-6)
                        over = max(head - avail, 0.0)
                        head = head + 20.0 * over
                    M[_seg] = M[_seg] + Abar[_seg] * head / dx
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
        _scale = tol * max(1.0, Qpeak)
        converged = False
        _fail_step = False
        it = 0
        for it in range(max_newton):
            if np.max(np.abs(F)) < _scale:
                converged = True
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
                _fail_step = True
                break
            if not np.all(np.isfinite(dU)):
                failed += 1
                _fail_step = True
                break
            U = U + dU
            # 保正
            U[:Nx] = np.maximum(U[:Nx], 1e-4)
            F = residual(U)
        if not converged:
            converged = bool(np.max(np.abs(F)) < _scale)
        if not converged and not _fail_step:
            failed += 1
        iters_total += (it + 1)
        max_residual = max(max_residual, float(np.max(np.abs(F))))

        h = U[:Nx]
        Q = U[Nx:]
        inflow += Qu * dt
        outflow += float(Q[-1]) * dt
        t = t_new
        if step % rec_every == 0 or step == steps - 1:
            record(t)
        while si < len(snap_t) and snap_t[si] <= t + 1e-9:
            _ts = float(snap_t[si])
            if t - t_prev > 1e-12:
                _w = (_ts - t_prev) / (t - t_prev)
                _hs = h_old + _w * (h - h_old)
            else:
                _hs = h.copy()
            h_snap.append(_hs); t_snap.append(_ts); si += 1
        t_prev = t

    # 质量守恒（与扩散波同口径）
    storage1 = float(np.sum(_area(b, m, h)) * dx)
    storage0 = float(np.sum(_area(b, m, np.interp(x, np.linspace(0.0, L, len(h_start)), h_start) if len(h_start) != Nx else h_start)) * dx)
    net_in = inflow - outflow
    mass_err = abs((storage1 - storage0) - net_in) / max(abs(inflow), 1e-9)

    qg_final = (0.5 * (float(Q[structs[0]["seg"]]) + float(Q[structs[0]["seg"] + 1]))
                if gate_on else 0.0)
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

    h_peak_normal = float(normal_depth(b, m, n, i0, Qpeak))
    c_k_peak = float(kinematic_speed(b, m, n, i0, np.array([h_peak_normal]))[0])
    st0, st1 = stations_out[0], stations_out[-1]
    travel = st1["peak_t"] - st0["peak_t"]
    ck_meas = ((x[stations[-1]] - x[stations[0]]) / travel) if travel > 1e-6 else 0.0
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
            "max_courant": round(float(max_courant), 3),
            "mass_err": round(float(mass_err), 6),
            "newton_iters": iters_total,
            "newton_failed": failed,
            "max_residual": float(max_residual),
            "status": "ok" if failed == 0 else "partial",
            "h_final": [round(float(v), 4) for v in h],
            "t_final": round(float(t), 1),
            "theta": theta, "psi": psi,
            "c_k_h0": round(ck_h0, 4),
            "c_k_peak": round(c_k_peak, 4),
            "c_k_measured": round(float(ck_meas), 4),
            "boundary_peak_err": round(abs(st0["peak_Q"] - Qpeak) / max(Qpeak, 1e-9), 5),
            "h_peak_normal": round(h_peak_normal, 5),
            "h_dn_used": round(float(h_dn), 4),
            "h_dn_is_normal": bool(abs(h_dn - h_n0) < 1e-9),
            "gate_on": bool(gate_on),
            "gate_count": sum(1 for st in structs if st.get("type") == "gate"),
            "weir_count": sum(1 for st in structs if st.get("type") == "weir"),
            "gate_x": (round(structs[0]["x"], 2) if gate_on else None),
            "gate_Q_final": round(qg_final, 4),
        },
        "gate": ({
            "on": True, "type": structs[0].get("type", "gate"),
            "x": round(structs[0]["x"], 2),
            "b_g": structs[0]["b_g"], "z_s": structs[0]["z_s"],
            "mu": structs[0]["mu"],
            "t": [round(v, 1) for v in structs[0]["hist"]["t"][::keep]],
            "a": [round(v, 4) for v in structs[0]["hist"]["a"][::keep]],
            "Q": [round(v, 4) for v in structs[0]["hist"]["Q"][::keep]],
            "hu": [round(v, 4) for v in structs[0]["hist"]["hu"][::keep]],
            "hd": [round(v, 4) for v in structs[0]["hist"]["hd"][::keep]],
        } if gate_on else {"on": False}),
        "structures": [{
            "type": st.get("type", "gate"),
            "x": round(st["x"], 2), "b_g": st["b_g"], "b_w": st.get("b_w"),
            "C": st.get("C"), "z_s": st["z_s"], "mu": st["mu"],
            "regime": st.get("_reg_last", st.get("_reg")),
            "sigma": round(float(st.get("_sig_last", st.get("_sig", 1.0))), 3),
            "H0": round(float(st.get("_H0_last", 0.0)), 3),
            "H0_hist": [round(v, 4) for v in st["hist"].get("H0", [])[::keep]],
            "sig_hist": [round(v, 3) for v in st["hist"].get("sig", [])[::keep]],
            "reg_hist": st["hist"].get("reg", [])[::keep],
            "t": [round(v, 1) for v in st["hist"]["t"][::keep]],
            "a": [round(v, 4) for v in st["hist"]["a"][::keep]],
            "Q": [round(v, 4) for v in st["hist"]["Q"][::keep]],
            "hu": [round(v, 4) for v in st["hist"]["hu"][::keep]],
            "hd": [round(v, 4) for v in st["hist"]["hd"][::keep]],
        } for st in structs],
        "meta": {"L": L, "b": b, "m": m, "n": n, "i0": i0,
                 "Q0": Q0, "Qpeak": Qpeak, "t_peak": t_peak, "t_base": t_base,
                 "h_n": round(float(h_n0), 5), "h_dn": round(float(h_dn), 5),
                 "c_k": round(ck_h0, 5), "model": "preissmann"},
    }
