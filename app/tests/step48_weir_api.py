# -*- coding: utf-8 -*-
"""宽顶堰服务端接口验收（物理内核通过 HTTP 也能跑）。"""
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
    body = dict(model="preissmann", L=2000, b=5, m=0, n=0.015, i0=0.001,
                Q0=20, Qpeak=20, t_peak=3600, t_base=7200, t_end=6 * 3600,
                Nx=61, h_dn=1.5,
                gate={"structures": [
                    dict(type="weir", s=1000, b_w=5, z_s=0.5, C=0.35)]})
    r = post("/api/unsteady/run", body)
    check("HTTP 宽顶堰可算", r.get("ok"), r.get("error"))
    res = r.get("result") or {}
    d = res.get("diagnostics") or {}
    st = (res.get("structures") or [{}])[0]
    check("识别为堰", d.get("weir_count") == 1 and d.get("gate_count") == 0, d)
    check("过堰流量 20", abs((st.get("Q") or [0])[-1] - 20) < 0.2, (st.get("Q") or [0])[-1])
    check("水量守恒", d.get("mass_err", 1) < 0.005, d.get("mass_err"))
    check("状态 ok", d.get("status") == "ok", d.get("status"))
    check("输出流态字段", st.get("regime") in ("free", "submerged"), st.get("regime"))

    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
