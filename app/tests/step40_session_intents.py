# -*- coding: utf-8 -*-
"""多轮会话意图验收：能力/随机/目标/兜底不再一律重算。"""
import json
import os
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
OK = TOTAL = 0
LINES = []


def check(label, cond, detail=""):
    global OK, TOTAL
    TOTAL += 1
    if cond:
        OK += 1
    LINES.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def post(path, obj, timeout=300):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    P = dict(L=2000, b=6, m=0, n=0.015, i0=0.001, Q0=10, Qpeak=30,
             t_peak=3600, t_base=7200, t_end=28800, Nx=61)
    r = post("/api/unsteady/schedule/session",
             dict(P, text="闸上不能超过5米，尽量削峰"))
    sid = r.get("session_id")
    check("会话创建", bool(sid), r.get("error"))

    r = post("/api/unsteady/schedule/turn", {"session_id": sid, "text": "讲一讲你能做什么"})
    check("能力介绍动作", r.get("action") == "capability", r.get("action"))
    check("能力介绍不重算", "已重新计算" not in (r.get("reply") or ""))
    check("能力介绍列功能", "我能做" in (r.get("reply") or ""))

    r = post("/api/unsteady/schedule/turn", {"session_id": sid, "text": "再保守一点"})
    check("保守调整仍可用", r.get("action") == "adjust", r.get("action"))

    r = post("/api/unsteady/schedule/turn", {"session_id": sid, "text": "随机算例"})
    check("随机算例动作", r.get("action") == "random", r.get("action"))
    check("随机算例换了参数",
          "已换随机算例" in (r.get("reply") or "") and "L=" in (r.get("reply") or ""))

    r = post("/api/unsteady/schedule/turn", {"session_id": sid, "text": "尽量削峰"})
    check("目标确认动作", r.get("action") == "objective", r.get("action"))
    check("目标确认不重算", "已重新计算" not in (r.get("reply") or ""))

    r = post("/api/unsteady/schedule/turn", {"session_id": sid, "text": "今天天气不错"})
    check("闲聊兜底", r.get("action") == "clarify", r.get("action"))
    check("兜底给示例", "没太听懂" in (r.get("reply") or ""))

    h = post("/api/unsteady/schedule/history", {"session_id": sid})
    acts = {x.get("action") for x in (h.get("history") or [])}
    check("历史含新动作", {"capability", "random", "objective", "clarify"} <= acts, acts)

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
