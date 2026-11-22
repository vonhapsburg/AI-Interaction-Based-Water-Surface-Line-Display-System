# -*- coding: utf-8 -*-
"""合法性 A~I 分类 + 内核联调验收（进程内，不走 HTTP）。

对照《输入合法性分类总表》逐条打分：先看槽位是否解析对，再看合法判定，
最后能算的把内核跑一遍看曲线型。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent.merge import merge                 # noqa: E402
from agent import adapter, legality           # noqa: E402
import gvf_core                               # noqa: E402

# (编号, 输入, 期望槽位, 期望合法码, 期望曲线(可为None), 说明)
CASES = [
    # ---------- A 类 ----------
    ("A1", "矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10个方，水深2米，算500米长",
     {"b": 5, "n": 0.015, "i": 0.001, "Q": 10, "h0": 2, "L": 500, "section": "rectangular"},
     "B", "M1", "文档基准例（含千分之一，按自洽口径归 B）"),
    ("A2", "矩形断面，b=5m，n=0.015，i=0.001，Q=10，下游水深2m，L=600m",
     {"b": 5, "n": 0.015, "i": 0.001, "Q": 10, "h0": 2, "L": 600, "section": "rectangular"},
     "A", "M1", "符号表达式"),
    ("A3", "梯形渠，底宽6米，边坡1比1.5，糙率0.022，坡度0.0008，流量12，水深2.2米，长800米",
     {"b": 6, "m": 1.5, "n": 0.022, "i": 0.0008, "Q": 12, "h0": 2.2, "L": 800,
      "section": "trapezoidal"},
     "A", None, "梯形断面"),
    ("A4", "底宽8米矩形渠，糙率0.02，坡降0.002，流量15立方米每秒，渠末自由跌水，长1000米",
     {"b": 8, "n": 0.02, "i": 0.002, "Q": 15, "L": 1000, "section": "rectangular"},
     "C", "M2", "自由跌水按临界控制"),
    ("A5", "宽5米的矩形渠，n取0.015，i取0.001，Q为10，渠长600m，下游水位1.5m",
     {"b": 5, "n": 0.015, "i": 0.001, "Q": 10, "L": 600, "section": "rectangular"},
     "C", None, "给定水位（缺渠底高程要提示）"),
    ("A6", "梯形断面 b=5 m=2 n=0.025 i=0.003 Q=20 h0=1.8 L=400",
     {"b": 5, "m": 2, "n": 0.025, "i": 0.003, "Q": 20, "h0": 1.8, "L": 400,
      "section": "trapezoidal"},
     "A", None, "全符号"),
    ("A7", "矩形明渠，底宽4米，糙率0.013，底坡0.005，流量8个方，末端水深0.6米，算300米",
     {"b": 4, "n": 0.013, "i": 0.005, "Q": 8, "h0": 0.6, "L": 300,
      "section": "rectangular"},
     "A", None, "末端水深"),
    ("A8", "Q=25, b=6, n=0.014, i=0.0009, 下游水深3m, 长1200m, 矩形",
     {"b": 6, "n": 0.014, "i": 0.0009, "Q": 25, "h0": 3, "L": 1200,
      "section": "rectangular"},
     "A", None, "英文混排"),
    ("A9", "陡坡矩形渠，底宽5米，糙率0.015，坡度0.02，流量10，渠首水深0.3米，长400米",
     {"b": 5, "n": 0.015, "i": 0.02, "Q": 10, "h0": 0.3, "L": 400,
      "section": "rectangular"},
     "C", None, "渠首水深 → 上游控制"),
    ("A10", "平坡渠，底宽5米，糙率0.015，坡度0，流量10，水深1.2米，长200米",
     {"b": 5, "n": 0.015, "i": 0, "Q": 10, "h0": 1.2, "L": 200},
     "A", "H2", "平坡（断面由 adapter 默认矩形）"),

    # ---------- B 类 ----------
    ("B1", "五米宽的渠，流量十个方，坡度千分之一，水深两米，糙率零点零一五，长五百米",
     {"b": 5, "n": 0.015, "i": 0.001, "Q": 10, "h0": 2, "L": 500},
     "B", None, "全中文数字"),
    ("B2", "矩形渠，底宽5m，糙率15‰，坡降1‰，流量10 m³/s，水深2米，长600米",
     {"b": 5, "n": 0.015, "i": 0.001, "Q": 10, "L": 600}, "B", None, "千分号"),
    ("B3", "矩形渠，宽500公分，流量10000升每秒，糙率0.015，坡度0.001，水深2米，长600米",
     {"b": 5, "Q": 10, "n": 0.015, "i": 0.001, "L": 600}, "B", None, "单位量级差 1000 倍"),
    ("B4", "矩形渠，底宽5米，坡度为百分之零点一，糙率0.015，流量10个方，水深2米，长600米",
     {"i": 0.001}, "B", None, "百分之零点一"),
    ("B5", "矩形渠，底宽0.005公里，长0.5公里，糙率0.015，坡度千分之一，流量10个方，水深2米",
     {"b": 5, "L": 500}, "B", None, "公里换算"),
    ("B6", "矩形渠，底宽5米，糙率0.015，底坡1/1000，流量10，水深2米，长600米",
     {"i": 0.001}, "B", None, "分数式"),
    ("B7", "矩形渠，底宽5米，坡度万分之五，糙率0.02，流量10个方，水深2米，长600米",
     {"i": 0.0005}, "B", None, "万分之"),
    ("B8", "梯形渠，底宽6米，边坡1:2，流量12个方，水深2米，坡降0.8‰，糙率0.022，长800米",
     {"m": 2, "i": 0.0008}, "B", None, "边坡写法+千分号"),
    ("B9", "矩形渠，底宽5米，坡度0.05%，流量十个立方，糙率0.015，水深2米，长600米",
     {"i": 0.0005, "Q": 10}, "B", None, "百分号"),
    ("B10", "矩形渠，底宽5米，水深一米二，算三百米，糙率0.015，坡度千分之一，流量10个方",
     {"h0": 1.2, "L": 300}, "B", None, "小数口语"),
    ("B11", "底宽5m，Q是10方每秒，i=0.001，n=0.015，末端水深2m，L=500m",
     {"b": 5, "Q": 10, "i": 0.001, "n": 0.015, "h0": 2, "L": 500}, "A", None,
     "全阿拉伯数字符号"),
    ("B12", "我要一条五米宽的矩形渠，过十个方的水，坡降千分之一，水深两米，长五百米，糙率取0.015",
     {"b": 5, "Q": 10, "i": 0.001, "h0": 2, "L": 500, "n": 0.015}, "B", None, "语序打乱"),

    # ---------- C 类 ----------
    ("C8", "矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10个方，长500米，下游水深2米",
     {"h0": 2}, "B", None, "下游水深（含千分之一，归 B）"),

    # ---------- E 类（不完整）----------
    ("E1", "帮我算个水面线", {}, "E", None, "全缺"),
    ("E2", "矩形渠，底宽5米", {}, "E", None, "只给底宽"),
    ("E3", "梯形渠，边坡1比1.5，流量10", {}, "E", None, "缺底宽"),
    ("E4", "底宽5米，糙率0.015，坡度0.001，流量10", {}, "E", None, "缺水深与渠长"),
    ("E7", "底宽5米，长500米，其余你看着定", {}, "E", None, "不允许默认"),

    # ---------- F 类（主题越界）----------
    ("F1", "帮我算管道水锤压力", {}, "F", None, "有压瞬变"),
    ("F3", "洪水演进过程线", {}, "F", None, "非恒定流"),
    ("F5", "圆形排水管满管流水深", {}, "F", None, "圆形断面"),
    ("F7", "非棱柱体收缩段水面线", {}, "F", None, "非棱柱"),
    ("F10", "渠道边坡稳定分析", {}, "F", None, "岩土"),

    # ---------- G 类（参数越界）----------
    ("G1", "矩形渠，底宽5米，糙率0.5，底坡千分之一，流量10，水深2米，长500米",
     {}, "G", None, "n>0.30"),
    ("G3", "梯形渠，底宽5米，边坡1:5，糙率0.015，底坡千分之一，流量10，水深2米，长500米",
     {}, "G", None, "m>3"),
    ("G5", "矩形渠，底宽5米，糙率0.015，底坡千分之一，流量0，水深2米，长500米",
     {}, "G", None, "Q=0"),
    ("G7", "矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10，水深2米，长0米",
     {}, "G", None, "L=0"),

    # ---------- H 类（自相矛盾）----------
    ("H1", "矩形渠，边坡1比1.5，底宽5米，糙率0.015，底坡0.001，流量10，水深2米，长500米",
     {}, "H", None, "矩形边坡→按矩形处理 m=0"),
    ("H3", "平坡渠，底宽5米，糙率0.015，坡度0，流量10，长500米，下游按正常水深",
     {}, "H", None, "平坡无正常水深"),

    # ---------- I 类（无法解析）----------
    ("I2", "????", {}, "I", None, "纯符号"),
    ("I6", "我想算水面线。" * 90, {}, "I", None, "超过 500 字"),
]


def main():
    lines = []
    n_pass = n_fail = 0
    fails = []

    for cid, text, exp_slots, exp_code, exp_curve, note in CASES:
        use_llm = False           # 全部用规则通道，保证可重复、快
        m = merge(text, use_llm=use_llm)
        a = adapter.build(m)
        lg = legality.classify(text, m, a)

        bad = []
        if exp_code and lg["code"] != exp_code:
            bad.append("合法码 期望 %s 实得 %s（%s）" % (exp_code, lg["code"], lg["label"]))
        for k, v in exp_slots.items():
            got = (m.get("slots") or {}).get(k)
            if got is None:
                bad.append("槽位 %s 缺失（期望 %s）" % (k, v))
            elif isinstance(v, str):
                if got != v:
                    bad.append("槽位 %s 期望 %s 实得 %s" % (k, v, got))
            else:
                try:
                    if abs(float(got) - float(v)) > 1e-9:
                        bad.append("槽位 %s 期望 %s 实得 %s" % (k, v, got))
                except (TypeError, ValueError):
                    bad.append("槽位 %s 期望 %s 实得 %s" % (k, v, got))

        curve = None
        if a["params"] is not None:
            try:
                res = gvf_core.solve(a["params"])
                curve = [s.curve_type for s in res.segments]
                if exp_curve and exp_curve not in curve:
                    bad.append("曲线 期望含 %s 实得 %s" % (exp_curve, curve))
            except Exception as e:
                bad.append("内核计算异常 %s: %s" % (type(e).__name__, e))

        if bad:
            n_fail += 1
            fails.append(cid)
        else:
            n_pass += 1

        lines.append("=" * 68)
        lines.append("[%s] %s" % (cid, text[:70] + ("…" if len(text) > 70 else "")))
        lines.append("考点：%s" % note)
        lines.append("判定：%s / %s / %s" % (lg["code"], lg["verdict"], lg["label"]))
        if lg["reasons"]:
            lines.append("理由：%s" % "；".join(lg["reasons"]))
        if lg["hints"]:
            lines.append("提示：%s" % "；".join(lg["hints"]))
        lines.append("missing=%s  violations=%s"
                     % (json.dumps(a["missing"], ensure_ascii=False),
                        json.dumps([v["field"] for v in a["violations"]], ensure_ascii=False)))
        if curve is not None:
            lines.append("曲线：%s" % curve)
        if bad:
            lines.append("❌ 不通过：")
            for b in bad:
                lines.append("   - " + b)
        else:
            lines.append("✅ 通过")

    lines.append("")
    lines.append("=" * 68)
    lines.append("通过 %d / %d" % (n_pass, n_pass + n_fail))
    if fails:
        lines.append("未通过：%s" % ", ".join(fails))

    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step10-legality.txt")
    open(target, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", target)
    print("pass %d / %d" % (n_pass, n_pass + n_fail))


if __name__ == "__main__":
    main()
