# -*- coding: utf-8 -*-
"""V4.1/V4.2 验收：闸门对话解析、多段开度、闸门出题。"""
import json
import os
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
OK = TOTAL = 0
LINES = []

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unsteady import chat  # noqa: E402


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
    # 1) 规则解析
    cases = {
        "2小时后关到0.5米": {"t_close": 2.0, "a_end": 0.5},
        "闸宽6米": {"b_g": 6.0},
        "闸位在40%": {"s_pct": 40.0},
        "初始开度1.2米": {"a0": 1.2},
        "用0.5小时关到0.4": {"t_close": 0.0, "t_ramp": 0.5, "a_end": 0.4},
    }
    for text, want in cases.items():
        g = chat.parse_gate(text) or {}
        ok = all(abs(float(g.get(k, -9)) - v) < 1e-6 for k, v in want.items())
        check("解析：%s" % text, ok, g)

    g = chat.parse_gate("1小时关到0.6，再过2小时关到0.3") or {}
    check("解析：多段过程线", g.get("sched") == [(1.0, 0.6), (3.0, 0.3)], g.get("sched"))
    check("问句不触发改参数", chat.parse_gate("为什么关闸后上游水位会升高") is None)

    # 2) 对话接口
    r = post("/api/unsteady/chat", {"text": "2小时后关到0.5米", "params": {}, "summary": ""})
    check("对话：单段关闸", r.get("act") == "gate_set" and
          abs(r["gate"].get("t_close", 0) - 2) < 1e-6, r)
    r = post("/api/unsteady/chat", {"text": "闸宽改成6米", "params": {}, "summary": ""})
    check("对话：改闸宽", r.get("act") == "gate_set" and r["gate"].get("b_g") == 6, r)
    r = post("/api/unsteady/chat", {"text": "1小时关到0.6，再过2小时关到0.3",
                                    "params": {}, "summary": ""})
    check("对话：多段过程线", r.get("act") == "gate_set" and
          r["gate"].get("sched") == [[1, 0.6], [3, 0.3]], r.get("gate"))
    r = post("/api/unsteady/chat", {"text": "为什么关闸后上游水位会升高",
                                    "params": {}, "summary": ""})
    check("对话：解释题不改成参数", r.get("act") != "gate_set", r.get("act"))

    # 3) 多段开度真算
    body = {"model": "preissmann", "L": 2000, "b": 6, "m": 0, "n": 0.015,
            "i0": 0.001, "Q0": 10, "Qpeak": 30, "t_peak": 3600, "t_base": 7200,
            "t_end": 8 * 3600, "Nx": 81,
            "gate": {"s": 1000, "b_g": 6, "z_s": 0, "mu": 0.62,
                     "sched": [[0, 1.0], [1, 0.6], [3, 0.3]]}}
    res = post("/api/unsteady/run", body)
    check("多段开度可计算", res.get("ok"), res.get("error"))
    if res.get("ok"):
        g = res["result"]["gate"]
        check("开度按过程线变化", abs(g["a"][0] - 1.0) < 0.05 and abs(g["a"][-1] - 0.3) < 0.05,
              "%s → %s" % (g["a"][0], g["a"][-1]))
        check("多段开度质量守恒 <0.5%", res["result"]["diagnostics"]["mass_err"] < 0.005,
              "%.4f%%" % (res["result"]["diagnostics"]["mass_err"] * 100))
        check("多段开度 Newton 无失败",
              res["result"]["diagnostics"]["newton_failed"] == 0)

    # 4) 闸门出题
    P = {"L": 2000, "b": 6, "Q0": 10, "Qpeak": 30, "mode": "engineering",
         "gate": {"s_pct": 50, "b_g": 6, "a0": 1.0, "a_end": 0.6,
                  "t_close": 0.8, "t_ramp": 0.5}}
    q = post("/api/unsteady/chat", {"text": "出几道关于闸门的题", "params": P,
                                    "summary": "含闸门，闸上最高 5.6 m"})
    items = q.get("items") or []
    check("闸门出题有 3 题", len(items) == 3, len(items))
    n_gate = sum(1 for it in items if "闸" in (it.get("q") or ""))
    check("至少 2 题涉及闸门", n_gate >= 2, n_gate)

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
