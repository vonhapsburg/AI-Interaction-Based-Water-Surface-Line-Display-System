# -*- coding: utf-8 -*-
"""本地服务：会话槽位表 + 双通道解析 + 参数适配 + 合法性判定 + 内核计算 + 历史回放。

记忆在服务端：
  STATE["slots"]  当前累计参数表（Python 进程内，模型不承担记忆）
  STATE["texts"]  已输入句子的累计文本（给 adapter 提取水位/渠底高程用）
  runs.db         SQLite，每次成功计算落一条记录，支持点击回放
"""
import json
import os
import random
import re
import copy
import sqlite3
import sys
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
DB_PATH = os.path.join(ROOT, "runs.db")
sys.path.insert(0, ROOT)

from agent.merge import merge, turn, missing_of   # noqa: E402
from agent import llm, adapter, legality, chat as chatmod, generator   # noqa: E402
import gvf_core                                   # noqa: E402

STATE = {"slots": {}, "texts": [], "raws": [], "chat": []}
CACHE = {"params": None, "result": None}
LOCK = threading.Lock()
MAX_PTS = 1200

# 表单提交时由表单整体持有的键：提交 reaches 视为"完整参数集"，
# 先清掉这些键再覆盖，避免上一轮的渠首条件/模式残留。
FORM_KEYS = ("Q", "reaches", "downstream", "h0", "zw", "upstream", "up_h",
             "mode", "alpha", "beta", "jump_len_formula", "z_b0",
             "section", "b", "m", "n", "i", "L")

WARN_CODE_TO_H = {
    "W_DOWNSTREAM_SUPERCRITICAL": "H4",
    "W_OVER_CONSTRAINED": "H5",
    "W_OUTFALL_SUPERCRITICAL": "H6",
    "W_H0_ABSENT": "H3",
}


# ---------------------------------------------------------------- 工具
def _json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [_json_safe(v) for v in obj.tolist()]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def _idx(n, cap=MAX_PTS):
    if n <= cap:
        return list(range(n))
    return [int(i * (n - 1) / (cap - 1)) for i in range(cap)]


def pack(res):
    segs = []
    for s in res.segments:
        ix = _idx(len(s.x))
        segs.append({
            "seg_id": s.seg_id, "reach_index": s.reach_index,
            "curve_type": s.curve_type, "regime": s.regime, "direction": s.direction,
            "x": [float(s.x[k]) for k in ix],
            "h": [float(s.h[k]) for k in ix],
            "z_b": [float(s.z_b[k]) for k in ix],
            "z_w": [float(s.z_w[k]) for k in ix],
            "Fr": [float(s.Fr[k]) for k in ix],
            "v": [float(s.v[k]) for k in ix],
            "start_reason": s.start_reason, "end_reason": s.end_reason,
            "len_99": s.len_to_99pct_h0, "flags": _json_safe(s.flags),
        })
    reaches = [{
        "index": r.index, "name": r.name, "x_start": r.x_start, "x_end": r.x_end,
        "i": r.i, "b": r.b, "m": r.m, "n": r.n,
        "z_b_start": r.z_b_start, "z_b_end": r.z_b_end,
        "h0": r.h0, "hc": r.hc, "ic": r.ic,
        "slope_class": r.slope_class, "L": r.L,
    } for r in res.reaches]
    return _json_safe({
        "status": res.status, "reaches": reaches, "nodes": res.nodes,
        "controls": res.controls, "segments": segs,
        "transitions": [t.to_dict() for t in res.transitions],
        "facts": res.facts,
        "warnings": [w.to_dict() for w in res.warnings],
        "diagnostics": res.diagnostics,
    })


def _kernel_warn_codes(res):
    return sorted({WARN_CODE_TO_H[w.code] for w in res.warnings if w.code in WARN_CODE_TO_H})


# ---------------------------------------------------------------- 预设算例
PRESET_LABELS = {
    "M1": "M1 壅水 · 缓坡 + 高尾水",
    "M2": "M2 降水 · 缓坡 + 渠末跌水",
    "outfall": "渠末自由跌水 · 临界控制",
    "steep": "S2 降水 · 陡坡 + 渠首急流",
    "mild_to_steep": "缓坡转陡坡 · M2｜临界｜S2",
    "jump": "陡坡转缓坡 · 含水跃",
    "trapezoid": "梯形断面 · 壅水",
}


def _preset_list():
    return [{"key": k, "name": PRESET_LABELS.get(k, k)}
            for k in gvf_core.PRESETS]


_TYPE_CODES = ("M1", "M2", "M3", "S1", "S2", "S3", "C1", "C3",
               "H2", "H3", "A2", "A3")


def _point_note(x):
    """把"纵剖面上选中的位置"整理成一句话，交给对话模型当上下文。"""
    if x is None:
        return ""
    res, pr = CACHE["result"], CACHE["params"]
    if res is None or pr is None:
        return "用户在纵剖面上选中 x=%.1f m，但当前还没有计算结果。" % x
    try:
        sa = gvf_core.section_at_point(pr, res.reaches, res.segments,
                                       res.transitions, x)
    except Exception:
        return "用户在纵剖面上选中 x=%.1f m。" % x
    if not sa or not sa.get("found"):
        return "用户在纵剖面上选中 x=%.1f m。" % x
    regime = "急流" if sa.get("regime") == "supercritical" else "缓流"
    return ("用户在纵剖面上选中 x=%.1f m：第%d段，水深 h=%.3f m，渠底高程 %.3f m，"
            "水面高程 %.3f m，Fr=%.3f，曲线型号 %s（%s）。"
            % (x, int(sa.get("reach_index", 0)) + 1, sa.get("h", 0.0),
               sa.get("z_b", 0.0), sa.get("z_w", 0.0), sa.get("Fr", 0.0),
               sa.get("curve_type", ""), regime))


