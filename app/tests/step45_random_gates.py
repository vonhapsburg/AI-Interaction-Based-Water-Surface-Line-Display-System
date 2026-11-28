# -*- coding: utf-8 -*-
"""随机单闸 / 随机双闸验收：真随机、可算、可回填。"""
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
    # 单闸：多次生成，闸位/闸宽/开度应变化
    s_list = []
    b_list = []
    a_list = []
    for _ in range(6):
        r = post("/api/unsteady/random", {"gates": 1})
        if not r.get("ok"):
            check("随机单闸生成失败", False, r.get("error"))
            continue
        g = (r.get("params") or {}).get("gate") or {}
        s_list.append(g.get("s_pct"))
        b_list.append(g.get("b_g"))
        a_list.append(g.get("a_end"))
    check("随机单闸可用", len(s_list) == 6, len(s_list))
    check("单闸闸位真随机", len(set(s_list)) >= 3, s_list)
    check("单闸闸宽真随机", len(set(b_list)) >= 3, b_list)
    check("单闸开度真随机", len(set(a_list)) >= 3, a_list)
    check("单闸闸位不是固定 50", any(abs(x - 50) > 3 for x in s_list if x is not None), s_list)

    # 双闸：结构、位置、参数
    two_ok = 0
    for _ in range(4):
        r = post("/api/unsteady/random", {"gates": 2})
        if not r.get("ok"):
            continue
        g = (r.get("params") or {}).get("gate") or {}
        arr = g.get("structures") or []
        if len(arr) != 2:
            continue
        s1, s2 = arr[0].get("s_pct"), arr[1].get("s_pct")
        if s1 is None or s2 is None or not (s1 < s2):
            continue
        if not (15 <= s1 <= 45 and 55 <= s2 <= 85):
            continue
        if arr[0].get("b_g") == arr[1].get("b_g") and arr[0].get("a_end") == arr[1].get("a_end"):
            continue
        # 用返回参数实际跑一次，确认双闸能被识别
        params = {k: r.get(k) for k in
                  ("L", "b", "m", "n", "i0", "Q0", "Qpeak", "tp", "tb", "te", "hdn")}
        body = dict(model="preissmann", L=params["L"], b=params["b"], m=params["m"],
                    n=params["n"], i0=params["i0"], Q0=params["Q0"],
                    Qpeak=params["Qpeak"], t_peak=params["tp"] * 3600,
                    t_base=params["tb"] * 3600, t_end=min(params["te"], 3) * 3600,
                    Nx=41, h_dn=params["hdn"], gate={"structures": arr})
        rr = post("/api/unsteady/run", body)
        res = rr.get("result") or {}
        d = res.get("diagnostics") or {}
        if d.get("gate_count") == 2 and d.get("status") == "ok":
            two_ok += 1
    check("随机双闸可生成且可算", two_ok >= 2, two_ok)

    html = urllib.request.urlopen(BASE + "/unsteady.html", timeout=10).read().decode("utf-8")
    check("页面有随机单闸按钮", 'id="randGateBtn"' in html and "随机单闸" in html)
    check("页面有随机双闸按钮", 'id="randTwoBtn"' in html and "随机双闸" in html)
    check("请求带 gates 数量", "gates: mode" in html)

    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
