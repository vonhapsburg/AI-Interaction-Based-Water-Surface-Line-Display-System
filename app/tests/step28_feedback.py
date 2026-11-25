# -*- coding: utf-8 -*-
"""本轮 8 条反馈的验收：出题随机、关键标红、上下文猜你想问、大工况耗时。"""
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "web", "unsteady.html")
CHAT = os.path.join(ROOT, "unsteady", "chat.py")
BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step28-feedback.txt")

lines = []
ok = total = 0


def check(label, cond, detail=""):
    global ok, total
    total += 1
    if cond:
        ok += 1
    lines.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def post(path, payload, timeout=300):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode("utf-8"))
    return body, time.time() - t0


def main():
    html = open(HTML, encoding="utf-8").read()
    chat = open(CHAT, encoding="utf-8").read()

    lines.append("=== 提示词（问题 2/4）===")
    check("关键句用【】包裹的指令", "【】" in chat)
    check("禁止“实测”字样", "严禁使用“实测”" in chat or "严禁使用\"实测\"" in chat)
    check("不要求题面、可直接答", "不要说“缺少题面”" in chat)
    check("已注明当前模式", "当前模式" in chat)
    check("涌波/溃坝给理论量级", "Ritter" in chat and "激波" in chat)

    lines.append("")
    lines.append("=== 前端（问题 2/3）===")
    check("回复渲染函数", "function renderReply" in html)
    check("重点标红样式", 'class="hl"' in html and ".msg .hl{" in html)
    check("注意块样式", "class=\"warn\"" in html)
    check("上下文猜你想问", "refreshChips" in html and "/api/unsteady/chips" in html)
    check("① 图包络带", "包络带" in html)
    check("绳套标签有引线", "setLineDash([3,2])" in html)

    lines.append("")
    lines.append("=== 猜你想问按上一句延伸 ===")
    c1, _ = post("/api/unsteady/chips", {"text": "圣维南方程组是什么"})
    c2, _ = post("/api/unsteady/chips", {"text": "绳套为什么是逆时针"})
    c3, _ = post("/api/unsteady/chips", {"text": "涌波怎么算"})
    check("圣维南 → 特征线相关", any("特征线" in c for c in c1.get("chips", [])),
          c1.get("chips"))
    check("绳套 → 环向相关", any("环" in c for c in c2.get("chips", [])), c2.get("chips"))
    check("涌波 → Ritter 相关", any("Ritter" in c for c in c3.get("chips", [])), c3.get("chips"))
    check("两类问题的建议不同", c1.get("chips") != c2.get("chips"))

    lines.append("")
    lines.append("=== 出题：换一批不重复、类型多样 ===")
    P = {"L": 3000, "b": 5, "Q0": 10, "Qpeak": 30,
         "t_peak_h": 1, "t_base_h": 2, "t_end_h": 6, "mode": "engineering"}
    S = "求解器=preissmann；正常水深 1.124 m；断面峰值 30→27；c_k=3.25"
    post("/api/reset", {})   # 清掉上一轮测试留下的“已出过的题”
    q1, _ = post("/api/unsteady/chat", {"text": "出几道题", "params": P, "summary": S})
    q2, _ = post("/api/unsteady/chat", {"text": "换一批", "params": P, "summary": S})
    i1 = [it.get("q") for it in (q1.get("items") or [])]
    i2 = [it.get("q") for it in (q2.get("items") or [])]
    check("第一次 3 题", len(i1) == 3, len(i1))
    check("换一批仍是 3 题", len(i2) == 3, len(i2))
    check("两批题目不重复", set(i1).isdisjoint(set(i2)), (i1, i2))
    t1 = {it.get("type") for it in (q1.get("items") or [])}
    check("题型至少两类", len(t1) >= 2, t1)

    lines.append("")
    lines.append("=== 大工况耗时（问题 8）===")
    big = {"model": "preissmann", "L": 20000, "b": 10, "m": 0, "n": 0.02,
           "i0": 0.0003, "Q0": 30, "Qpeak": 80, "t_peak": 7200, "t_base": 14400,
           "t_end": 86400, "Nx": 201}
    r, sec = post("/api/unsteady/run", big)
    check("重工况 10 s 内完成", sec < 10, "%.1f s" % sec)
    check("返回 ok", r.get("ok"), r.get("error"))
    if r.get("ok"):
        d = r["result"]["diagnostics"]
        check("自动加粗网格", d.get("auto_coarsened") is True, d.get("nx"))
        check("质量守恒 <0.5%", d["mass_err"] < 0.005, "%.3f%%" % (d["mass_err"] * 100))
        lines.append("  用时 %.1f s，nx=%s，步数=%s" % (sec, d.get("nx"), d["steps"]))

    lines.append("")
    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
