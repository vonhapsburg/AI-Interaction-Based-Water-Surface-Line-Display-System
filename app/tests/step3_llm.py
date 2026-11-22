# -*- coding: utf-8 -*-
"""第 3 步验收 + qwen 模型调试：结构化输出能力逐条打分。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent import llm  # noqa: E402

# (编号, 输入, 期望字典, 说明)
CASES = [
    ("A", "矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10个方，水深2米，算500米长",
     {"section": "rectangular", "b": 5, "m": 0, "n": 0.015, "i": 0.001, "Q": 10, "h0": 2, "L": 500},
     "文档示例句，应有 8 个值"),
    ("B", "五米宽的梯形渠，边坡1比1.5，糙率十五，坡度千分之一，流量十个方，水深两米，算三百米",
     {"section": "trapezoidal", "b": 5, "m": 1.5, "n": 0.015, "i": 0.001, "Q": 10, "h0": 2, "L": 300},
     "中文数字 + 边坡比值，文档重点用例"),
    ("C", "帮我算个水面线",
     {"section": None, "b": -1, "m": -1, "n": -1, "i": -1, "Q": -1, "h0": -1, "L": -1},
     "信息不足，必须全部 -1，不许编造"),
    ("D", "矩形渠宽3米，糙率0.3，底宽5米，坡度百分之五，流量20方，渠末水位给定，水深1.8米，长200米",
     {"section": "rectangular", "n": 0.3, "i": 0.05, "Q": 20, "h0": 1.8, "L": 200, "downstream": "given_zw"},
     "百分之五 = 0.05；给定水位；显式小数不能被改量级"),
    ("E", "梯形渠道，底宽2.5米，边坡1:2，糙率0.025，底坡0.0015，流量1.2立方米每秒，渠末正常水深，渠长1.2公里",
     {"section": "trapezoidal", "b": 2.5, "m": 2, "n": 0.025, "i": 0.0015, "Q": 1.2,
      "h0": -1, "downstream": "normal_depth", "L": 1200},
     "小数、1:2、公里换算、正常水深（h0 必须留空）"),
    ("F", "底坡1/1000，流量0.5个方",
     {"i": 0.001, "Q": 0.5},
     "1/1000 写法 与 0.5 小数流量"),
]

FIELDS = ["b", "m", "n", "i", "Q", "h0", "L"]


def main():
    lines = []
    ok_all = True
    lines.append("模型：%s    地址：%s" % (llm.MODEL, llm.OLLAMA_URL))
    lines.append("health：%s" % llm.health())
    lines.append("")

    for cid, text, expect, note in CASES:
        t0 = time.time()
        r = llm.ask(text, timeout=180)
        dt = time.time() - t0

        lines.append("=" * 70)
        lines.append("[%s] %s" % (cid, text))
        lines.append("期望要点：%s" % json.dumps(expect, ensure_ascii=False))
        lines.append("耗时：%.2fs" % dt)

        if "error" in r:
            ok_all = False
            lines.append("!! 调用失败：%s" % r["error"])
            lines.append("")
            continue

        raw = r.get("raw", {})
        lines.append("原始 JSON：%s" % json.dumps(raw, ensure_ascii=False))
        lines.append("reason：%s" % r.get("reason", ""))
        lines.append("清洗后 slots：%s" % json.dumps(r["slots"], ensure_ascii=False))

        diffs = []
        for k, v in expect.items():
            if k == "section":
                got = r["slots"].get("section")
                if got != v:
                    diffs.append("section 期望 %s 实得 %s" % (v, got))
            elif k == "downstream":
                got = r["slots"].get("downstream")
                if got != v:
                    diffs.append("downstream 期望 %s 实得 %s" % (v, got))
            else:
                got = raw.get(k)
                if got is None:
                    diffs.append("%s 未出现在 JSON" % k)
                elif abs(float(got) - float(v)) > 1e-9:
                    diffs.append("%s 期望 %s 实得 %s" % (k, v, got))
        # 额外检查：不该编造字段
        for k in FIELDS:
            if k in expect:
                continue
            got = raw.get(k)
            if expect.get("section") == "unknown" and got not in (-1, None):
                diffs.append("%s 不应有值，实得 %s（编造）" % (k, got))

        if diffs:
            ok_all = False
            lines.append("判定：不通过")
            for d in diffs:
                lines.append("   - " + d)
        else:
            lines.append("判定：通过")
        lines.append("")

    lines.append("=" * 70)
    lines.append("总体：%s" % ("全部通过" if ok_all else "存在不通过项"))

    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step3-llm.txt")
    open(target, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", target)


if __name__ == "__main__":
    main()
