# -*- coding: utf-8 -*-
"""Fr（缓流）运行期检查验收。"""
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
    mild = dict(L=2000, b=5, m=0, n=0.015, i0=0.001, Q0=10, Qpeak=30,
                t_peak=3600, t_base=7200, t_end=2 * 3600, Nx=41)
    r = post("/api/unsteady/run", dict(mild, model="preissmann"))
    check("缓流工况可算", r.get("ok"), r.get("error"))
    d = (r.get("result") or {}).get("diagnostics") or {}
    check("缓流无警告", d.get("fr_warning") == "", d.get("fr_warning"))
    check("缓流 Fr<1", (d.get("fr_max") or 9) < 1.0, d.get("fr_max"))
    check("缓流状态", d.get("fr_regime") == "subcritical", d.get("fr_regime"))

    steep = dict(L=1000, b=4, m=0, n=0.015, i0=0.02, Q0=10, Qpeak=10,
                 t_peak=3600, t_base=7200, t_end=1800, Nx=41)
    r = post("/api/unsteady/run", dict(steep, model="preissmann"))
    check("陡坡仍返回结果（警告而非崩溃）", r.get("ok"), r.get("error"))
    d = (r.get("result") or {}).get("diagnostics") or {}
    check("陡坡给出 Fr 警告", bool(d.get("fr_warning")), d.get("fr_warning"))
    check("陡坡判定为超临界/陡坡",
          d.get("fr_regime") in ("supercritical", "steep_normal"), d.get("fr_regime"))
    check("正常水深小于临界水深", d.get("h_n0", 9) < d.get("h_c0", 0),
          (d.get("h_n0"), d.get("h_c0")))

    # 扩散波同样标注
    r = post("/api/unsteady/run", dict(steep, model="diffusion"))
    d = (r.get("result") or {}).get("diagnostics") or {}
    check("扩散波也标注 Fr", "fr_warning" in d and bool(d.get("fr_warning")),
          d.get("fr_warning"))

    # 页面提示
    html = urllib.request.urlopen(BASE + "/unsteady.html", timeout=10).read().decode("utf-8")
    check("页面有 Fr 警示条", 'id="frWarn"' in html and "fr_warning" in html)

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
