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

from agent import llm
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
    r = opt.optimize(base, text="", max_stage=max_stage, steps=steps, use_ai=False, refine=False)
    return r


def create(base, text="", max_stage=None, use_ai=False, current_plan=None):
    intent = chat.parse_schedule_intent(text or "") or {}
    if max_stage is None:
        max_stage = (intent.get("constraints") or {}).get("max_stage")
    sid = uuid.uuid4().hex[:10]
    sess = {"id": sid, "base": base, "max_stage": max_stage,
            "result": None, "history": [], "created": time.time(),
            "use_ai": bool(use_ai), "current_plan": current_plan or None}
    SESSIONS[sid] = sess
    if max_stage is None:
        sess["history"].append({"text": text or "(创建)", "action": "clarify",
                                "summary": "等待闸上允许水位"})
        return {"ok": True, "session_id": sid, "need_clarify": True,
                "reply": "闸上最高水位允许到多少？", "options": [5.0, 5.5, 6.0],
                "history": sess["history"], "ai_used": False, "base": base}
    sess["result"] = _new_result(base, max_stage, steps=5)
    reply = "已给出初始方案。" + _summary(sess["result"])
    sess["history"].append({"text": text or "(创建)", "action": "optimize",
                            "summary": reply})
    return {"ok": True, "session_id": sid, "need_clarify": False,
            "reply": reply, "result": sess["result"], "history": sess["history"],
            "ai_used": False, "base": base}


def _rule_turn(sid, text):
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

    # 2) 读取当前方案
    if any(k in text for k in ("当前方案", "现在方案", "方案卡", "推荐方案是什么")):
        cp = sess.get("current_plan") or ((sess.get("result") or {}).get("recommend") or {})
        if cp:
            reply = ("当前方案：%s，开度 %.2f m，峰值 %.2f m³/s，削峰 %.1f%%，闸上最高 %.2f m。"
                     % (cp.get("name", "推荐"), cp.get("a_end", 0), cp.get("peak", 0),
                        (cp.get("cut", 0) or 0) * 100, cp.get("hmax", 0)))
        else:
            reply = "当前方案卡还是空的，先生成一个方案，或直接说“闸上不能超过6米，尽量削峰”。"
        sess["history"].append({"text": text, "action": "current", "summary": reply[:120]})
        return {"ok": True, "action": "current", "reply": reply,
                "history": sess["history"]}

    # 3) 能力介绍
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


def _llm_decide(sess, text):
    """让模型判意图；失败返回 None，由规则接管。"""
    try:
        if not llm.health():
            return None
    except Exception:
        return None
    rec = (sess.get("result") or {}).get("recommend") or {}
    base = sess.get("base") or {}
    cp = sess.get("current_plan") or {}
    ctx = ("当前方案卡：%s（开度 %.2f m、峰值 %.2f、闸上 %.2f m）；\n"
           "会话状态：闸上允许最高 %.2f m；当前推荐 %s（开度 %.2f m、峰值 %.2f、"
           "削峰 %.1f%%、闸上 %.2f m）；渠道 L=%.0f m、b=%.1f m、Q0=%.1f、Qpeak=%.1f。\n"
           "用户这句话：%s" % (
               cp.get("name", "无"), cp.get("a_end", 0), cp.get("peak", 0), cp.get("hmax", 0),
               sess.get("max_stage") or 0, rec.get("name", "无"),
               rec.get("a_end", 0), rec.get("peak", 0), rec.get("cut", 0) * 100,
               rec.get("hmax", 0), base.get("L", 0), base.get("b", 0),
               base.get("Q0", 0), base.get("Qpeak", 0), text))
    sys = ("你是闸门调度会话助手。只输出一个 JSON 对象，不要解释。字段：\n"
           "action：capability|random|objective|adjust|explain|counterfactual|reoptimize|clarify|current；\n"
           "max_stage：当 action=reoptimize 时给出数字（1–20）；\n"
           "a_end：当 action=counterfactual 时给出开度（0.2–2.0）；\n"
           "direction：当 action=adjust 时填 conservative 或 aggressive；\n"
           "（conservative 指开度调大、闸上水位降低；aggressive 相反）\n"
           "reply：中文回复，≤120 字，只能使用上面给出的数字，禁止“实测/全局最优/保证”。\n"
           "不确定就 clarify。")
    try:
        r = llm.chat([{"role": "system", "content": sys},
                      {"role": "user", "content": ctx}],
                     timeout=10, max_tokens=280, temperature=0.2)
        if "text" not in r:
            return None
        data = llm._extract_json(r["text"])
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _reply_safe(reply, sess, result):
    if not reply or len(reply) > 260:
        return False
    for k in ("实测", "全局最优", "保证安全", "保证"):
        if k in reply:
            return False
    rec = (result or {}).get("recommend") or {}
    cf = (result or {}).get("counterfactual") or {}
    allowed = {0.08, float(sess.get("max_stage") or 0)}
    for d in (rec, cf):
        for k in ("a_end", "peak", "hmax", "cut"):
            v = d.get(k)
            if isinstance(v, (int, float)):
                allowed.add(float(v) * (100 if k == "cut" else 1))
    for k in ("L", "b", "Q0", "Qpeak"):
        v = (sess.get("base") or {}).get(k)
        if isinstance(v, (int, float)):
            allowed.add(float(v))
    for tok in re.findall(r"-?\d+(?:\.\d+)?", reply):
        x = float(tok)
        if "." not in tok and abs(x) <= 10:
            continue
        if not any(abs(x - v) <= 0.06 for v in allowed):
            return False
    return True


