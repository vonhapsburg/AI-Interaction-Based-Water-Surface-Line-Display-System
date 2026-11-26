# -*- coding: utf-8 -*-
"""V4.3.3 验收：多轮调度会话、回放、调度指令评估集。"""
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from unsteady import chat  # noqa: E402

OK = TOTAL = 0
LINES = []
P = dict(L=2000, b=6, m=0, n=0.015, i0=0.001, Q0=10, Qpeak=30,
         t_peak=3600, t_base=7200, t_end=28800, Nx=61)


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


def eval_intents():
    cases = [
        ("闸上不能超过6米，尽量削峰", ("max_stage", 6.0)),
        ("上游水位不超过 5.5 米", ("max_stage", 5.5)),
        ("闸前水位小于4.8m", ("max_stage", 4.8)),
        ("闸上水位 ≤5.2 米", ("max_stage", 5.2)),
        ("把峰值压下来", ("objective", "min_peak")),
        ("尽量削峰", ("objective", "min_peak")),
        ("比较关到0.6和0.4", ("plans", [0.6, 0.4])),
        ("比较关到0.7和0.5", ("plans", [0.7, 0.5])),
        ("帮我看看怎么关比较好", ("none", None)),
        ("今天天气不错", ("none", None)),
    ]
    ok = 0
    for text, (kind, want) in cases:
        it = chat.parse_schedule_intent(text) or {}
        if kind == "max_stage":
            got = (it.get("constraints") or {}).get("max_stage")
            ok += 1 if got == want else 0
        elif kind == "objective":
            ok += 1 if it.get("objective") == want else 0
        elif kind == "plans":
            ok += 1 if it.get("plans_a") == want else 0
        else:
            ok += 1 if not it else 0
    return ok, len(cases)


def main():
    # 0) 调度指令评估集（规则层）
    ok, n = eval_intents()
    check("调度指令评估集 ≥80%", ok / n >= 0.8, "%d/%d" % (ok, n))

    # 1) 首轮：带约束创建会话
    t0 = time.time()
    r = post("/api/unsteady/schedule/session",
             dict(P, text="闸上不能超过6米，尽量削峰"))
    el = time.time() - t0
    check("会话创建成功", r.get("ok") and r.get("session_id"), r.get("error"))
    sid = r.get("session_id")
    check("首轮给出方案", bool((r.get("result") or {}).get("recommend")))
    check("首轮耗时 <12 s", el < 12, "%.1f s" % el)
    rec0 = r["result"]["recommend"]

    # 2) 再保守一点
    r2 = post("/api/unsteady/schedule/turn", {"session_id": sid, "text": "再保守一点"})
    check("保守调整动作", r2.get("action") == "adjust", r2.get("action"))
    cf = r2.get("counterfactual") or {}
    check("保守后开度更大", cf.get("a_end", 0) > rec0["a_end"] - 1e-9, (rec0["a_end"], cf.get("a_end")))
    check("保守后闸上更低", cf.get("hmax", 99) <= rec0["hmax"] + 1e-9,
          (rec0["hmax"], cf.get("hmax")))

    # 3) 解释
    r3 = post("/api/unsteady/schedule/turn", {"session_id": sid, "text": "为什么推荐它"})
    check("解释动作", r3.get("action") == "explain", r3.get("action"))
    check("解释提到推荐方案", "推荐" in (r3.get("reply") or ""), (r3.get("reply") or "")[:40])

    # 4) 反事实
    r4 = post("/api/unsteady/schedule/turn", {"session_id": sid, "text": "关到0.5呢"})
    check("反事实动作", r4.get("action") == "counterfactual", r4.get("action"))
    check("反事实字段完整", {"peak", "cut", "hmax", "feasible"} <= set(r4.get("counterfactual") or {}),
          sorted(r4.get("counterfactual") or {}))

    # 5) 改约束重算
    r5 = post("/api/unsteady/schedule/turn", {"session_id": sid, "text": "闸上不能超过5.5米"})
    check("改约束重算", r5.get("action") == "reoptimize", r5.get("action"))
    check("新约束生效", (r5.get("result") or {}).get("max_stage") == 5.5,
          (r5.get("result") or {}).get("max_stage"))

    # 6) 回放
    rh = post("/api/unsteady/schedule/history", {"session_id": sid})
    hist = rh.get("history") or []
    check("历史 ≥5 轮", len(hist) >= 5, len(hist))
    acts = {h.get("action") for h in hist}
    check("动作类型齐全", {"adjust", "explain", "counterfactual", "reoptimize"} <= acts, acts)
    check("回放带当前约束", rh.get("constraint") == 5.5, rh.get("constraint"))

    # 7) 缺约束 → 先追问，再补
    r7 = post("/api/unsteady/schedule/session", dict(P, text="帮我削峰"))
    check("缺约束先追问", r7.get("need_clarify") is True)
    sid2 = r7.get("session_id")
    r8 = post("/api/unsteady/schedule/turn", {"session_id": sid2, "text": "6米"})
    check("补充约束后给方案", r8.get("action") == "optimize" and
          bool((r8.get("result") or {}).get("recommend")), r8.get("action"))

    # 8) 页面
    html = urllib.request.urlopen(BASE + "/unsteady.html", timeout=10).read().decode("utf-8")
    check("页面有多轮会话入口", 'id="schedSend"' in html and 'id="schedLog"' in html)
    check("页面有回放", 'id="schedReplay"' in html and "schedule/history" in html)

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
