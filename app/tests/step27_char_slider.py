# -*- coding: utf-8 -*-
"""B2-① 特征线演示 + B2-② 时间轴滑块 验收。"""
import json
import os
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "unsteady.html")
BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step27-char-slider.txt")

lines = []
ok = total = 0


def check(label, cond, detail=""):
    global ok, total
    total += 1
    if cond:
        ok += 1
    lines.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def post(path, payload, timeout=180):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    # ---------- 页面 ----------
    html = open(HTML, encoding="utf-8").read()
    lines.append("=== 页面元素 ===")
    check("有时间轴滑块", 'id="tSlider"' in html and 'id="tLabel"' in html)
    check("有特征线画布", 'id="cChar"' in html)
    check("有特征线交互", "runChar(" in html and "drawCharChart(" in html)
    check("滑块驱动重绘", "initTimeSlider" in html and "drawFamily(R, k)" in html)
    check("请求密集快照", "dense: true" in html)

    # ---------- 密集快照 ----------
    lines.append("")
    lines.append("=== 密集快照（滑块数据源）===")
    run = post("/api/unsteady/run", {"model": "diffusion", "L": 3000, "b": 5, "m": 0,
                                     "n": 0.015, "i0": 0.001, "Q0": 10, "Qpeak": 30,
                                     "t_peak": 3600, "t_base": 7200, "t_end": 6 * 3600,
                                     "Nx": 61, "dense": True, "frames": 41})
    check("返回 ok", run.get("ok"), run.get("error"))
    if run.get("ok"):
        hours = run["result"]["t_snap_hours"]
        snaps = run["result"]["h_snap"]
        check("帧数 41", len(hours) == 41, len(hours))
        check("首帧 t=0", abs(hours[0]) < 1e-9, hours[0])
        check("末帧 t=6 h", abs(hours[-1] - 6.0) < 1e-6, hours[-1])
        check("每帧长度与 x 一致",
              all(len(h) == len(run["result"]["x"]) for h in snaps), len(snaps))

    # ---------- 特征线 ----------
    lines.append("")
    lines.append("=== 特征线：缓流 ===")
    sub = post("/api/unsteady/characteristic", {})
    check("缓流识别正确", sub.get("regime") == "subcritical", sub.get("regime"))
    check("Fr < 1", 0 < sub.get("Fr", 9) < 1, sub.get("Fr"))
    check("c−v > 0", sub.get("c_minus_v", -1) > 0, sub.get("c_minus_v"))
    check("检测到上游响应", sub.get("verdict") == "rises", sub.get("verdict"))
    if sub.get("measured_up"):
        check("实测与理论偏差 < 15%", abs(sub["err"]) < 0.15,
              "%.1f%%" % (sub["err"] * 100))
        lines.append("  理论 %.0f s，实测 %.0f s，偏差 %.1f%%"
                     % (sub["theory_up"], sub["measured_up"], sub["err"] * 100))

    lines.append("")
    lines.append("=== 特征线：急流 ===")
    sup = post("/api/unsteady/characteristic",
               {"L": 1000, "b": 4, "m": 0, "n": 0.015, "i0": 0.02, "Q0": 10, "amp": 0.2})
    check("急流识别正确", sup.get("regime") == "supercritical", sup.get("regime"))
    check("Fr > 1", sup.get("Fr", 0) > 1, sup.get("Fr"))
    check("v−c < 0（扰动不能上溯）", sup.get("c_minus_v", 1) < 0, sup.get("c_minus_v"))
    check("判定为 blocked", sup.get("verdict") == "blocked", sup.get("verdict"))

    lines.append("")
    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