def _focus_from_reply(reply, res):
    """回答里提到水跃/水跌/临界时，让图跟着跳到那个位置（对话 → 图）。"""
    if res is None or not reply:
        return None
    r = reply
    if "跃" in r:
        for t in res.transitions:
            if t.kind in ("jump", "jump_submerged", "jump_washed"):
                return round(float(t.x), 1)
    if "水跌" in r or "跌水" in r:
        for t in res.transitions:
            if t.kind == "outfall":
                return round(float(t.x), 1)
    if "临界" in r:
        for t in res.transitions:
            if t.kind == "critical":
                return round(float(t.x), 1)
    return None


_BOUNDARY_WORDS = re.compile(
    r"渠末|末端|下游|出口|渠首|上游|进口|跌水|自由出流|正常水深控制|临界控制")


def _ambiguity(text, added):
    """找出这一句里"看着像、但有两种合理理解"的地方，交给用户一句话点选。

    典型：只说「水深2米」——可能是渠末控制，也可能是渠首控制。
    默认按渠末算，但必须当次就说明并给出切换，而不是等用户第二次纠正。
    """
    out = []
    t = text or ""
    if "h0" in (added or []) and not _BOUNDARY_WORDS.search(t):
        out.append({
            "field": "h0",
            "question": "这个水深是「渠末控制」还是「渠首控制」？当前先按渠末算。",
            "options": [
                {"label": "渠末控制（默认）",
                 "patch": {"downstream": "given_h", "upstream": "none"}},
                {"label": "渠首控制",
                 "patch": {"upstream": "given_h", "downstream": "free_outfall"}},
            ],
        })
    if "m" in (added or []) and not re.search(r"矩形|梯形|长方形|方形", t):
        out.append({
            "field": "section",
            "question": "给了边坡说明是梯形断面，确认一下断面型式？当前按梯形算。",
            "options": [
                {"label": "梯形（默认）", "patch": {"section": "trapezoidal"}},
                {"label": "矩形", "patch": {"section": "rectangular"}},
            ],
        })
    return out


_QUIZ_RE = re.compile(r"出题|题目|练习|考考|做题|测验|考试|习题|出.{0,4}题|来.{0,3}道?题|考我")


def _quiz_intent(text):
    return bool(_QUIZ_RE.search(text or ""))


def _f3(v, n=3):
    if v is None:
        return "—"
    try:
        return ("%%.%df" % n) % float(v)
    except (TypeError, ValueError):
        return str(v)


def _build_quiz():
    """按当前算例出题：题干与答案由内核数值直接生成，解析交给模型润色。"""
    res, pr = CACHE["result"], CACHE["params"]
    if res is None or pr is None:
        return None
    cn = {"mild": "缓坡", "steep": "陡坡", "critical": "临界坡",
          "horizontal": "平坡", "adverse": "负坡"}
    rs = res.reaches
    seq = " → ".join(s.curve_type for s in res.segments)
    items = []

    items.append({
        "q": "本渠各段的正常水深 h₀ 与临界水深 h_c 分别是多少？（并说明 h₀ 与 h_c 谁大谁小意味着什么）",
        "a": "；".join("第%d段 h₀=%s m、h_c=%s m" % (r.index + 1, _f3(r.h0), _f3(r.hc))
                      for r in rs),
    })
    items.append({
        "q": "各段的坡型如何判别？水面线型号序列是什么？",
        "a": "；".join("第%d段 i=%s、i_c=%s，属%s"
                      % (r.index + 1, _f3(r.i, 5), _f3(r.ic, 5),
                         cn.get(r.slope_class, r.slope_class)) for r in rs)
             + "；型序列：" + seq,
    })
    jump = next((t for t in res.transitions if t.kind == "jump"), None)
    if jump is not None:
        d = jump.data or {}
        items.append({
            "q": "水跃发生在哪个里程？跃前、跃后水深与跃长、消能系数各是多少？",
            "a": ("x=%s m；h₁=%s m，h₂=%s m，跃长 L_j=%s m，消能系数 K_j=%s"
                  % (_f3(jump.x, 1), _f3(d.get("h1")), _f3(d.get("h2")),
                     _f3(d.get("L_j")), _f3(d.get("Kj")))),
        })
    crit = next((t for t in res.transitions if t.kind == "critical"), None)
    if crit is not None:
        hc = (crit.data or {}).get("hc")
        items.append({
            "q": "坡折处（x=%s m）为什么会出现水面跌落？" % _f3(crit.x, 1),
            "a": ("该处由缓坡进入陡坡，缓坡不能维持缓流，水面在坡折处降到临界水深 "
                  "h_c=%s m 后进入急流，形成跌水" % _f3(hc)),
        })
    r0 = rs[0]
    items.append({
        "q": "临界底坡 i_c 的物理含义是什么？本渠第 1 段的 i 与 i_c 相比说明什么？",
        "a": ("i_c=%s；第 1 段 i=%s，%s"
              % (_f3(r0.ic, 5), _f3(r0.i, 5),
                 "i < i_c → 缓坡" if r0.i < r0.ic else
                 ("i > i_c → 陡坡" if r0.i > r0.ic else "i = i_c → 临界坡"))),
    })

    # 解析：交给模型写；失败就用内核给出的判别依据
    blob = "\n".join("%d. %s\n   标准答案：%s" % (i + 1, it["q"], it["a"])
                     for i, it in enumerate(items))
    why = []
    rep = llm.chat([
        {"role": "system", "content":
         "你是水力学老师。下面是一份依据某个水面线算例生成的题目与标准答案。"
         "请为每题写 2–3 句解析，说明依据的公式或判据（如曼宁公式、临界水深条件、"
         "Fr=1、水跃共轭水深等）。只能使用给定答案里出现的数字，不要新增数值。"
         '输出严格的 JSON：{"why": ["第1题解析", "第2题解析", ...]}'},
        {"role": "user", "content": blob},
    ], max_tokens=900, temperature=0.3)
    if "text" in rep:
        try:
            data = llm._extract_json(rep["text"])
            why = list(data.get("why") or [])
        except Exception:
            why = []
    if len(why) < len(items):
        fallback = (res.facts or {}).get("reasons") or []
        while len(why) < len(items):
            why.append(fallback[len(why) % len(fallback)] if fallback else "")
    for i, it in enumerate(items):
        it["why"] = why[i]
    return items


