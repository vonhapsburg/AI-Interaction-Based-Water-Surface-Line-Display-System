# -*- coding: utf-8 -*-
"""V4.3.2 验收：枚举寻优、Pareto、放宽、鲁棒性、反事实、建议书。"""
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


def main():
    # 1) 寻优（规则模式）
    t0 = time.time()
    r = post("/api/unsteady/optimize",
             dict(P, text="闸上不能超过6米，尽量削峰", no_ai=True, steps=7))
    el = time.time() - t0
    check("寻优接口可用", r.get("ok") and not r.get("need_clarify"), r.get("error"))
    check("寻优待时 <20 s", el < 20, "%.1f s" % el)
    rows = r.get("candidates") or []
    check("候选数≥7（含边界细化）", len(rows) >= 7, len(rows))
    check("Pareto 标记与候选等长", len(r.get("pareto") or []) == len(rows))
    check("推荐满足约束",
          r.get("recommend") is None or r["recommend"]["hmax"] <= r["max_stage"] + 1e-9,
          r.get("recommend"))
    check("推荐是可行里峰值最小",
          r.get("recommend") is None or
          all(c["peak"] >= r["recommend"]["peak"] - 1e-9
              for c in rows if c["feasible"]), None)
    check("鲁棒性有两组扰动", len(r.get("robust") or []) == 2,
          [x.get("case") for x in (r.get("robust") or [])])
    check("建议书含候选与推荐",
          ("建议书" in r.get("report_md", "")) and
          (("推荐方案" in r["report_md"]) or ("没有满足" in r["report_md"])))
    check("解释非空", bool(r.get("explanation")))

    # 2) 约束过严 → 放宽建议可用
    r2 = post("/api/unsteady/optimize",
              dict(P, text="闸上不能超过3.0米", no_ai=True, steps=7))
    check("过严约束判不可行", r2.get("feasible_any") is False)
    check("全开仍不满足时给出最低可达水位",
          bool(r2.get("no_feasible_even_open")) and (r2.get("suggest_stage") or 0) > 3.0,
          (r2.get("min_hmax_possible"), r2.get("suggest_stage")))
    check("给出两级放宽", len(r2.get("relax") or []) == 2)
    check("放宽后存在可行解", any(x["feasible_any"] for x in r2.get("relax") or []),
          r2.get("relax"))
    check("不可行时仍给解释", bool(r2.get("explanation")))

    # 3) 反事实
    cf = post("/api/unsteady/counterfactual", dict(P, a_end=0.5, max_stage=6.0))
    check("反事实接口可用", cf.get("ok"))
    x = cf.get("result") or {}
    check("反事实字段完整", {"peak", "cut", "hmax", "feasible"} <= set(x),
          sorted(x))
    check("反事实可行性自洽",
          x.get("feasible") == (x.get("hmax", 99) <= 6.0 + 1e-9), x)

    # 4) AI 解释模式
    t0 = time.time()
    r3 = post("/api/unsteady/optimize",
              dict(P, text="闸上不能超过6米，尽量削峰", steps=7))
    el = time.time() - t0
    check("AI 寻优可用", r3.get("ok"), r3.get("error"))
    check("AI 寻优待时 <30 s", el < 30, "%.1f s" % el)
    if not r3.get("fallback_used"):
        trace_rows = (r3.get("candidates") or []) + [
            {"a_end": 0, "cut": 0, "peak": z.get("peak", 0), "hmax": z.get("hmax", 0)}
            for z in (r3.get("robust") or []) if "hmax" in z]
        bad = schedule_ai._trace_ok(r3.get("explanation", ""), trace_rows,
                                    r3.get("max_stage", 6))
        check("AI 解释数字可追溯", not bad, bad)
    else:
        check("AI 不可用时降级并给出模板解释", bool(r3.get("explanation")))

    # 5) 页面
    html = urllib.request.urlopen(BASE + "/unsteady.html", timeout=10).read().decode("utf-8")
    check("页面有寻优入口", 'id="optBtn"' in html and "runOpt" in html)
    check("页面有 Pareto 图", 'id="cPareto"' in html and "drawPareto" in html)
    check("页面可下载建议书", "optReport" in html and "report_md" in html)
    check("页面有反事实入口", 'id="cfBtn"' in html)

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
