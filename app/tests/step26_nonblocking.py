# -*- coding: utf-8 -*-
"""恒定流页「计算不锁死页面」验收。

1. 页面有进度胶囊（不遮挡、pointer-events:none）
2. 计算流程先让浏览器绘制再重绘（nextPaint）
3. 模型回答进行中，普通计算仍能快速返回（锁已移出模型调用）
4. 页面没有全屏遮罩拦截点击
"""
import json
import os
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "web", "index.html")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step26-nonblocking.txt")
BASE = "http://127.0.0.1:8000"

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
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8")
    return time.time() - t0, body


def main():
    html = open(INDEX, encoding="utf-8").read()
    check("有进度胶囊", 'id="busyPill"' in html and "busyPillText" in html)
    check("胶囊不拦截鼠标", ".busyPill{" in html and "pointer-events:none" in html)
    check("先绘制再重绘", "function nextPaint" in html and "await nextPaint()" in html)
    # 全屏遮罩：不允许出现 inset:0 的 fixed 元素（错误横幅除外）
    check("没有全屏遮罩", "position:fixed;inset:0" not in html)

    # 模型回答进行中，计算是否仍快
    result = {}

    def chat():
        try:
            result["chat"] = post("/api/turn",
                                  {"text": "请用不少于300字谈谈明渠非恒定流的工程意义"})
        except Exception as e:
            result["chat"] = (0, str(e))

    th = threading.Thread(target=chat)
    th.start()
    time.sleep(0.4)
    dt, _ = post("/api/solve", {"overrides": {
        "Q": 10,
        "reaches": [{"L": 300, "i": 0.001, "b": 5, "m": 0, "n": 0.015},
                    {"L": 300, "i": 0.02, "b": 5, "m": 0, "n": 0.015}],
        "downstream": "free_outfall", "upstream": "none"}})
    th.join(180)
    ct = result.get("chat", (0, ""))[0]
    check("模型回答进行中，计算 < 2 s", dt < 2.0, "%.2f s" % dt)
    check("模型回答确实更慢（说明是并发场景）", ct > 1.0, "%.2f s" % ct)

    lines.append("")
    lines.append("  模型回答 %.2f s 期间，计算 %.2f s 返回" % (ct, dt))
    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
