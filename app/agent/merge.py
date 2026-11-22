# -*- coding: utf-8 -*-
"""双通道合并：规则解析优先，模型补缺。"""
import re

from .parser import parse as rule_parse
from . import llm

REQUIRED_BASE = ["section", "b", "n", "i", "Q", "h0", "L"]
NUMERIC = ("b", "m", "n", "i", "Q", "h0", "L")
CLEARABLE = ("section", "b", "m", "n", "i", "Q", "h0", "L", "downstream", "zw")


def required_of(slots):
    need = list(REQUIRED_BASE)
    if slots.get("section") == "trapezoidal":
        need.append("m")
    if slots.get("downstream") == "given_zw":
        need.append("zw")
    return need


def missing_of(slots):
    return [k for k in required_of(slots) if slots.get(k) in (None, "")]


def _apply(state, delta, warns, src):
    """把 delta 写进 state。返回本轮真正新增/改动的字段列表。"""
    added = []
    for k, v in (delta or {}).items():
        if v is None or v == "":
            continue
        if k not in CLEARABLE:
            continue
        old = state.get(k)
        if old is None or old == "":
            state[k] = v
            added.append(k)
        elif isinstance(v, (int, float)) and isinstance(old, (int, float)):
            if abs(float(old) - float(v)) > 1e-9:
                state[k] = v
                warns.append("%s 由 %s 改为 %s（%s）" % (k, old, v, src))
                added.append(k)
        elif old != v:
            state[k] = v
            warns.append("%s 由 %s 改为 %s（%s）" % (k, old, v, src))
            added.append(k)
    return added


def turn(text, state, use_llm=True):
    """增量处理一句话：只把这一句里的字段并进 state，state 原地更新。

    这是"记忆"的正确位置——参数表在 Python 进程里，模型只做本句抽取。
    """
    warns = []
    r = rule_parse(text)
    chans = ["rule"]

    if r.get("bc_up"):
        state["bc_up"] = r["bc_up"]

    delta = {k: v for k, v in (r["slots"] or {}).items() if v not in (None, "")}

    # 表单里的多段 reaches 与对话里的单段参数会打架：adapter 优先用 reaches，
    # 于是"用户说了完整一句话却按旧的两段算"。这里定一条规则：
    # 这句话如果是在**完整描述一条新渠道**（给了断面型式，或 b+i+L 齐了），
    # 就以对话为准，丢掉表单的多段设置；若只是"边坡改成2"这种局部修改，则保留。
    if state.get("reaches"):
        # parser 会从「边坡」反推 section，所以不能把 section 当"显式断面型式"，
        # 要看原句里到底有没有断面关键词。
        explicit_section = bool(re.search(r"矩形|梯形|长方形|方形渠", r.get("normalized") or ""))
        full_desc = explicit_section or all(k in delta for k in ("b", "i", "L"))
        if full_desc:
            state.pop("reaches", None)
            warns.append("这句话完整描述了渠道，已按单段重建（清除表单中的多段设置）")
        elif any(k in delta for k in ("b", "n", "i", "m", "L")):
            warns.append("当前是多段渠道，b/n/i/m/L 请直接在参数表单里按段修改（本句已记入参数表）")

    added = _apply(state, delta, warns, "规则")
    warns.extend(r["warnings"])

    # 规则没抽干净、仍缺字段时才叫模型。
    # 早先的策略是"规则一个字段都没抽到才叫"，那会让半截句式（如「糙率按…」
    # 没被正则覆盖）永远补不上，用户只能手动再补一句。
    # DeepSeek 一次约 0.8 秒，可以放宽：长句里只要还缺字段就问一次模型；
    # 短句（「底宽5米」这类逐条输入）仍不叫，避免多轮时每句都发请求。
    missing = missing_of(state)
    long_sentence = len((r.get("normalized") or "")) >= 16
    if use_llm and missing and (not added or long_sentence):
        lr = llm.ask(text, current=state)
        if "error" in lr:
            warns.append("模型通道不可用：%s；已退回规则解析" % lr["error"])
        else:
            chans.append("llm")
            added += _apply(state, lr["slots"], warns, "模型")

    # 糙率量级兜底（模型/口语常把 0.015 给成 15）
    n = state.get("n")
    if isinstance(n, (int, float)) and 1.0 <= n <= 30.0:
        warns.append("糙率 %g 明显偏大，按 %g 处理，请确认" % (n, n / 1000.0))
        state["n"] = n / 1000.0

    # 矩形 + 边坡 冲突（方案 H1）
    if state.get("section") == "rectangular" and state.get("m"):
        warns.append("已声明矩形断面，边坡系数 %g 被忽略（m 置 0）" % state["m"])
        state["m"] = 0.0

    return {
        "channels": chans,
        "slots": dict(state),
        "delta": delta,
        "added": added,
        "missing": missing_of(state),
        "warnings": warns,
        "normalized": r["normalized"],
    }


def merge(text, use_llm=True):
    """返回统一结果：channels / slots / missing / warnings / need_clarify"""
    r = rule_parse(text)
    slots = dict(r["slots"])
    warnings = list(r["warnings"])
    channels = ["rule"]
    llm_raw = None

    missing = [k for k in required_of(slots) if slots.get(k) in (None, "")]

    # 规则没填全，才叫模型（省 10 秒）
    if missing and use_llm:
        lr = llm.ask(text)
        if "error" in lr:
            warnings.append("模型通道不可用：%s；已退回规则解析" % lr["error"])
        else:
            channels.append("llm")
            llm_raw = lr.get("raw")
            for k, v in lr["slots"].items():
                if slots.get(k) in (None, "") and v is not None:
                    slots[k] = v
                elif slots.get(k) not in (None, "") and v is not None:
                    # 字符串字段（section / downstream）不能做 float 比较，
                    # 之前这里直接 float() 会抛 ValueError 把整个请求打成 500。
                    if k in NUMERIC:
                        same = abs(float(slots[k]) - float(v)) <= 1e-9
                    else:
                        same = slots[k] == v
                    if not same:
                        warnings.append("%s 规则=%s 与模型=%s 不一致，已采用规则值"
                                        % (k, slots[k], v))
            # 糙率数值异常（模型常把 0.015 说成 15）
            # 注意：这段兜底不能只写在模型分支里——规则通道自己也会把
            # 「糙率十五」解析成 15，那时根本不会调用模型，兜底就失效了。

    # 糙率量级兜底：无论值来自规则还是模型，>= 1 一律按千分位理解
    if slots.get("n") and slots["n"] >= 1:
        raw_n = slots["n"]
        fixed = raw_n / 1000.0
        # 规则层已按原始值 15 报过「超出范围」，这里再报「已按 0.015 处理」
        # 会自相矛盾，先把那条去掉。
        warnings = [w for w in warnings
                    if not (w.startswith("糙率") and ("%.3f" % raw_n) in w)]
        warnings.append("糙率 %.3f 明显偏大，已按 %.4f 处理，请确认"
                        % (raw_n, fixed))
        slots["n"] = fixed

    missing = [k for k in required_of(slots) if slots.get(k) in (None, "")]
    return {
        "channels": channels,
        "slots": slots,
        "missing": missing,
        "warnings": warnings,
        "need_clarify": len(missing) > 0,
        "normalized": r["normalized"],
        "llm_raw": llm_raw,
    }
