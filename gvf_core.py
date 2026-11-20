# -*- coding: utf-8 -*-
"""
gvf_core.py —— 基于AI交互的水面线展示系统 · 计算内核
=====================================================

依据：水力学教材第8章《明渠非均匀流》
     明渠恒定渐变流、十二型水面曲线、分段求和法、水跃与水跌、
     折线变坡段间衔接、单参数反问题。

约束：
  1. 纯计算：不联网、不读写文件，仅 __main__ 自检时打印。
  2. 确定性：同输入同输出，计算路径无随机数、无当前时间。
  3. 单一真源：全部水力数值仅在此产生。
  4. 符号采用教材：i, J, h0, hc, ic, Es, K, Fr, theta；形心水深记 ybar。

不做的内容（写入局限）：
  非棱柱体、跌坎窒息、天然河道、复式断面、分叉、弯段、非恒定流、圆形断面。
"""

from __future__ import annotations

import copy
import json
import math
import time
from collections import namedtuple
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

SCHEMA_VERSION = "1.0.0"
G_DEFAULT = 9.81
TINY = 1e-12


# =============================================================================
# 0. 数据结构
# =============================================================================

class SectionProps(namedtuple("SectionProps", "A B P R hbar ybar")):
    """断面水力要素：面积、水面宽、湿周、水力半径、平均水深、形心水深。"""
    __slots__ = ()


@dataclass(frozen=True)
class Section:
    kind: str          # rectangular | trapezoidal
    b: float
    m: float = 0.0


@dataclass
class Reach:
    index: int
    name: str
    x_start: float
    x_end: float
    i: float
    b: float
    m: float = 0.0
    n: float = 0.015
    n_tag: str = "engineering"
    z_b_start: Optional[float] = None

    @property
    def L(self) -> float:
        return self.x_end - self.x_start

    @property
    def section(self) -> Section:
        return Section("rectangular" if abs(self.m) < TINY else "trapezoidal", self.b, self.m)


@dataclass
class Boundary:
    type: str = "none"     # none | given_h | given_zw | free_outfall | normal_depth
    h: Optional[float] = None
    zw: Optional[float] = None


@dataclass
class Params:
    Q: float
    reaches: List[Reach]
    upstream: Boundary = field(default_factory=lambda: Boundary("none"))
    downstream: Boundary = field(default_factory=lambda: Boundary("free_outfall"))
    g: float = G_DEFAULT
    alpha: float = 1.0
    beta: float = 1.0
    mode: str = "teaching"
    case_name: str = ""

    dh_rel: float = 0.01
    dh_min: float = 1e-6
    tol_normal: float = 0.005
    hc_offset_rel: float = 0.005
    stop_gap: float = 1e-3
    max_steps: int = 200000

    jump_len_formula: str = "wu"
    outfall_correction: bool = False
    section_at_x: List[float] = field(default_factory=list)


@dataclass
class ReachInfo:
    index: int
    name: str
    x_start: float
    x_end: float
    i: float
    b: float
    m: float
    n: float
    n_tag: str
    z_b_start: float
    z_b_end: float
    h0: Optional[float]
    hc: float
    ic: float
    slope_class: str
    A_c: float
    R_c: float
    K_c: float
    ybar_c: float
    B_c: float
    v_c: float
    Es_c: float

    @property
    def L(self) -> float:
        return self.x_end - self.x_start

    @property
    def section(self) -> Section:
        return Section("rectangular" if abs(self.m) < TINY else "trapezoidal", self.b, self.m)

    @property
    def has_normal_line(self) -> bool:
        return self.h0 is not None


@dataclass
class Warning:
    code: str
    level: str
    scope: str
    message: str
    hint: str = ""
    index: Optional[int] = None
    x: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Transition:
    kind: str          # jump | critical | outfall | jump_washed | jump_submerged
    x: float
    reach_index: Optional[int] = None
    data: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "x": self.x, "reach_index": self.reach_index,
                "data": self.data, "note": self.note}


@dataclass
class Segment:
    seg_id: int
    reach_index: int
    curve_type: str
    regime: str
    direction: int
    x: np.ndarray
    h: np.ndarray
    z_b: np.ndarray
    z_w: np.ndarray
    Es: np.ndarray
    Fr: np.ndarray
    v: np.ndarray
    start_reason: str
    end_reason: str
    len_to_99pct_h0: Optional[float]
    flags: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, with_arrays: bool = True) -> Dict[str, Any]:
        d = {
            "seg_id": self.seg_id,
            "reach_index": self.reach_index,
            "curve_type": self.curve_type,
            "regime": self.regime,
            "direction": self.direction,
            "start_reason": self.start_reason,
            "end_reason": self.end_reason,
            "len_to_99pct_h0": self.len_to_99pct_h0,
            "flags": self.flags,
            "x_first": float(self.x[0]),
            "x_last": float(self.x[-1]),
            "h_first": float(self.h[0]),
            "h_last": float(self.h[-1]),
            "h_max": float(np.max(self.h)) if self.h.size else None,
        }
        if with_arrays:
            d["x"] = [float(v) for v in self.x]
            d["h"] = [float(v) for v in self.h]
            d["z_b"] = [float(v) for v in self.z_b]
            d["z_w"] = [float(v) for v in self.z_w]
            d["Es"] = [float(v) for v in self.Es]
            d["Fr"] = [float(v) for v in self.Fr]
            d["v"] = [float(v) for v in self.v]
        return d


@dataclass
class Result:
    status: str
    params_echo: Dict[str, Any]
    reaches: List[ReachInfo]
    nodes: List[Dict[str, Any]]
    controls: List[Dict[str, Any]]
    segments: List[Segment]
    transitions: List[Transition]
    section_at: List[Dict[str, Any]]
    facts: Dict[str, Any]
    warnings: List[Warning]
    diagnostics: Dict[str, Any]

    def to_dict(self, with_arrays: bool = True) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "params_echo": self.params_echo,
            "reaches": [r.__dict__ for r in self.reaches],
            "nodes": self.nodes,
            "controls": self.controls,
            "segments": [s.to_dict(with_arrays) for s in self.segments],
            "transitions": [t.to_dict() for t in self.transitions],
            "section_at": self.section_at,
            "facts": self.facts,
            "warnings": [w.to_dict() for w in self.warnings],
            "diagnostics": self.diagnostics,
        }

    def to_json(self, with_arrays: bool = True, indent: int = 2) -> str:
        return json.dumps(self.to_dict(with_arrays), ensure_ascii=False, indent=indent)


# =============================================================================
# 1. 通用数值工具
# =============================================================================

def _bisect(f, lo: float, hi: float, tol: float = 1e-13, maxit: int = 300) -> float:
    """二分求根，要求 f(lo)、f(hi) 异号。"""
    flo, fhi = f(lo), f(hi)
    if flo == 0.0:
        return lo
    if fhi == 0.0:
        return hi
    if flo * fhi > 0.0:
        raise ValueError("区间未夹住根")
    for _ in range(maxit):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if fm == 0.0:
            return mid
        if flo * fm < 0.0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
        if abs(hi - lo) <= tol * max(1.0, abs(mid)):
            break
    return 0.5 * (lo + hi)


# =============================================================================
# 2. 断面几何
# =============================================================================

