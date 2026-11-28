# -*- coding: utf-8 -*-
"""调度页一体化验收：结果带 base、会话读当前方案、可回填。"""
import json
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
    r = post("/api/unsteady/optimize", dict(P, text="闸上不能超过4.5米",
                                            no_ai=True, steps=7))
    check("寻优结果带 base", r.get("base", {}).get("L") == 2000, r.get("base"))
    rec = r.get("recommend") or {}
    check("寻优有推荐", bool(rec))

    cp = {"name": rec.get("name"), "a_end": rec.get("a_end"),
          "peak": rec.get("peak"), "hmax": rec.get("hmax")}
    s = post("/api/unsteady/schedule/session",
             dict(P, text="闸上不能超过4.5米", use_ai=False, current_plan=cp))
    check("会话创建带 base", s.get("base", {}).get("L") == 2000)
    sid = s.get("session_id")
    t = post("/api/unsteady/schedule/turn",
             {"session_id": sid, "text": "再保守一点", "use_ai": False,
              "current_plan": cp})
    check("会话轮次带 base", t.get("base", {}).get("L") == 2000, t.get("base"))
    check("会话调整可用", t.get("action") == "adjust", t.get("action"))
    t2 = post("/api/unsteady/schedule/turn",
              {"session_id": sid, "text": "当前方案是什么", "use_ai": False,
               "current_plan": cp})
    check("会话能读当前方案", t2.get("action") == "current"
          and (rec.get("name") or "") in (t2.get("reply") or ""), t2.get("reply"))

    html = urllib.request.urlopen(BASE + "/unsteady.html", timeout=10).read().decode("utf-8")
    check("页面保存 base", "lastPlanBase" in html and "lastPlanStage" in html)
    check("会话携带当前方案", "current_plan: lastPlan" in html)
    check("应用方案回填参数", "setV(\"#L\", b.L)" in html and "b.t_peak" in html)

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
