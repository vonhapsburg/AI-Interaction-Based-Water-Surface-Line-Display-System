# -*- coding: utf-8 -*-
"""对话体验验收：歧义确认、图↔对话互通、跨站拦截、限流配置。

对应反馈：
  1. 对话与纵剖面图要能互通（图上选点带进对话；回答能把图定位到现象处）
  2. 有歧义时当次就问清楚，不能按习惯猜完等用户二次纠正
  3. key 防护（只监听本机、拒绝跨站、可限流）
"""
import json
import os
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step19-dialogue.txt")
lines = []
ok = total = 0


def req(path, payload=None, headers=None, timeout=120):
    if payload is None:
        r = urllib.request.Request(BASE + path, headers=headers or {})
    else:
        r = urllib.request.Request(
            BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=dict({"Content-Type": "application/json"}, **(headers or {})),
            method="POST")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body or "{}")
        except Exception:
            return e.code, {"raw": body}


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


def main():
    req("/api/reset", {})

    # ---------- 歧义：当次就问 ----------
    lines.append("=== 歧义确认 ===")
    code, t = req("/api/turn",
                  {"text": "矩形渠，底宽5米，糙率0.015，坡度千分之一，流量10个方，水深2米，长600米"})
    amb = t.get("ambiguities") or []
    check("只说「水深2米」会提示歧义", len(amb) >= 1, [a.get("field") for a in amb])
    if amb:
        check("给出可点选的两种理解", len(amb[0].get("options") or []) == 2,
              [o["label"] for o in amb[0]["options"]])
        check("同时说明当前按哪种算",
              "当前" in (amb[0].get("question") or ""), amb[0].get("question"))
        lines.append("  " + amb[0]["question"])

    req("/api/reset", {})
    code, t2 = req("/api/turn",
                   {"text": "矩形渠，底宽5米，糙率0.015，坡度千分之一，流量10个方，渠末水深2米，长600米"})
    check("说明「渠末」后不再追问", len(t2.get("ambiguities") or []) == 0,
          t2.get("ambiguities"))
    lines.append("")

    # ---------- 图 → 对话 ----------
    lines.append("=== 图上选点 → 对话 ===")
    code, s = req("/api/solve", {"overrides": TWO})
    check("先算出两段结果", s.get("ok"), [x["curve_type"] for x in s["result"]["segments"]] if s.get("ok") else None)
    code, sec = req("/api/section?x=420")
    h_at = round(float(sec.get("h", 0)), 2) if sec.get("found") else None
    code, t3 = req("/api/turn", {"text": "这一点水深多少？为什么是急流？",
                                 "context": {"x": 420}})
    rp = t3.get("reply") or ""
    check("走回答通道", t3.get("act") == "answer", t3.get("act"))
    check("回答引用了该点位置", "420" in rp, rp[:60])
    check("回答用到了该点水深（来自内核）",
          h_at is not None and (str(h_at) in rp or ("%.1f" % h_at) in rp
                                or ("%.3f" % float(sec.get("h", 0))) in rp),
          "内核 h=%.3f" % float(sec.get("h", 0)))
    lines.append("  Q: 这一点水深多少？（传入 x=420）")
    lines.append("  A: " + rp.replace("\n", " ")[:150])
    lines.append("")

    # ---------- 对话 → 图 ----------
    lines.append("=== 对话 → 图定位 ===")
    code, pr = req("/api/preset?key=case_jump")
    code, sj = req("/api/solve", {"overrides": pr["params"]})
    check("载入水跃算例", sj.get("ok"))
    code, t4 = req("/api/turn", {"text": "水跃在什么位置？"})
    fx = t4.get("focus_x")
    check("回答给出了图上要跳转的位置", fx is not None, fx)
    L = sum(r["L"] for r in sj["result"]["reaches"]) if sj.get("ok") else 0
    check("位置在渠内", fx is not None and 0 <= fx <= L, "x=%s L=%s" % (fx, L))
    lines.append("  Q: 水跃在什么位置？ → focus_x=%s" % fx)
    lines.append("")

    # ---------- 跨站请求 ----------
    lines.append("=== key 防护 ===")
    code, j = req("/api/reset", {}, headers={"Origin": "http://evil.example.com"})
    check("拒绝其他站点的跨域请求", code == 403, code)
    code, j2 = req("/api/reset", {}, headers={"Origin": "http://127.0.0.1:8000"})
    check("本机页面的请求正常放行", code == 200, code)
    code, h = req("/api/health")
    check("健康信息里不含 key", "sk-" not in json.dumps(h, ensure_ascii=False),
          "已检查")
    lines.append("")

    # ---------- 出题 ----------
    lines.append("=== 按当前算例出题 ===")
    code, s2 = req("/api/solve", {"overrides": TWO})
    code, t5 = req("/api/turn", {"text": "请根据现有水面线试着出一些题目"})
    quiz = t5.get("quiz") or []
    check("识别为出题请求", t5.get("act") == "quiz", t5.get("act"))
    check("生成了多道题", len(quiz) >= 3, len(quiz))
    if quiz:
        check("每题都有参考答案", all(q.get("a") for q in quiz),
              [q.get("q") for q in quiz][:2])
        check("答案含内核算出的水深",
              any("h₀=" in (q.get("a") or "") for q in quiz), quiz[0].get("a"))
        check("带解析", any((q.get("why") or "") for q in quiz),
              (quiz[0].get("why") or "")[:40])
        lines.append("  Q1: " + quiz[0]["q"])
        lines.append("  答: " + quiz[0]["a"][:100])
    lines.append("")

    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
