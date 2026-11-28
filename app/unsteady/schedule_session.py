# -*- coding: utf-8 -*-
"""V4.6.0：多轮调度会话 + 决策留痕/回放。

设计：
- 首轮用枚举寻优给出候选与推荐（一次计算）；
- 后续轮次只做“改约束 / 调保守度 / 反事实 / 解释”，每轮 1–5 s；
- 所有回复里的数字都来自会话内最后一次计算结果；
- 历史记录保留每轮动作与摘要，支持回放。
"""
import re
import random
import time
import uuid

from unsteady import chat
from unsteady import schedule_ai as ai
from unsteady import schedule_opt as opt

SESSIONS = {}


def _summary(result):
    if not result:
        return ""
    rec = result.get("recommend") or {}
    if rec:
        return "%s：开度 %.2f m，峰值 %.2f，闸上 %.2f m" % (
            rec.get("name", ""), rec.get("a_end", 0), rec.get("peak", 0), rec.get("hmax", 0))
    return "无可行方案"


def _random_base():
    L = random.choice([1000.0, 1500.0, 2000.0, 3000.0, 5000.0])
    b = round(random.uniform(4.0, 8.0), 1)
    m = random.choice([0.0, 1.0, 1.5, 2.0])
    n = round(random.uniform(0.012, 0.025), 3)
    i0 = round(random.uniform(0.0004, 0.002), 5)
    Q0 = round(random.uniform(6.0, 14.0), 1)
    Qpeak = round(Q0 * random.uniform(1.8, 3.0), 1)
    return dict(L=L, b=b, m=m, n=n, i0=i0, Q0=Q0, Qpeak=Qpeak,
                t_peak=3600.0, t_base=7200.0, t_end=8 * 3600.0)


def _capability_reply(sess):
    rec = (sess.get("result") or {}).get("recommend") or {}
    base = ("我能做这些事：① 按“闸上不超 X 米”枚举开度并推荐；"
            "② 比较指定开度（如“关到0.5呢”）；③ 调保守/激进；"
            "④ 解释推荐理由；⑤ 换一个随机算例；⑥ 改约束重算。")
    if rec:
        base += " 当前推荐 %s：开度 %.2f m、峰值 %.2f、闸上 %.2f m。" % (
            rec.get("name", ""), rec.get("a_end", 0), rec.get("peak", 0), rec.get("hmax", 0))
    return base


def _new_result(base, max_stage, steps=5):
    r = opt.optimize(base, text="", max_stage=max_stage, steps=steps, use_ai=False)
    return r


def create(base, text="", max_stage=None):
    intent = chat.parse_schedule_intent(text or "") or {}
    if max_stage is None:
        max_stage = (intent.get("constraints") or {}).get("max_stage")
    sid = uuid.uuid4().hex[:10]
    sess = {"id": sid, "base": base, "max_stage": max_stage,
            "result": None, "history": [], "created": time.time()}
    SESSIONS[sid] = sess
    if max_stage is None:
        sess["history"].append({"text": text or "(创建)", "action": "clarify",
                                "summary": "等待闸上允许水位"})
        return {"ok": True, "session_id": sid, "need_clarify": True,
                "reply": "闸上最高水位允许到多少？", "options": [5.0, 5.5, 6.0],
                "history": sess["history"]}
    sess["result"] = _new_result(base, max_stage, steps=5)
    reply = "已给出初始方案。" + _summary(sess["result"])
    sess["history"].append({"text": text or "(创建)", "action": "optimize",
                            "summary": reply})
    return {"ok": True, "session_id": sid, "need_clarify": False,
            "reply": reply, "result": sess["result"], "history": sess["history"]}


