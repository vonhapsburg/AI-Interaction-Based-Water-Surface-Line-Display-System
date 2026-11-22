# -*- coding: utf-8 -*-
"""会话记忆 + 历史回放验收（走真实 HTTP）。

对应《病因：模型没有"记忆"》第七节的逐条输入测试：
参数表保存在服务端，模型只做本句抽取。
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step12-memory.txt")
lines = []
ok = total = 0


def req(path, payload=None, timeout=300):
    if payload is None:
        r = urllib.request.Request(BASE + path)
    else:
        r = urllib.request.Request(BASE + path,
                                   data=json.dumps(payload).encode("utf-8"),
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


def check(label, cond, detail=""):
    global ok, total
    total += 1
    if cond:
        ok += 1
    lines.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def turn(text):
    return req("/api/turn", {"text": text, "use_llm": False})


def solve():
    return req("/api/solve", {})


def main():
    req("/api/reset", {})

    # ---------- 第一节：8 句逐条输入 ----------
    lines.append("=== 逐条输入（每句只带一个参数）===")
    steps = [
        ("矩形渠", {"section": "rectangular"}, None),
        ("底宽5米", {"b": 5.0}, None),
        ("糙率0.015", {"n": 0.015}, None),
        ("坡度千分之一", {"i": 0.001}, None),
        ("流量10个方", {"Q": 10.0}, None),
        ("渠末水深2米", {"h0": 2.0}, ["L"]),
        ("长500米", {"L": 500.0}, []),
    ]
    st = None
    for text, expect, expect_missing in steps:
        code, j, dt = turn(text)
        st = j
        okv = all(abs(float(j["slots"].get(k, -1e9)) - float(v)) < 1e-9
                  if not isinstance(v, str) else j["slots"].get(k) == v
                  for k, v in expect.items())
        check("「%s」→ %s" % (text, json.dumps(expect, ensure_ascii=False)),
              okv, "累计=%s 新增=%s" % (json.dumps(j["slots"], ensure_ascii=False),
                                       j["added"]))
        if expect_missing is not None:
            check("  此时仍缺 %s" % expect_missing, j["missing"] == expect_missing,
                  "missing=%s ready=%s" % (j["missing"], j["ready"]))

    check("8 句后参数齐备 ready=True", st["ready"] is True, "missing=%s" % st["missing"])

    code, j, dt = solve()
    curves = [s["curve_type"] for s in j["result"]["segments"]] if j.get("ok") else []
    check("自动计算 → M1", j.get("ok") and curves == ["M1"],
          "curves=%s status=%s" % (curves, j.get("result", {}).get("status")))
    lines.append("")

    # ---------- 第二节：跨轮修改 ----------
    lines.append("=== 跨轮修改（考察指代与覆盖）===")
    code, j, dt = turn("糙率改成0.02")
    check("「糙率改成0.02」→ n=0.02 且其他参数不动",
          abs(j["slots"]["n"] - 0.02) < 1e-9 and abs(j["slots"]["b"] - 5) < 1e-9,
          "slots=%s 警告=%s" % (json.dumps(j["slots"], ensure_ascii=False),
                                [w for w in j["warnings"] if "改为" in w]))
    check("  本句只新增 n 一个字段", j["added"] == ["n"], "added=%s" % j["added"])

    code, j, dt = solve()
    curves = [s["curve_type"] for s in j["result"]["segments"]] if j.get("ok") else []
    check("  重算成功（型序列 %s）" % curves, j.get("ok"), "status=%s"
          % (j.get("result", {}).get("status")))

    code, j, dt = turn("水深改成1米")
    check("「水深改成1米」→ h0=1", abs(j["slots"]["h0"] - 1) < 1e-9,
          "h0=%s" % j["slots"].get("h0"))

    code, j, dt = turn("改成梯形，边坡1比1.5")
    check("「改成梯形，边坡1比1.5」→ section/m 同轮更新",
          j["slots"].get("section") == "trapezoidal" and abs(j["slots"]["m"] - 1.5) < 1e-9,
          "section=%s m=%s" % (j["slots"].get("section"), j["slots"].get("m")))

    code, j, dt = turn("边坡改成2")
    check("「边坡改成2」→ m=2，b/n/i/Q/L 都没丢",
          abs(j["slots"]["m"] - 2) < 1e-9 and abs(j["slots"]["b"] - 5) < 1e-9
          and abs(j["slots"]["L"] - 500) < 1e-9,
          "slots=%s" % json.dumps(j["slots"], ensure_ascii=False))

    code, j, dt = turn("坡度取0")
    check("「坡度取0」→ i=0（平坡）", abs(j["slots"]["i"]) < 1e-12,
          "i=%s" % j["slots"].get("i"))
    code, j, dt = solve()
    curves = [s["curve_type"] for s in j["result"]["segments"]] if j.get("ok") else []
    check("  平坡重算 → H2", curves == ["H2"], "curves=%s" % curves)

    code, j, dt = turn("下游改成自由跌水")
    check("「下游改成自由跌水」→ downstream=free_outfall",
          j["slots"].get("downstream") == "free_outfall",
          "downstream=%s" % j["slots"].get("downstream"))
    lines.append("")

    # ---------- 第三节：历史记录 ----------
    lines.append("=== 历史记录（SQLite runs.db）===")
    code, j, dt = solve()
    check("再算一次并落库", j.get("ok") and j.get("run_id"),
          "run_id=%s" % j.get("run_id"))

    code, h, dt = req("/api/history?limit=20")
    runs = h.get("runs") or []
    check("历史列表非空", len(runs) >= 4, "%d 条" % len(runs))
    check("每条都有时间/曲线/参数",
          all(r.get("ts") and "curves" in r and r.get("slots") for r in runs),
          json.dumps(runs[0], ensure_ascii=False)[:180] if runs else "空")

    # 回放最早那条（应是 M1）
    target = runs[-1]
    code, j, dt = req("/api/history/%d/load" % target["id"], {})
    curves = [s["curve_type"] for s in j["result"]["segments"]] if j.get("ok") else []
    check("回放历史 #%d 成功" % target["id"], j.get("ok"),
          "curves=%s" % curves)
    # 逐键比对（历史既可能来自对话，也可能来自表单，键集合不固定）
    same = all(j["slots"].get(k) == v for k, v in target["slots"].items())
    check("  回放后会话状态被还原为那次参数", same,
          "回放=%s 记录=%s" % (json.dumps(j["slots"], ensure_ascii=False)[:90],
                              json.dumps(target["slots"], ensure_ascii=False)[:90]))

    # ---------- 第四节：清空 ----------
    lines.append("")
    lines.append("=== 清空会话 ===")
    code, j, dt = req("/api/reset", {})
    code, s, dt = req("/api/state")
    check("清空后槽位为空", s.get("slots") == {}, "slots=%s" % s.get("slots"))
    check("清空后 missing 覆盖全部必填", len(s.get("missing") or []) >= 5,
          "missing=%s" % s.get("missing"))
    check("清空不影响历史", len((req("/api/history")[1].get("runs") or [])) >= 4,
          "历史仍在")

    lines.append("")
    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
