# -*- coding: utf-8 -*-
"""宽顶堰页面/对话验收。"""
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
    check("页面有宽顶堰开关", 'id="weirOn"' in html)
    check("页面有堰参数", 'id="weirBw"' in html and 'id="weirZ"' in html and 'id="weirC"' in html)
    check("payload 含 weir", "currentWeir" in html and 'type: "weir"' in html)
    check("⑥ 率定曲线卡", 'id="cWeirRate"' in html and "drawWeirRate" in html)
    check("⑦ 局部放大卡", 'id="cWeirZoom"' in html and "drawWeirZoom" in html)
    check("⑤ 堰分支", "drawWeirGate" in html and 'g.type === "weir"' in html)
    check("预设含堰", 'data-p="weir_free"' in html and 'data-p="weir_sub"' in html)

    r = post("/api/unsteady/chat",
             {"text": "加一座宽顶堰，堰宽6米，堰顶0.5米，堰位60%"})
    w = r.get("weir") or {}
    check("对话识别宽顶堰", r.get("act") == "weir_set", r.get("act"))
    check("解析堰参数", w.get("b_w") == 6.0 and w.get("z_s") == 0.5 and w.get("s_pct") == 60.0, w)
    check("回复明确", "宽顶堰" in (r.get("reply") or ""), r.get("reply"))

    r = post("/api/unsteady/chips", {
        "text": "堰", "params": {"weir": {"type": "weir", "b_w": 6, "z_s": 0.5}},
        "summary": "堰"})
    chips = "\n".join(r.get("chips") or [])
    check("chips 含堰内容", ("堰顶" in chips) or ("淹没" in chips), chips)

    body = dict(model="preissmann", L=2000, b=5, m=0, n=0.015, i0=0.001,
                Q0=10, Qpeak=20, t_peak=3600, t_base=7200, t_end=6 * 3600,
                Nx=61, h_dn=1.5,
                gate={"structures": [dict(type="weir", s=1200, b_w=5,
                                          z_s=0.5, C=0.35)]})
    r = post("/api/unsteady/run", body)
    res = r.get("result") or {}
    st = (res.get("structures") or [{}])[0]
    check("页面同款 payload 可算", r.get("ok"), r.get("error"))
    check("返回堰历史量", bool(st.get("H0_hist")) and bool(st.get("reg_hist")))
    check("堰流态字段", st.get("regime") in ("free", "submerged"), st.get("regime"))

    print("=" * 60)
    print("通过 %d / %d" % (OK, TOTAL))
    return 0 if OK == TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
