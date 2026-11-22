# -*- coding: utf-8 -*-
"""第 2 步验收：规则解析器。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.parser import parse  # noqa: E402

CASES = [
    "矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10个方，渠末自由跌水，水深2米，算500米长",
    "五米宽的梯形渠，边坡1比1.5，糙率十五，坡度千分之一，流量十个方，水深两米，算三百米",
]

out = []
for i, text in enumerate(CASES, 1):
    r = parse(text)
    out.append("### 用例 %d" % i)
    out.append("输入： " + text)
    out.append("normalized： " + r["normalized"])
    out.append("slots： " + json.dumps(r["slots"], ensure_ascii=False))
    out.append("missing： " + json.dumps(r["missing"], ensure_ascii=False))
    out.append("warnings： " + json.dumps(r["warnings"], ensure_ascii=False))
    out.append("")

target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step2-parser.txt")
open(target, "w", encoding="utf-8").write("\n".join(out))
print("written", target)
