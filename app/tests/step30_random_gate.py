# -*- coding: utf-8 -*-
"""随机算例（含/不含闸门）验收。"""
import json
import os
import time
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


def post(path, obj, timeout=240):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    # 1) 带闸门随机：字段与合理性
    gates = []
    for _ in range(4):
        r = post("/api/unsteady/random", {"gate": True})
        p = r.get("params") or {}
        gates.append(p)
    check("随机闸门都能生成", all(p.get("gate") for p in gates), len(gates))
    check("随机闸门强制工程模式",
          all(p.get("mode") == "engineering" for p in gates),
          [p.get("mode") for p in gates])
    check("初始开度大于终止开度",
          all(p["gate"]["a0"] > p["gate"]["a_end"] for p in gates if p.get("gate")))
    check("关闭时刻与历时为正",
          all(p["gate"]["t_close"] > 0 and p["gate"]["t_ramp"] > 0
              for p in gates if p.get("gate")))
    check("开度在合理范围（0.3–3 m）",
          all(0.3 <= p["gate"]["a_end"] <= 3.0 for p in gates if p.get("gate")))

    # 2) 拿一个随机闸门算例真跑一次
    p = next((q for q in gates if q.get("gate")), None)
    if p:
        body = {
            "model": "preissmann",
            "L": p["L"], "b": p["b"], "m": p["m"], "n": p["n"], "i0": p["i0"],
            "Q0": p["Q0"], "Qpeak": p["Qpeak"],
            "t_peak": p["tp"] * 3600, "t_base": p["tb"] * 3600,
            "t_end": p["te"] * 3600, "Nx": 81,
            "h_dn": None if p["hdn"] == "auto" else p["hdn"],
            "gate": p["gate"],
        }
        t0 = time.time()
        res = post("/api/unsteady/run", body)
        el = time.time() - t0
        check("随机闸门可计算", res.get("ok"), res.get("error"))
        if res.get("ok"):
            d = res["result"]["diagnostics"]
            check("随机闸门算例守时（<8 s）", el < 8, "%.1f s" % el)
            check("随机闸门质量守恒 <0.5%", d["mass_err"] < 0.005,
                  "%.4f%%" % (d["mass_err"] * 100))
            check("随机闸门 Newton 无失败", d["newton_failed"] == 0)
            check("结果里带闸门过程", bool(res["result"]["gate"]["on"]))

    # 2b) 猜你想问轮换里应有随机入口
    seen = set()
    for _ in range(5):
        c = post("/api/unsteady/chips", {"text": "", "params": {}, "summary": ""})
        seen.update(c.get("chips") or [])
    check("猜你想问含随机闸门入口", any("随机闸门" in x for x in seen), list(seen)[:4])
    check("猜你想问含随机算例入口", any("随机算例" in x for x in seen))

    # 3) 普通随机不带闸门
    plain = [post("/api/unsteady/random", {}) for _ in range(3)]
    check("普通随机不含闸门",
          all("gate" not in (r.get("params") or {}) for r in plain))

    print("\n".join(LINES))
    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
