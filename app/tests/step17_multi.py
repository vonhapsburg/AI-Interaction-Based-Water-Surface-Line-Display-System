# -*- coding: utf-8 -*-
"""多渠段交互验收：假设问句不改参数、按段修改只动那一段。

对应反馈："对话里问多渠段问题，参数表单和水面线图表示不出来多渠段"。
"""
import json
import os
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step17-multi.txt")
lines = []
ok = total = 0


def req(path, payload=None, timeout=180):
    if payload is None:
        r = urllib.request.Request(BASE + path)
    else:
        r = urllib.request.Request(
            BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")


def check(label, cond, detail=""):
    global ok, total
    total += 1
    if cond:
        ok += 1
    lines.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


TWO = {"Q": 10,
       "reaches": [{"L": 300, "i": 0.001, "b": 5, "m": 0, "n": 0.015},
                   {"L": 300, "i": 0.020, "b": 5, "m": 0, "n": 0.015}],
       "downstream": "free_outfall", "upstream": "none"}


def state_reaches():
    code, s = req("/api/state")
    return s.get("slots", {}).get("reaches") or []


def main():
    req("/api/reset", {})
    code, j = req("/api/solve", {"overrides": TWO})
    check("建立两段渠道", j.get("ok") and len(j["result"]["reaches"]) == 2,
          "段数=%s 型=%s" % (len(j["result"]["reaches"]) if j.get("ok") else None,
                            [s["curve_type"] for s in j["result"]["segments"]] if j.get("ok") else None))
    lines.append("")

    # ---------- 假设问句：不能改参数 ----------
    lines.append("=== 假设问句 ===")
    code, t = req("/api/turn", {"text": "如果把第1段底坡改成0.02，会怎么样？"})
    check("判为回答", t.get("act") == "answer", t.get("act"))
    check("给出试算结果", bool(t.get("hypothesis")),
          (t.get("hypothesis") or {}).get("curves"))
    rs = state_reaches()
    check("会话仍是两段", len(rs) == 2, [r["L"] for r in rs])
    check("第1段底坡没被改掉", abs(float(rs[0]["i"]) - 0.001) < 1e-12, rs[0]["i"])
    check("第2段底坡没被动", abs(float(rs[1]["i"]) - 0.020) < 1e-12, rs[1]["i"])
    reply = t.get("reply") or ""
    check("回答里说明了是假设", any(k in reply for k in ("假设", "试算", "当前参数")),
          reply[:80])
    lines.append("  Q: 如果把第1段底坡改成0.02，会怎么样？")
    lines.append("  A: " + reply.replace("\n", " ")[:150])
    lines.append("")

    # ---------- 指令式：只改点名的段 ----------
    lines.append("=== 按段修改 ===")
    code, t = req("/api/turn", {"text": "第2段坡度改成0.005"})
    rs = state_reaches()
    check("仍是两段", len(rs) == 2, [r["L"] for r in rs])
    check("第2段底坡改成 0.005", abs(float(rs[1]["i"]) - 0.005) < 1e-12, rs[1]["i"])
    check("第1段底坡保持 0.001", abs(float(rs[0]["i"]) - 0.001) < 1e-12, rs[0]["i"])
    check("提示写明了改哪一段",
          any("第2段" in w for w in (t.get("warnings") or [])), t.get("warnings"))

    code, t = req("/api/turn", {"text": "第1段糙率改成0.02"})
    rs = state_reaches()
    check("第1段糙率改成 0.02", abs(float(rs[0]["n"]) - 0.02) < 1e-12, rs[0]["n"])
    check("第2段糙率保持 0.015", abs(float(rs[1]["n"]) - 0.015) < 1e-12, rs[1]["n"])
    lines.append("")

    # ---------- 计算与图形数据仍含两段 ----------
    lines.append("=== 结果仍为两段 ===")
    code, s = req("/api/solve", {})
    check("计算成功", s.get("ok"))
    if s.get("ok"):
        R = s["result"]
        check("结果里两段都在", len(R["reaches"]) == 2,
              [(r["index"], round(r["i"], 4)) for r in R["reaches"]])
        check("曲线覆盖两段",
              len({seg["reach_index"] for seg in R["segments"]}) == 2,
              [seg["curve_type"] for seg in R["segments"]])
    lines.append("")

    # ---------- 完整描述才会重建成单段（原有规则要保留） ----------
    lines.append("=== 完整描述 → 单段重建 ===")
    code, t = req("/api/turn", {"text": "矩形渠，底宽5米，糙率0.015，坡度千分之一，流量10个方，水深2米，长600米"})
    rs = state_reaches()
    # 单段渠道以顶层 b/n/i/L 形式保存（不写 reaches 数组），因此 0 或 1 都算正确
    check("按单段重建", (t.get("act") == "compute") and (len(rs) <= 1),
          "act=%s reaches=%d" % (t.get("act"), len(rs)))
    code, s = req("/api/solve", {})
    check("计算结果只有一段",
          s.get("ok") and len(s["result"]["reaches"]) == 1,
          len(s["result"]["reaches"]) if s.get("ok") else None)
    check("明确告知用户清除了多段",
          any("单段" in w for w in (t.get("warnings") or [])), t.get("warnings"))
    lines.append("")

    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