def turn(sid, text):
    sess = SESSIONS.get(sid)
    if not sess:
        return {"ok": False, "error": "会话不存在或已过期"}
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "内容为空"}

    # 1) 还在等约束
    if sess["max_stage"] is None:
        intent = chat.parse_schedule_intent(text) or {}
        ms = (intent.get("constraints") or {}).get("max_stage")
        if ms is None:
            m2 = re.search(r"(\d+(?:\.\d+)?)\s*(?:米|m)", text)
            if m2:
                ms = float(m2.group(1))
        if ms is None:
            return {"ok": True, "action": "clarify", "reply": "请先给一个闸上允许水位，例如“6 米”。",
                    "history": sess["history"]}
        sess["max_stage"] = float(ms)
        sess["result"] = _new_result(sess["base"], sess["max_stage"], steps=5)
        reply = "已按闸上≤%.1f m 给出方案。" % sess["max_stage"] + _summary(sess["result"])
        sess["history"].append({"text": text, "action": "optimize", "summary": reply})
        return {"ok": True, "action": "optimize", "reply": reply,
                "result": sess["result"], "history": sess["history"]}

    # 2) 能力介绍
    if any(k in text for k in ("你能做什么", "你会什么", "有什么功能", "讲一讲",
                               "介绍一下", "怎么用", "帮助")):
        reply = _capability_reply(sess)
        sess["history"].append({"text": text, "action": "capability", "summary": reply[:120]})
        return {"ok": True, "action": "capability", "reply": reply,
                "history": sess["history"]}

    # 3) 随机算例
    if any(k in text for k in ("随机算例", "换个算例", "换一个算例", "来一个算例",
                               "随机工况", "换一个")):
        nb = _random_base()
        if sess["base"].get("gate"):
            nb["gate"] = sess["base"]["gate"]
        sess["base"] = nb
        sess["result"] = _new_result(sess["base"], sess["max_stage"], steps=5)
        reply = ("已换随机算例：L=%.0f m、b=%.1f m、Q0=%.1f、Qpeak=%.1f、i0=%.4f。%s"
                 % (nb["L"], nb["b"], nb["Q0"], nb["Qpeak"], nb["i0"],
                    _summary(sess["result"])))
        sess["history"].append({"text": text, "action": "random", "summary": reply})
        return {"ok": True, "action": "random", "reply": reply,
                "result": sess["result"], "history": sess["history"]}

    # 4) 简单寒暄/致谢
    if any(k in text for k in ("谢谢", "好的", "收到", "辛苦了")):
        reply = "不客气。需要的话可以直接说“再保守一点”“为什么推荐它”“关到0.5呢”。"
        sess["history"].append({"text": text, "action": "chat", "summary": reply})
        return {"ok": True, "action": "chat", "reply": reply, "history": sess["history"]}

    # 5) 解释
    if any(k in text for k in ("为什么", "解释", "理由")):
        r = sess["result"] or {}
        rec = r.get("recommend") or {}
        if rec and rec.get("name") == "调整方案":
            reply = ("当前推荐 %s（开度 %.2f m、峰值 %.2f、闸上 %.2f m）。"
                     "这是在你上一轮推荐基础上手动微调的方案，尚未重新做候选解释。" % (
                         rec.get("name", ""), rec.get("a_end", 0), rec.get("peak", 0),
                         rec.get("hmax", 0)))
        elif rec:
            reply = "当前推荐 %s（开度 %.2f m、峰值 %.2f、闸上 %.2f m）。%s" % (
                rec.get("name", ""), rec.get("a_end", 0), rec.get("peak", 0),
                rec.get("hmax", 0), (r.get("explanation") or ""))
        else:
            reply = "还没有可解释的方案。"
        sess["history"].append({"text": text, "action": "explain", "summary": reply[:120]})
        return {"ok": True, "action": "explain", "reply": reply, "history": sess["history"]}

    # 3) 新约束 → 重算
    intent = chat.parse_schedule_intent(text) or {}
    ms = (intent.get("constraints") or {}).get("max_stage")
    if ms is not None and abs(float(ms) - float(sess["max_stage"])) > 1e-9:
        sess["max_stage"] = float(ms)
        sess["result"] = _new_result(sess["base"], sess["max_stage"], steps=5)
        reply = "已按闸上≤%.1f m 重算。" % sess["max_stage"] + _summary(sess["result"])
        sess["history"].append({"text": text, "action": "reoptimize", "summary": reply})
        return {"ok": True, "action": "reoptimize", "reply": reply,
                "result": sess["result"], "history": sess["history"]}

    # 4) 指定开度 → 反事实
    a_vals = [float(x) for x in intent.get("plans_a") or []]
    if a_vals:
        a = min(max(a_vals[0], 0.2), 2.0)
        cf = opt.counterfactual(sess["base"], a, sess["max_stage"])
        x = cf["result"]
        reply = "如果关到 %.2f m：峰值 %.2f、削峰 %.1f%%、闸上 %.2f m，%s。" % (
            a, x["peak"], x["cut"] * 100, x["hmax"], "可行" if x["feasible"] else "不可行")
        sess["history"].append({"text": text, "action": "counterfactual", "summary": reply})
        return {"ok": True, "action": "counterfactual", "reply": reply,
                "counterfactual": x, "history": sess["history"]}

    # 5) 保守/激进
    if any(k in text for k in ("保守", "稳一点", "安全", "激进", "再压")):
        rec = (sess["result"] or {}).get("recommend") or {}
        if rec:
            step = 0.08 if any(k in text for k in ("保守", "稳一点", "安全")) else -0.08
            a = min(max(rec["a_end"] + step, 0.2), 1.2)
            cf = opt.counterfactual(sess["base"], a, sess["max_stage"])
            x = cf["result"]
            sess["result"]["recommend"] = {
                "name": "调整方案", "a_end": round(a, 2), "peak": x["peak"],
                "cut": x["cut"], "hmax": x["hmax"], "feasible": x["feasible"],
                "mass_err": x.get("mass_err", 0)}
            reply = "%s到 %.2f m：峰值 %.2f、削峰 %.1f%%、闸上 %.2f m，%s。" % (
                "调保守" if step > 0 else "调激进", a, x["peak"], x["cut"] * 100,
                x["hmax"], "可行" if x["feasible"] else "不可行")
            sess["history"].append({"text": text, "action": "adjust", "summary": reply})
            return {"ok": True, "action": "adjust", "reply": reply,
                    "counterfactual": x, "result": sess["result"],
                    "history": sess["history"]}
        reply = "当前没有可行推荐方案，无法再调保守/激进。可以先放宽约束，例如“闸上不能超过5.5米”。"
        sess["history"].append({"text": text, "action": "adjust_na", "summary": reply})
        return {"ok": True, "action": "adjust_na", "reply": reply,
                "history": sess["history"]}

    # 6) 只表达目标（如“尽量削峰”）→ 确认目标，不重复重算
    if any(k in text for k in ("削峰", "峰值", "压低", "压下来")):
        reply = ("已记录目标：下游峰值尽量小。当前推荐 " + _summary(sess["result"]) +
                 "。要改结果，可以说“再保守一点”“闸上不能超过5.5米”“关到0.5呢”。")
        sess["history"].append({"text": text, "action": "objective", "summary": reply[:120]})
        return {"ok": True, "action": "objective", "reply": reply,
                "history": sess["history"]}

    # 7) 兜底：问清楚，不瞎算
    reply = ("没太听懂这句话。可以直接说：\n"
             "· 闸上不能超过6米（改约束）\n"
             "· 再保守一点 / 调激进\n"
             "· 为什么推荐它\n"
             "· 关到0.5呢\n"
             "· 换个随机算例")
    sess["history"].append({"text": text, "action": "clarify", "summary": "未识别指令"})
    return {"ok": True, "action": "clarify", "reply": reply,
            "history": sess["history"]}


def history(sid):
    sess = SESSIONS.get(sid)
    if not sess:
        return {"ok": False, "error": "会话不存在"}
    return {"ok": True, "session_id": sid, "constraint": sess["max_stage"],
            "history": sess["history"], "result": sess["result"]}
