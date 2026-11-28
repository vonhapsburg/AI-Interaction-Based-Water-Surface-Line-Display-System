# -*- coding: utf-8 -*-
"""多闸门演进验收：第二座闸输入、s_pct 位置、两个结构可视化。"""
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
    gs = [
        dict(s_pct=30, b_g=6, z_s=0, mu=0.62, a0=1.0, a_end=0.6,
             t_close=2880, t_ramp=1800),
        dict(s_pct=70, b_g=6, z_s=0, mu=0.62, a0=1.0, a_end=0.5,
             t_close=3600, t_ramp=1800),
    ]
    body = dict(model="preissmann", L=2000, b=6, m=0, n=0.015, i0=0.001,
                Q0=10, Qpeak=30, t_peak=3600, t_base=7200, t_end=7200,
                Nx=81, h_dn=None, gate={"structures": gs})
    r = post("/api/unsteady/run", body)
    check("双闸算例可算", r.get("ok"), r.get("error"))
    res = r.get("result") or {}
    d = res.get("diagnostics") or {}
    check("识别两座闸", d.get("gate_count") == 2, d.get("gate_count"))
    check("两座闸位置正确（600/1400 m）",
          [round(s["x"]) for s in res.get("structures", [])] == [600, 1400],
          [s.get("x") for s in res.get("structures", [])])
    check("双闸水量守恒", d.get("mass_err", 1) < 0.01, d.get("mass_err"))
    check("双闸求解收敛", d.get("status") == "ok", d.get("status"))
    check("每座闸都有过程",
          all(len(s.get("t") or []) == len(s.get("a") or [])
              and len(s.get("Q") or []) == len(s.get("hu") or [])
              for s in res.get("structures", [])))
    check("两座闸过程不同",
          len(res.get("structures", [])) == 2 and
          max(res["structures"][0]["hu"]) != max(res["structures"][1]["hu"]),
          [round(max(s["hu"]), 3) for s in res.get("structures", [])])

    # 单闸非 50% 闸位必须真的按 s_pct 放
    one = post("/api/unsteady/run", dict(body, gate={"structures": [dict(gs[0], s_pct=25)]}))
    one_res = one.get("result") or {}
    check("单闸 s_pct=25 定位到 500 m",
          [round(s["x"]) for s in one_res.get("structures", [])] == [500],
          [s.get("x") for s in one_res.get("structures", [])])

    html = urllib.request.urlopen(BASE + "/unsteady.html", timeout=10).read().decode("utf-8")
    check("页面有第二座闸开关", 'id="gate2On"' in html)
    check("页面有第二座闸参数", 'id="gate2S"' in html and 'id="gate2A1"' in html)
    check("payload 走 structures", "currentStructures" in html and "structures: structs" in html)
    check("① 图遍历多闸", "R.structures && R.structures.length" in html)
    check("⑤ 图有闸门选择器", 'id="gatePick"' in html and "drawGate(last)" in html)
    check("第二座闸自动切工程模式", "$(\"#gate2On\").checked" in html)

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
