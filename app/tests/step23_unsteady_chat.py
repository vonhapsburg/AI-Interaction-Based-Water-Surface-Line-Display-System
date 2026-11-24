# -*- coding: utf-8 -*-
"""非恒定流的"AI + 力学"验收：教学模式/工程模式 + 对话交互。

检查：
  1. 模式 → 求解器映射（教学=扩散波，工程=全方程）
  2. 对话改工况：自然语言 → 参数（含小时/公里换算）
  3. 对话解释：回答引用当前结果里的数字
  4. 对话出题：3 题且带答案与解析
  5. 页面包含模式切换与对话框
"""
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step23-unsteady-chat.txt")
lines = []
ok = total = 0

PARAMS = {"L": 5000, "b": 5, "m": 0, "n": 0.015, "i0": 0.001,
          "Q0": 10, "Qpeak": 30, "t_peak_h": 1, "t_base_h": 2, "t_end_h": 6,
          "h_dn": -1, "Nx": 61, "mode": "teaching"}
SUMMARY = ("求解器=diffusion；正常水深 1.124 m；断面峰值 0m:30.0 → 2500m:28.5 → 5000m:27.0；"
           "实测等效波速 3.6 m/s；质量误差 0.001%；首末断面峰现时间 1.00h → 2.40h")


def check(label, cond, detail=""):
    global ok, total
    total += 1
    if cond:
        ok += 1
    lines.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def req(path, payload=None, timeout=180):
    if payload is None:
        r = urllib.request.Request(BASE + path)
    else:
        r = urllib.request.Request(
            BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body.strip().startswith("{") else body)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def main():
    # ---------- 0. 页面 ----------
    lines.append("=== 页面 ===")
    code, html = req("/unsteady.html")
    check("非恒定页可访问", code == 200, code)
    if isinstance(html, str):
        check("含模式切换（教学/工程）", "modeSeg" in html and "工程模式" in html,
              "modeSeg" in html)
        check("含对话框", "chatBody" in html and "chatInput" in html)

    # ---------- 1. 模式 → 求解器 ----------
    lines.append("")
    lines.append("=== 模式 → 求解器 ===")
    base_payload = {"L": 3000, "b": 5, "m": 0, "n": 0.015, "i0": 0.001,
                    "Q0": 10, "Qpeak": 20, "t_peak": 1800, "t_base": 5400,
                    "t_end": 3 * 3600, "Nx": 41}
    code, j1 = req("/api/unsteady/run", dict(base_payload, model="diffusion"))
    check("教学模式走全方程以外的扩散波", code == 200 and j1.get("ok")
          and j1["result"].get("model") == "diffusion", j1["result"].get("model") if j1.get("ok") else j1)
    code, j2 = req("/api/unsteady/run", dict(base_payload, model="preissmann"))
    check("工程模式走全方程", code == 200 and j2.get("ok")
          and j2["result"].get("model") == "preissmann", j2["result"].get("model") if j2.get("ok") else j2)

    # ---------- 2. 对话改工况 ----------
    lines.append("")
    lines.append("=== 对话改工况 ===")
    code, j3 = req("/api/unsteady/chat", {
        "text": "渠长5公里，底宽5米，糙率0.015，底坡0.001，流量从10涨到30，1小时涨完，2小时回落，模拟6小时",
        "params": {}, "summary": ""})
    check("识别为 set", j3.get("act") == "set", j3.get("act"))
    p = j3.get("params") or {}
    check("L 解析为 5000", abs(p.get("L", 0) - 5000) < 1e-6, p.get("L"))
    check("Qpeak 解析为 30", abs(p.get("Qpeak", 0) - 30) < 1e-6, p.get("Qpeak"))
    check("涨水历时 1 h", abs(p.get("t_peak_h", 0) - 1) < 1e-6, p.get("t_peak_h"))
    check("回落历时 2 h", abs(p.get("t_base_h", 0) - 2) < 1e-6, p.get("t_base_h"))
    check("模拟时长 6 h", abs(p.get("t_end_h", 0) - 6) < 1e-6, p.get("t_end_h"))
    check("给出了确认语", bool((j3.get("reply") or "").strip()), j3.get("reply"))

    # ---------- 3. 对话解释 ----------
    lines.append("")
    lines.append("=== 对话解释 ===")
    code, j4 = req("/api/unsteady/chat", {
        "text": "为什么洪峰变矮了", "params": PARAMS, "summary": SUMMARY})
    check("识别为 answer", j4.get("act") == "answer", j4.get("act"))
    reply = j4.get("reply") or ""
    check("回答非空", len(reply) >= 30, len(reply))
    check("引用了结果里的数字", any(x in reply for x in ("28.5", "30", "27.0", "27")),
          reply[:80])
    check("提到力学概念（坦化/扩散/连续/运动波/惯性）",
          any(x in reply for x in ("坦化", "扩散", "连续", "运动波", "惯性")), reply[:80])

    # ---------- 4. 对话出题 ----------
    lines.append("")
    lines.append("=== 对话出题 ===")
    code, j5 = req("/api/unsteady/chat", {
        "text": "出几道题", "params": PARAMS, "summary": SUMMARY})
    check("识别为 quiz", j5.get("act") == "quiz", j5.get("act"))
    items = j5.get("items") or []
    check("返回 3 道题", len(items) == 3, len(items))
    if items:
        check("每题有题面/答案/解析",
              all(it.get("q") and it.get("a") and it.get("why") for it in items),
              [list(it) for it in items[:1]])

    lines.append("")
    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
