# -*- coding: utf-8 -*-
"""随机生成合规算例：先随机取参数，再用内核验证结果里确实出现目标现象。

模型不参与数值生成——它只负责听懂"我要一个水跃样例"，参数由这里搜索并验证。
"""
import json
import os
import random

from . import adapter
import gvf_core

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_PATH = os.path.join(ROOT, "presets_catalog.json")

_cache = {"items": None, "mtime": None}


# ---------------------------------------------------------------- 预设库
def catalog():
    try:
        mtime = os.path.getmtime(CATALOG_PATH)
    except OSError:
        return []
    if _cache["items"] is not None and _cache["mtime"] == mtime:
        return _cache["items"]
    try:
        with open(CATALOG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items", [])
    except Exception:
        items = []
    _cache["items"] = items
    _cache["mtime"] = mtime
    return items


def grouped():
    """按分组返回，供前端做分组按钮。"""
    groups = []
    seen = {}
    for it in catalog():
        g = it.get("group", "其他")
        if g not in seen:
            seen[g] = {"name": g, "items": []}
            groups.append(seen[g])
        seen[g]["items"].append({"key": it["key"], "label": it["label"],
                                 "desc": it.get("desc", ""),
                                 "expect_types": it.get("expect_types"),
                                 "section": "trapezoidal" if it["key"].endswith("_T")
                                            else "rectangular"})
    return groups


def get(key):
    for it in catalog():
        if it["key"] == key:
            return dict(it["params"])
    return None


# ---------------------------------------------------------------- 求解工具
def _solve(params):
    a = adapter.build({"slots": dict(params), "warnings": [], "normalized": ""})
    if a["params"] is None:
        return None
    try:
        return gvf_core.solve(a["params"])
    except Exception:
        return None


def _two(i1, i2, L1, L2, b, m1, m2, n, q, up_h=None, down_h=None, down="free_outfall"):
    return {"Q": q, "mode": "teaching", "jump_len_formula": "wu",
            "reaches": [{"L": L1, "i": i1, "b": b, "m": m1, "n": n},
                        {"L": L2, "i": i2, "b": b, "m": m2, "n": n}],
            "downstream": down,
            "h0": down_h if down == "given_h" else None,
            "upstream": "given_h" if up_h is not None else "none",
            "up_h": up_h}


def _one(i, L, b, m, n, q, down="free_outfall", h0=None, up_h=None):
    return {"Q": q, "mode": "teaching", "jump_len_formula": "wu",
            "reaches": [{"L": L, "i": i, "b": b, "m": m, "n": n}],
            "downstream": down, "h0": h0,
            "upstream": "given_h" if up_h is not None else "none",
            "up_h": up_h}


def _summarize(res, params, key, label, note=""):
    seq = [s.curve_type for s in res.segments]
    jump = next((t for t in res.transitions if t.kind == "jump"), None)
    return {"key": key, "label": label, "note": note,
            "params": params, "types": seq,
            "jump_x": round(jump.x, 1) if jump else None,
            "transitions": [t.kind for t in res.transitions],
            "reaches": [{"L": round(r.L, 1), "i": r.i, "b": r.b, "m": r.m,
                         "n": r.n, "slope": r.slope_class,
                         "h0": None if r.h0 is None else round(r.h0, 4),
                         "hc": round(r.hc, 4)} for r in res.reaches]}


# ---------------------------------------------------------------- 随机生成
def random_jump(tries=400, rng=None):
    """随机找一组"陡转缓、渠内出现水跃"的参数。"""
    rng = rng or random.Random()
    for _ in range(tries):
        q = round(rng.uniform(8, 20), 1)
        b = round(rng.uniform(4, 8), 1)
        n = round(rng.uniform(0.012, 0.020), 3)
        i1 = round(rng.uniform(0.020, 0.060), 4)
        i2 = round(rng.uniform(0.0005, 0.0020), 5)
        L1 = round(rng.uniform(80, 250), -1)
        L2 = round(rng.uniform(300, 600), -1)
        up_h = round(rng.uniform(0.20, 0.50), 2)
        down_h = round(rng.uniform(0.9, 1.8), 2)
        p = _two(i1, i2, L1, L2, b, 0, 0, n, q, up_h=up_h, down_h=down_h,
                 down="given_h")
        res = _solve(p)
        if res is None or res.status != "ok":
            continue
        jump = next((t for t in res.transitions if t.kind == "jump"), None)
        if jump is None:
            continue
        if not (L1 * 0.3 <= jump.x <= L1 + L2 - 20):
            continue
        return _summarize(res, p, "rand_jump", "随机水跃样例",
                          "跃位 %.1f m" % jump.x)
    return None


def random_outfall(tries=200, rng=None):
    """随机找一组"缓坡 + 渠末自由跌水"，末端必然出现水跌。"""
    rng = rng or random.Random()
    for _ in range(tries):
        q = round(rng.uniform(6, 20), 1)
        b = round(rng.uniform(4, 8), 1)
        n = round(rng.uniform(0.012, 0.020), 3)
        i = round(rng.uniform(0.0005, 0.002), 5)
        L = round(rng.uniform(300, 800), -1)
        p = _one(i, L, b, 0, n, q, down="free_outfall")
        res = _solve(p)
        if res is None or res.status != "ok":
            continue
        kinds = [t.kind for t in res.transitions]
        if "outfall" not in kinds:
            continue
        return _summarize(res, p, "rand_outfall", "随机水跌样例",
                          "渠末自由跌水，%s" % " → ".join(s.curve_type for s in res.segments))
    return None


def random_type(target, tries=200, rng=None):
    """随机生成能复现指定型号的参数（在预设附近抖动，再用内核验证）。"""
    rng = rng or random.Random()
    base = None
    for it in catalog():
        if it.get("expect_types") and it["expect_types"][0] == target:
            base = it
            break
    if base is None:
        return None
    # 临界坡对 Q/b/n 极敏感（i 必须正好等于 i_c），抖动后基本都跨区，
    # 这类直接用固定算例，不做随机化。
    if target.startswith("C"):
        res = _solve(base["params"])
        if res is None:
            return None
        return _summarize(res, dict(base["params"]), "rand_" + target,
                          "临界坡 %s 算例" % target,
                          "临界坡对参数极敏感，使用固定算例")
    bp = base["params"]
    for _ in range(tries):
        p = json.loads(json.dumps(bp))
        p["Q"] = round(float(p.get("Q") or 10) * rng.uniform(0.85, 1.15), 2)
        for r in p.get("reaches", []):
            r["n"] = round(float(r["n"]) * rng.uniform(0.9, 1.1), 4)
            r["b"] = round(float(r["b"]) * rng.uniform(0.9, 1.1), 2)
        for k in ("h0", "up_h"):
            if p.get(k):
                p[k] = round(float(p[k]) * rng.uniform(0.95, 1.05), 3)
        res = _solve(p)
        if res is None or res.status != "ok":
            continue
        if target not in [s.curve_type for s in res.segments]:
            continue
        return _summarize(res, p, "rand_" + target,
                          "随机 %s 样例" % target,
                          "、".join(s.curve_type for s in res.segments))
    return None


def random_drop(tries=300, rng=None):
    """随机找一组"缓坡转陡坡、坡折处临界控制形成跌水"的参数。"""
    rng = rng or random.Random()
    for _ in range(tries):
        q = round(rng.uniform(8, 18), 1)
        b = round(rng.uniform(4, 8), 1)
        n = round(rng.uniform(0.012, 0.020), 3)
        i1 = round(rng.uniform(0.0005, 0.0020), 5)
        i2 = round(rng.uniform(0.010, 0.050), 4)
        L1 = round(rng.uniform(200, 500), -1)
        L2 = round(rng.uniform(200, 400), -1)
        p = _two(i1, i2, L1, L2, b, 0, 0, n, q)
        res = _solve(p)
        if res is None or res.status != "ok":
            continue
        crit = [t for t in res.transitions if t.kind == "critical"]
        if not crit:
            continue
        if abs(crit[0].x - L1) > 5:      # 必须发生在坡折处
            continue
        return _summarize(res, p, "rand_drop", "随机坡折水跌样例",
                          "缓坡转陡坡，坡折处临界控制、水面跌落（里程 %.0f m）"
                          % crit[0].x)
    return None


def random_combo(kind, rng=None):
    rng = rng or random.Random()
    if kind == "jump":
        return random_jump(rng=rng)
    if kind == "outfall":
        return random_outfall(rng=rng)
    if kind == "drop":
        return random_drop(rng=rng)
    if kind in ("M1", "M2", "M3", "S1", "S2", "S3", "C1", "C3",
                "H2", "H3", "A2", "A3"):
        return random_type(kind, rng=rng)
    return from_catalog(kind)


def from_catalog(key):
    """按 key 取预设（组合/现象类），并用内核重新验证一次。"""
    p = get(key)
    if p is None:
        return None
    res = _solve(p)
    if res is None:
        return None
    label = key
    for it in catalog():
        if it["key"] == key:
            label = it.get("label", key)
            break
    return _summarize(res, p, key, label, "预设算例")


def describe(item):
    """把生成结果转成一段人类可读的参数说明（不含模型参与）。"""
    if not item:
        return ""
    rs = item.get("reaches") or []
    parts = []
    for i, r in enumerate(rs):
        parts.append("第%d段：长度 %g m，底坡 i=%g，底宽 %g m，边坡 %g，糙率 %g（%s）"
                     % (i + 1, r["L"], r["i"], r["b"], r["m"], r["n"],
                        {"mild": "缓坡", "steep": "陡坡", "critical": "临界坡",
                         "horizontal": "平坡", "adverse": "负坡"}.get(r["slope"], r["slope"])))
    p = item.get("params") or {}
    bc = []
    if p.get("downstream") == "given_h":
        bc.append("渠末给定水深 %g m" % (p.get("h0") or 0))
    elif p.get("downstream") == "given_zw":
        bc.append("渠末给定水位 %g m" % (p.get("zw") or 0))
    elif p.get("downstream") == "free_outfall":
        bc.append("渠末自由跌水")
    if p.get("upstream") == "given_h":
        bc.append("渠首给定水深 %g m" % (p.get("up_h") or 0))
    head = "流量 Q=%g m³/s；%s" % (p.get("Q") or 0, "；".join(bc))
    return ("生成参数：" + "；".join(parts) + "。" + head + "。\n"
            + "内核计算结果：" + " → ".join(item.get("types") or [])
            + ("，水跃位于 %.1f m" % item["jump_x"] if item.get("jump_x") else "")
            + "。\n点下面的按钮可以把它填进表单并计算。")
