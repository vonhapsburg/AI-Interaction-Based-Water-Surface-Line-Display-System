# -*- coding: utf-8 -*-
"""稳健性探针：专挑「规则解析器认不出」的句子，看 qwen 补位质量。

这些句子才是模型真正的战场——规则能认的句子根本不会叫模型。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.parser import parse as rule_parse  # noqa: E402
from agent import llm  # noqa: E402

# (编号, 输入, 期望, 说明)
CASES = [
    ("R1", "水深和渠宽都没写单位，按米算：宽3，水深1.5，糙率0.02，坡度1/200，流量5，长1000",
     {"b": 3, "h0": 1.5, "n": 0.02, "i": 0.005, "Q": 5, "L": 1000},
     "全部省略单位"),
    ("R2", "渠道底宽2米，边坡1:1.5，曼宁系数0.022，i=0.0008，Q=3.5，h0=1.1，L=800",
     {"b": 2, "m": 1.5, "n": 0.022, "i": 0.0008, "Q": 3.5, "h0": 1.1, "L": 800},
     "符号写法（i= Q= h0=）"),
    ("R3", "我想算矩形渠水面线，宽度四米，水深一米二，糙率零点零二，坡降万分之五，流量三方每秒，长度五百米",
     {"b": 4, "h0": 1.2, "n": 0.02, "i": 0.0005, "Q": 3, "L": 500},
     "万分之五 + 一米二 + 五百米"),
    ("R4", "梯形断面 底宽6m 边坡1比2.5 糙率0.03 底坡0.002 流量15m3/s 水深2.2m 长1200m",
     {"b": 6, "m": 2.5, "n": 0.03, "i": 0.002, "Q": 15, "h0": 2.2, "L": 1200},
     "英文单位混排"),
    ("R5", "渠末水位3.5米，其他参数：宽5米，梯形，边坡1:1，糙率0.025，底坡千分之一，流量12，长度900",
     {"b": 5, "m": 1, "n": 0.025, "i": 0.001, "Q": 12, "L": 900,
      "downstream": "given_zw"},
     "给定水位边界"),
    ("R6", "渠长2公里，底宽8米，梯形边坡1比1.5，糙率0.018，比降0.0005，流量20，控制水深2.5",
     {"b": 8, "m": 1.5, "n": 0.018, "i": 0.0005, "Q": 20, "h0": 2.5, "L": 2000},
     "公里换算 + 控制水深"),
]

FIELDS = ("b", "m", "n", "i", "Q", "h0", "L")


def main():
    lines = []
    total_fields = hit_fields = 0
    used_llm = 0

    for cid, text, expect, note in CASES:
        rp = rule_parse(text)
        missing_before = rp["missing"]
        t0 = time.time()
        r = llm.ask(text, timeout=180)
        dt = time.time() - t0
        if missing_before:
            used_llm += 1

        lines.append("=" * 70)
        lines.append("[%s] %s" % (cid, text))
        lines.append("考点：%s" % note)
        lines.append("规则通道先缺：%s" % json.dumps(missing_before, ensure_ascii=False))
        lines.append("耗时：%.2fs" % dt)
        if "error" in r:
            lines.append("!! 模型失败：%s" % r["error"])
            lines.append("")
            continue

        raw = r.get("raw", {})
        lines.append("模型原始：%s" % json.dumps(raw, ensure_ascii=False))

        bad = []
        for k, v in expect.items():
            if k == "downstream":
                got = raw.get("downstream")
                total_fields += 1
                if got == v:
                    hit_fields += 1
                else:
                    bad.append("%s 期望 %s 实得 %s" % (k, v, got))
                continue
            got = raw.get(k)
            total_fields += 1
            if got is None:
                bad.append("%s 未输出" % k)
            elif abs(float(got) - float(v)) <= 1e-9:
                hit_fields += 1
            else:
                bad.append("%s 期望 %s 实得 %s" % (k, v, got))

        if bad:
            lines.append("判定：%d 项不符" % len(bad))
            for b in bad:
                lines.append("   - " + b)
        else:
            lines.append("判定：全部正确")
        lines.append("")

    lines.append("=" * 70)
    lines.append("字段命中率：%d/%d = %.1f%%"
                 % (hit_fields, total_fields, 100.0 * hit_fields / max(total_fields, 1)))
    lines.append("规则缺席、实际依赖模型的句子：%d/%d" % (used_llm, len(CASES)))

    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step9-robust.txt")
    open(target, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", target)


if __name__ == "__main__":
    main()
