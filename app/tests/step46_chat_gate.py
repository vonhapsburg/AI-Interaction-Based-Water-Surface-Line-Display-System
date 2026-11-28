# -*- coding: utf-8 -*-
"""非恒定对话与闸门联动验收：第二座闸、随机单/双闸、猜你想问。"""
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


def post(path, obj, timeout=120):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    gate_ctx = {"on": True, "s_pct": 30, "b_g": 6, "z_s": 0, "mu": 0.62,
                "a0": 1.0, "a_end": 0.6, "t_close": 0.8, "t_ramp": 0.5}

    r = post("/api/unsteady/chat", {"text": "第二座闸关到0.5米", "params": {"gate": gate_ctx}})
    g = r.get("gate") or {}
    check("第二座闸识别", r.get("act") == "gate_set", r.get("act"))
    check("标注 which=2", g.get("which") == 2, g.get("which"))
    check("终止开度 0.5", g.get("a_end") == 0.5, g.get("a_end"))
    check("回复提到第二座闸", "第二座闸" in (r.get("reply") or ""), r.get("reply"))

    r = post("/api/unsteady/chat", {"text": "第二座闸闸宽7米", "params": {"gate": gate_ctx}})
    g = r.get("gate") or {}
    check("第二座闸闸宽", g.get("b_g") == 7.0 and g.get("which") == 2, g)

    r = post("/api/unsteady/chat", {"text": "第二座闸再关小一点", "params": {"gate": gate_ctx}})
    g = r.get("gate") or {}
    check("第二座闸相对调整", g.get("which") == 2 and g.get("delta") == -0.1, g)
    r = post("/api/unsteady/chat", {"text": "再关小一点", "params": {"gate": gate_ctx}})
    check("单闸相对调整", (r.get("gate") or {}).get("delta") == -0.1, r.get("gate"))

    r = post("/api/unsteady/chat", {"text": "随机双闸"})
    check("随机双闸动作", r.get("act") == "random" and r.get("gates") == 2, r)
    r = post("/api/unsteady/chat", {"text": "随机单闸"})
    check("随机单闸动作", r.get("act") == "random" and r.get("gates") == 1, r)

    # 猜你想问：双闸上下文
    two = {"structures": [gate_ctx, dict(gate_ctx, s_pct=70)]}
    r = post("/api/unsteady/chips", {"text": "闸门", "params": {"gate": two}, "summary": "闸门"})
    chips = "\n".join(r.get("chips") or [])
    check("双闸 chips 提第二座闸", "第二座闸" in chips or "两座闸" in chips, chips)
    check("双闸 chips 提随机双闸", "随机双闸" in chips or "双闸" in chips, chips)

    one = {"on": True, **gate_ctx}
    r = post("/api/unsteady/chips", {"text": "闸门", "params": {"gate": one}, "summary": "闸门"})
    chips = "\n".join(r.get("chips") or [])
    check("单闸 chips 有闸门内容", ("再关小" in chips) or ("随机单闸" in chips), chips)

    html = urllib.request.urlopen(BASE + "/unsteady.html", timeout=10).read().decode("utf-8")
    check("页面支持第二座闸补丁", "g.which === 2" in html and 'set2("#gate2A1"' in html)
    check("聊天随机按 gates", "randomCase(_m)" in html and "(r.gates != null)" in html)

    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
