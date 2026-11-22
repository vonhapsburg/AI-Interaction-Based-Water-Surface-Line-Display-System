# -*- coding: utf-8 -*-
"""第七章验收清单（12 条）的端到端自动核对，走真实 HTTP。"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step11-e2e.txt")
lines = []


def req(path, payload=None, timeout=300):
    url = BASE + path
    if payload is None:
        r = urllib.request.Request(url)
    else:
        r = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                   headers={"Content-Type": "application/json"},
                                   method="POST")
    t = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), time.time() - t
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"error": raw[:200]}
        return e.code, body, time.time() - t


def solve(text):
    return req("/api/solve", {"text": text})


def main():
    ok_count = 0
    total = 0

    def check(label, cond, detail=""):
        nonlocal ok_count, total
        total += 1
        if cond:
            ok_count += 1
        lines.append(("%s %s" % ("✅" if cond else "❌", label))
                     + (("  —— " + str(detail)) if detail else ""))
        return cond

    # 0. 健康检查
    code, h, dt = req("/api/health")
    lines.append("=== /api/health ===")
    check("内核版本暴露", h.get("core") == "1.1.0", json.dumps(h, ensure_ascii=False))
    lines.append("")

    # 1. A1 → M1
    lines.append("=== 1. A1 基准例 ===")
    code, j, dt = solve("矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10个方，水深2米，算500米长")
    if j.get("ok"):
        curves = [s["curve_type"] for s in j["result"]["segments"]]
        check("出 M1 曲线", curves == ["M1"], "curves=%s status=%s" % (curves, j["result"]["status"]))
        check("参数对应表每行都有值",
              all(r["value"] not in (None, "") for r in j["map"]),
              "%d 行" % len(j["map"]))
        check("合法判定为 ok", j["legal"]["code"] in "ABCD", j["legal"]["code"])
    else:
        check("A1 应算成功", False, json.dumps(j, ensure_ascii=False)[:300])
    lines.append("耗时 %.2fs" % dt)
    lines.append("")

    # 2. A4 → M2 + 水跌
    lines.append("=== 2. A4 自由跌水 ===")
    code, j, dt = solve("底宽8米矩形渠，糙率0.02，坡降0.002，流量15立方米每秒，渠末自由跌水，长1000米")
    if j.get("ok"):
        curves = [s["curve_type"] for s in j["result"]["segments"]]
        kinds = [t["kind"] for t in j["result"]["transitions"]]
        check("出 M2 曲线", "M2" in curves, "curves=%s" % curves)
        check("有 outfall 水跌事件", "outfall" in kinds, "transitions=%s" % kinds)
        check("合法判定为 C", j["legal"]["code"] == "C", j["legal"]["code"])
    else:
        check("A4 应算成功", False, json.dumps(j, ensure_ascii=False)[:300])
    lines.append("")

    # 3. A9 陡坡渠首水深 → S 型
    lines.append("=== 3. A9 陡坡 + 渠首水深 ===")
    code, j, dt = solve("陡坡矩形渠，底宽5米，糙率0.015，坡度0.02，流量10，渠首水深0.3米，长400米")
    if j.get("ok"):
        curves = [s["curve_type"] for s in j["result"]["segments"]]
        check("出 S 型曲线（S2/S3）", any(c.startswith("S") for c in curves), "curves=%s" % curves)
        check("上游控制生效", any("upstream" in json.dumps(c) or "上游" in json.dumps(c, ensure_ascii=False)
                                for c in j["result"]["controls"]),
              json.dumps(j["result"]["controls"], ensure_ascii=False)[:160])
    else:
        check("A9 应算成功", False, json.dumps(j, ensure_ascii=False)[:300])
    lines.append("")

    # 4. B1 口语 → 与 A1 一致
    lines.append("=== 4. B1 口语等价 A1 ===")
    _, j1, _ = solve("矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10个方，水深2米，算500米长")
    _, j2, _ = solve("五米宽的渠，流量十个方，坡度千分之一，水深两米，糙率零点零一五，长五百米")
    if j1.get("ok") and j2.get("ok"):
        a = j1["result"]["segments"][0]
        b = j2["result"]["segments"][0]
        same = (a["curve_type"] == b["curve_type"]
                and abs(a["h"][-1] - b["h"][-1]) < 1e-6)
        check("中文数字归一化后结果一致", same,
              "%s vs %s，末端 h=%.6f vs %.6f" % (a["curve_type"], b["curve_type"],
                                                 a["h"][-1], b["h"][-1]))
        check("B1 判为 B 类", j2["legal"]["code"] == "B", j2["legal"]["code"])
    else:
        check("B1 应算成功", False, json.dumps(j2, ensure_ascii=False)[:300])
    lines.append("")

    # 5. A10 平坡 → H2
    lines.append("=== 5. A10 平坡 ===")
    code, j, dt = solve("平坡渠，底宽5米，糙率0.015，坡度0，流量10，水深1.2米，长200米")
    if j.get("ok"):
        curves = [s["curve_type"] for s in j["result"]["segments"]]
        h0s = [r["h0"] for r in j["result"]["reaches"]]
        check("出 H2 曲线", "H2" in curves, "curves=%s" % curves)
        check("正常水深线不出现（h0 为 None）", h0s == [None], "h0=%s" % h0s)
    else:
        check("A10 应算成功", False, json.dumps(j, ensure_ascii=False)[:300])
    lines.append("")

    # 6. E4 → 缺参数追问
    lines.append("=== 6. E4 缺参数 ===")
    code, j, dt = solve("底宽5米，糙率0.015，坡度0.001，流量10")
    check("不计算", not j.get("ok"))
    check("判为 E（不完整）", j["legal"]["code"] == "E", j["legal"]["code"])
    check("缺控制水深与渠长",
          set(["h0", "L"]) <= set(j.get("missing") or []),
          "missing=%s" % (j.get("missing"),))
    lines.append("")

    # 7. G1 n=0.5 → 越界
    lines.append("=== 7. G1 糙率越界 ===")
    code, j, dt = solve("矩形渠，底宽5米，糙率0.5，底坡千分之一，流量10，水深2米，长500米")
    check("不计算", not j.get("ok"))
    check("判为 G（参数越界）", j["legal"]["code"] == "G", j["legal"]["code"])
    check("提示糙率上限", any("糙率" in r for r in j["legal"]["reasons"]),
          json.dumps(j["legal"]["reasons"], ensure_ascii=False))
    lines.append("")

    # 8. H1 矩形+边坡
    lines.append("=== 8. H1 矩形带边坡 ===")
    code, j, dt = solve("矩形渠，边坡1比1.5，底宽5米，糙率0.015，底坡0.001，流量10，水深2米，长500米")
    check("判为 H", j["legal"]["code"] == "H", j["legal"]["code"])
    check("仍能算出结果", j.get("ok"), "ok=%s" % j.get("ok"))
    if j.get("ok"):
        m_core = [r["value"] for r in j["map"] if r["slot"] == "m"]
        check("内核 m 被置为 0", m_core and float(m_core[0]) == 0.0, "m=%s" % m_core)
    lines.append("")

    # 9. F5 圆管 → 拒答
    lines.append("=== 9. F5 圆管 ===")
    code, j, dt = solve("圆形排水管满管流水深")
    check("不计算", not j.get("ok"))
    check("判为 F（主题越界）", j["legal"]["code"] == "F", j["legal"]["code"])
    check("说明圆形断面不支持", any("圆形" in r for r in j["legal"]["reasons"]),
          json.dumps(j["legal"]["reasons"], ensure_ascii=False))
    lines.append("")

    # 10/11. 断面查询
    lines.append("=== 10-11. 点击取断面 ===")
    _, j1, _ = solve("矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10个方，水深2米，算500米长")
    code, s, dt = req("/api/section?x=250")
    check("断面查询成功", code == 200 and s.get("found"),
          "h=%.3f A=%.3f Fr=%.3f" % (s.get("h", 0), s.get("A", 0), s.get("Fr", 0))
          if s.get("found") else json.dumps(s, ensure_ascii=False))
    if s.get("found"):
        check("断面要素齐全",
              all(k in s for k in ("h", "A", "B", "R", "v", "Fr", "Es", "z_b", "z_w")),
              sorted(s.keys())[:12])
    lines.append("")

    # 12. 只看规则通道也能算（模拟 Ollama 不可用）
    lines.append("=== 12. 不依赖模型的路径 ===")
    code, j, dt = solve("矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10个方，水深2米，算500米长")
    check("A1 走纯规则即可出图（无需模型）", j.get("ok") and dt < 3.0,
          "耗时 %.2fs" % dt)
    lines.append("")

    # 附加：presets 与页面
    lines.append("=== 附加检查 ===")
    code, p, _ = req("/api/presets")
    check("预置算例可用", len(p.get("presets") or []) >= 5, str(p.get("presets")))
    with urllib.request.urlopen(BASE + "/", timeout=10) as r:
        html = r.read().decode("utf-8", "replace")
    check("页面含合法性横幅", 'id="legal"' in html)
    check("页面含断面点击", "/api/section?x=" in html)

    lines.append("")
    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok_count, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok_count, total))


if __name__ == "__main__":
    main()
