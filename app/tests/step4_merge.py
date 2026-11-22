# -*- coding: utf-8 -*-
"""第 4 步 + 第 8 步验收：双通道合并，逐条对照文档的验收表。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.merge import merge  # noqa: E402

LABEL = {
    "section": "断面型式", "b": "底宽b", "m": "边坡m", "n": "糙率n", "i": "底坡i",
    "Q": "流量Q", "h0": "控制水深h0", "L": "渠长L", "downstream": "渠末边界",
}

CASES = [
    (1, "矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10个方，水深2米，算500米长，渠末自由跌水",
     "立刻出表，8 个字段全有值，来源只有规则"),
    (2, "五米宽的梯形渠，边坡1比1.5，流量十个方",
     "缺项应被列出"),
    (3, "帮我算个水面线",
     "全部缺失，提示补充"),
    (4, "糙率0.3，底宽5米，糙率0.3",
     "出现黄色警告（教学夸张取值）"),
    (41, "矩形渠，底宽5米，糙率0.3，底坡千分之一，流量10个方，水深2米，算500米",
     "同样出现教学夸张警告，且 n 保持 0.3 不被改量级"),
    (6, "五米宽的梯形渠，边坡1比1.5，糙率十五，坡度千分之一，流量十个方，水深两米，算三百米",
     "模型补上字段，标签出现模型"),
]


def main():
    lines = []
    for cid, text, expect in CASES:
        t0 = time.time()
        r = merge(text, use_llm=True)
        dt = time.time() - t0

        lines.append("=" * 70)
        lines.append("验收 #%s  %s" % (cid, text))
        lines.append("文档期望：%s" % expect)
        lines.append("耗时：%.2fs" % dt)
        lines.append("通道：%s" % r["channels"])
        lines.append("need_clarify：%s" % r["need_clarify"])

        shown = {}
        for k in LABEL:
            v = r["slots"].get(k)
            shown[k] = v if v not in (None, "") else None
        lines.append("slots：%s" % json.dumps(shown, ensure_ascii=False))
        lines.append("missing：%s" % json.dumps(r["missing"], ensure_ascii=False))
        lines.append("warnings：%s" % json.dumps(r["warnings"], ensure_ascii=False))
        lines.append("normalized：%s" % r["normalized"])
        if r.get("llm_raw"):
            lines.append("模型原始：%s" % json.dumps(r["llm_raw"], ensure_ascii=False))
        lines.append("")

    # ---- 验收 #5：模拟 Ollama 挂掉 ----
    from agent import llm
    real_url = llm.OLLAMA_URL
    llm.OLLAMA_URL = "http://127.0.0.1:59999"   # 无人监听的端口
    try:
        t0 = time.time()
        r = merge("五米宽的梯形渠，边坡1比1.5，流量十个方", use_llm=True)
        dt = time.time() - t0
        lines.append("=" * 70)
        lines.append("验收 #5  Ollama 不可用时的降级（把模型地址指向死端口）")
        lines.append("文档期望：提示本地模型不可用，规则能解析的部分照样出来，不崩溃")
        lines.append("耗时：%.2fs" % dt)
        lines.append("通道：%s" % r["channels"])
        lines.append("slots：%s" % json.dumps({k: r["slots"].get(k) for k in LABEL},
                                              ensure_ascii=False))
        lines.append("missing：%s" % json.dumps(r["missing"], ensure_ascii=False))
        lines.append("warnings：%s" % json.dumps(r["warnings"], ensure_ascii=False))
        lines.append("")
    finally:
        llm.OLLAMA_URL = real_url

    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step4-merge.txt")
    open(target, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", target)


if __name__ == "__main__":
    main()
