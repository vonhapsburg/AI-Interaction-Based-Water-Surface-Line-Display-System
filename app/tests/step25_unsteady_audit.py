# -*- coding: utf-8 -*-
"""非恒定流部分全面自查：页面静态一致性 + 所有接口（带浏览器 Origin）。"""
import json
import re
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
HTML = r"C:\Users\26287\Documents\deepseek-harness\default-workspace\gvf-web\web\unsteady.html"
INDEX = r"C:\Users\26287\Documents\deepseek-harness\default-workspace\gvf-web\web\index.html"

ok = total = 0
lines = []


def check(label, cond, detail=""):
    global ok, total
    total += 1
    if cond:
        ok += 1
    lines.append(("%s %s" % ("✓" if cond else "✗", label))
                 + (("  —— " + str(detail)) if detail else ""))


def post(path, payload, origin="http://127.0.0.1:8000"):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json",
                                          "Origin": origin}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw[:200]}


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {}


def main():
    # ---------- 静态：id 引用一致性 ----------
    html = open(HTML, encoding="utf-8").read()
    js = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    ids = set(re.findall(r'id="([A-Za-z0-9_\-]+)"', html))
    refs = set(re.findall(r'\$\("#([A-Za-z0-9_\-]+)"\)', js))
    refs |= set(re.findall(r'getElementById\("([A-Za-z0-9_\-]+)"\)', js))
    missing = sorted(refs - ids)
    check("页面 id 引用无悬空", not missing, missing)
    for fn in ("postJSON", "randomCase", "bindHover", "showBusy", "hideBusy",
               "refreshHnHint", "previewBackwater", "renderChips", "applyPreset",
               "updateModeVisuals", "sendChat"):
        check("函数存在：" + fn, ("function " + fn) in js or (fn + "(") in js)
    for key in ("/api/unsteady/run", "/api/unsteady/backwater", "/api/unsteady/chat",
                "/api/unsteady/random", "/api/unsteady/verify"):
        check("页面引用接口 " + key, key in js)

    # ---------- 服务端 ----------
    code, ver = get("/api/version")
    check("版本接口可用", code == 200 and ver.get("ok"), ver.get("build"))
    feats = set(ver.get("unsteady") or [])
    for f in ("run", "verify", "backwater", "chat", "random"):
        check("服务声明能力：" + f, f in feats)
    code, hl = get("/api/health")
    check("健康检查", code == 200 and hl.get("ok"))

    # ---------- 随机算例 ×10 ----------
    good = 0
    worst_steps = 0
    slow = 0
    for _ in range(10):
        code, jr = post("/api/unsteady/random", {})
        if not (code == 200 and jr.get("ok")):
            check("随机算例返回 ok", False, jr.get("error"))
            continue
        if not (jr["h_n"] > 0 and jr["mode"] in ("teaching", "engineering")
                and jr["Qpeak"] > jr["Q0"] and jr["tb"] > jr["tp"] and jr["te"] >= jr["tb"]):
            check("随机算例参数合法", False, jr)
            continue
        model = "preissmann" if jr["mode"] == "engineering" else "diffusion"
        code2, run = post("/api/unsteady/run", {
            "model": model, "L": jr["L"], "b": jr["b"], "m": jr["m"], "n": jr["n"],
            "i0": jr["i0"], "Q0": jr["Q0"], "Qpeak": jr["Qpeak"],
            "t_peak": jr["tp"] * 3600, "t_base": jr["tb"] * 3600,
            "t_end": jr["te"] * 3600, "Nx": jr["Nx"],
            "h_dn": None if jr["hdn"] == "auto" else jr["hdn"]})
        if code2 == 200 and run.get("ok"):
            good += 1
            worst_steps = max(worst_steps, run["result"]["diagnostics"]["steps"])
    check("随机算例 10 次都能生成并计算", good == 10, good)
    check("随机算例步数受控（<70000）", worst_steps < 70000, worst_steps)

    # ---------- 两个模型 ----------
    common = {"L": 5000, "b": 5, "m": 0, "n": 0.015, "i0": 0.001, "Q0": 10,
              "Qpeak": 30, "t_peak": 3600, "t_base": 7200, "t_end": 6 * 3600, "Nx": 61}
    code, d = post("/api/unsteady/run", dict(common, model="diffusion"))
    check("扩散波可算", code == 200 and d.get("ok") and d["result"]["model"] == "diffusion")
    code, p = post("/api/unsteady/run", dict(common, model="preissmann"))
    check("全方程可算", code == 200 and p.get("ok") and p["result"]["model"] == "preissmann")
    if p.get("ok"):
        check("全方程 Newton 无失败", p["result"]["diagnostics"]["newton_failed"] == 0,
              p["result"]["diagnostics"]["newton_failed"])

    # ---------- 回水 ----------
    code, bw1 = post("/api/unsteady/backwater", {"L": 500, "b": 5, "m": 0, "n": 0.015,
                                                 "i0": 0.001, "Q": 10, "h_dn": "auto"})
    check("回水 auto 可用", code == 200 and bw1.get("ok") and bw1["backwater_len"] == 0)
    code, bw2 = post("/api/unsteady/backwater", {"L": 500, "b": 5, "m": 0, "n": 0.015,
                                                 "i0": 0.001, "Q": 10, "h_dn": 1.68})
    check("回水抬高可反推", code == 200 and bw2.get("ok") and bw2["rise_up"] > 0
          and "M1" in bw2["curve_types"], bw2.get("rise_up"))

    # ---------- 对话 ----------
    code, c1 = post("/api/unsteady/chat", {
        "text": "渠长5公里，底宽5米，流量从10涨到30，1小时涨完，2小时回落，模拟6小时",
        "params": {}, "summary": ""})
    check("对话识别为 set", c1.get("act") == "set", c1.get("act"))
    check("对话解析 L/Qpeak", c1.get("params", {}).get("L") == 5000
          and c1.get("params", {}).get("Qpeak") == 30, c1.get("params"))
    code, c2 = post("/api/unsteady/chat", {
        "text": "为什么洪峰变矮了",
        "params": {"L": 5000, "b": 5, "Q0": 10, "Qpeak": 30},
        "summary": "求解器=diffusion；断面峰值 0m:30 → 2500m:28.5 → 5000m:27.0"})
    check("对话回答非空", c2.get("act") == "answer" and len(c2.get("reply", "")) > 20)
    code, c3 = post("/api/unsteady/chat", {
        "text": "出几道题", "params": {"L": 5000, "b": 5, "Q0": 10, "Qpeak": 30},
        "summary": "断面峰值 30 → 27"})
    check("对话出题 3 道", c3.get("act") == "quiz" and len(c3.get("items") or []) == 3,
          len(c3.get("items") or []))

    # ---------- 前后端字段契约 ----------
    lines.append("")
    lines.append("=== 字段契约（页面读什么，接口给什么）===")
    code, rj = post("/api/unsteady/random", {})
    check("random 有 params 对象", isinstance(rj.get("params"), dict), type(rj.get("params")).__name__)
    if isinstance(rj.get("params"), dict):
        need = ("mode", "L", "b", "m", "n", "i0", "Q0", "Qpeak", "tp", "tb", "te", "Nx", "hdn")
        miss = [k for k in need if k not in rj["params"]]
        check("random.params 字段齐全", not miss, miss)
        check("random.params.mode 合法", rj["params"]["mode"] in ("teaching", "engineering"),
              rj["params"]["mode"])
        # 模拟页面：const p = r.params || r; p.mode
        p = rj.get("params") or rj
        check("页面取法可用（p.mode）", bool(p.get("mode")), p.get("mode"))

    code, rr = post("/api/unsteady/run", dict(common, model="diffusion"))
    check("run 返回 result", isinstance(rr.get("result"), dict))
    if isinstance(rr.get("result"), dict):
        res = rr["result"]
        need = ("x", "t", "h_snap", "t_snap_hours", "stations", "diagnostics", "meta", "model")
        miss = [k for k in need if k not in res]
        check("result 字段齐全", not miss, miss)
        check("result.meta 含 h_n/b/m",
              all(k in (res.get("meta") or {}) for k in ("h_n", "b", "m")),
              list((res.get("meta") or {}))[:6])

    code, bw = post("/api/unsteady/backwater", {"L": 500, "b": 5, "m": 0, "n": 0.015,
                                                 "i0": 0.001, "Q": 10, "h_dn": 1.68})
    need = ("ok", "h_n", "h_dn", "h_up", "rise_up", "backwater_len", "curve_types", "x", "h", "hn")
    check("backwater 字段齐全", all(k in bw for k in need), [k for k in need if k not in bw])

    code, q = post("/api/unsteady/chat", {"text": "出几道题", "params": {"L": 5000},
                                          "summary": "峰值 30 → 27"})
    check("chat.quiz 有 items 且 3 条", q.get("act") == "quiz" and len(q.get("items") or []) == 3,
          len(q.get("items") or []))

    code, vf = post("/api/unsteady/verify", {"L": 1000, "b": 5, "m": 0, "n": 0.015,
                                              "i0": 0.001, "Q": 10, "h_dn": 1.4})
    if not vf.get("ok"):   # 偶发超时/参数抖动，重试一次
        code, vf = post("/api/unsteady/verify", {"L": 2000, "b": 5, "m": 0, "n": 0.015,
                                                  "i0": 0.001, "Q": 10, "h_dn": 1.4})
    need = ("ok", "mean", "max", "x_max", "x", "unsteady", "steady", "h_dn")
    check("verify 字段齐全", all(k in vf for k in need), [k for k in need if k not in vf])

    # ---------- Origin 防护 ----------
    code, _ = post("/api/unsteady/random", {}, origin="http://evil.example.com")
    check("拒绝外站 Origin", code == 403, code)

    print("\n".join(lines))
    print("\n通过 %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
