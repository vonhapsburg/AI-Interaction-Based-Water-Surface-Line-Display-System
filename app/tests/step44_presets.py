# -*- coding: utf-8 -*-
"""预设布局 + 双闸预设验收。"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
OK = TOTAL = 0


def check(label, cond, detail=""):
    global OK, TOTAL
    TOTAL += 1
    if cond:
        OK += 1
    print(("%s %s" % ("✅" if cond else "❌", label))
          + (("  —— " + str(detail)) if detail else ""))


def post(path, obj, timeout=300):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    html = urllib.request.urlopen(BASE + "/unsteady.html", timeout=10).read().decode("utf-8")
    check("预设两列网格", "grid-template-columns:1fr 1fr" in html and ".preset.rand" in html)
    check("有双闸联调预设", 'data-p="two_gate"' in html and "双闸联调" in html)
    check("有双闸错峰预设", 'data-p="two_gate_stagger"' in html and "双闸错峰" in html)
    check("预设里带 gate2", "gate2:{s:70" in html)
    check("应用预设会写第二座闸", 'set("#gate2S"' in html)

    body = dict(model="preissmann", L=2000, b=6, m=0, n=0.015, i0=0.001,
                Q0=10, Qpeak=30, t_peak=3600, t_base=7200, t_end=7200,
                Nx=81, h_dn=1.4, gate={"structures": [
                    dict(s_pct=30, b_g=6, z_s=0, mu=0.62, a0=1.0, a_end=0.6,
                         t_close=2880, t_ramp=1800),
                    dict(s_pct=70, b_g=6, z_s=0, mu=0.62, a0=1.0, a_end=0.5,
                         t_close=4320, t_ramp=1800)]})
    r = post("/api/unsteady/run", body)
    res = r.get("result") or {}
    d = res.get("diagnostics") or {}
    check("双闸联调预设可算", r.get("ok"), r.get("error"))
    check("双闸位置 600/1400", [round(s["x"]) for s in res.get("structures", [])] == [600, 1400])
    check("双闸水量", d.get("mass_err", 1) < 0.01, d.get("mass_err"))
    check("双闸收敛", d.get("status") == "ok", d.get("status"))

    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
