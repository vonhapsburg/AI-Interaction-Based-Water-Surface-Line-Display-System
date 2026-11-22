# -*- coding: utf-8 -*-
"""模型通道验收：DeepSeek / Ollama / none 三种后端 + 失败降级。

无需联网即可跑的：
  · 未配置 Key 时 health 为 False、ask 返回 error、merge 退回规则且不崩
  · provider=none 时同上
  · 错误 Key 时给出 401 明确提示
配置了真实 Key 时额外跑一遍抽取准确率（与 step3 同一组用例）。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import llm                  # noqa: E402
from agent.merge import merge          # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step14-llm.txt")
lines = []
ok = total = 0


def check(label, cond, detail=""):
    global ok, total
    total += 1
    if cond:
        ok += 1
    lines.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


CASES = [
    ("A", "矩形渠，底宽5米，糙率0.015，底坡千分之一，流量10个方，水深2米，算500米",
     {"section": "rectangular", "b": 5, "m": 0, "n": 0.015, "i": 0.001, "Q": 10, "h0": 2, "L": 500}),
    ("B", "五米宽的梯形渠，边坡1比1.5，糙率十五，坡度千分之一，流量十个方，水深两米，算三百米",
     {"section": "trapezoidal", "b": 5, "m": 1.5, "n": 0.015, "i": 0.001, "Q": 10, "h0": 2, "L": 300}),
    ("C", "帮我算个水面线",
     {"b": -1, "m": -1, "n": -1, "i": -1, "Q": -1, "h0": -1, "L": -1}),
    ("D", "矩形渠宽3米，糙率0.3，底宽5米，坡度百分之五，流量20方，渠末水位给定，水深1.8米，长200米",
     {"section": "rectangular", "n": 0.3, "i": 0.05, "Q": 20, "h0": 1.8, "L": 200,
      "downstream": "given_zw"}),
    ("E", "梯形渠道，底宽2.5米，边坡1:2，糙率0.025，底坡0.0015，流量1.2立方米每秒，渠末正常水深，渠长1.2公里",
     {"section": "trapezoidal", "b": 2.5, "m": 2, "n": 0.025, "i": 0.0015, "Q": 1.2,
      "h0": -1, "downstream": "normal_depth", "L": 1200}),
    ("F", "底坡1/1000，流量0.5个方", {"i": 0.001, "Q": 0.5}),
]


def run_accuracy():
    lines.append("=== 抽取准确率（真实 API）===")
    hit = miss = 0
    for cid, text, expect in CASES:
        t0 = time.time()
        r = llm.ask(text)
        dt = time.time() - t0
        if "error" in r:
            check("[%s] 调用成功" % cid, False, r["error"])
            continue
        raw, clean = r["raw"], r["slots"]
        bad = []
        for k, v in expect.items():
            if k in ("section", "downstream"):
                got = clean.get(k)
                if v == "given_zw" or v == "normal_depth":
                    if raw.get(k) != v:
                        bad.append("%s 期望 %s 实得 %s" % (k, v, raw.get(k)))
                elif v == "rectangular":
                    if clean.get(k) != "rectangular":
                        bad.append("%s 期望 %s 实得 %s" % (k, v, clean.get(k)))
                continue
            got = raw.get(k)
            if got is None:
                bad.append("%s 未输出" % k)
            elif abs(float(got) - float(v)) > 1e-9:
                bad.append("%s 期望 %s 实得 %s" % (k, v, got))
        if bad:
            miss += 1
            check("[%s] %s" % (cid, text[:26]), False, "；".join(bad))
        else:
            hit += 1
            check("[%s] %s" % (cid, text[:26]), True,
                  "%.2fs  %s" % (dt, json.dumps(raw, ensure_ascii=False)[:110]))
    lines.append("命中 %d / %d" % (hit, len(CASES)))
    lines.append("")


def run_multiturn():
    lines.append("=== 跨轮指代（带 current）===")
    state = {"section": "rectangular", "b": 5.0, "n": 0.015, "i": 0.001, "Q": 10.0}
    r = llm.ask("改成梯形，边坡1比1.5", current=state)
    if "error" in r:
        check("指代解析", False, r["error"])
    else:
        check("「改成梯形，边坡1比1.5」只返回新字段",
              r["raw"].get("section") == "trapezoidal" and abs(float(r["raw"].get("m", -1)) - 1.5) < 1e-9,
              json.dumps(r["raw"], ensure_ascii=False)[:160])
        check("没有把 current 抄回来（b 应为 -1）", r["raw"].get("b") == -1,
              "b=%s" % r["raw"].get("b"))
    lines.append("")


def main():
    info = llm.describe()
    lines.append("=== 当前配置 ===")
    lines.append("provider = %s | model = %s | ready = %s"
                 % (info["provider"], info["model"], info["ready"]))
    if info.get("note"):
        lines.append("note: %s" % info["note"])
    lines.append("")

    # ---------- 1. 未就绪时的降级路径 ----------
    lines.append("=== 降级路径 ===")
    if info["ready"]:
        check("模型通道就绪（跳过降级断言）", True, "已配置 Key")
    else:
        check("health() 为 False", llm.health() is False)
        r = llm.ask("帮我算个水面线")
        check("ask() 返回 error 而不是抛异常", "error" in r, r.get("error"))
        m = merge("五米宽的梯形渠，边坡1比1.5，流量十个方", use_llm=True)
        check("merge 退回规则通道", m["channels"] == ["rule"], m["channels"])
        check("给出可读的降级提示",
              any("模型通道不可用" in w for w in m["warnings"]),
              m["warnings"][:1])
        check("规则能解析的部分照样出来",
              abs(m["slots"].get("b", 0) - 5) < 1e-9 and m["slots"].get("section") == "trapezoidal",
              json.dumps(m["slots"], ensure_ascii=False))
    lines.append("")

    # ---------- 2. provider = none ----------
    lines.append("=== provider = none ===")
    old = os.environ.get("GVF_LLM_PROVIDER")
    os.environ["GVF_LLM_PROVIDER"] = "none"
    try:
        llm._cache["cfg"] = None                      # 强制重载
        check("provider() 返回 none", llm.provider() == "none", llm.provider())
        check("health() 为 False", llm.health() is False)
        r = llm.ask("矩形渠 底宽5米")
        check("ask() 返回 error", "error" in r, r.get("error"))
        m = merge("矩形渠，底宽5米", use_llm=True)
        check("仍可用规则解析", m["slots"].get("b") == 5.0, m["channels"])
    finally:
        if old is None:
            os.environ.pop("GVF_LLM_PROVIDER", None)
        else:
            os.environ["GVF_LLM_PROVIDER"] = old
        llm._cache["cfg"] = None
    lines.append("")

    # ---------- 3. 错误 Key ----------
    lines.append("=== 错误 Key 的处理 ===")
    if info["provider"] == "deepseek":
        old_key = os.environ.get("DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_API_KEY"] = "sk-invalid-for-test-0000000000"
        llm._cache["cfg"] = None
        try:
            r = llm.ask("矩形渠 底宽5米", timeout=15)
            check("返回 error 而不是抛异常", "error" in r, r.get("error"))
            check("错误信息能看出是 Key 问题", "Key" in str(r.get("error", "")),
                  r.get("error"))
        finally:
            if old_key is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = old_key
            llm._cache["cfg"] = None
    else:
        check("（非 deepseek 后端，跳过）", True)
    lines.append("")

    # ---------- 4. 真实 API（有 Key 才跑）----------
    if llm.health():
        run_accuracy()
        run_multiturn()
    else:
        lines.append("=== 真实 API 抽取 ===")
        lines.append("跳过：模型通道未就绪（配置 config.ini 或 DEEPSEEK_API_KEY 后重跑本脚本）")
        lines.append("")

    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