def _generate_intent(text):
    """识别「给我一个水跃样例 / 来个 M2 的例子」这类请求。"""
    t = text or ""
    if not re.search(r"样例|例子|示例|案例|生成|来一个|来一组|给我.*个|出一|演示|随机", t):
        return None
    m = re.search(r"(M1|M2|M3|S1|S2|S3|C1|C3|H2|H3|A2|A3)", t)
    if m:
        return m.group(1)
    if re.search(r"水跃", t):
        return "jump"
    if re.search(r"水跌|跌水", t):
        # 坡折处/缓转陡的跌水，与渠末自由跌水是两种不同成因
        if re.search(r"坡折|变坡|缓.*陡|转坡|坡脚|跌坎", t):
            return "drop"
        return "outfall"
    if re.search(r"缓坡.*陡坡|缓.*转.*陡", t):
        return "combo_mild_steep"
    if re.search(r"陡坡.*缓坡|陡.*转.*缓", t):
        return "combo_steep_mild"
    if re.search(r"缓坡.*缓坡", t):
        return "combo_mild_mild"
    if re.search(r"陡坡.*陡坡", t):
        return "combo_steep_steep"
    return None


def _params_to_form(pr):
    """把内核 Params 转成前端口径的表单参数。"""
    return {
        "Q": pr.Q,
        "mode": pr.mode,
        "alpha": pr.alpha,
        "beta": pr.beta,
        "jump_len_formula": pr.jump_len_formula,
        "reaches": [{"L": r.L, "i": r.i, "b": r.b, "m": r.m, "n": r.n}
                    for r in pr.reaches],
        "downstream": pr.downstream.type,
        "h0": pr.downstream.h,
        "zw": pr.downstream.zw,
        "upstream": pr.upstream.type,
        "up_h": pr.upstream.h,
        "z_b0": (pr.reaches[0].z_b_start if pr.reaches else 0.0) or 0.0,
    }


def _ctx():
    """归一化后的累计文本：给 adapter 提取水位/渠底高程用（它认数字）。"""
    return "\n".join(STATE["texts"])


def _raw():
    """用户原句累计文本：给合法性判定用（它要认中文数字/口语特征）。"""
    return "\n".join(STATE["raws"])


def _evaluate(ctx_text, slots, raw_text=None):
    """用给定槽位跑 adapter + legality，返回 (adapter_out, legal, rows)。

    adapter 吃归一化文本，legality 吃原句 —— 混用会导致 B 类被误判成 A 类。
    """
    merged = {"slots": dict(slots), "warnings": [], "normalized": ctx_text}
    a = adapter.build(merged)
    # 纯表单模式（没有任何原句）时给个占位，避免被 I 类"输入为空"误拦
    raw = raw_text if raw_text not in (None, "") else (ctx_text or "表单参数")
    legal = legality.classify(raw, merged, a)
    return a, legal, a["rows"]


# ---------------------------------------------------------------- 历史（SQLite）
HISTORY = {"ok": None}


def history_ok():
    """历史库是否可写（目录只读时返回 False，但计算照常）。"""
    if HISTORY["ok"] is None:
        try:
            conn = _db()
            conn.close()
            HISTORY["ok"] = True
        except Exception:
            HISTORY["ok"] = False
    return HISTORY["ok"]


def _db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("""CREATE TABLE IF NOT EXISTS runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        text TEXT,
        raw TEXT,
        slots TEXT,
        curves TEXT,
        status TEXT)""")
    # 兼容早先没有 raw 列的库
    cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)")]
    if "raw" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN raw TEXT DEFAULT ''")
        conn.commit()
    return conn


def save_run(text, raw, slots, curves, status):
    """写一条历史。写不进去（目录只读等）只影响历史，不影响计算。"""
    try:
        with LOCK:
            conn = _db()
            try:
                cur = conn.execute(
                    "INSERT INTO runs(ts,text,raw,slots,curves,status) VALUES(?,?,?,?,?,?)",
                    (time.strftime("%Y-%m-%d %H:%M:%S"), text, raw,
                     json.dumps(slots, ensure_ascii=False),
                     json.dumps(curves, ensure_ascii=False), status))
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()
    except Exception as e:
        print("[历史] 写入失败（不影响计算）：%s: %s" % (type(e).__name__, e))
        return None


