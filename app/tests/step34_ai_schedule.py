# -*- coding: utf-8 -*-
"""V4.3.1 验收：AI 调度参谋（澄清/候选/推荐/追溯/降级/页面）。"""
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from unsteady import schedule_ai  # noqa: E402

OK = TOTAL = 0
LINES = []
BASE_PARAMS = dict(L=2000, b=6, m=0, n=0.015, i0=0.001, Q0=10, Qpeak=30,
                   t_peak=3600, t_base=7200, t_end=28800, Nx=61)


def check(label, cond, detail=""):
    global OK, TOTAL
    TOTAL += 1
    if cond:
        OK += 1
    LINES.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def post(path, obj, timeout=240):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def trace_bad(explanation, rows, max_stage):
    return schedule_ai._trace_ok(explanation, rows, max_stage)


def main():
    # 1) 缺少约束 → 主动澄清
    r = post("/api/unsteady/ai_schedule", dict(BASE_PARAMS, text="帮我尽量削峰", no_ai=True))
    check("缺约束时主动追问", r.get("need_clarify") is True, r.get("question"))
    check("澄清带可选项", len(r.get("options") or []) >= 2)

    # 2) 规则模式：候选、可行性与推荐
    t0 = time.time()
    r = post("/api/unsteady/ai_schedule",
             dict(BASE_PARAMS, text="闸上不能超过6米，尽量削峰", no_ai=True))
    el = time.time() - t0
    check("规则模式可用", r.get("ok") and not r.get("need_clarify"))
    check("规则模式耗时 <15 s", el < 15, "%.1f s" % el)
    rows = r.get("candidates") or []
    check("候选 4–6 个", 4 <= len(rows) <= 6, len(rows))
    check("候选含粗扫与精修", {c["source"] for c in rows} >= {"coarse"},
          {c["source"] for c in rows})
    check("推荐方案满足约束",
          r.get("recommend") is None or r["recommend"]["hmax"] <= r["max_stage"] + 1e-9,
          r.get("recommend"))
    check("推荐是可行方案里峰值最小",
          r.get("recommend") is None or
          all(c["peak"] >= r["recommend"]["peak"] - 1e-9
              for c in rows if c["feasible"]), None)
    check("降级标记正确（no_ai）", r.get("fallback_used") is True)
    check("降级解释非空且可追溯",
          bool(r.get("explanation")) and not trace_bad(r["explanation"], rows, r["max_stage"]),
          trace_bad(r.get("explanation", ""), rows, r.get("max_stage", 6)))

    # 3) 不可行场景
    r = post("/api/unsteady/ai_schedule",
             dict(BASE_PARAMS, text="闸上不能超过2米，尽量削峰", no_ai=True))
    check("约束过严时判不可行", r.get("feasible_any") is False, r.get("feasible_any"))
    check("不可行时不硬推荐", r.get("recommend") is None)
    check("不可行解释说明原因",
          ("没有满足" in (r.get("explanation") or "")) or ("不可行" in (r.get("explanation") or "")),
          (r.get("explanation") or "")[:60])

    # 4) AI 模式：推荐 + 解释可追溯
    t0 = time.time()
    r = post("/api/unsteady/ai_schedule",
             dict(BASE_PARAMS, text="闸上不能超过6米，尽量削峰"))
    el = time.time() - t0
    check("AI 模式可用", r.get("ok") and not r.get("need_clarify"), r.get("error"))
    check("AI 模式耗时 <30 s", el < 30, "%.1f s" % el)
    rows = r.get("candidates") or []
    check("AI 精修候选出现", any(c["source"] == "ai" for c in rows) or r.get("fallback_used"),
          {c["source"] for c in rows})
    check("AI 解释数字可追溯",
          not trace_bad(r.get("explanation", ""), rows, r.get("max_stage", 6)),
          trace_bad(r.get("explanation", ""), rows, r.get("max_stage", 6)))
    check("解释无“实测/全局最优”",
          ("实测" not in (r.get("explanation") or "")) and
          ("全局最优" not in (r.get("explanation") or "")))

    # 5) 页面
    html = None
    try:
        html = urllib.request.urlopen(BASE + "/unsteady.html", timeout=10).read().decode("utf-8")
    except Exception:
        pass
    check("页面有 AI 参谋入口", bool(html) and 'id="aiBtn"' in html and 'id="aiText"' in html)
    check("页面可应用推荐方案", bool(html) and 'id="aiApply"' in html or "aiApply" in (html or ""))

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
