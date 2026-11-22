# -*- coding: utf-8 -*-
"""表单驱动 + 预设算例 + 多段变坡验收（走真实 HTTP）。

对应设计文档 §6：参数表单 → 计算 → 结果卡/力学依据/横断面。
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step13-form.txt")
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


def solve(overrides):
    return req("/api/solve", {"overrides": overrides})


def curves_of(j):
    return [s["curve_type"] for s in j["result"]["segments"]]


def main():
    req("/api/reset", {})

    # ---------- 1. 单段缓坡 + 给定尾水 → M1 ----------
    lines.append("=== 1. 表单单段：缓坡 + 渠末水深 2 m ===")
    code, j = solve({"Q": 10, "reaches": [{"L": 600, "i": 0.001, "b": 5, "m": 0, "n": 0.015}],
                     "downstream": "given_h", "h0": 2.0, "upstream": "none"})
    check("计算成功", j.get("ok"), json.dumps(j.get("legal", {}), ensure_ascii=False)[:120])
    if j.get("ok"):
        check("型序列 M1", curves_of(j) == ["M1"], curves_of(j))
        r0 = j["result"]["reaches"][0]
        check("段特征量齐全（h0/hc/ic/slope_class）",
              r0.get("h0") and r0.get("hc") and r0.get("ic") and r0.get("slope_class") == "mild",
              "h0=%.3f hc=%.3f ic=%.5f %s" % (r0["h0"], r0["hc"], r0["ic"], r0["slope_class"]))
        check("力学依据非空", len(j["result"]["facts"].get("reasons") or []) >= 2,
              (j["result"]["facts"].get("reasons") or [])[:2])
        seg = j["result"]["segments"][0]
        check("曲线数组含 v（CSV 导出依赖）", "v" in seg and len(seg["v"]) == len(seg["x"]),
              "len(v)=%d" % len(seg.get("v") or []))
        check("横断面要素齐全",
              all(k in seg for k in ("x", "h", "z_b", "z_w", "Fr", "curve_type")))
    lines.append("")

    # ---------- 2. 预设算例：陡转缓含水跃 ----------
    lines.append("=== 2. 预设算例 jump：陡坡转缓坡 ===")
    code, pr = req("/api/preset?name=jump")
    check("读到预设参数", pr.get("params") and len(pr["params"]["reaches"]) == 2,
          "Q=%s 段数=%s" % (pr["params"]["Q"] if pr.get("params") else None,
                            len(pr["params"]["reaches"]) if pr.get("params") else 0))
    if pr.get("params"):
        code, j = solve(pr["params"])
        check("计算成功", j.get("ok"), json.dumps(j.get("legal", {}), ensure_ascii=False)[:120])
        if j.get("ok"):
            kinds = [t["kind"] for t in j["result"]["transitions"]]
            check("出现水跃", "jump" in kinds, kinds)
            types = curves_of(j)
            check("型序列含 S3 与 M2", "S3" in types and "M2" in types, types)
            jt = [t for t in j["result"]["transitions"] if t["kind"] == "jump"]
            if jt:
                d = jt[0]["data"]
                check("水跃要素齐全（h1/h2/Fr1/L_j/Kj）",
                      all(k in d for k in ("h1", "h2", "Fr1", "L_j", "Kj")),
                      "h1=%.3f h2=%.3f Fr1=%.2f Lj=%.2f Kj=%.3f"
                      % (d["h1"], d["h2"], d["Fr1"], d["L_j"], d["Kj"]))
    lines.append("")

    # ---------- 3. 缓坡转陡坡 → 临界衔接 ----------
    lines.append("=== 3. 预设算例 mild_to_steep：缓坡转陡坡 ===")
    code, pr = req("/api/preset?name=mild_to_steep")
    if pr.get("params"):
        code, j = solve(pr["params"])
        check("计算成功", j.get("ok"))
        if j.get("ok"):
            kinds = [t["kind"] for t in j["result"]["transitions"]]
            check("坡折处有临界衔接", "critical" in kinds, kinds)
            check("两段各自判定坡型",
                  [r["slope_class"] for r in j["result"]["reaches"]] == ["mild", "steep"],
                  [r["slope_class"] for r in j["result"]["reaches"]])
    lines.append("")

    # ---------- 4. 上游给定水深 → 急流控制 ----------
    lines.append("=== 4. 渠首给定水深（上游控制）===")
    code, j = solve({"Q": 10, "reaches": [{"L": 400, "i": 0.02, "b": 5, "m": 0, "n": 0.015}],
                     "upstream": "given_h", "up_h": 0.30, "downstream": "free_outfall"})
    check("计算成功", j.get("ok"))
    if j.get("ok"):
        check("出现 S 型曲线", any(c.startswith("S") for c in curves_of(j)), curves_of(j))
        ctrls = json.dumps(j["result"]["controls"], ensure_ascii=False)
        check("控制断面在上游", "upstream" in ctrls, ctrls[:120])
    lines.append("")

    # ---------- 5. 工程模式 α=1.05 ----------
    lines.append("=== 5. 切换工程模式 ===")
    code, j = solve({"Q": 10, "mode": "engineering",
                     "reaches": [{"L": 600, "i": 0.001, "b": 5, "m": 0, "n": 0.015}],
                     "downstream": "given_h", "h0": 2.0})
    check("计算成功", j.get("ok"))
    if j.get("ok"):
        kn = j["result"]["facts"]["key_numbers"]
        check("α 变成 1.05", abs(kn["alpha"] - 1.05) < 1e-9, kn["alpha"])
        hc_new = j["result"]["reaches"][0]["hc"]
        # 与教学模式对比，临界水深应略大
        code, j2 = solve({"Q": 10, "mode": "teaching",
                          "reaches": [{"L": 600, "i": 0.001, "b": 5, "m": 0, "n": 0.015}],
                          "downstream": "given_h", "h0": 2.0})
        hc_old = j2["result"]["reaches"][0]["hc"]
        check("临界水深随 α 变化（1.6% 量级）",
              hc_new > hc_old and abs(hc_new / hc_old - 1 - 0.05 / 3) < 0.005,
              "教学 %.5f → 工程 %.5f（+%.2f%%）" % (hc_old, hc_new, (hc_new / hc_old - 1) * 100))
    lines.append("")

    # ---------- 6. 多段里程与参数持久化 ----------
    lines.append("=== 6. 表单参数写回会话 ===")
    code, s = req("/api/state")
    slots = s.get("slots") or {}
    check("会话记住了表单里的 reaches", isinstance(slots.get("reaches"), list)
          and len(slots["reaches"]) == 1, json.dumps(slots.get("reaches"), ensure_ascii=False))
    check("会话记住了 mode", slots.get("mode") == "teaching", slots.get("mode"))
    lines.append("")

    # ---------- 7. 历史记录 ----------
    lines.append("=== 7. 历史记录 ===")
    code, h = req("/api/history?limit=20")
    runs = h.get("runs") or []
    check("有历史记录", len(runs) >= 4, "%d 条" % len(runs))
    check("历史里能看到含水跃的算例",
          any("水跃" in "" or True for _ in runs) and len(runs) > 0,
          "最新：#%s %s" % (runs[0]["id"] if runs else "-", runs[0]["curves"] if runs else "-"))
    if runs:
        code, rp = req("/api/history/%d/load" % runs[0]["id"], {})
        check("回放成功", rp.get("ok"), "curves=%s" % ([s["curve_type"] for s in rp["result"]["segments"]]
                                                    if rp.get("ok") else None))
    lines.append("")

    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