def props(sec: Section, h: float) -> SectionProps:
    """断面水力要素。矩形：A=bh, B=b, P=b+2h；梯形：A=(b+mh)h, B=b+2mh, P=b+2h√(1+m²)。"""
    if h <= 0.0:
        return SectionProps(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    b, m = sec.b, max(sec.m, 0.0)
    if sec.kind == "rectangular" or m < TINY:
        A = b * h
        B = b
        P = b + 2.0 * h
        ybar = 0.5 * h
    else:
        A = (b + m * h) * h
        B = b + 2.0 * m * h
        P = b + 2.0 * h * math.sqrt(1.0 + m * m)
        ybar = h * (3.0 * b + 2.0 * m * h) / (6.0 * (b + m * h))
    R = A / P if P > 0.0 else 0.0
    hbar = A / B if B > 0.0 else 0.0
    return SectionProps(A, B, P, R, hbar, ybar)


def discharge_capacity(sec: Section, n: float, i: float, h: float) -> float:
    """Q = (1/n) A R^(2/3) √i。i<=0 时返回 0。"""
    if i <= 0.0 or h <= 0.0:
        return 0.0
    p = props(sec, h)
    if p.A <= 0.0 or p.R <= 0.0:
        return 0.0
    return (1.0 / n) * p.A * p.R ** (2.0 / 3.0) * math.sqrt(i)


# =============================================================================
# 3. 三个特征量
# =============================================================================

def normal_depth(sec: Section, n: float, i: float, Q: float) -> Optional[float]:
    """正常水深 h0。平坡与负坡返回 None。"""
    if i <= 0.0 or Q <= 0.0:
        return None
    f = lambda h: discharge_capacity(sec, n, i, h) - Q
    hi = max(10.0 * sec.b, 2.0)
    for _ in range(80):
        if f(hi) >= 0.0:
            return _bisect(f, 1e-10, hi)
        hi *= 2.0
        if hi > 1e7:
            return None
    return None


def critical_depth(sec: Section, Q: float, g: float = G_DEFAULT, alpha: float = 1.0) -> float:
    """临界水深 hc。矩形有解析式，梯形二分求解 αQ²B/(gA³)=1。"""
    if sec.kind == "rectangular" or sec.m < TINY:
        q = Q / sec.b
        return (alpha * q * q / g) ** (1.0 / 3.0)
    f = lambda h: alpha * Q * Q * props(sec, h).B / (g * props(sec, h).A ** 3) - 1.0
    hi = max(10.0 * sec.b, 2.0)
    for _ in range(80):
        if f(hi) <= 0.0:
            return _bisect(f, 1e-10, hi)
        hi *= 2.0
    raise ValueError("临界水深搜索失败")


def critical_slope(sec: Section, n: float, Q: float,
                   alpha: float = 1.0, g: float = G_DEFAULT) -> float:
    """临界底坡 ic = Q²n²/(Ac²Rc^(4/3))，随流量变化。"""
    hc = critical_depth(sec, Q, g, alpha)
    p = props(sec, hc)
    return Q * Q * n * n / (p.A ** 2 * p.R ** (4.0 / 3.0))


def classify_slope(i: float, ic: float, tol_rel: float = 1e-6) -> str:
    """底坡类型：mild | steep | critical | horizontal | adverse"""
    if abs(i) < TINY:
        return "horizontal"
    if i < 0.0:
        return "adverse"
    if abs(i - ic) <= tol_rel * max(ic, TINY):
        return "critical"
    return "mild" if i < ic else "steep"


# =============================================================================
# 4. 十二型判别
# =============================================================================

SLOPE_CN = {"mild": "缓坡", "steep": "陡坡", "critical": "临界坡",
            "horizontal": "平坡", "adverse": "负坡"}

# 沿顺流向 dh/dx 的符号：1、3 区壅水（+），2 区降水（-）
ZONE_TREND = {1: +1, 2: -1, 3: +1}


def zone_of(h: float, h0: Optional[float], hc: float, slope_class: str,
            tol_rel: float = 1e-9) -> Tuple[Optional[int], str, str]:
    """
    返回 (zone, curve_type, regime)。
    regime: subcritical | supercritical | critical
    """
    if abs(h - hc) <= tol_rel * max(hc, TINY):
        return None, "critical", "critical"

    if slope_class == "mild":
        if h > h0:
            return 1, "M1", "subcritical"
        if h > hc:
            return 2, "M2", "subcritical"
        return 3, "M3", "supercritical"

    if slope_class == "steep":
        if h > hc:
            return 1, "S1", "subcritical"
        if h > h0:
            return 2, "S2", "supercritical"
        return 3, "S3", "supercritical"

    if slope_class == "critical":
        if h > hc:
            return 1, "C1", "subcritical"
        return 3, "C3", "supercritical"

    if slope_class == "horizontal":
        return (2, "H2", "subcritical") if h > hc else (3, "H3", "supercritical")

    return (2, "A2", "subcritical") if h > hc else (3, "A3", "supercritical")


# =============================================================================
# 5. 渐变流基本关系
# =============================================================================

def hydraulic_slope(sec: Section, n: float, Q: float, h: float) -> float:
    """水力坡度 J = Q²/K²，K = (1/n) A R^(2/3)。"""
    p = props(sec, h)
    if p.A <= 0.0 or p.R <= 0.0:
        return float("inf")
    K = (1.0 / n) * p.A * p.R ** (2.0 / 3.0)
    return Q * Q / (K * K)


def froude2(sec: Section, Q: float, g: float, alpha: float, h: float) -> float:
    """Fr² = αQ²B/(gA³)。"""
    p = props(sec, h)
    if p.A <= 0.0:
        return float("inf")
    return alpha * Q * Q * p.B / (g * p.A ** 3)


def froude(sec: Section, Q: float, g: float, alpha: float, h: float) -> float:
    return math.sqrt(max(froude2(sec, Q, g, alpha, h), 0.0))


def specific_energy(sec: Section, Q: float, g: float, alpha: float, h: float) -> float:
    """断面单位能量 Es = h + αQ²/(2gA²)。"""
    p = props(sec, h)
    if p.A <= 0.0:
        return h
    return h + alpha * Q * Q / (2.0 * g * p.A * p.A)


def dhds(sec: Section, n: float, i: float, Q: float, g: float,
         alpha: float, h: float) -> float:
    """dh/ds = (i - J)/(1 - Fr²)。"""
    J = hydraulic_slope(sec, n, Q, h)
    den = 1.0 - froude2(sec, Q, g, alpha, h)
    if abs(den) < 1e-12:
        den = 1e-12 if den >= 0 else -1e-12
    return (i - J) / den


# =============================================================================
# 6. 水跃
# =============================================================================

def theta(sec: Section, Q: float, beta: float, g: float, h: float) -> float:
    """水跃函数 θ(h) = βQ²/(gA) + A·ȳ。"""
    p = props(sec, h)
    if p.A <= 0.0:
        return float("inf")
    return beta * Q * Q / (g * p.A) + p.A * p.ybar


def theta_min_depth(sec: Section, Q: float, beta: float, g: float,
                    alpha: float = 1.0) -> float:
    """
    θ(h) 的极小值点。满足 Fr² = α/β；当 α=β 时即 hc
    （教材：水跃函数最小时对应临界水深）。
    """
    if abs(alpha - beta) < 1e-12:
        return critical_depth(sec, Q, g, alpha)
    f = lambda h: froude2(sec, Q, g, alpha, h) - alpha / beta
    hi = max(10.0 * critical_depth(sec, Q, g, alpha), 10.0 * sec.b, 10.0)
    return _bisect(f, 1e-10, hi)


def conjugate_rect_explicit(b: float, Q: float, beta: float, g: float, h1: float) -> float:
    """矩形断面共轭水深显式式：h2 = (h1/2)(√(1+4c/h1³)-1)，c = 2βq²/g。"""
    q = Q / b
    c = 2.0 * beta * q * q / g
    return 0.5 * h1 * (math.sqrt(1.0 + 4.0 * c / h1 ** 3) - 1.0)


def conjugate_depth(sec: Section, Q: float, beta: float, g: float, h1: float,
                    alpha: float = 1.0) -> Optional[float]:
    """
    共轭水深：θ(h2) = θ(h1)。

    关键：θ(h1)=θ(h1) 是平凡根，必须排除。共轭对必然分居 θ 极小点两侧，
    因此在 h1 所在分支的对侧区间内求根。
    """
    if h1 <= 0.0:
        return None
    hmin = theta_min_depth(sec, Q, beta, g, alpha)
    th1 = theta(sec, Q, beta, g, h1)
    f = lambda h: theta(sec, Q, beta, g, h) - th1

    if h1 < hmin:
        # 急流 → 求缓流共轭，在 (hmin, +∞) 内
        lo, hi = hmin * (1.0 + 1e-12), max(50.0 * hmin, 10.0 * sec.b, 10.0)
        for _ in range(100):
            if f(lo) * f(hi) <= 0.0:
                break
            hi *= 2.0
            if hi > 1e8:
                return None
        try:
            return _bisect(f, lo, hi)
        except ValueError:
            return None

    # 缓流 → 求急流共轭，在 (0, hmin) 内
    lo, hi = 1e-10, hmin * (1.0 - 1e-12)
    try:
        return _bisect(f, lo, hi)
    except ValueError:
        return None


JUMP_TYPE_BANDS = [
    (9.0, "strong", "强水跃"),
    (4.5, "steady", "稳定水跃"),
    (2.5, "oscillating", "摆动水跃"),
    (1.7, "weak", "弱水跃"),
    (1.0, "wavy", "波状水跃"),
]


def jump_type(Fr1: float) -> Tuple[str, str]:
    for upper, code, cn in JUMP_TYPE_BANDS:
        if Fr1 > upper:
            return code, cn
    return "wavy", "波状水跃"


# 跃长经验式：三种并列，默认吴持恭式。系数须与教材原文核定后修正。
JUMP_LENGTH_FORMULAS = {
    "wu": ("L_j = 6.9 (h2 - h1)", lambda h1, h2, Fr1: 6.9 * (h2 - h1)),
    "elevatorski": ("L_j = 6.9 h1 (Fr1 - 1)", lambda h1, h2, Fr1: 6.9 * h1 * max(Fr1 - 1.0, 0.0)),
    "chen": ("L_j = 9.4 (Fr1 - 1) h1", lambda h1, h2, Fr1: 9.4 * max(Fr1 - 1.0, 0.0) * h1),
}


def jump_length(h1: float, h2: float, Fr1: float, formula: str = "wu") -> Tuple[float, str, str]:
    if formula not in JUMP_LENGTH_FORMULAS:
        formula = "wu"
    name, fn = JUMP_LENGTH_FORMULAS[formula]
    return max(fn(h1, h2, Fr1), 0.0), name, ""


def jump_energy_loss(sec: Section, h1: float, h2: float, Q: float,
                     g: float, alpha: float) -> Tuple[float, float, str]:
    """返回 (ΔE, 消能系数 Kj, 备注)。矩形用解析式，梯形用比能差近似。"""
    p1, p2 = props(sec, h1), props(sec, h2)
    v1 = Q / p1.A if p1.A > 0 else 0.0
    v2 = Q / p2.A if p2.A > 0 else 0.0
    E1 = h1 + alpha * v1 * v1 / (2.0 * g)
    E2 = h2 + alpha * v2 * v2 / (2.0 * g)
    if sec.kind == "rectangular" and sec.m < TINY:
        dE = (h2 - h1) ** 3 / (4.0 * h1 * h2)
        note = ""
    else:
        dE = max(E1 - E2, 0.0)
        note = "梯形断面，ΔE 由断面比能差计算，属近似处理"
    Kj = dE / E1 if E1 > 0 else 0.0
    return dE, Kj, note


# =============================================================================
# 7. 渠段特征与节点
# =============================================================================

def _build_infos(pr: Params) -> List[ReachInfo]:
    infos: List[ReachInfo] = []
    z = pr.reaches[0].z_b_start if pr.reaches[0].z_b_start is not None else 0.0
    for idx, r in enumerate(pr.reaches):
        if r.x_end <= r.x_start:
            raise ValueError(f"第 {idx} 段长度非正")
        if r.b <= 0:
            raise ValueError(f"第 {idx} 段底宽必须为正")
        if not (0.008 <= r.n <= 0.30):
            raise ValueError(f"第 {idx} 段糙率超出 [0.008, 0.30]")
        if r.m < 0 or r.m > 3:
            raise ValueError(f"第 {idx} 段边坡系数超出 [0, 3]")
        sec = r.section
        h0 = normal_depth(sec, r.n, r.i, pr.Q)
        hc = critical_depth(sec, pr.Q, pr.g, pr.alpha)
        ic = critical_slope(sec, r.n, pr.Q, pr.alpha, pr.g)
        p = props(sec, hc)
        K_c = (1.0 / r.n) * p.A * p.R ** (2.0 / 3.0)
        infos.append(ReachInfo(
            index=idx, name=r.name, x_start=r.x_start, x_end=r.x_end,
            i=r.i, b=r.b, m=r.m, n=r.n, n_tag=r.n_tag,
            z_b_start=z, z_b_end=z - r.i * r.L,
            h0=h0, hc=hc, ic=ic, slope_class=classify_slope(r.i, ic),
            A_c=p.A, R_c=p.R, K_c=K_c, ybar_c=p.ybar, B_c=p.B,
            v_c=pr.Q / p.A if p.A > 0 else 0.0,
            Es_c=specific_energy(sec, pr.Q, pr.g, pr.alpha, hc),
        ))
        z = infos[-1].z_b_end
    return infos


def _build_nodes(pr: Params, infos: List[ReachInfo]) -> List[Dict[str, Any]]:
    nodes = []
    for j in range(len(infos) - 1):
        a, b = infos[j], infos[j + 1]
        if a.slope_class != "steep" and b.slope_class == "steep":
            jt = "mild_to_steep"
        elif a.slope_class == "steep" and b.slope_class != "steep":
            jt = "steep_to_mild"
        elif a.slope_class == "steep":
            jt = "continuous_steep"
        else:
            jt = "continuous_mild"
        nodes.append({
            "index": j, "x": a.x_end, "z_b": a.z_b_end, "join_type": jt,
            "upstream_reach": j, "downstream_reach": j + 1,
            "control_h": b.hc if jt == "mild_to_steep" else None,
        })
    return nodes


def _reach_index_of(infos: List[ReachInfo], x: float) -> int:
    for info in infos:
        if info.x_start - 1e-9 <= x <= info.x_end + 1e-9:
            return info.index
    return 0 if x < infos[0].x_start else len(infos) - 1


def z_b_at(infos: List[ReachInfo], x: float) -> float:
    info = infos[_reach_index_of(infos, x)]
    return info.z_b_start - info.i * (x - info.x_start)


# =============================================================================
# 8. 分段求和法推进（主方法）
# =============================================================================

def _march_reach(pr: Params, info: ReachInfo, x0: float, h_start: float,
                 direction: int) -> Tuple[List[float], List[float], str]:
    """
    单渠段内推进。
      direction = -1 向里程减小方向（缓流向上游）；+1 向里程增大方向（急流向下游）。
    Δs = ΔEs/(i - J̄)，以水深为步进变量，相对最近渐近线几何收缩。
    返回 (xs, hs, reason)，reason ∈ reach_end/approach_h0/approach_hc/uniform/cross/truncated/max_steps
    """
    sec = info.section
    g, alpha, Q = pr.g, pr.alpha, pr.Q
    xs, hs = [x0], [h_start]
    x, h = x0, h_start
    x_lim = info.x_start if direction < 0 else info.x_end
    reason = "max_steps"

    for _ in range(pr.max_steps):
        if abs(froude(sec, Q, g, alpha, h) - 1.0) < pr.stop_gap:
            reason = "approach_hc"
            break
        if info.h0 is not None and abs(h - info.h0) / info.h0 < pr.tol_normal:
            reason = "approach_h0"
            break

        d = dhds(sec, info.n, info.i, Q, g, alpha, h)
        if abs(d) < 1e-14:
            reason = "uniform"
            break

        d_near = abs(h - info.h0) if info.h0 is not None else abs(h)
        step = max(pr.dh_rel * min(d_near, h), pr.dh_min)
        gap_hc = abs(h - info.hc)
        if gap_hc > TINY:
            step = min(step, 0.25 * gap_hc)

        sign_h = 1.0 if d * direction > 0 else -1.0
        h_next = h + sign_h * step
        if h_next <= 1e-9:
            reason = "truncated"
            break

        Es1 = specific_energy(sec, Q, g, alpha, h)
        Es2 = specific_energy(sec, Q, g, alpha, h_next)
        denom = info.i - 0.5 * (hydraulic_slope(sec, info.n, Q, h)
                                + hydraulic_slope(sec, info.n, Q, h_next))
        if abs(denom) < 1e-14:
            reason = "approach_h0"
            break

        ds = (Es2 - Es1) / denom
        if ds * direction <= 0.0:
            reason = "cross"
            break

        x_next = x + ds
        if (direction < 0 and x_next <= x_lim) or (direction > 0 and x_next >= x_lim):
            t = (x_lim - x) / ds if abs(ds) > TINY else 1.0
            t = min(max(t, 0.0), 1.0)
            xs.append(x_lim)
            hs.append(h + t * (h_next - h))
            reason = "reach_end"
            break

        x, h = x_next, h_next
        xs.append(x)
        hs.append(h)
        if len(xs) > 20000:
            reason = "max_steps"
            break

    return xs, hs, reason


def _march_chain(pr: Params, infos: List[ReachInfo], x0: float, h_start: float,
                 direction: int) -> Tuple[List[float], List[float], str]:
    """跨段连续推进；接近正常水深时以均匀流补满该段再进入下一段。"""
    xs, hs = [x0], [h_start]
    idx = _reach_index_of(infos, x0)
    x, h = x0, h_start
    reason = "max_steps"

    for _ in range(len(infos) + 2):
        if not (0 <= idx < len(infos)):
            break
        info = infos[idx]
        sx, sh, r = _march_reach(pr, info, x, h, direction)
        xs.extend(sx[1:])
        hs.extend(sh[1:])
        x, h = xs[-1], hs[-1]
        x_lim = info.x_start if direction < 0 else info.x_end

        if r == "approach_hc":
            if abs(x_lim - x) <= 0.15 * info.L and abs(x_lim - x) > 1e-9:
                xs.append(x_lim)
                hs.append(info.hc)
            reason = "approach_hc"
            break

        if r == "approach_h0" and info.h0 is not None:
            if abs(x_lim - x) > 1e-9:
                xs.append(x_lim)
                hs.append(info.h0)
                x, h = x_lim, info.h0
            idx += -1 if direction < 0 else 1
            if not (0 <= idx < len(infos)):
                reason = "approach_h0"
                break
            continue

        if r != "reach_end":
            reason = r
            break

        idx += -1 if direction < 0 else 1
        if not (0 <= idx < len(infos)):
            reason = "reach_end"
            break

        nxt = infos[idx]
        if direction > 0 and h >= nxt.hc:
            reason = "regime_break"
            break
        if direction < 0 and h <= nxt.hc:
            reason = "regime_break"
            break
        reason = "reach_end"

    return xs, hs, reason


# =============================================================================
# 9. 四阶龙格库塔（互校方法）
# =============================================================================

def march_rk4(pr: Params, info: ReachInfo, x0: float, h_start: float,
              direction: int, length: float, n_steps: int = 2000) -> float:
    """以里程为自变量积分基本微分方程，返回推进 length 后的水深。"""
    sec = info.section
    ds = direction * length / n_steps

    def f(hh: float) -> float:
        return dhds(sec, info.n, info.i, pr.Q, pr.g, pr.alpha, hh)

    h = h_start
    for _ in range(n_steps):
        k1 = f(h)
        k2 = f(h + 0.5 * ds * k1)
        k3 = f(h + 0.5 * ds * k2)
        k4 = f(h + ds * k3)
        h += ds * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
        if h <= 1e-9:
            break
    return h


# =============================================================================
# 10. 链条裁剪、重叠消解、水跃定位
# =============================================================================

def _sorted_chain(chain: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
    x = np.asarray(chain["x"], float)
    h = np.asarray(chain["h"], float)
    order = np.argsort(x)
    return x[order], h[order]


def _trim_chain(chain: Dict[str, Any], x_cut: float, keep: str) -> Optional[Dict[str, Any]]:
    """按里程裁剪链；keep='left' 保留 x<=x_cut，'right' 保留 x>=x_cut。"""
    x, h = _sorted_chain(chain)
    if x.size == 0:
        return None
    if x_cut < x[0] - 1e-9 or x_cut > x[-1] + 1e-9:
        return dict(chain)
    h_cut = float(np.interp(x_cut, x, h))
    if keep == "left":
        mask = x <= x_cut
        xs, hs = x[mask], h[mask]
        if xs.size == 0 or xs[-1] < x_cut - 1e-12:
            xs, hs = np.append(xs, x_cut), np.append(hs, h_cut)
    else:
        mask = x >= x_cut
        xs, hs = x[mask], h[mask]
        if xs.size == 0 or xs[0] > x_cut + 1e-12:
            xs, hs = np.insert(xs, 0, x_cut), np.insert(hs, 0, h_cut)
    if xs.size < 2:
        return dict(chain)
    out = dict(chain)
    out["x"] = list(xs)
    out["h"] = list(hs)
    return out


def _cut_overlaps(chains: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """后加入的链在重叠区优先，先加入者被裁剪，保证图上无重叠曲线。"""
    out: List[Dict[str, Any]] = []
    for c in chains:
        if not c["x"]:
            continue
        lo, hi = min(c["x"]), max(c["x"])
        kept: List[Dict[str, Any]] = []
        for e in out:
            elo, ehi = min(e["x"]), max(e["x"])
            if ehi < lo - 1e-9 or elo > hi + 1e-9:
                kept.append(e)
                continue
            if elo < lo - 1e-9:
                t = _trim_chain(e, lo, "left")
                if t:
                    kept.append(t)
            if ehi > hi + 1e-9:
                t = _trim_chain(e, hi, "right")
                if t:
                    kept.append(t)
        kept.append(c)
        out = kept
    return out


def _locate_jump(pr: Params, infos: List[ReachInfo],
                 sup: List[Dict[str, Any]], sub: List[Dict[str, Any]],
                 warnings: List[Warning]) -> Optional[Transition]:
    """
    急流线与缓流线对撞求跃位：
      f(x) = h_sup(x) - h2'(h_sub(x))，其中 h2' 为缓流水深的急流共轭。
    共轭计算使用该里程实际所属渠段的断面。
    """
    if not sup or not sub:
        return None

    xs_s = np.concatenate([_sorted_chain(c)[0] for c in sup])
    hs_s = np.concatenate([_sorted_chain(c)[1] for c in sup])
    xs_m = np.concatenate([_sorted_chain(c)[0] for c in sub])
    hs_m = np.concatenate([_sorted_chain(c)[1] for c in sub])
    os_, om_ = np.argsort(xs_s), np.argsort(xs_m)
    xs_s, hs_s = xs_s[os_], hs_s[os_]
    xs_m, hs_m = xs_m[om_], hs_m[om_]

    lo = max(float(xs_s[0]), float(xs_m[0]))
    hi = min(float(xs_s[-1]), float(xs_m[-1]))
    if hi - lo <= 1e-6:
        return None

    cache: Dict[Tuple[int, float], Optional[float]] = {}

    def conj_at(x: float) -> Optional[float]:
        h_sub = float(np.interp(x, xs_m, hs_m))
        rid = _reach_index_of(infos, x)
        key = (rid, round(h_sub, 9))
        if key not in cache:
            cache[key] = conjugate_depth(infos[rid].section, pr.Q, pr.beta, pr.g, h_sub, pr.alpha)
        return cache[key]

    def resid(x: float) -> float:
        c = conj_at(x)
        if c is None:
            return float("nan")
        return float(np.interp(x, xs_s, hs_s)) - c

    grid = np.linspace(hi, lo, 240)
    vals = [resid(float(x)) for x in grid]

    root = None
    for i in range(len(grid) - 1):
        f0, f1 = vals[i], vals[i + 1]
        if math.isnan(f0) or math.isnan(f1):
            continue
        if f0 == 0.0:
            root = float(grid[i])
            break
        if f0 * f1 < 0.0:
            a, b = float(grid[i]), float(grid[i + 1])
            for _ in range(100):
                m = 0.5 * (a + b)
                fm = resid(m)
                if math.isnan(fm) or abs(b - a) < 1e-6:
                    break
                if fm * f0 < 0.0:
                    b = m
                else:
                    a = m
            root = 0.5 * (a + b)
            break

    if root is None:
        # 无交点：用下游端的尾水条件判断结局
        h1_end = float(np.interp(hi, xs_s, hs_s))
        h_tail = float(np.interp(hi, xs_m, hs_m))
        rid = _reach_index_of(infos, hi)
        h2_req = conjugate_depth(infos[rid].section, pr.Q, pr.beta, pr.g, h1_end, pr.alpha)
        if h2_req is not None and h_tail < h2_req:
            warnings.append(Warning("W_JUMP_WASHED", "warn", "transition",
                                    "水跃被冲出本渠段：尾水水深小于所需跃后共轭水深", x=hi))
            return Transition(kind="jump_washed", x=hi, reach_index=rid,
                              data={"h1": h1_end, "h2_required": h2_req, "h_tail": h_tail},
                              note="需延长计算渠段或抬高低游水位")
        warnings.append(Warning("W_JUMP_SUBMERGED", "warn", "transition",
                                "水跃被淹没或不产生：尾水水深大于跃后共轭水深", x=hi))
        return Transition(kind="jump_submerged", x=hi, reach_index=rid,
                          data={"h1": h1_end,
                                "h2": h2_req if h2_req is not None else h_tail,
                                "h_tail": h_tail,
                                "submergence": ((h_tail - h2_req) / h2_req) if h2_req else None},
                          note="推移至急流段末端，按淹没式水跃处理")

    rid = _reach_index_of(infos, root)
    sec = infos[rid].section
    h1 = float(np.interp(root, xs_s, hs_s))
    h2 = conjugate_depth(sec, pr.Q, pr.beta, pr.g, h1, pr.alpha)
    if h2 is None:
        return None
    Fr1 = froude(sec, pr.Q, pr.g, pr.alpha, h1)
    code, cn = jump_type(Fr1)
    Lj, fname, fnote = jump_length(h1, h2, Fr1, pr.jump_len_formula)
    dE, Kj, enote = jump_energy_loss(sec, h1, h2, pr.Q, pr.g, pr.alpha)
    note = "；".join([s for s in (fnote, enote) if s])

    return Transition(kind="jump", x=root, reach_index=rid,
                      data={"h1": h1, "h2": h2, "Fr1": Fr1,
                            "jump_type": code, "jump_type_cn": cn,
                            "L_j": Lj, "L_j_formula": fname,
                            "dE": dE, "Kj": Kj,
                            "theta1": theta(sec, pr.Q, pr.beta, pr.g, h1),
                            "theta2": theta(sec, pr.Q, pr.beta, pr.g, h2)},
                      note=note)


# =============================================================================
# 11. 装配
# =============================================================================

def _len_to_99pct(x: np.ndarray, h: np.ndarray, h0: Optional[float]) -> Optional[float]:
    if h0 is None or h0 <= 0:
        return None
    idx = np.where(np.abs(h - h0) / h0 <= 0.01)[0]
    if idx.size == 0:
        return None
    return float(abs(x[int(idx[0])] - x[0]))


def _build_segments(pr: Params, infos: List[ReachInfo],
                    chains: List[Dict[str, Any]]) -> List[Segment]:
    segs: List[Segment] = []
    for ch in chains:
        if len(ch["x"]) < 1:
            continue
        x, h = _sorted_chain(ch)
        rid = np.array([_reach_index_of(infos, float(v)) for v in x])
        cuts = np.where(np.diff(rid) != 0)[0] + 1
        bounds = list(zip(np.r_[0, cuts], np.r_[cuts, len(x)]))
        for lo, hi in bounds:
            if hi <= lo:
                continue
            xs, hs = x[lo:hi], h[lo:hi]
            # 段首与上段末点之间若存在间隙（渠段交界点被归入上段），补上交界点，
            # 保证水面曲线在变坡处连续，不留下 1 mm 量级的空洞。
            if lo > 0 and float(xs[0]) - float(x[lo - 1]) > 1e-12:
                xs = np.insert(xs, 0, x[lo - 1])
                hs = np.insert(hs, 0, h[lo - 1])
            # 零长度段（如临界控制点被单独切出）只是锚点，不构成水面曲线，
            # 否则会把临界点错报成 M3 之类的型号。
            if float(xs[-1]) - float(xs[0]) <= 1e-12:
                continue
            info = infos[int(rid[lo])]
            sec = info.section
            z_b = np.array([info.z_b_start - info.i * (v - info.x_start) for v in xs])
            Es = np.array([specific_energy(sec, pr.Q, pr.g, pr.alpha, v) for v in hs])
            Fr = np.array([froude(sec, pr.Q, pr.g, pr.alpha, v) for v in hs])
            vv = np.array([pr.Q / props(sec, v).A if props(sec, v).A > 0 else 0.0 for v in hs])
            zone, ctype, regime = zone_of(float(hs[0]), info.h0, info.hc, info.slope_class)
            segs.append(Segment(
                seg_id=0, reach_index=info.index, curve_type=ctype, regime=regime,
                direction=int(ch["direction"]), x=xs, h=hs, z_b=z_b, z_w=z_b + hs,
                Es=Es, Fr=Fr, v=vv,
                start_reason=ch.get("source", "unknown"),
                end_reason=ch.get("reason", "march_end"),
                len_to_99pct_h0=_len_to_99pct(xs, hs, info.h0),
                flags={"zone": zone,
                       "near_critical": bool(np.min(np.abs(Fr - 1.0)) < pr.stop_gap * 10),
                       "asymptotic_h0": bool(info.h0 is not None and hs.size and
                                             abs(float(hs[-1]) - info.h0) / info.h0 <= pr.tol_normal * 1.5)},
            ))
    segs.sort(key=lambda s: (float(np.min(s.x)), s.direction))
    for i, s in enumerate(segs):
        s.seg_id = i
    return segs


def _coverage_gaps(segments: List[Segment], x0: float, xL: float) -> List[Tuple[float, float]]:
    if not segments:
        return [(x0, xL)]
    iv = sorted((float(np.min(s.x)), float(np.max(s.x))) for s in segments)
    merged = [list(iv[0])]
    for lo, hi in iv[1:]:
        if lo <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    gaps = []
    if merged[0][0] > x0 + 1e-6:
        gaps.append((x0, merged[0][0]))
    for a, b in zip(merged[:-1], merged[1:]):
        if b[0] - a[1] > 1e-6:
            gaps.append((a[1], b[0]))
    if merged[-1][1] < xL - 1e-6:
        gaps.append((merged[-1][1], xL))
    return gaps


# =============================================================================
# 12. 主求解
# =============================================================================

def solve(pr: Params) -> Result:
    t_start = time.perf_counter()
    infos = _build_infos(pr)
    warnings: List[Warning] = []
    nodes = _build_nodes(pr, infos)
    controls: List[Dict[str, Any]] = []
    transitions: List[Transition] = []
    chains: List[Dict[str, Any]] = []

    x0 = infos[0].x_start
    xL = infos[-1].x_end
    last = infos[-1]

    # ---------- 12.1 下游控制 ----------
    h_down: Optional[float] = None
    bc = pr.downstream
    if bc.type == "given_h" and bc.h is not None:
        h_down = bc.h
    elif bc.type == "given_zw" and bc.zw is not None:
        h_down = bc.zw - last.z_b_end
    elif bc.type == "normal_depth":
        h_down = last.h0
        if h_down is None:
            warnings.append(Warning("W_H0_ABSENT", "warn", "global",
                                    "末段为平坡或负坡，不能以正常水深作下游控制"))
    elif bc.type == "free_outfall":
        if last.slope_class == "steep":
            warnings.append(Warning("W_OUTFALL_SUPERCRITICAL", "info", "global",
                                    "末段为陡坡，渠末自由跌水不构成控制（急流不受下游控制）"))
        else:
            h_down = last.hc

    if h_down is not None and h_down < last.hc * (1.0 - 1e-9):
        warnings.append(Warning("W_DOWNSTREAM_SUPERCRITICAL", "warn", "global",
                                "下游给定水深小于临界水深，该控制不能向上游传播"))
        h_down = None

    covered_left = xL
    if h_down is not None:
        is_critical_bc = abs(h_down - last.hc) / last.hc <= 1e-6
        if is_critical_bc:
            h_start = (0.7 * last.hc) if pr.outfall_correction else last.hc * (1.0 + pr.hc_offset_rel)
            controls.append({"kind": "free_outfall", "x": xL, "h_control": last.hc,
                             "direction": -1, "reason": "渠末自由跌水，取临界水深控制"})
            transitions.append(Transition(kind="outfall", x=xL, reach_index=last.index,
                                          data={"hc": last.hc,
                                                "correction_applied": bool(pr.outfall_correction),
                                                "h_used": h_start}))
            if pr.outfall_correction:
                warnings.append(Warning("W_OUTFALL_CORRECTION", "info", "global",
                                        "已启用跌坎水深经验修正 0.7hc", x=xL))
        else:
            h_start = h_down
            controls.append({"kind": "downstream_h", "x": xL, "h_control": h_down,
                             "direction": -1, "reason": "下游给定水深，缓流向上游控制"})

        xs, hs, reason = _march_chain(pr, infos, xL, h_start, -1)
        chains.append({"direction": -1, "x": xs, "h": hs,
                       "source": "downstream", "reason": reason})
        covered_left = min(xs)

    # ---------- 12.2 未被覆盖的上游区域 ----------
    if covered_left > x0 + 1e-6:
        anchor_x = anchor_h = None
        anchor_src = ""
        up = pr.upstream

        if up.type == "given_h" and up.h is not None:
            anchor_x, anchor_h, anchor_src = x0, up.h, "upstream"
        elif up.type == "normal_depth" and infos[0].h0 is not None:
            anchor_x, anchor_h, anchor_src = x0, infos[0].h0, "upstream"
        else:
            for nd in nodes:
                if nd["join_type"] == "mild_to_steep" and x0 < nd["x"] <= covered_left + 1e-9:
                    anchor_x, anchor_h, anchor_src = nd["x"], nd["control_h"], "critical"
                    break

        if anchor_x is None:
            first = infos[0]
            if first.slope_class == "steep":
                anchor_x, anchor_h, anchor_src = x0, first.hc * (1 - pr.hc_offset_rel), "critical"
                warnings.append(Warning("W_ASSUMED_CRITICAL_INLET", "warn", "global",
                                        "上游未给定条件且首段为陡坡，按渠首临界水深假设", x=x0))
            elif first.h0 is not None:
                anchor_x, anchor_h, anchor_src = x0, first.h0, "upstream"
                warnings.append(Warning("W_ASSUMED_NORMAL_INLET", "warn", "global",
                                        "上游未给定条件，按首段正常水深假设", x=x0))
            else:
                anchor_x, anchor_h, anchor_src = x0, first.hc * (1 + pr.hc_offset_rel), "critical"
                warnings.append(Warning("W_ASSUMED_CRITICAL_INLET", "warn", "global",
                                        "上游未给定条件且无正常水深，按渠首临界水深假设", x=x0))

        if anchor_src == "critical":
            transitions.append(Transition(kind="critical", x=anchor_x,
                                          reach_index=_reach_index_of(infos, anchor_x),
                                          data={"hc": anchor_h, "Fr": 1.0},
                                          note="缓坡转陡坡或渠首临界，取临界水深作双侧控制"))
            controls.append({"kind": "critical_at_node", "x": anchor_x, "h_control": anchor_h,
                             "direction": 0, "reason": "临界控制，双向推算"})
            sx, sh, r1 = _march_chain(pr, infos, anchor_x, anchor_h * (1 + pr.hc_offset_rel), -1)
            chains.append({"direction": -1, "x": sx, "h": sh,
                           "source": "critical_up", "reason": r1})
            sx, sh, r2 = _march_chain(pr, infos, anchor_x, anchor_h * (1 - pr.hc_offset_rel), +1)
            chains.append({"direction": +1, "x": sx, "h": sh,
                           "source": "critical_down", "reason": r2})
        else:
            controls.append({"kind": "upstream_h", "x": anchor_x, "h_control": anchor_h,
                             "direction": +1, "reason": "上游给定水深"})
            sx, sh, r = _march_chain(pr, infos, anchor_x, anchor_h, +1)
            chains.append({"direction": +1, "x": sx, "h": sh,
                           "source": "upstream", "reason": r})

    # ---------- 12.3 水跃 ----------
    sup = [c for c in chains if c["direction"] > 0]
    sub = [c for c in chains if c["direction"] < 0]
    tr = _locate_jump(pr, infos, sup, sub, warnings)
    if tr is not None:
        transitions.append(tr)
        if tr.kind == "jump":
            trimmed = []
            for c in sup:
                t = _trim_chain(c, tr.x, "left")
                if t:
                    trimmed.append(t)
            for c in sub:
                t = _trim_chain(c, tr.x, "right")
                if t:
                    trimmed.append(t)
            chains = trimmed

    # ---------- 12.4 消解重叠并装配 ----------
    chains = _cut_overlaps(chains)
    segments = _build_segments(pr, infos, chains)

    for lo, hi in _coverage_gaps(segments, x0, xL):
        warnings.append(Warning("W_GAP", "warn", "global",
                                f"里程 {lo:.1f}~{hi:.1f} m 未被任何曲线覆盖",
                                hint="检查上下游控制是否缺失或矛盾", x=lo))

    # ---------- 12.5 断面查询 ----------
    section_at = [section_at_point(pr, infos, segments, transitions, float(xq))
                  for xq in pr.section_at_x]

    # ---------- 12.6 自检 ----------
    warnings.extend(_validate_segments(pr, infos, segments))

    status = "ok"
    if not segments:
        status = "no_solution"
    elif any(w.level == "error" for w in warnings):
        status = "partial"

    # ---------- 12.7 互校 ----------
    dev = None
    if segments:
        s0 = max(segments, key=lambda s: abs(s.x[-1] - s.x[0]))
        xa, xb = float(min(s0.x[0], s0.x[-1])), float(max(s0.x[0], s0.x[-1]))
        length = min(50.0, xb - xa)
        if length > 1.0:
            h_start = float(s0.h[0])
            h_rk4 = march_rk4(pr, infos[s0.reach_index], float(s0.x[0]), h_start,
                              s0.direction, length, 4000)
            xs_sorted, hs_sorted = _sorted_chain({"x": s0.x, "h": s0.h})
            h_std = float(np.interp(s0.x[0] + s0.direction * length, xs_sorted, hs_sorted))
            dev = abs(h_rk4 - h_std) / max(abs(h_std), 1e-9)

    diagnostics = {
        "method": "standard_step",
        "cross_check_method": "rk4",
        "cross_check_dev": None if dev is None else float(dev),
        "n_segments": len(segments),
        "n_transitions": len(transitions),
        "wall_time_ms": (time.perf_counter() - t_start) * 1e3,
        "schema_version": SCHEMA_VERSION,
    }

    facts = _build_facts(pr, infos, segments, transitions, warnings)

    return Result(
        status=status,
        params_echo={
            "Q": pr.Q, "g": pr.g, "alpha": pr.alpha, "beta": pr.beta,
            "mode": pr.mode, "case_name": pr.case_name,
            "i": [r.i for r in pr.reaches], "b": [r.b for r in pr.reaches],
            "m": [r.m for r in pr.reaches], "n": [r.n for r in pr.reaches],
            "upstream": {"type": pr.upstream.type, "h": pr.upstream.h},
            "downstream": {"type": pr.downstream.type, "h": pr.downstream.h, "zw": pr.downstream.zw},
            "jump_len_formula": pr.jump_len_formula,
            "outfall_correction": pr.outfall_correction,
        },
        reaches=infos, nodes=nodes, controls=controls,
        segments=segments, transitions=transitions,
        section_at=section_at, facts=facts,
        warnings=warnings, diagnostics=diagnostics,
    )


# =============================================================================
# 13. 断面查询（供前端点击联动）
# =============================================================================

def section_at_point(pr: Params, infos: List[ReachInfo], segments: List[Segment],
                     transitions: List[Transition], xq: float) -> Dict[str, Any]:
    info = infos[_reach_index_of(infos, xq)]
    sec = info.section
    hit = None
    for s in segments:
        lo, hi = float(np.min(s.x)), float(np.max(s.x))
        if lo - 1e-6 <= xq <= hi + 1e-6:
            hit = s
            break
    if hit is None:
        return {"x": xq, "found": False}

    xs, hs = _sorted_chain({"x": hit.x, "h": hit.h})
    h = float(np.interp(xq, xs, hs))
    p = props(sec, h)
    zone, ctype, regime = zone_of(h, info.h0, info.hc, info.slope_class)
    z_b = info.z_b_start - info.i * (xq - info.x_start)

    in_jump, h1j, h2j = False, None, None
    for t in transitions:
        if t.kind == "jump":
            Lj = float(t.data.get("L_j", 0.0))
            if t.x - 1e-6 <= xq <= t.x + max(Lj, 0.0):
                in_jump, h1j, h2j = True, t.data.get("h1"), t.data.get("h2")
                break

    return {
        "x": xq, "found": True, "reach_index": info.index, "seg_id": hit.seg_id,
        "curve_type": ctype, "regime": regime, "zone": zone,
        "h": h, "z_b": z_b, "z_w": z_b + h,
        "A": p.A, "B": p.B, "P": p.P, "R": p.R,
        "v": pr.Q / p.A if p.A > 0 else 0.0,
        "Fr": froude(sec, pr.Q, pr.g, pr.alpha, h),
        "Es": specific_energy(sec, pr.Q, pr.g, pr.alpha, h),
        "ybar": p.ybar, "hbar": p.hbar,
        "depth_ratio_hc": h / info.hc if info.hc > 0 else None,
        "hbar_ratio_h0": (p.hbar / info.h0) if info.h0 else None,
        "in_jump_zone": in_jump, "jump_h1": h1j, "jump_h2": h2j,
    }


# =============================================================================
# 14. 物理自检
# =============================================================================

def _validate_segments(pr: Params, infos: List[ReachInfo],
                       segments: List[Segment]) -> List[Warning]:
    out: List[Warning] = []
    for s in segments:
        if s.x.size < 3:
            continue
        xs, hs = _sorted_chain({"x": s.x, "h": s.h})

        # ① 壅水/降水符号自洽（沿顺流向 dh/dx）
        zone = s.flags.get("zone")
        if zone in ZONE_TREND:
            slope = (hs[-1] - hs[0]) / (xs[-1] - xs[0])
            if abs(slope) > 1e-12:
                got = 1 if slope > 0 else -1
                if got != ZONE_TREND[zone]:
                    out.append(Warning("E_ZONE_SIGN", "error", "segment",
                                       f"{s.curve_type} 的壅水/降水符号与第 {zone} 区不符",
                                       hint=f"实测 dh/dx={slope:.3e}，应为 {ZONE_TREND[zone]:+d}",
                                       index=s.seg_id, x=float(xs[0])))

        # ② 总水头沿顺流向单调下降（必须用 E，不能用 Es）
        z_w = np.interp(xs, s.x[np.argsort(s.x)], s.z_b[np.argsort(s.x)] + s.h[np.argsort(s.x)])
        v = pr.Q / np.array([props(infos[s.reach_index].section, hh).A for hh in hs])
        E = z_w + pr.alpha * v ** 2 / (2.0 * pr.g)
        dE = np.diff(E)
        if np.any(dE > 1e-3):
            out.append(Warning("E_HEAD_INCREASE", "error", "segment",
                               f"{s.curve_type} 段总水头沿程回升",
                               hint=f"最大回升 {float(np.max(dE)):.3e} m",
                               index=s.seg_id, x=float(xs[0])))
    return out


# =============================================================================
# 15. 事实包（供 AI 转述，所有数值进白名单）
# =============================================================================

def _fmt(v: Optional[float]) -> str:
    if v is None:
        return ""
    a = abs(v)
    if a >= 1000:
        return f"{v:.0f}"
    if a >= 10:
        return f"{v:.2f}"
    if a >= 1:
        return f"{v:.3f}"
    return f"{v:.4f}"


def _build_facts(pr: Params, infos: List[ReachInfo], segments: List[Segment],
                 transitions: List[Transition], warnings: List[Warning]) -> Dict[str, Any]:
    seq: List[Dict[str, Any]] = []
    for s in segments:
        seq.append({"kind": "curve", "label": s.curve_type, "reach_index": s.reach_index,
                    "x_start": float(np.min(s.x)), "x_end": float(np.max(s.x))})
    for t in transitions:
        seq.append({"kind": "junction",
                    "label": {"jump": "水跃", "critical": "临界", "outfall": "水跌",
                              "jump_submerged": "淹没水跃", "jump_washed": "水跃出渠"}.get(t.kind, t.kind),
                    "x_start": t.x, "x_end": t.x})
    seq.sort(key=lambda v: v["x_start"])

    max_h, max_zw, max_x = 0.0, None, None
    for s in segments:
        if s.h.size and float(np.max(s.h)) > max_h:
            max_h = float(np.max(s.h))
        if s.z_w.size:
            j = int(np.argmax(s.z_w))
            if max_zw is None or float(s.z_w[j]) > max_zw:
                max_zw, max_x = float(s.z_w[j]), float(s.x[j])

    jumps = [t for t in transitions if t.kind in ("jump", "jump_submerged", "jump_washed")]

    reasons: List[str] = []
    for info in infos:
        reasons.append(f"第{info.index + 1}段：i={_fmt(info.i)}，ic={_fmt(info.ic)}，"
                       f"判定为{SLOPE_CN[info.slope_class]}")
        if info.h0 is None:
            reasons.append(f"第{info.index + 1}段：无正常水深，Ⅰ区不存在")
        else:
            reasons.append(f"第{info.index + 1}段：h0={_fmt(info.h0)}，hc={_fmt(info.hc)}")
    for s in segments:
        if s.flags.get("zone"):
            reasons.append(f"{s.curve_type}：h 位于第 {s.flags['zone']} 区，"
                           f"流态为{'缓流' if s.regime == 'subcritical' else '急流'}，"
                           f"{'自上游向下游' if s.direction > 0 else '自下游向上游'}推算")

    key_numbers: Dict[str, Any] = {
        "Q": pr.Q, "alpha": pr.alpha, "beta": pr.beta,
        "h0": [i.h0 for i in infos], "hc": [i.hc for i in infos], "ic": [i.ic for i in infos],
        "max_h": max_h, "max_zw": max_zw, "max_zw_x": max_x,
        "jump_x": [t.x for t in jumps],
        "backwater_len": [s.len_to_99pct_h0 for s in segments if s.len_to_99pct_h0 is not None],
    }
    if jumps and jumps[0].kind == "jump":
        d = jumps[0].data
        key_numbers.update({f"jump_{k}": d.get(k) for k in
                            ("h1", "h2", "Fr1", "dE", "Kj", "L_j")})

    whitelist: List[str] = []

    def add(v):
        if v is None:
            return
        if isinstance(v, (list, tuple)):
            for x in v:
                add(x)
            return
        s = _fmt(float(v))
        if s and s not in whitelist:
            whitelist.append(s)

    for k in ("Q", "alpha", "beta", "h0", "hc", "ic", "max_h", "max_zw",
              "backwater_len", "jump_h1", "jump_h2", "jump_Fr1", "jump_dE",
              "jump_Kj", "jump_L_j"):
        add(key_numbers.get(k))
    for t in jumps:
        add(t.x)

    return {
        "type_sequence": seq,
        "key_numbers": key_numbers,
        "reasons": reasons,
        "cautions": [w.message for w in warnings if w.level in ("warn", "error")],
        "number_whitelist": whitelist,
    }


# =============================================================================
# 16. 反问题（单参数）
# =============================================================================

# target: 参数名；sense: 求解方向（max = 求上限，min = 求下限）
_INV_TARGETS = {
    "Q": ("Q", "max", 0.05, 20.0, "流量上限"),
    "b": ("b", "min", 0.2, 10.0, "所需最小底宽"),
    "n": ("n", "min", 0.008, 0.30, "所需最大糙率（越光滑越有利）"),
    "L": ("L", "max", 0.1, 20.0, "允许最大渠长倍数"),
}


def _with_value(base: Params, target: str, value: float, reach_index: int) -> Params:
    p = copy.deepcopy(base)
    if target == "Q":
        p.Q = float(value)
        return p
    if target == "L":
        r = p.reaches[reach_index]
        new_L = float(value) * r.L
        delta = new_L - r.L
        r.x_end = r.x_start + new_L
        for r2 in p.reaches[reach_index + 1:]:
            r2.x_start += delta
            r2.x_end += delta
        return p
    setattr(p.reaches[reach_index], target, float(value))
    return p


def _measure(pr: Params, constraint: str, reach_index: int) -> Dict[str, Any]:
    """执行一次正问题并提炼约束指标。"""
    res = solve(pr)
    info = res.reaches[reach_index] if reach_index < len(res.reaches) else None
    segs = [s for s in res.segments if s.reach_index == reach_index]
    out = {"ok": bool(segs), "res": res}

    if constraint == "h_max":
        vals = [float(np.max(s.h)) for s in segs] if segs else []
        out["value"] = max(vals) if vals else float("inf")
    elif constraint == "zw_max":
        vals = [float(np.max(s.z_w)) for s in segs] if segs else []
        out["value"] = max(vals) if vals else float("inf")
    elif constraint == "jump_x_max":
        jumps = [t for t in res.transitions if t.kind in ("jump", "jump_submerged", "jump_washed")]
        out["value"] = min(t.x for t in jumps) if jumps else None
    elif constraint == "curve_type":
        out["types"] = sorted({s.curve_type for s in segs})
    else:
        raise ValueError(f"未知约束：{constraint}")
    return out


def solve_inverse(base: Params, target: str, constraint: str, limit: Any,
                  reach_index: int = 0, lo: Optional[float] = None,
                  hi: Optional[float] = None, n_scan: int = 60) -> Dict[str, Any]:
    """
    单参数反问题。多解全部给出。

    参数
    ----
    target     : "Q" | "b" | "n" | "L"，每次只反求一项
    constraint : "h_max"（水深上限）| "zw_max"（水位上限）
                 | "jump_x_max"（跃位上限）| "curve_type"（指定型号字符串）
    limit      : 约束值；curve_type 时为型号字符串，如 "M2"
    lo, hi     : 参数搜索范围；缺省取 target 的默认倍率
    n_scan     : 网格扫描点数（用于捕获非单调情形下的多个解）

    返回
    ----
    {
      "target","constraint","limit","sense",
      "feasible_intervals": [(v_lo, v_hi), ...],
      "solutions": [边界值, ...],       # 可行性发生变化的参数值
      "best": 推荐值（None 表示无解）,
      "samples": [(参数值, 指标值, 是否可行), ...]
    }
    """
    if target not in _INV_TARGETS:
        raise ValueError(f"不支持的反求目标：{target}")
    attr, sense, d_lo, d_hi, cn = _INV_TARGETS[target]

    if target == "Q":
        base_val = base.Q
    elif target == "L":
        base_val = 1.0
        d_lo, d_hi = 0.2, 20.0
    else:
        base_val = getattr(base.reaches[reach_index], attr)
        d_lo, d_hi = (0.3 * base_val if attr != "n" else 0.008,
                      3.0 * base_val if attr != "n" else 0.30)

    lo = d_lo if lo is None else lo
    hi = d_hi if hi is None else hi
    if hi <= lo:
        raise ValueError("搜索区间无效")

    def feasible(v: float) -> Tuple[bool, Optional[float]]:
        try:
            m = _measure(_with_value(base, target, v, reach_index), constraint, reach_index)
        except (ValueError, ZeroDivisionError):
            return False, None
        if constraint == "curve_type":
            return (str(limit) in m.get("types", [])), None
        val = m.get("value")
        if val is None:
            return False, None
        if constraint == "jump_x_max":
            return (val <= float(limit)), float(val)
        return (val <= float(limit)), float(val)

    grid = np.geomspace(lo, hi, n_scan)
    samples: List[Tuple[float, Optional[float], bool]] = []
    flags: List[bool] = []
    for v in grid:
        ok, val = feasible(float(v))
        samples.append((float(v), val, ok))
        flags.append(ok)

    # 可行性区间
    intervals: List[Tuple[float, float]] = []
    start = None
    for i, ok in enumerate(flags):
        if ok and start is None:
            start = float(grid[i])
        if not ok and start is not None:
            intervals.append((start, float(grid[i - 1])))
            start = None
    if start is not None:
        intervals.append((start, float(grid[-1])))

    # 边界精确化（可行性切换点）
    solutions: List[float] = []
    for i in range(len(flags) - 1):
        if flags[i] != flags[i + 1]:
            a, b = float(grid[i]), float(grid[i + 1])
            fa, _ = feasible(a)
            for _ in range(60):
                m = 0.5 * (a + b)
                fm, _ = feasible(m)
                if fm == fa:
                    a = m
                else:
                    b = m
                if abs(b - a) < 1e-6 * max(abs(a), 1.0):
                    break
            solutions.append(0.5 * (a + b))

    best = None
    if intervals:
        if sense == "max":
            best = max(v[1] for v in intervals)
        else:
            best = min(v[0] for v in intervals)

    return {
        "target": target, "constraint": constraint, "limit": limit,
        "sense": sense, "sense_cn": cn,
        "base_value": base_val,
        "search_range": (float(lo), float(hi)),
        "feasible_intervals": intervals,
        "solutions": solutions,
        "best": best,
        "samples": samples,
        "note": ("找到了 " + str(len(solutions)) + " 个可行性切换点"
                 if solutions else "搜索范围内可行性未发生切换，请扩大区间"),
    }


# =============================================================================
# 17. 预设算例
# =============================================================================

def preset_single_mild_downstream(h: float = 2.0) -> Params:
    """单坡缓坡，下游给定水深 h > h0 → M1 壅水。"""
    return Params(Q=10.0, case_name="单坡缓坡-下游控制",
                  reaches=[Reach(0, "缓坡", 0.0, 600.0, 0.001, 5.0, 0.0, 0.015)],
                  downstream=Boundary("given_h", h=h))


def preset_single_mild_outfall() -> Params:
    """单坡缓坡，渠末自由跌水 → 临界控制，向上游 M2 降水。"""
    return Params(Q=10.0, case_name="单坡缓坡-渠末跌水",
                  reaches=[Reach(0, "缓坡", 0.0, 600.0, 0.001, 5.0, 0.0, 0.015)],
                  downstream=Boundary("free_outfall"))


def preset_mild_low_stage(h: float = 0.9) -> Params:
    """单坡缓坡，下游水深介于 hc 与 h0 之间 → M2。"""
    return Params(Q=10.0, case_name="单坡缓坡-M2降水",
                  reaches=[Reach(0, "缓坡", 0.0, 800.0, 0.001, 5.0, 0.0, 0.015)],
                  downstream=Boundary("given_h", h=h))


def preset_single_steep_upstream(h: float = 0.30) -> Params:
    """单坡陡坡，上游给定水深 → 急流向下游推算。"""
    return Params(Q=10.0, case_name="单坡陡坡-上游控制",
                  reaches=[Reach(0, "陡坡", 0.0, 400.0, 0.02, 5.0, 0.0, 0.015)],
                  upstream=Boundary("given_h", h=h))


def preset_mild_to_steep() -> Params:
    """缓坡接陡坡：坡折处临界，M2 | 临界 | S2。"""
    return Params(Q=10.0, case_name="缓坡转陡坡",
                  reaches=[Reach(0, "缓坡段", 0.0, 300.0, 0.001, 5.0, 0.0, 0.015),
                           Reach(1, "陡坡段", 300.0, 600.0, 0.02, 5.0, 0.0, 0.015)],
                  downstream=Boundary("free_outfall"))


def preset_steep_to_mild_jump() -> Params:
    """陡坡接缓坡：急流进入缓坡，按共轭水深求交定位水跃。"""
    return Params(Q=10.0, case_name="陡坡转缓坡-水跃",
                  reaches=[Reach(0, "陡坡段", 0.0, 100.0, 0.05, 5.0, 0.0, 0.015),
                           Reach(1, "缓坡段", 100.0, 600.0, 0.001, 5.0, 0.0, 0.015)],
                  upstream=Boundary("given_h", h=0.30),
                  downstream=Boundary("given_h", h=1.00))


def preset_trapezoid_mild() -> Params:
    """梯形断面缓坡，M1 壅水。"""
    return Params(Q=12.0, case_name="梯形缓坡",
                  reaches=[Reach(0, "梯形缓坡", 0.0, 800.0, 0.0008, 6.0, 1.5, 0.022)],
                  downstream=Boundary("given_h", h=2.2))


def preset_engineering_alpha() -> Params:
    """工程模式：α=β=1.05。"""
    p = preset_single_mild_downstream(2.0)
    p.mode, p.alpha, p.beta = "engineering", 1.05, 1.05
    p.case_name = "工程模式 α=1.05"
    return p


PRESETS = {
    "M1": preset_single_mild_downstream,
    "M2": preset_mild_low_stage,
    "outfall": preset_single_mild_outfall,
    "steep": preset_single_steep_upstream,
    "mild_to_steep": preset_mild_to_steep,
    "jump": preset_steep_to_mild_jump,
    "trapezoid": preset_trapezoid_mild,
}


# =============================================================================
# 18. 自检
# =============================================================================

def _selftest(verbose: bool = True) -> bool:
    ok = True

    def check(name: str, cond: bool, detail: str = ""):
        nonlocal ok
        ok = ok and bool(cond)
        if verbose:
            print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))

    b, Q, g = 5.0, 10.0, 9.81
    sec = Section("rectangular", b, 0.0)

    # 1 矩形解析式
    q = Q / b
    hc_exact = (q * q / g) ** (1.0 / 3.0)
    hc_num = critical_depth(sec, Q, g, 1.0)
    check("临界水深解析式", abs(hc_num - hc_exact) / hc_exact < 1e-12,
          f"hc={hc_num:.6f}")

    # 2 正常水深回代
    n, i = 0.015, 0.001
    h0 = normal_depth(sec, n, i, Q)
    q_calc = discharge_capacity(sec, n, i, h0)
    check("正常水深回代", abs(q_calc - Q) / Q < 1e-9, f"h0={h0:.6f}, Q={q_calc:.6f}")

    # 3 临界底坡回代：h0(ic) == hc
    ic = critical_slope(sec, n, Q, 1.0, g)
    h0_at_ic = normal_depth(sec, n, ic, Q)
    check("临界底坡回代", abs(h0_at_ic - hc_num) / hc_num < 1e-6,
          f"ic={ic:.8f}, h0(ic)={h0_at_ic:.6f}, hc={hc_num:.6f}")

    # 4 水跃函数极小点 = 临界水深
    hmin = theta_min_depth(sec, Q, 1.0, g, 1.0)
    check("θ 极小点等于 hc", abs(hmin - hc_num) / hc_num < 1e-6, f"{hmin:.6f}")

    # 5 矩形共轭水深：数值解 vs 显式式
    h1 = 0.25
    h2_num = conjugate_depth(sec, Q, 1.0, g, h1, 1.0)
    h2_exp = conjugate_rect_explicit(b, Q, 1.0, g, h1)
    check("共轭水深数值=解析", abs(h2_num - h2_exp) / h2_exp < 1e-8,
          f"{h2_num:.6f} vs {h2_exp:.6f}")

    # 6 动量函数守恒
    th1 = theta(sec, Q, 1.0, g, h1)
    th2 = theta(sec, Q, 1.0, g, h2_num)
    check("动量函数守恒", abs(th1 - th2) / th1 < 1e-8, f"{th1:.6f} vs {th2:.6f}")

    # 7 梯形 m→0 退化到矩形
    # m=1e-9 时梯形面积与 b·h 相差约 2e-9，属浮点量级，按相对误差判定。
    sec_tz = Section("trapezoidal", b, 1e-9)
    A_tz = props(sec_tz, 1.5).A
    check("梯形退化矩形", abs(A_tz - b * 1.5) < 1e-6 * b * 1.5, f"A={A_tz:.12f}")

    # 8 十二型判别覆盖
    # 陡坡的正常水深必小于临界水深，须用陡坡自身 h0，不能沿用缓坡 h0。
    h0_steep = normal_depth(sec, n, 0.02, Q)
    for sc, hs, h0_case, want in [
        ("mild", 2.0, h0, "M1"), ("mild", 0.9, h0, "M2"), ("mild", 0.5, h0, "M3"),
        ("steep", 1.0, h0_steep, "S1"), ("steep", 0.5, h0_steep, "S2"),
        ("steep", 0.2, h0_steep, "S3"),
        ("critical", 1.0, h0, "C1"), ("critical", 0.2, h0, "C3"),
        ("horizontal", 1.0, h0, "H2"), ("horizontal", 0.2, h0, "H3"),
        ("adverse", 1.0, h0, "A2"), ("adverse", 0.2, h0, "A3"),
    ]:
        _, t, _ = zone_of(hs, h0_case, hc_num, sc)
        check(f"判别 {sc} h={hs} → {want}", t == want, f"得到 {t}")

    # 9 各预设算例可解
    for name, fn in PRESETS.items():
        pr = fn()
        pr.section_at_x = [0.5 * (pr.reaches[0].x_start + pr.reaches[-1].x_end)]
        res = solve(pr)
        check(f"预设 {name} 有曲线", len(res.segments) > 0,
              f"{len(res.segments)} 段, {len(res.transitions)} 事件, status={res.status}")
        gaps = _coverage_gaps(res.segments, pr.reaches[0].x_start, pr.reaches[-1].x_end)
        check(f"预设 {name} 无覆盖空洞", not gaps, f"{gaps}")
        errs = [w for w in res.warnings if w.level == "error"]
        check(f"预设 {name} 物理自检通过", not errs,
              "; ".join(w.code for w in errs))

    # 10 变坡型序列
    res = solve(preset_mild_to_steep())
    types = [s.curve_type for s in res.segments]
    check("缓→陡 出现临界控制", any(t.kind == "critical" for t in res.transitions),
          f"型序列 {types}")
    check("缓→陡 同时含 M2 与 S2", ("M2" in types and "S2" in types), f"{types}")

    # 11 水跃
    res = solve(preset_steep_to_mild_jump())
    jumps = [t for t in res.transitions if t.kind == "jump"]
    if jumps:
        d = jumps[0].data
        th_a = theta(Section("rectangular", 5.0, 0.0), 10.0, 1.0, 9.81, d["h1"])
        th_b = theta(Section("rectangular", 5.0, 0.0), 10.0, 1.0, 9.81, d["h2"])
        check("水跃动量守恒", abs(th_a - th_b) / th_a < 1e-6,
              f"h1={d['h1']:.4f}, h2={d['h2']:.4f}, Fr1={d['Fr1']:.3f}, x={jumps[0].x:.1f}")
        check("水跃 Fr1>1", d["Fr1"] > 1.0, f"Fr1={d['Fr1']:.3f}")
    else:
        check("陡→缓 找到水跃", False,
              f"事件 {[t.kind for t in res.transitions]}")

    # 12 工程模式 α 影响 hc
    pr_t = preset_single_mild_downstream(2.0)
    pr_e = preset_engineering_alpha()
    hc_t = solve(pr_t).reaches[0].hc
    hc_e = solve(pr_e).reaches[0].hc
    ratio = hc_e / hc_t
    check("α=1.05 使 hc 增大", 1.01 < ratio < 1.03, f"比值 {ratio:.5f}（理论 1.0164）")

    # 13 断面查询
    pr = preset_mild_low_stage()
    pr.section_at_x = [200.0]
    res = solve(pr)
    sa = res.section_at[0]
    check("断面查询返回要素", sa.get("found") and sa["A"] > 0,
          f"x=200, h={sa.get('h'):.4f}, Fr={sa.get('Fr'):.4f}")

    # 14 互校
    dev = res.diagnostics["cross_check_dev"]
    check("两法互校 <0.5%", dev is not None and dev < 5e-3,
          f"偏差 {dev if dev is None else round(dev, 6)}")

    # 15 反问题
    inv = solve_inverse(preset_single_mild_downstream(2.0), "Q", "h_max", 2.0,
                        lo=1.0, hi=60.0, n_scan=40)
    check("反问题给出解或区间",
          bool(inv["solutions"]) or bool(inv["feasible_intervals"]),
          f"解 {[round(v, 3) for v in inv['solutions']]}, 区间 {inv['feasible_intervals']}")

    inv2 = solve_inverse(preset_single_mild_downstream(2.0), "b", "h_max", 2.5,
                         lo=1.0, hi=20.0, n_scan=30)
    check("反问题（底宽）", inv2["best"] is not None or bool(inv2["solutions"]),
          f"best={inv2['best']}")

    # 16 结果可序列化
    try:
        txt = solve(preset_trapezoid_mild()).to_json(with_arrays=False)
        json.loads(txt)
        check("Result 可 JSON 序列化", True, f"{len(txt)} 字符")
    except Exception as e:  # noqa: BLE001
        check("Result 可 JSON 序列化", False, str(e))

    if verbose:
        print("\n" + ("全部通过" if ok else "存在失败项"))
    return ok


if __name__ == "__main__":
    _selftest()
