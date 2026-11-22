# -*- coding: utf-8 -*-
"""对话通道验收：能聊、能解释、不追问（需先启动服务，且模型通道可用）。

对照用户反馈：问"你会什么"必须能答；闲聊/概念不允许变成"还缺 X 参数"。
"""
import json
import os
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step15-chat.txt")
lines = []
ok = total = 0


def req(path, payload=None, timeout=180):
    if payload is None:
        r = urllib.request.Request(BASE + path)
    else:
        r = urllib.request.Request(
            BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")


def check(label, cond, detail=""):
    global ok, total
    total += 1
    if cond:
        ok += 1
    lines.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def ask(text):
    code, j = req("/api/turn", {"text": text})
    return j


def main():
    code, h = req("/api/health")
    if not h.get("llm"):
        msg = "模型通道未就绪（%s），跳过对话验收。" % (h.get("llm_info", {}).get("note") or "未知")
        print(msg)
        open(OUT, "w", encoding="utf-8").write(msg + "\n")
        return

    req("/api/reset", {})

    # ---------- 1. 能力类问题 ----------
    lines.append("=== 能力与概念问题 ===")
    j = ask("你会什么？")
    check("「你会什么」被判为回答而不是抽参数", j.get("act") == "answer", j.get("act"))
    check("给出了实质回答（不是反问补参数）",
          len((j.get("reply") or "")) >= 40
          and ("还缺" not in (j.get("reply") or "")),
          (j.get("reply") or "")[:90])
    lines.append("  Q: 你会什么？")
    lines.append("  A: " + (j.get("reply") or "").replace("\n", " ")[:160])

    j = ask("什么是临界水深？")
    check("概念题可答", j.get("act") == "answer" and len(j.get("reply") or "") >= 20,
          (j.get("reply") or "")[:90])
    check("答案切题（提到临界/弗劳德/缓流等）",
          any(k in (j.get("reply") or "") for k in ("临界", "Fr", "缓流", "急流")),
          (j.get("reply") or "")[:60])

    j = ask("水跃是怎么判定的？")
    check("水跃问题可答", j.get("act") == "answer" and len(j.get("reply") or "") > 20,
          (j.get("reply") or "")[:80])
    lines.append("")

    # ---------- 2. 闲聊式的"帮我算"不应生硬拒答 ----------
    lines.append("=== 「帮我算个水面线」===")
    j = ask("帮我算个水面线")
    check("走对话通道", j.get("act") == "answer", j.get("act"))
    check("不是一句冷冰冰的「还缺…」",
          (j.get("reply") or "").strip() != "还缺：" ,
          (j.get("reply") or "")[:80])
    lines.append("  A: " + (j.get("reply") or "").replace("\n", " ")[:160])
    lines.append("")

    # ---------- 3. 只给一个参数：记录但不追问 ----------
    lines.append("=== 只给一个参数 ===")
    j = ask("底宽5米")
    check("记为 record（不是追问）", j.get("act") == "record", j.get("act"))
    check("确实记下了 b=5", "b" in (j.get("added") or []), j.get("added"))
    check("没有生成追问话术", not (j.get("reply") or ""), repr(j.get("reply")))
    lines.append("")

    # ---------- 4. 完整一句 → 直接计算 ----------
    lines.append("=== 完整描述 ===")
    j = ask("矩形渠，底宽5米，糙率0.015，坡度千分之一，流量10个方，水深2米，长600米")
    check("判定为计算", j.get("act") == "compute", j.get("act"))
    check("参数齐备", j.get("ready") is True, j.get("missing"))
    code, s = req("/api/solve", {})
    check("计算成功", s.get("ok"), "curves=%s" % ([x["curve_type"] for x in s["result"]["segments"]]
                                                if s.get("ok") else None))
    lines.append("")

    # ---------- 5. 结合结果解释 ----------
    lines.append("=== 基于当前结果的解释 ===")
    j = ask("这个工况为什么是M1？它属于哪个流区？")
    rp = j.get("reply") or ""
    check("能结合当前结果作答", j.get("act") == "answer" and len(rp) > 20, rp[:90])
    check("答案引用了实际数值或型号",
          any(k in rp for k in ("M1", "Ⅰ区", "一区", "缓坡", "h0", "h₀")), rp[:90])
    lines.append("  A: " + rp.replace("\n", " ")[:200])
    lines.append("")

    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
