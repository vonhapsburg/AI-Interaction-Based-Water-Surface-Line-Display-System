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


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}


def _target_reach(text, n):
    """识别「第2段 / 末段 / 首段」这类指向。返回 0-based 下标或 None。"""
    if n <= 0:
        return None
    m = re.search(r"第\s*([一二两三四1-4])\s*段", text or "")
    if m:
        idx = _CN_NUM.get(m.group(1))
        if idx and 1 <= idx <= n:
            return idx - 1
        return None
    if re.search(r"末段|最后一段|最末段", text or ""):
        return n - 1
    if re.search(r"首段", text or ""):
        return 0
    return None


def turn(text, state, use_llm=True, restructure=True):
    """增量处理一句话：只把这一句里的字段并进 state，state 原地更新。

    restructure=False 用于"提问"：只做参数推演，不允许改变渠道的分段结构
    （否则一句"如果改成梯形…"就会把用户的两段渠道弹成单段）。
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
        explicit_section = bool(re.search(r"矩形|梯形|长方形|方形渠", r.get("normalized") or ""))
        full_desc = restructure and (explicit_section or all(k in delta for k in ("b", "i", "L")))
        if full_desc:
            state.pop("reaches", None)
            warns.append("这句话完整描述了渠道，已按单段重建（清除表单中的多段设置）")
        else:
            # 局部修改：只改「被点到的那一段」；没点名就同步到全部段。
            target = _target_reach(r.get("normalized") or "", len(state["reaches"]))
            segs = [state["reaches"][target]] if target is not None else state["reaches"]
            per = {k: delta[k] for k in ("b", "n", "i", "m") if k in delta}
            if delta.get("section") == "rectangular":
                per["m"] = 0.0
            if per:
                for rr in segs:
                    for k, v in per.items():
                        try:
                            rr[k] = float(v)
                        except (TypeError, ValueError):
                            pass
                where = ("第%d段" % (target + 1)) if target is not None else (
                    "全部 %d 段" % len(state["reaches"]))
                warns.append("已同步到%s：%s" % (where, "、".join(
                    "%s=%g" % (k, float(v)) for k, v in per.items())))
            if "L" in delta:
                if target is not None:
                    try:
                        state["reaches"][target]["L"] = float(delta["L"])
                        warns.append("第%d段长度已改为 %g m" % (target + 1, float(delta["L"])))
                    except (TypeError, ValueError):
                        pass
                elif len(state["reaches"]) == 1:
                    try:
                        state["reaches"][0]["L"] = float(delta["L"])
                        warns.append("第1段长度已改为 %g m" % float(delta["L"]))
                    except (TypeError, ValueError):
                        pass
                else:
                    warns.append("多段渠道的长度请在表单里按段修改（本句已记入参数表）")

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
            # 设计约定是「规则优先、模型补缺」：
            # 本句规则已经抽到的字段，不再让模型覆盖（只记一条不一致提示）；
            # 规则没抽到的字段才由模型补上，包括修改此前轮次留下的旧值。
            llm_delta, conflicts = {}, []
            for k, v in (lr["slots"] or {}).items():
                if v is None or v == "":
                    continue
                if k in delta:            # 本句规则已给出 → 规则说了算
                    old = delta[k]
                    if isinstance(v, (int, float)) and isinstance(old, (int, float)):
                        same = abs(float(old) - float(v)) <= 1e-9
                    else:
                        same = (old == v)
                    if not same:
                        conflicts.append("%s 规则=%s 模型=%s" % (k, old, v))
                    continue
                llm_delta[k] = v
            if conflicts:
                warns.append("模型与规则不一致，已采用规则值（%s）" % "；".join(conflicts))
            added += _apply(state, llm_delta, warns, "模型")

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