def list_runs(limit=20):
    try:
        conn = _db()
        try:
            rows = conn.execute(
                "SELECT id,ts,text,raw,slots,curves,status FROM runs ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    out = []
    for rid, ts, text, raw, slots, curves, status in rows:
        try:
            sl = json.loads(slots or "{}")
        except Exception:
            sl = {}
        out.append({"id": rid, "ts": ts, "text": (text or "")[-60:],
                    "raw": (raw or "")[-60:], "slots": sl,
                    "curves": json.loads(curves or "[]"), "status": status})
    return out


def get_run(rid):
    try:
        conn = _db()
        try:
            row = conn.execute(
                "SELECT id,ts,text,raw,slots,curves,status FROM runs WHERE id=?", (rid,)).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not row:
        return None
    rid, ts, text, raw, slots, curves, status = row
    return {"id": rid, "ts": ts, "text": text, "raw": raw,
            "slots": json.loads(slots or "{}"),
            "curves": json.loads(curves or "[]"), "status": status}


# ---------------------------------------------------------------- HTTP
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def _json(self, obj, code=200):
        data = json.dumps(_json_safe(obj), ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        # 页面改版后浏览器仍可能用旧缓存，这里显式禁用 HTML 缓存
        try:
            if self.path.split("?")[0].endswith(("/", ".html")):
                self.send_header("Cache-Control", "no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
        except Exception:
            pass
        super().end_headers()

    # ---------------- GET ----------------
    def do_GET(self):
        u = urlparse(self.path)

        if u.path == "/api/health":
            return self._json({"ok": True, "llm": llm.health(),
                               "llm_info": llm.describe(),
                               "core": gvf_core.SCHEMA_VERSION,
                               "history": history_ok(),
                               "session": len(STATE["slots"])})

        if u.path == "/api/presets":
            groups = generator.grouped()
            legacy = _preset_list()
            return self._json({"presets": legacy, "groups": groups})

        if u.path == "/api/preset":
            qs = parse_qs(u.query)
            name = qs.get("key", qs.get("name", [""]))[0]
            # 先查预设库（12 型 / 组合 / 现象），再回落到内核 PRESETS
            p = generator.get(name)
            if p is not None:
                return self._json({"name": name, "params": p})
            if name not in gvf_core.PRESETS:
                return self._json({"error": "没有这个预设算例"}, 404)
            pr = gvf_core.PRESETS[name]()
            return self._json({"name": name, "params": _params_to_form(pr)})

        if u.path == "/api/generate":
            qs = parse_qs(u.query)
            kind = qs.get("kind", ["jump"])[0]
            section = qs.get("section", ["rectangular"])[0]
            item = generator.random_combo(kind, section=section)
            if item is None:
                return self._json({"error": "没能生成符合条件的算例，请换个说法"}, 404)
            return self._json({"ok": True, "item": item,
                               "reply": generator.describe(item)})

        if u.path == "/api/state":
            a, legal, rows = _evaluate(_ctx(), STATE["slots"], _raw())
            return self._json({"slots": STATE["slots"], "missing": a["missing"],
                               "rows": rows, "legal": legal,
                               "ready": a["params"] is not None
                                        and legal["verdict"] != "reject"})

        if u.path == "/api/history":
            qs = parse_qs(u.query)
            try:
                limit = int(qs.get("limit", ["20"])[0])
            except ValueError:
                limit = 20
            return self._json({"runs": list_runs(limit)})

        if u.path == "/api/section":
            qs = parse_qs(u.query)
            try:
                x = float(qs.get("x", ["0"])[0])
            except ValueError:
                return self._json({"found": False, "error": "x 不是数字"}, 400)
            if CACHE["result"] is None:
                return self._json({"found": False, "error": "尚未计算"}, 400)
            try:
                sa = gvf_core.section_at_point(
                    CACHE["params"], CACHE["result"].reaches,
                    CACHE["result"].segments, CACHE["result"].transitions, x)
                return self._json(sa)
            except Exception as e:
                return self._json({"found": False,
                                   "error": "%s: %s" % (type(e).__name__, e)}, 500)

        return super().do_GET()

    # ---------------- POST ----------------
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        # 只接受本机页面发来的请求，避免被其他站点/局域网页面驱动去消耗 key
        origin = self.headers.get("Origin") or ""
        if origin:
            host = origin.split("//")[-1].split("/")[0].split(":")[0]
            if host not in ("127.0.0.1", "localhost", "::1", ""):
                return self._json({"error": "拒绝来自其他站点的请求"}, 403)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json({"error": "bad json"}, 400)
        path = urlparse(self.path).path

        # ---- 非恒定流（扩散波）实验：独立模块，与恒定流完全隔离 ----
        if path == "/api/unsteady/run":
            try:
                from unsteady import solver as us

                def num(k, d):
                    try:
                        v = body.get(k, d)
                        return d if v in (None, "", "auto") else float(v)
                    except (TypeError, ValueError):
                        return d

                L = num("L", 10000.0); b = num("b", 5.0); m = num("m", 0.0)
                n = num("n", 0.015); i0 = num("i0", 0.001)
                Q0 = num("Q0", 10.0); Qpeak = num("Qpeak", 30.0)
                tp = num("t_peak", 3600.0); tb = num("t_base", 7200.0)
                te = num("t_end", 43200.0) or None
                Nx = int(num("Nx", 121))
                h_dn_body = body.get("h_dn")
                h_dn = None if h_dn_body in (None, "", "auto") else float(h_dn_body)
                model = (body.get("model") or "diffusion").lower()

                if model == "preissmann":
                    # 全方程：初场取恒定解，避免 t=0 的虚假波
                    from unsteady import preissmann as up
                    hn = us.normal_depth(b, m, n, i0, Q0)
                    hd = hn if h_dn is None else h_dn
                    h_init = None
                    try:
                        slots = {"Q": Q0, "mode": "teaching",
                                 "reaches": [{"L": L, "i": i0, "b": b, "m": m, "n": n}],
                                 "downstream": "given_h", "h0": hd, "upstream": "none"}
                        a0 = adapter.build({"slots": slots, "warnings": [], "normalized": ""})
                        st = gvf_core.solve(a0["params"])
                        sg = st.segments[0]
                        sx = np.asarray(sg.x, float); sh = np.asarray(sg.h, float)
                        order = np.argsort(sx)
                        x_uni = np.linspace(0.0, L, Nx)
                        h_init = np.interp(x_uni, sx[order], sh[order])
                    except Exception:
                        h_init = None
                    res = up.run(
                        L=L, b=b, m=m, n=n, i0=i0, Q0=Q0, Qpeak=Qpeak,
                        t_peak=tp, t_base=tb, t_end=te, h_dn=hd,
                        h_init=h_init, Nx=Nx)
                else:
                    res = us.run(
                        L=L, b=b, m=m, n=n, i0=i0, Q0=Q0, Qpeak=Qpeak,
                        t_peak=tp, t_base=tb, t_end=te,
                        h_dn=h_dn, Nx=Nx)
                return self._json({"ok": True, "result": res})
            except Exception as e:
                return self._json({"ok": False,
                                   "error": "%s: %s" % (type(e).__name__, e)}, 500)

        # ---- 回水预览：给定下游水位，立刻反推沿程水面线与上游抬升 ----
        if path == "/api/unsteady/backwater":
            try:
                from unsteady import solver as us
                import numpy as _np

                def nv(k, d):
                    try:
                        v = body.get(k, d)
                        return d if v in (None, "", "auto") else float(v)
                    except (TypeError, ValueError):
                        return d

                L = nv("L", 10000.0); b = nv("b", 5.0); m = nv("m", 0.0)
                n = nv("n", 0.015); i0 = nv("i0", 0.001); Q = nv("Q", 10.0)
                hn = us.normal_depth(b, m, n, i0, Q)
                hd_raw = body.get("h_dn")
                hd = hn if hd_raw in (None, "", "auto") else float(hd_raw)

                slots = {"Q": Q, "mode": "teaching",
                         "reaches": [{"L": L, "i": i0, "b": b, "m": m, "n": n}],
                         "downstream": "given_h", "h0": hd, "upstream": "none"}
                a = adapter.build({"slots": slots, "warnings": [], "normalized": ""})
                res = gvf_core.solve(a["params"])
                if not res.segments:
                    return self._json({"ok": False, "error": "无解：请检查下游水位是否合理"})

                xs = _np.concatenate([_np.asarray(s.x, float) for s in res.segments])
                hs = _np.concatenate([_np.asarray(s.h, float) for s in res.segments])
                o = _np.argsort(xs)
                xs, hs = xs[o], hs[o]
                xg = _np.linspace(0.0, L, 81)
                hg = _np.interp(xg, xs, hs)
                rise = hg - hn
                maxr = float(hd - hn)
                thr = 0.05 * abs(maxr)
                up_idx = 0
                if abs(maxr) > 1e-9:
                    idx = _np.where(_np.abs(rise) >= thr)[0]
                    up_idx = int(idx[0]) if len(idx) else len(xg) - 1
                bw_len = float(L - xg[up_idx]) if abs(maxr) > 1e-9 else 0.0
                types = [s.curve_type for s in res.segments]
                return self._json({
                    "ok": True,
                    "h_n": round(float(hn), 4),
                    "h_dn": round(float(hd), 4),
                    "h_up": round(float(hg[0]), 4),
                    "rise_up": round(float(hg[0] - hn), 4),
                    "max_rise": round(maxr, 4),
                    "backwater_len": round(bw_len, 1),
                    "curve_types": types,
                    "x": [round(float(v), 1) for v in xg[::2]],
                    "h": [round(float(v), 4) for v in hg[::2]],
                    "hn": round(float(hn), 4),
                    "warnings": [w.code for w in res.warnings],
                })
            except Exception as e:
                return self._json({"ok": False,
                                   "error": "%s: %s" % (type(e).__name__, e)}, 500)

        # ---- 随机算例：生成一个物理合理、可直接计算的非恒定工况 ----
        if path == "/api/unsteady/random":
            try:
                import random as _rd
                from unsteady import solver as us

                picked = None
                for _ in range(40):
                    L = float(_rd.choice([2000, 3000, 5000, 8000, 10000]))
                    b = round(_rd.uniform(3.0, 10.0), 1)
                    m = _rd.choice([0.0, 0.0, 0.5, 1.0, 1.5])
                    n = round(_rd.uniform(0.012, 0.028), 3)
                    i0 = round(_rd.uniform(0.0003, 0.0012), 4)
                    Q0 = round(_rd.uniform(5.0, 20.0), 1)
                    Qpeak = round(Q0 * _rd.uniform(1.6, 2.8), 1)
                    tp = float(_rd.choice([0.5, 1.0, 1.5, 2.0]))
                    tb = round(tp + _rd.choice([0.5, 1.0, 1.5, 2.0]), 1)
                    te = max(6.0, round(tb * 3.0, 1))
                    Nx = int(_rd.choice([81, 101, 121]))
                    try:
                        hn = us.normal_depth(b, m, n, i0, Q0)
                        ic = gvf_core.critical_slope(
                            gvf_core.Section("rectangular" if m < 1e-9 else "trapezoidal", b, m),
                            n, Q0, 1.0, 9.81)
                    except Exception:
                        continue
                    if hn is None or hn <= 0 or i0 >= ic:
                        continue
                    mode = _rd.choice(["teaching", "engineering"])
                    if mode == "engineering":
                        hd = round(hn * _rd.uniform(0.85, 1.25), 3)
                        hdn = hd
                    else:
                        hdn = "auto"
                    # 估算步数，避免抽到需要几十秒的算例
                    import math as _m
                    B = b + 2.0 * m * hn
                    D = Qpeak / (2.0 * max(B, 1e-6) * i0)
                    dx = L / (Nx - 1)
                    if mode == "teaching":
                        dt_est = 0.7 * dx * dx / (2.0 * D)
                        if dt_est <= 0 or te * 3600.0 / dt_est > 60000:
                            continue
                    else:
                        c_est = _m.sqrt(9.81 * hn)
                        dt_est = min(max(0.5 * dx / c_est, 5.0), 60.0)
                        if te * 3600.0 / dt_est > 6000:
                            continue
                    picked = {"mode": mode, "L": L, "b": b, "m": m, "n": n, "i0": i0,
                              "Q0": Q0, "Qpeak": Qpeak, "tp": tp, "tb": tb, "te": te,
                              "Nx": Nx, "hdn": hdn, "h_n": round(float(hn), 4)}
                    break
                if picked is None:
                    return self._json({"ok": False, "error": "随机生成失败，请重试"})
                picked["ok"] = True
                return self._json(picked)
            except Exception as e:
                return self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)

        # ---- 非恒定流对话：自然语言改工况 / 解释 / 出题 ----
        if path == "/api/unsteady/chat":
            text = (body.get("text") or "").strip()
            if not text:
                return self._json({"error": "text 为空"}, 400)
            try:
                from unsteady import chat as uchat
                params = body.get("params") or {}
                summary = body.get("summary") or ""
                ex = uchat.extract(text, params)
                if ex.get("act") == "set":
                    return self._json({"act": "set", "params": ex.get("params") or {},
                                       "reply": ex.get("reply") or "已更新工况"})
                if ex.get("act") == "quiz":
                    q = uchat.quiz(params, summary)
                    return self._json({"act": "quiz", "items": q.get("items") or [],
                                       "error": q.get("error")})
                if ex.get("act") == "error":
                    return self._json({"act": "answer", "reply": ex.get("reply")})
                ans = uchat.answer(text, params, summary)
                return self._json({"act": "answer", "reply": ans.get("reply", "")})
            except Exception as e:
                return self._json({"act": "answer",
                                   "reply": "对话通道出错：%s: %s" % (type(e).__name__, e)})

        # ---- 非恒定流稳态回归：与恒定流内核逐点对比（外部锚点） ----
        if path == "/api/unsteady/verify":
            try:
                from unsteady import solver as us
                from agent import adapter as _ad
                import numpy as _np

                def vnum(k, d):
                    try:
                        v = body.get(k, d)
                        return d if v in (None, "", "auto") else float(v)
                    except (TypeError, ValueError):
                        return d

                L = vnum("L", 10000.0)
                b = vnum("b", 5.0)
                m = vnum("m", 0.0)
                n = vnum("n", 0.015)
                i0 = vnum("i0", 0.001)
                Q = vnum("Q", vnum("Q0", 10.0))
                hn = us.normal_depth(b, m, n, i0, Q)
                hd = body.get("h_dn")
                h_dn = hn if hd in (None, "", "auto") else float(hd)

                x1, h1 = us.steady(b=b, m=m, n=n, i0=i0, Q=Q, h_dn=h_dn,
                                   L=L, Nx=61, hours=40.0)
                slots = {"Q": Q, "mode": "teaching",
                         "reaches": [{"L": L, "i": i0, "b": b, "m": m, "n": n}],
                         "downstream": "given_h", "h0": h_dn, "upstream": "none"}
                a = _ad.build({"slots": slots, "warnings": [], "normalized": ""})
                res = gvf_core.solve(a["params"])
                sg = res.segments[0]
                xs = _np.asarray(sg.x, float)
                hs = _np.asarray(sg.h, float)
                o = _np.argsort(xs)
                hg = _np.interp(x1, xs[o], hs[o])
                dev = _np.abs(h1 - hg) / _np.maximum(hg, 1e-9)
                idx = int(_np.argmax(dev))
                step = max(1, len(x1) // 80)
                return self._json({
                    "ok": True,
                    "h_dn": round(float(h_dn), 4),
                    "mean": round(float(dev.mean()), 6),
                    "max": round(float(dev.max()), 6),
                    "x_max": round(float(x1[idx]), 1),
                    "x": [round(float(v), 1) for v in x1[::step]],
                    "unsteady": [round(float(v), 4) for v in h1[::step]],
                    "steady": [round(float(v), 4) for v in hg[::step]],
                })
            except Exception as e:
                return self._json({"ok": False,
                                   "error": "%s: %s" % (type(e).__name__, e)}, 500)

        # ---- 清空会话 ----
        if path == "/api/reset":
            with LOCK:
                STATE["slots"] = {}
                STATE["texts"] = []
                STATE["raws"] = []
                STATE["chat"] = []
                CACHE["params"] = CACHE["result"] = None
            return self._json({"ok": True, "slots": {}, "missing": missing_of({})})

        # ---- 一句话 → 更新会话状态 / 或直接回答问题 ----
        if path == "/api/turn":
            text = (body.get("text") or "").strip()
            if not text:
                return self._json({"error": "text 为空"}, 400)
            try:
                is_q = chatmod.looks_like_question(text)
                hyp_result = None
                hyp_slots = None
                hyp_curves = None
                # 前端把"纵剖面上选中的位置"带上来，回答才能对上那一点
                ctx_in = body.get("context") or {}
                sel_x = ctx_in.get("x")
                try:
                    sel_x = float(sel_x) if sel_x is not None else None
                except (TypeError, ValueError):
                    sel_x = None

                # 「给我一个水跃/水跌/M1 的样例」优先于参数抽取：
                # 这类请求由生成器出参数并当场验证，不允许被当成"记录参数"。
                gen_kind = _generate_intent(text)
                gen_item = None
                if gen_kind == "outfall":
                    # 只说"水跌"时，两种成因都要有机会出现：
                    # 渠末自由跌水 / 缓转陡坡折跌水
                    if random.random() < 0.5:
                        gen_kind = "drop"
                if gen_kind:
                    try:
                        gen_item = generator.random_combo(gen_kind)
                    except Exception:
                        gen_item = None
                    if gen_item is None and gen_kind == "drop":
                        gen_item = generator.random_combo("outfall")
                if gen_item is not None:
                    reply = generator.describe(gen_item)
                    with LOCK:
                        STATE["chat"].append({"role": "user", "content": text})
                        STATE["chat"].append({"role": "assistant", "content": reply})
                        STATE["chat"] = STATE["chat"][-12:]
                        slots_now = dict(STATE["slots"])
                    a, legal, rows = _evaluate(_ctx() or text, slots_now, _raw() or text)
                    return self._json({
                        "act": "answer", "reply": reply,
                        "hypothesis": {"slots": gen_item["params"],
                                       "curves": list(gen_item.get("types") or [])},
                        "channels": ["generator"], "added": [], "delta": {},
                        "slots": slots_now, "normalized": text,
                        "missing": a["missing"], "rows": rows, "legal": legal,
                        "warnings": [], "ready": False,
                    })

                # 「请根据现有水面线出一些题目」→ 按当前结果生成题与参考答案
                if _quiz_intent(text):
                    items = _build_quiz()
                    if not items:
                        with LOCK:
                            slots_now = dict(STATE["slots"])
                        a, legal, rows = _evaluate(_ctx() or text, slots_now, _raw() or text)
                        return self._json({
                            "act": "answer",
                            "reply": "还没有计算结果，先算一次水面线，我再照着它出题。",
                            "hypothesis": None, "channels": ["quiz"], "added": [],
                            "delta": {}, "slots": slots_now, "normalized": text,
                            "missing": a["missing"], "rows": rows, "legal": legal,
                            "warnings": [], "ready": False,
                        })
                    reply = "根据当前算例出了 %d 道题（题干与答案来自内核数值）。" % len(items)
                    with LOCK:
                        STATE["chat"].append({"role": "user", "content": text})
                        STATE["chat"].append({"role": "assistant", "content": reply})
                        STATE["chat"] = STATE["chat"][-12:]
                        slots_now = dict(STATE["slots"])
                    a, legal, rows = _evaluate(_ctx() or text, slots_now, _raw() or text)
                    return self._json({
                        "act": "quiz", "reply": reply, "quiz": items,
                        "hypothesis": None, "channels": ["quiz"], "added": [],
                        "delta": {}, "slots": slots_now, "normalized": text,
                        "missing": a["missing"], "rows": rows, "legal": legal,
                        "warnings": [], "ready": False,
                    })

                with LOCK:
                    # 提问走"沙盒"：在会话槽位的副本上推演，绝不改动真实参数
                    work = copy.deepcopy(STATE["slots"]) if is_q else STATE["slots"]
                    r = turn(text, work, use_llm=not is_q, restructure=not is_q)
                    if is_q:
                        slots = dict(STATE["slots"])
                        if r["added"]:
                            hyp_slots = work
                    else:
                        if r["added"]:
                            STATE["texts"].append(r["normalized"])
                            STATE["raws"].append(text)
                        slots = dict(STATE["slots"])
                    ctx = _ctx() or r["normalized"]
                    rawctx = _raw() or text

                # 问句里带了参数：按这组参数试算一次，用于回答"会怎样"
                if is_q and r["added"]:
                    try:
                        a2 = adapter.build({"slots": hyp_slots, "warnings": [],
                                            "normalized": r["normalized"]})
                        if a2["params"] is not None:
                            hyp_result = gvf_core.solve(a2["params"])
                            hyp_curves = [s.curve_type for s in hyp_result.segments]
                    except Exception:
                        hyp_result, hyp_slots, hyp_curves = None, None, None

                a, legal, rows = _evaluate(ctx, slots, rawctx)
                ready = (a["params"] is not None) and legal["verdict"] != "reject"
                added = r["added"]
                reply = ""
                channels = list(r["channels"])

                if is_q or not added:
                    # 对话通道：能聊、能解释，不追问
                    rep = chatmod.answer(text, slots,
                                         hyp_result if hyp_result is not None else CACHE["result"],
                                         STATE["chat"],
                                         hypothetical=hyp_result is not None,
                                         point=_point_note(sel_x))
                    if "text" in rep and rep["text"]:
                        reply = rep["text"]
                        channels.append("chat")
                    else:
                        reply = "（回答失败：%s）" % rep.get("error", "未知原因")
                    with LOCK:
                        STATE["chat"].append({"role": "user", "content": text})
                        STATE["chat"].append({"role": "assistant", "content": reply})
                        STATE["chat"] = STATE["chat"][-12:]
                    act = "answer"
                elif ready:
                    act = "compute"
                else:
                    # 记下了参数但还不齐：不主动追问，只在用户明确要算时提示一次
                    act = "record_ask" if re.search(
                        r"帮我算|计算|算一下|算个|出图", text) else "record"

                return self._json({
                    "act": act, "reply": reply,
                    "focus_x": _focus_from_reply(reply, CACHE["result"]),
                    "ambiguities": _ambiguity(text, added),
                    "hypothesis": ({"slots": hyp_slots, "curves": hyp_curves}
                                   if hyp_slots else None),
                    "channels": channels, "added": added, "delta": r["delta"],
                    "slots": slots, "normalized": r["normalized"],
                    "missing": a["missing"], "rows": rows, "legal": legal,
                    "warnings": r["warnings"] + a["warnings"],
                    "ready": ready,
                })
            except Exception as e:
                return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)

        # ---- 解析（无状态，保留给验收脚本）----
        if path == "/api/parse":
            text = (body.get("text") or "").strip()
            if not text:
                return self._json({"error": "text 为空"}, 400)
            try:
                m = merge(text, use_llm=body.get("use_llm", True))
                a = adapter.build(m, body.get("overrides"))
                legal = legality.classify(text, m, a)
                a.pop("params", None)
                m["adapter"] = a
                m["legal"] = legal
                return self._json(m)
            except Exception as e:
                return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)

        # ---- 回放某条历史 ----
        if path.startswith("/api/history/") and path.endswith("/load"):
            try:
                rid = int(path.split("/")[3])
            except (IndexError, ValueError):
                return self._json({"error": "bad id"}, 400)
            run = get_run(rid)
            if not run:
                return self._json({"error": "没有这条记录"}, 404)
            with LOCK:
                STATE["slots"] = dict(run["slots"])
                STATE["texts"] = [run["text"] or ""]
                STATE["raws"] = [run.get("raw") or run["text"] or ""]
                ctx = _ctx()
                a, legal, rows = _evaluate(ctx, STATE["slots"], _raw())
                if a["params"] is None or legal["verdict"] == "reject":
                    return self._json({"ok": False, "legal": legal,
                                       "missing": a["missing"], "rows": rows,
                                       "slots": STATE["slots"]})
                try:
                    res = gvf_core.solve(a["params"])
                except ValueError as e:
                    return self._json({"ok": False,
                                       "legal": {"code": "G", "verdict": "reject",
                                                 "label": legality.CLASS_LABEL["G"],
                                                 "reasons": ["参数不合法：%s" % e],
                                                 "hints": []}}, 400)
                CACHE["params"], CACHE["result"] = a["params"], res
            return self._json({"ok": True, "replayed": rid, "slots": STATE["slots"],
                               "map": rows, "warnings": a["warnings"],
                               "legal": legal, "result": pack(res)})

        # ---- 计算 ----
        if path == "/api/solve":
            text = (body.get("text") or "").strip()
            overrides = body.get("overrides") or {}
            try:
                if text:
                    # 一次性模式（不碰会话）
                    m = merge(text, use_llm=body.get("use_llm", True))
                    slots = dict(m["slots"])
                    slots.update({k: v for k, v in overrides.items()
                                  if v is not None and v != ""})
                    ctx = m["normalized"]
                    rawctx = text
                else:
                    # 表单整体提交（带 reaches）→ 以表单为准，先清掉表单持有的键
                    if "reaches" in overrides:
                        slots = {k: v for k, v in STATE["slots"].items()
                                 if k not in FORM_KEYS}
                    else:
                        slots = dict(STATE["slots"])
                    slots.update({k: v for k, v in overrides.items()
                                  if v is not None and v != ""})
                    ctx = _ctx()
                    rawctx = _raw()

                a, legal, rows = _evaluate(ctx, slots, rawctx)

                if legal["verdict"] == "reject":
                    return self._json({"ok": False, "legal": legal,
                                       "missing": a["missing"], "warnings": a["warnings"],
                                       "rows": a["rows"]})
                if legal["verdict"] == "incomplete" or a["params"] is None:
                    return self._json({"ok": False, "legal": legal,
                                       "missing": a["missing"], "warnings": a["warnings"],
                                       "rows": a["rows"]})

                res = gvf_core.solve(a["params"])
            except ValueError as e:
                return self._json({"ok": False,
                                   "legal": {"code": "G", "verdict": "reject",
                                             "label": legality.CLASS_LABEL["G"],
                                             "reasons": ["参数不合法：%s" % e],
                                             "hints": ["请检查糙率、边坡、底宽、渠长是否在合理范围"]},
                                   "error": "参数不合法：%s" % e}, 400)
            except Exception as e:
                return self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                                   "legal": {"code": "I", "verdict": "reject",
                                             "label": "计算失败", "reasons": [str(e)],
                                             "hints": []}}, 500)

            h_codes = _kernel_warn_codes(res)
            if h_codes:
                legal = dict(legal)
                legal["kernel_codes"] = h_codes
                legal["reasons"] = list(legal.get("reasons") or []) + \
                    ["内核提示：%s" % "、".join(h_codes)]

            CACHE["params"], CACHE["result"] = a["params"], res
            # 表单驱动（没有新文本）时，把这次用的参数写回会话，
            # 这样"改表单 → 计算 → 再接着说话"不会丢参数。
            if not text:
                with LOCK:
                    STATE["slots"] = dict(slots)
            curves = [s.curve_type for s in res.segments]
            run_id = save_run(ctx[-120:], rawctx[-120:], slots, curves, res.status)
            return self._json({"ok": True, "run_id": run_id, "map": a["rows"],
                               "warnings": a["warnings"], "legal": legal,
                               "slots": slots, "result": pack(res)})

        return self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        sys.stdout.write("[web] %s\n" % (fmt % args))


