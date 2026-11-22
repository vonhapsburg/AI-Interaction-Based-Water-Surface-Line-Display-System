# -*- coding: utf-8 -*-
"""第 7/8 步验收：走真实 HTTP 接口，模拟网页的调用路径。"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step8-http.txt")
lines = []


def get(path, timeout=10):
    t = time.time()
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace"), time.time() - t


def post(path, payload, timeout=180):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8")), time.time() - t
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8")), time.time() - t


def main():
    # 1. 页面
    code, html, dt = get("/")
    lines.append("=== GET / 页面 ===")
    lines.append("HTTP %s, %.1f KB, %.2fs" % (code, len(html) / 1024, dt))
    lines.append("含标题：%s" % ("基于AI交互的水面线展示系统" in html))
    lines.append("含 /api/parse 调用：%s" % ("/api/parse" in html))
    lines.append("")

    # 2. health
    code, body, dt = get("/api/health")
    lines.append("=== GET /api/health ===")
    lines.append("HTTP %s  %s  %.2fs" % (code, json.dumps(body, ensure_ascii=False), dt))
    lines.append("")

    # 3. 验收表 6 条
    cases = [
        ("#1 全参数（应只走规则，秒回）",
         "矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10个方，水深2米，算500米长，渠末自由跌水"),
        ("#2 信息不全（应叫模型并列出缺失）",
         "五米宽的梯形渠，边坡1比1.5，流量十个方"),
        ("#3 几乎没有信息",
         "帮我算个水面线"),
        ("#4 教学夸张糙率",
         "矩形渠，底宽5米，糙率0.3，底坡千分之一，流量10个方，水深2米，算500米"),
        ("#6 中文数字 + 三百米",
         "五米宽的梯形渠，边坡1比1.5，糙率十五，坡度千分之一，流量十个方，水深两米，算三百米"),
        ("#7 口语化变坡（应给变坡警告）",
         "梯形渠，底宽4米，边坡1比2，糙率0.02，底坡0.001，流量8个方，水深1.6米，算600米，中间变坡"),
    ]
    lines.append("=== POST /api/parse ===")
    for label, text in cases:
        code, body, dt = post("/api/parse", {"text": text})
        lines.append("-" * 66)
        lines.append("%s" % label)
        lines.append("输入：%s" % text)
        lines.append("HTTP %s  %.2fs  通道=%s  need_clarify=%s"
                     % (code, dt, body.get("channels"), body.get("need_clarify")))
        if "error" in body:
            lines.append("!! error: %s" % body["error"])
        else:
            lines.append("slots：%s" % json.dumps(
                {k: v for k, v in body["slots"].items() if v not in (None, "")},
                ensure_ascii=False))
            lines.append("missing：%s" % json.dumps(body["missing"], ensure_ascii=False))
            lines.append("warnings：%s" % json.dumps(body["warnings"], ensure_ascii=False))
    lines.append("")

    # 4. /api/solve 占位
    code, body, dt = post("/api/solve", {"text": "test"})
    lines.append("=== POST /api/solve（预留） ===")
    lines.append("HTTP %s  %s" % (code, json.dumps(body, ensure_ascii=False)))
    lines.append("")

    # 5. 异常路径
    lines.append("=== 异常路径 ===")
    code, body, dt = post("/api/parse", {"text": "   "})
    lines.append("空文本：HTTP %s  %s" % (code, json.dumps(body, ensure_ascii=False)))
    code, body, dt = post("/api/nope", {})
    lines.append("未知接口：HTTP %s  %s" % (code, json.dumps(body, ensure_ascii=False)))

    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)


if __name__ == "__main__":
    main()