def _ai_turn(sess, text, data):
    action = str(data.get("action") or "").strip()
    if action not in ("capability", "random", "objective", "adjust",
                      "explain", "counterfactual", "reoptimize", "clarify",
                      "current"):
        return None
    sid = sess["id"]
    synth = None
    if action == "capability":
        synth = "讲一讲你能做什么"
    elif action == "current":
        synth = "当前方案是什么"
    elif action == "random":
        synth = "随机算例"
    elif action == "objective":
        synth = "尽量削峰"
    elif action == "explain":
        synth = "为什么推荐它"
    elif action == "adjust":
        synth = "调激进" if str(data.get("direction")) == "aggressive" else "再保守一点"
    elif action == "counterfactual":
        a = data.get("a_end")
        if a is None:
            m = re.search(r"(\d+(?:\.\d+)?)", text)
            a = m.group(1) if m else None
        try:
            a = float(a)
        except (TypeError, ValueError):
            return None
        if not (0.2 <= a <= 2.0):
            return None
        synth = "关到%.2f呢" % a
    elif action == "reoptimize":
        try:
            ms = float(data.get("max_stage"))
        except (TypeError, ValueError):
            return None
        if not (1.0 <= ms <= 20.0):
            return None
        sess["max_stage"] = ms
        sess["result"] = _new_result(sess["base"], ms, steps=5)
        reply = "已按闸上≤%.1f m 重算。%s" % (ms, _summary(sess["result"]))
        sess["history"].append({"text": text, "action": "reoptimize", "summary": reply})
        res = {"ok": True, "action": "reoptimize", "reply": reply,
               "result": sess["result"], "history": sess["history"]}
        ai_reply = data.get("reply")
        if ai_reply and _reply_safe(ai_reply, sess, res):
            res["reply"] = ai_reply.strip()
            res["history"][-1]["summary"] = res["reply"][:120]
        res["ai_used"] = True
        return res
    elif action == "clarify":
        synth = "__unknown__"

    if synth is None:
        return None
    res = _rule_turn(sid, synth)
    if not res.get("ok"):
        return None
    ai_reply = data.get("reply")
    _ctx = {k: v for k, v in (sess.get("result") or {}).items()}
    if res.get("counterfactual"):
        _ctx["counterfactual"] = res["counterfactual"]
    if ai_reply and _reply_safe(ai_reply, sess, _ctx):
        res["reply"] = ai_reply.strip()
        if res.get("history"):
            res["history"][-1]["summary"] = res["reply"][:120]
    res["ai_used"] = True
    return res


def turn(sid, text, use_ai=None, current_plan=None):
    sess = SESSIONS.get(sid)
    if not sess:
        return {"ok": False, "error": "会话不存在或已过期"}
    if current_plan is not None:
        sess["current_plan"] = current_plan
    if use_ai is not None:
        sess["use_ai"] = bool(use_ai)
    if sess.get("use_ai"):
        data = _llm_decide(sess, text)
        if data:
            res = _ai_turn(sess, text, data)
            if res:
                res.setdefault("base", sess["base"])
                return res
    res = _rule_turn(sid, text)
    res["ai_used"] = False
    res.setdefault("base", sess["base"])
    return res


def history(sid):
    sess = SESSIONS.get(sid)
    if not sess:
        return {"ok": False, "error": "会话不存在"}
    return {"ok": True, "session_id": sid, "constraint": sess["max_stage"],
            "history": sess["history"], "result": sess["result"]}