if __name__ == "__main__":
    # 端口自适应：8000 被占（比如已经开着一个）就往后找，避免双击启动失败
    base = int(os.environ.get("GVF_PORT", "8000"))
    httpd = None
    port = base
    for cand in range(base, base + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", cand), Handler)
            port = cand
            break
        except OSError:
            continue
    if httpd is None:
        print("端口 %d~%d 都被占用，无法启动。" % (base, base + 19))
        sys.exit(1)

    # 写端口文件：启动脚本据此打开浏览器
    try:
        with open(os.path.join(ROOT, ".port"), "w", encoding="utf-8") as f:
            f.write(str(port))
    except OSError:
        pass

    print("=" * 56)
    print("服务地址：http://127.0.0.1:%d" % port)
    print("内核：gvf_core %s" % gvf_core.SCHEMA_VERSION)
    print("会话：服务端槽位表（参数记忆在进程内，可清空）")
    print("历史：%s" % (DB_PATH if history_ok() else "不可写（计算不受影响，历史功能关闭）"))
    info = llm.describe()
    if info["provider"] == "none":
        print("模型通道：已关闭（仅规则解析）")
    elif info["provider"] == "deepseek":
        print("模型通道：DeepSeek %s（%s）" % (info["model"],
              "已配置 Key" if info["ready"] else "未配置 Key，将只用规则解析"))
    else:
        print("模型通道：本地 Ollama %s（%s）" % (info["model"],
              "在线" if info["ready"] else "未连接，将只用规则解析"))
    print("=" * 56)
    print("正在预热模型（约 10 秒，未装模型时自动跳过）...")
    llm.preheat()
    print("就绪。关闭本窗口即停止服务。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
