# -*- coding: utf-8 -*-
"""V4.9.1 验收：多轮会话接入 DeepSeek（判意图 + 写回复 + 规则兜底）。"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
OK = TOTAL = 0
LINES = []
VALID = {"capability", "random", "objective", "adjust", "explain",
         "counterfactual", "reoptimize", "clarify"}


def check(label, cond, detail=""):
    global OK, TOTAL
    TOTAL += 1
    if cond:
        OK += 1
    LINES.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def post(path, obj, timeout=180):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    P = dict(L=2000, b=6, m=0, n=0.015, i0=0.001, Q0=10, Qpeak=30,
             t_peak=3600, t_base=7200, t_end=28800, Nx=61)
    r = post("/api/unsteady/schedule/session",
             dict(P, text="闸上不能超过5米，尽量削峰", use_ai=True))
    check("AI 会话创建", r.get("ok"), r.get("error"))
    sid = r.get("session_id")
    check("创建带 ai_used 标记", "ai_used" in r, r.get("ai_used"))

    used = []
    texts = []
    for msg in ("你能帮我做什么", "今天这水怎么样", "关到0.5呢", "再保守一点"):
        t = post("/api/unsteady/schedule/turn",
                 {"session_id": sid, "text": msg, "use_ai": True})
        check("轮次可用：" + msg, t.get("ok"), t.get("error"))
        check("动作在允许集合：" + msg, t.get("action") in VALID, t.get("action"))
        check("回复非空：" + msg, bool(t.get("reply")))
        check("有 ai_used 标记：" + msg, "ai_used" in t)
        used.append(bool(t.get("ai_used")))
        texts.append(t.get("reply") or "")

    check("至少一轮由 AI 参与", any(used), used)
    joined = "\n".join(texts)
    check("解释不出现“实测/全局最优”",
          ("实测" not in joined) and ("全局最优" not in joined))

    # 模型不可用/关闭时仍能工作
    r2 = post("/api/unsteady/schedule/session",
              dict(P, text="闸上不能超过5米", use_ai=False))
    sid2 = r2.get("session_id")
    t2 = post("/api/unsteady/schedule/turn",
              {"session_id": sid2, "text": "再保守一点", "use_ai": False})
    check("规则兜底仍可用", t2.get("action") == "adjust" and t2.get("ai_used") is False,
          (t2.get("action"), t2.get("ai_used")))

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
