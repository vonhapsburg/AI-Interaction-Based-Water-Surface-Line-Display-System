# -*- coding: utf-8 -*-
"""对照实验：让模型直接"算" vs 由内核算，量化两者的差距。

用途：回答"最终数值到底是谁算的"。结论是——模型算不准，所以系统只让它解析
参数与解释结果；所有数值由 gvf_core 计算。

跑法：python tests/step16_ai_vs_kernel.py   （需要模型通道可用）
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import llm                      # noqa: E402
from agent import adapter                  # noqa: E402
import gvf_core                            # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step16-ai-vs-kernel.txt")
lines = []

# (标签, 矩形渠 b, n, i, Q)
CASES = [
    ("缓坡-1", 5.0, 0.015, 0.0010, 10.0),
    ("缓坡-2", 6.0, 0.017, 0.0008, 12.0),
    ("缓坡-3", 8.0, 0.020, 0.0005, 20.0),
    ("陡坡-1", 5.0, 0.015, 0.0200, 10.0),
    ("陡坡-2", 4.0, 0.013, 0.0500, 8.0),
    ("平坡-1", 5.0, 0.015, 0.0000, 10.0),
]


def kernel_values(b, n, i, Q):
    slots = {"Q": Q, "reaches": [{"L": 500, "i": i, "b": b, "m": 0, "n": n}],
             "downstream": "free_outfall", "upstream": "none"}
    a = adapter.build({"slots": slots, "warnings": [], "normalized": ""})
    res = gvf_core.solve(a["params"])
    r = res.reaches[0]
    return r.h0, r.hc, r.ic, r.slope_class


def ask_model(b, n, i, Q):
    prompt = (
        "已知一条矩形明渠：底宽 b=%g m，曼宁糙率 n=%g，底坡 i=%g，流量 Q=%g m³/s，"
        "重力加速度取 9.81。\n"
        "请直接给出：\n"
        "1) 正常水深 h0（m）\n"
        "2) 临界水深 hc（m）\n"
        "3) 底坡类型（缓坡/陡坡/临界坡/平坡）\n"
        "只输出三行，格式严格如下，不要解释、不要单位：\n"
        "h0=<数值>\n"
        "hc=<数值>\n"
        "坡型=<缓坡|陡坡|临界坡|平坡>" % (b, n, i, Q)
    )
    r = llm.chat([{"role": "user", "content": prompt}], max_tokens=200, temperature=0)
    if "error" in r:
        return None, r["error"]
    text = r["text"] or ""
    m0 = re.search(r"h0\s*[=:：]\s*([0-9]*\.?[0-9]+)", text, re.I)
    mc = re.search(r"h[_\s]*c\s*[=:：]\s*([0-9]*\.?[0-9]+)", text, re.I)
    ms = re.search(r"(缓坡|陡坡|临界坡|平坡)", text)
    return {
        "h0": float(m0.group(1)) if m0 else None,
        "hc": float(mc.group(1)) if mc else None,
        "slope": ms.group(1) if ms else None,
        "raw": text.strip().replace("\n", " | ")[:120],
    }, None


def rel_err(guess, truth):
    if guess is None or truth in (None, 0):
        return None
    return abs(guess - truth) / abs(truth)


def main():
    info = llm.describe()
    lines.append("=== 模型通道 ===")
    lines.append("%s / %s（ready=%s）" % (info["provider"], info["model"], info["ready"]))
    lines.append("")
    if not llm.health():
        msg = "模型通道未就绪，跳过本实验。"
        print(msg)
        open(OUT, "w", encoding="utf-8").write("\n".join(lines + [msg]) + "\n")
        return

    lines.append("=== 对照：内核 vs 模型直接作答 ===")
    lines.append("%-8s %-22s %-22s %s" % ("工况", "内核 h0 / hc / 坡型", "模型 h0 / hc / 坡型", "误差"))
    bad = 0
    errs = []
    for tag, b, n, i, Q in CASES:
        h0, hc, ic, cls = kernel_values(b, n, i, Q)
        cn = {"mild": "缓坡", "steep": "陡坡", "critical": "临界坡",
              "horizontal": "平坡", "adverse": "负坡"}[cls]
        got, err = ask_model(b, n, i, Q)
        if err:
            lines.append("%-8s %-22s %s" % (tag, "—", "调用失败：" + err))
            continue
        e0 = rel_err(got["h0"], h0)
        ec = rel_err(got["hc"], hc)
        worst = max([x for x in (e0, ec) if x is not None] or [0])
        errs.append(worst)
        if worst > 0.05:
            bad += 1
        lines.append("%-8s %-22s %-22s %s" % (
            tag,
            "%.3f / %.3f / %s" % (h0 if h0 else float("nan"), hc, cn),
            "%s / %s / %s" % (("%.3f" % got["h0"]) if got["h0"] else "—",
                              ("%.3f" % got["hc"]) if got["hc"] else "—",
                              got["slope"] or "—"),
            "h0 %s，hc %s" % (("%.1f%%" % (e0 * 100)) if e0 is not None else "—",
                              ("%.1f%%" % (ec * 100)) if ec is not None else "—")))
        lines.append("         模型原文：" + got["raw"])
    lines.append("")
    if errs:
        lines.append("模型直接作答：%d/%d 个工况误差超过 5%%，最大误差 %.0f%%。"
                     % (bad, len(errs), max(errs) * 100))
    lines.append("")
    lines.append("结论：h0、hc 是隐式方程求根，模型只能给近似值甚至臆测；"
                 "因此系统只让模型做「语言→参数」和「结果→解释」，数值一律由内核算出。")
    lines.append("")

    # ---- 另一条证据：对话回答引用的数值必须与内核一致 ----
    lines.append("=== 对话回答里的数值来源 ===")
    slots = {"Q": 10.0, "b": 5.0, "n": 0.015, "i": 0.001, "h0": 2.0, "L": 600.0,
             "section": "rectangular", "downstream": "given_h"}
    a = adapter.build({"slots": slots, "warnings": [], "normalized": ""})
    res = gvf_core.solve(a["params"])
    from agent import chat as chatmod
    rep = chatmod.answer("这个工况的正常水深和临界水深分别是多少？为什么是现在这个型号？",
                         slots, res, [])
    if "text" in rep:
        text = rep["text"]
        h0, hc = res.reaches[0].h0, res.reaches[0].hc
        lines.append("内核：h0=%.4f  hc=%.4f" % (h0, hc))
        lines.append("回答：" + text.replace("\n", " ")[:260])
        near0 = re.search(r"%s|%s" % (("%.2f" % h0), ("%.3f" % h0)), text)
        near_c = re.search(r"%s|%s" % (("%.2f" % hc), ("%.3f" % hc)), text)
        lines.append("回答中的 h0 与内核一致：%s ；hc 一致：%s" % (bool(near0), bool(near_c)))
    else:
        lines.append("对话调用失败：%s" % rep.get("error"))
    lines.append("")
    lines.append("=" * 60)
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)


if __name__ == "__main__":
    main()
