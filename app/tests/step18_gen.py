# -*- coding: utf-8 -*-
"""示例算例与随机生成验收。

- 12 型 + 坡度组合 + 典型现象：每条都要能算出它声称的型号
- 随机生成：水跃/水跌样例必须真的出现水跃/水跌（由内核验证）
- 对话生成意图：说"给我一个水跃样例"要能出参数，且不改动当前会话
"""
import json
import os
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step18-gen.txt")
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


def solve_params(params):
    return req("/api/solve", {"overrides": params})


def main():
    req("/api/reset", {})

    # ---------- 1. 分组与条目数 ----------
    lines.append("=== 预设库 ===")
    code, p = req("/api/presets")
    groups = {g["name"]: g["items"] for g in (p.get("groups") or [])}
    check("有「12 型」分组", "12 型" in groups, list(groups))
    check("12 型齐全（矩形+梯形各 12）", len(groups.get("12 型", [])) >= 12,
          len(groups.get("12 型", [])))
    _lbl = [x["label"] for x in groups.get("12 型", [])]
    check("12 型同时覆盖矩形与梯形断面",
          any("矩形" in x for x in _lbl) and any("梯形" in x for x in _lbl),
          _lbl[:3])
    check("有坡度组合（缓缓/陡陡/缓陡/陡缓）", len(groups.get("坡度组合", [])) >= 4,
          [x["label"] for x in groups.get("坡度组合", [])])
    check("有典型现象（水跃/水跌）", len(groups.get("典型现象", [])) >= 3,
          [x["label"] for x in groups.get("典型现象", [])])
    # ---- 五类综合：必须是第一部分，且两种断面都有 ----
    first = (p.get("groups") or [{}])[0].get("name")
    check("「五类综合」排在第一位", first == "五类综合", first)
    cls_items = groups.get("五类综合", [])
    check("五类综合覆盖 M/S/C/H/A", len({x["key"].split("_")[1]
                                       for x in cls_items}) == 5,
          [x["label"] for x in cls_items])
    check("五类综合同时有矩形与梯形",
          any("矩形" in x["label"] for x in cls_items)
          and any("梯形" in x["label"] for x in cls_items),
          [x["label"] for x in cls_items])
    want_slope = {"M": "mild", "S": "steep", "C": "critical",
                  "H": "horizontal", "A": "adverse"}
    bad_cls = []
    trap_ok = 0
    for it in cls_items:
        code, pr = req("/api/preset?key=" + it["key"])
        if not pr.get("params"):
            bad_cls.append((it["key"], "取参数失败"))
            continue
        code, s = solve_params(pr["params"])
        if not s.get("ok"):
            bad_cls.append((it["key"], "计算失败"))
            continue
        letter = it["key"].split("_")[1][0]
        slopes = {r["slope_class"] for r in s["result"]["reaches"]}
        if want_slope[letter] not in slopes:
            bad_cls.append((it["key"], slopes))
        if it["key"].endswith("_T"):
            m = s["result"]["reaches"][0]["m"]
            if m and m > 0:
                trap_ok += 1
            else:
                bad_cls.append((it["key"], "梯形 m=%s" % m))
    check("五类综合各自坡型正确", not bad_cls, bad_cls[:3])
    check("梯形算例确实是梯形断面（m>0）", trap_ok == 5, trap_ok)
    # ---- 水跌两种成因都要在 ----
    labels = [x["label"] for x in groups.get("典型现象", [])]
    check("典型现象含坡折跌水与渠末跌水",
          any("坡折" in x for x in labels) and any("渠末" in x for x in labels), labels)
    lines.append("")

    # ---------- 2. 每条预设都要真的算出它说的型号 ----------
    lines.append("=== 逐条验证：算出的型号与标注一致 ===")
    all_items = [it for g in groups.values() for it in g]
    bad = []
    for it in all_items:
        code, pr = req("/api/preset?key=" + it["key"])
        if not pr.get("params"):
            bad.append((it["label"], "取参数失败"))
            continue
        code, s = solve_params(pr["params"])
        if not s.get("ok"):
            bad.append((it["label"], "计算失败"))
            continue
        got = [x["curve_type"] for x in s["result"]["segments"]]
        want = it.get("expect_types") or []
        if want and got[:len(want)] != want:
            bad.append((it["label"], "期望 %s 实得 %s" % (want, got)))
    check("%d 条预设全部复现" % len(all_items), not bad, bad[:3])
    lines.append("")

    # ---------- 3. 随机生成 ----------
    lines.append("=== 随机生成 ===")
    code, g1 = req("/api/generate?kind=jump")
    item = g1.get("item") or {}
    check("水跃样例成功", bool(item), g1.get("error"))
    if item:
        check("确实出现水跃", "jump" in (item.get("transitions") or []),
              item.get("transitions"))
        check("跃位落在渠内", item.get("jump_x") is not None
              and 0 <= item["jump_x"] <= sum(r["L"] for r in item["reaches"]),
              item.get("jump_x"))
        check("参数齐全（两段）", len(item.get("reaches") or []) == 2,
              [r["L"] for r in item.get("reaches") or []])
        lines.append("  水跃样例：%s，跃位 %.1f m" % (" → ".join(item["types"]),
                                                item["jump_x"]))
    code, g2 = req("/api/generate?kind=outfall")
    it2 = g2.get("item") or {}
    check("水跌样例成功", bool(it2), g2.get("error"))
    if it2:
        check("确实出现水跌", "outfall" in (it2.get("transitions") or []),
              it2.get("transitions"))
        lines.append("  水跌样例：%s" % " → ".join(it2["types"]))

    # 再生成一次，参数应当不同（随机）
    code, g3 = req("/api/generate?kind=jump")
    it3 = g3.get("item") or {}
    same = (it3.get("params") == item.get("params")) if it3 and item else True
    check("两次生成的参数不同（确实随机）", not same,
          "Q=%s vs %s" % ((item.get("params") or {}).get("Q"),
                          (it3.get("params") or {}).get("Q")))

    # 坡折水跌：缓坡转陡坡，坡折处临界控制
    code, g4 = req("/api/generate?kind=drop")
    it4 = g4.get("item") or {}
    check("坡折水跌样例成功", bool(it4), g4.get("error"))
    if it4:
        check("坡折水跌发生在坡折处", "critical" in (it4.get("transitions") or []),
              it4.get("transitions"))
        check("坡折水跌是两段（缓→陡）", len(it4.get("reaches") or []) == 2,
              [r["slope"] for r in it4.get("reaches") or []])
        lines.append("  坡折水跌样例：%s" % " → ".join(it4["types"]))
    lines.append("")

    # ---------- 4. 对话里要样例 ----------
    lines.append("=== 对话生成意图 ===")
    code, s0 = req("/api/state")
    before = json.dumps(s0.get("slots") or {}, sort_keys=True, ensure_ascii=False)
    code, t = req("/api/turn", {"text": "给我一个发生水跃的样例"})
    check("判为回答", t.get("act") == "answer", t.get("act"))
    check("走了生成器", "generator" in (t.get("channels") or []), t.get("channels"))
    hyp = t.get("hypothesis") or {}
    check("返回了可直接应用的参数", bool(hyp.get("slots")), list(hyp)[:3])
    check("试算结果里含两段", len(hyp.get("curves") or []) >= 2, hyp.get("curves"))
    code, s1 = req("/api/state")
    after = json.dumps(s1.get("slots") or {}, sort_keys=True, ensure_ascii=False)
    check("会话参数未被生成动作改动", before == after,
          "before=%s after=%s" % (before[:60], after[:60]))
    lines.append("")
    lines.append("  用户：给我一个发生水跃的样例")
    lines.append("  回复：" + (t.get("reply") or "").replace("\n", " ")[:150])
    lines.append("")

    # ---------- 5. 应用后真的能算 ----------
    lines.append("=== 应用生成结果 ===")
    if hyp.get("slots"):
        code, s = solve_params(hyp["slots"])
        check("应用后计算成功", s.get("ok"))
        if s.get("ok"):
            kinds = [x["kind"] for x in s["result"]["transitions"]]
            check("应用后仍含水跃", "jump" in kinds, kinds)
    lines.append("")

    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
