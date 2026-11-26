# -*- coding: utf-8 -*-
"""非恒定流的对话通道：自然语言 → 工况参数；解释 / 出题 / 追问建议。

与恒定流的 chat 分开，因为要解析的对象不同（过程线参数而不是单点水深）。
所有数值只来自参数表与内核输出，模型不产生水力数值。
"""
import random
import re

from agent import llm

UNSTEADY_KEYS = ("L", "b", "m", "n", "i0", "Q0", "Qpeak",
                 "t_peak_h", "t_base_h", "t_end_h", "h_dn", "Nx")

EXTRACT_SYS = """你是«明渠非恒定流»教学系统的助手。把用户这句话转成工况参数，输出严格 JSON。

字段（缺的填 -1）：
  L        渠长 m
  b        底宽 m
  m        边坡系数（矩形填 0）
  n        糙率
  i0       底坡
  Q0       初始流量 m³/s
  Qpeak    峰值流量 m³/s
  t_peak_h 涨水历时 小时
  t_base_h 回落到初始流量 小时
  t_end_h  模拟总时长 小时
  h_dn     下游水位 m（没提到就填 -1，表示按正常水深）
  Nx       网格数（没提到填 -1）

规则：
1. 只转换用户明确说过的量；没说的一律 -1，禁止猜测。
2. 时间统一换算成小时，长度统一成 m，流量成 m³/s。
3. 中文数字要换算（“两小时”→2，“十方”→10）。
4. 提问（为什么/怎么/是什么/能不能解释）→ 返回 {"act":"ask"}。
5. 出题/练习 → {"act":"quiz"}；若同时说“换一批/再来几道/不满意/不好” → {"act":"quiz","more":true}。
6. 否则 {"act":"set","params":{...},"reply":"一句话确认"}。
只输出 JSON，不要解释。"""

ANSWER_SYS = """你是«明渠非恒定流»教学系统的助手，回答与当前工况相关的问题。

要求：
1. 口语化、简洁（3–6 句），先说结论再说理由。
2. 只能使用【当前工况】与【最近一次结果】里出现的数字，不编造。
3. 严禁使用“实测”“实验测得”“测点数据”这类词——本系统没有实测数据。
   涉及计算结果时写“按本模型计算”，理论估算写“按公式估算”。
4. 概念题、公式题、给定了条件的计算题：直接依据公式回答，不要说“缺少题面”。
   只有真的缺关键参数时，才问一个最短的澄清问题（只问一个）。
5. 【当前模式】已注明：
   - 若已是工程模式（全方程），不要建议“切到全方程/工程模式”；
   - 若还是教学模式（扩散波），涉及回水顶托时可建议切工程模式。
6. 涌波、溃坝、闸门急变：明确说明本系统不支持这类强间断（需激波捕捉），
   然后给出理论量级与判据，例如 c=√(gA/B)、Ritter 解 h(t)、涌波速度 ≈ √(g h)。
7. 把最重要的提醒或结论用【】包起来（最多两处）；风险提示用「注意：」开头。
8. 不要输出 JSON。"""

QUIZ_SYS = """你是水力学习题老师。依据下面的非恒定流算例，出 3 道题并给答案与解析。

硬性要求：
1. 三题类型必须互不相同，从这五类里选三：概念辨析、数值计算、结果对比、误差与验证、工程应用。
2. 不得与【已出过的题】重复；若用户说“换一批”，要换类型、换角度。
3. 只能使用【当前工况】【结果】里出现的数字，不得编造。
4. 每题的答案要能由给定数据直接算出或直接判断。
5. 输出严格 JSON：{"items":[{"type":"概念辨析","q":"题面","a":"答案","why":"解析"}]}"""


def _gate_text(p):
    g = (p or {}).get("gate") or {}
    if not g:
        return ""
    bits = []
    if g.get("s_pct") is not None:
        bits.append("闸位%g%%" % float(g["s_pct"]))
    if g.get("b_g") is not None:
        bits.append("闸宽%g m" % float(g["b_g"]))
    if g.get("sched"):
        bits.append("开度过程=" + "→".join("%gh:%.2fm" % (float(a), float(b))
                                          for a, b in g["sched"]))
    else:
        if g.get("a0") is not None:
            bits.append("初始开度%.2f m" % float(g["a0"]))
        if g.get("a_end") is not None:
            bits.append("终止开度%.2f m" % float(g["a_end"]))
        if g.get("t_close") is not None:
            bits.append("%g h 开始关" % float(g["t_close"]))
        if g.get("t_ramp") is not None:
            bits.append("关闭历时%g h" % float(g["t_ramp"]))
    return "含闸门：" + "，".join(bits)


def parse_gate(text):
    """把“2 小时后关到 0.5 米”一类的话解析成闸门参数；没有数字则返回 None。"""
    t = (text or "").strip()
    if not t or not any(w in t for w in ("闸", "开度", "关到", "关至", "关小", "关大")):
        return None
    out = {}
    m = re.search(r"闸位[^0-9]{0,8}(\d+(?:\.\d+)?)\s*(?:%|％)", t)
    if m:
        out["s_pct"] = float(m.group(1))
    m = re.search(r"闸宽[^0-9]{0,8}(\d+(?:\.\d+)?)", t)
    if m:
        out["b_g"] = float(m.group(1))
    m = re.search(r"(?:初始|起始)开度[^0-9]{0,6}(\d+(?:\.\d+)?)", t)
    if m:
        out["a0"] = float(m.group(1))

    # 多段：“1 小时关到 0.6，再过 2 小时关到 0.3”
    parts = re.split(r"[，,；;。]", t)
    segs = []
    acc = 0.0
    for part in parts:
        mm = re.search(r"(\d+(?:\.\d+)?)\s*(?:个)?小时(?:后|之后)?[^0-9]{0,10}"
                       r"(?:关到|关至|降到|减到)\s*(\d+(?:\.\d+)?)", part)
        if not mm:
            continue
        dt = float(mm.group(1))
        aa = float(mm.group(2))
        if part.strip().startswith("再") or "再过" in part:
            acc += dt
        else:
            acc = dt
        segs.append((round(acc, 3), aa))
    if len(segs) >= 2:
        out["sched"] = segs
    elif len(segs) == 1:
        out["t_close"] = segs[0][0]
        out["a_end"] = segs[0][1]
        out["t_ramp"] = 0.0

    m = re.search(r"(?:关闭|关闸|关门)历时[^0-9]{0,6}(\d+(?:\.\d+)?)\s*小时", t)
    if m:
        out["t_ramp"] = float(m.group(1))

    # “用 0.5 小时关到 0.4” → 从 0 时刻开始、线性关闭
    m = re.search(r"用\s*(\d+(?:\.\d+)?)\s*小时[^0-9]{0,6}"
                  r"(?:关到|关至|降到)\s*(\d+(?:\.\d+)?)", t)
    if m:
        out.pop("sched", None)
        out["t_close"] = 0.0
        out["t_ramp"] = float(m.group(1))
        out["a_end"] = float(m.group(2))

    return out or None


def parse_schedule_intent(text):
    """调度口语 → 约束/目标（规则，不经过模型）。"""
    t = text or ""
    out = {"constraints": {}, "objective": None}
    m = re.search(r"(?:闸上|上游|闸前)[^0-9]{0,8}(?:水位)?[^0-9]{0,6}"
                  r"(?:不超|不超过|不能超过|低于|小于|≤|<=)\s*(\d+(?:\.\d+)?)", t)
    if m:
        out["constraints"]["max_stage"] = float(m.group(1))
    if re.search(r"削峰|峰值|压低|压下来|压到", t):
        out["objective"] = "min_peak"
    nums = [float(x) for x in re.findall(r"关到\s*(\d+(?:\.\d+)?)", t)]
    if re.search(r"比较|对比|方案", t):
        for x in re.findall(r"(?:和|与|、|,|，)\s*(\d+(?:\.\d+)?)", t):
            v = float(x)
            if 0.2 <= v <= 2.0 and v not in nums:
                nums.append(v)
    if nums:
        out["plans_a"] = nums
    if not out["constraints"] and not out["objective"] and not nums:
        return None
    return out


def _mode_label(params):
    m = (params or {}).get("mode")
    if m == "engineering":
        return "工程模式（圣维南全方程，保留惯性项）"
    return "教学模式（扩散波，忽略惯性项）"


def _fmt_params(p):
    names = {"L": "渠长", "b": "底宽", "m": "边坡", "n": "糙率", "i0": "底坡",
             "Q0": "初始流量", "Qpeak": "峰值流量",
             "t_peak_h": "涨水历时(h)", "t_base_h": "回落历时(h)",
             "t_end_h": "模拟时长(h)", "h_dn": "下游水位", "Nx": "网格数"}
    out = []
    for k, v in (p or {}).items():
        if k in names and v not in (None, "", -1):
            out.append("%s=%s" % (names[k], v))
    gt = _gate_text(p)
    if gt:
        out.append(gt)
    return "，".join(out) if out else "（尚未设置）"


def extract(text, params):
    """返回 {'act': 'set'|'ask'|'quiz', 'params': {...}, 'reply': str, 'more': bool}。"""
    user = "当前工况：%s\n\n用户：%s" % (_fmt_params(params), text)
    r = llm.chat([{"role": "system", "content": EXTRACT_SYS},
                  {"role": "user", "content": user}],
                 max_tokens=500, temperature=0.1)
    if "error" in r:
        return {"act": "error", "reply": "模型通道不可用：%s" % r["error"]}
    try:
        data = llm._extract_json(r["text"])
    except Exception:
        data = {}
    act = (data.get("act") or "").lower()
    if act == "quiz":
        return {"act": "quiz", "more": bool(data.get("more"))}
    if act == "ask":
        return {"act": "ask"}
    if act != "set":
        return {"act": "ask"}          # 解析失败时按提问处理，不瞎改参数
    patch = {}
    for k, v in (data.get("params") or {}).items():
        if k not in UNSTEADY_KEYS:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv == -1 or fv is None:
            continue
        patch[k] = fv
    return {"act": "set", "params": patch,
            "reply": data.get("reply") or "已按你的描述更新工况。"}


def answer(text, params, summary):
    user = ("【当前模式】%s\n【当前工况】%s\n\n【最近一次结果】%s\n\n用户问题：%s"
            % (_mode_label(params), _fmt_params(params),
               summary or "（还没有计算）", text))
    r = llm.chat([{"role": "system", "content": ANSWER_SYS},
                  {"role": "user", "content": user}],
                 max_tokens=700, temperature=0.35)
    if "error" in r:
        return {"reply": "模型通道不可用：%s" % r["error"]}
    return {"reply": r.get("text", "").strip()}


def _local_quiz(params, summary, exclude):
    """模型不给力时的兜底题库；参数不全时只出概念题，绝不报错。"""
    import re as _re
    p = params or {}
    L = p.get("L"); Q0 = p.get("Q0"); Qp = p.get("Qpeak")
    tp = p.get("t_peak_h"); tb = p.get("t_base_h")
    hn = ck = pk = None
    m = _re.search(r"正常水深\s*([0-9.]+)", summary or "")
    if m:
        hn = float(m.group(1))
    m = _re.search(r"c_k\s*=\s*([0-9.]+)", summary or "")
    if m:
        ck = float(m.group(1))
    m = _re.search(r"([0-9.]+)\s*→\s*([0-9.]+)", summary or "")
    if m:
        pk = float(m.group(2))

    out = []

    def add(t, q, a, w):
        out.append({"type": t, "q": q, "a": a, "why": w})

    def num(x):
        return isinstance(x, (int, float)) and x is not None

    try:
        if num(Qp) and num(Q0) and num(tp) and tp:
            add("数值计算", "涨水阶段流量平均变化率是多少（m³/s per h）？",
                "(%.1f − %.1f)/%.1f = %.2f m³/s per h" % (Qp, Q0, tp, (Qp - Q0) / tp),
                "三角形过程线涨水段斜率 = (Q_peak − Q₀)/t_peak。")
    except Exception:
        pass
    try:
        if num(Qp) and num(Q0) and num(tp) and num(tb) and tb > tp:
            add("数值计算", "回落阶段流量平均变化率是多少（m³/s per h）？",
                "(%.1f − %.1f)/(%.1f − %.1f) = %.2f m³/s per h"
                % (Qp, Q0, tb, tp, (Qp - Q0) / (tb - tp)),
                "回落段历时 = t_base − t_peak，斜率取负。")
    except Exception:
        pass
    try:
        if num(pk) and num(Qp) and Qp:
            add("结果对比", "下游断面峰值相对上游削减了多少（%）？",
                "(%.1f − %.1f)/%.1f = %.1f%%" % (Qp, pk, Qp, (Qp - pk) / Qp * 100),
                "峰值沿程衰减即坦化，削峰率 = (Q_peak − Q_down)/Q_peak。")
    except Exception:
        pass
    try:
        if num(L) and num(ck) and ck:
            add("数值计算", "按运动波速估算洪水波传播时间（h）？",
                "%.0f/%.2f ≈ %.2f h" % (L, ck, L / ck / 3600),
                "传播时间 ≈ L/c_k；实际峰现还要加上坦化造成的滞后。")
    except Exception:
        pass

    # 以下四题不依赖具体参数，保证任何情况下都能凑够 3 题
    add("概念辨析", "为什么同一流量在涨水段和落水段对应不同水深？",
        "涨水段 ∂h/∂t>0，附加比降使同流量下 h 偏小；落水段相反，于是形成绳套。",
        "h–Q 多值来自非恒定流的附加比降项。")
    add("误差与验证", "质量守恒误差接近 0 能说明结果正确吗？",
        "不能。它只说明离散格式守恒；物理正确性要用与恒定解的回归对比来验证。",
        "格式校验 ≠ 物理验证。")
    add("概念辨析", "扩散波与全方程最本质的差别是什么？",
        "扩散波忽略惯性项，是抛物型；全方程保留惯性项，是双曲型，能描述扰动以 v±c 传播。",
        "惯性项决定扰动传播的有限速度。")
    add("工程应用", "若下游水位抬高，上游水深会怎样变化？为什么？",
        "会抬高（回水顶托），扰动沿 v−c 上溯，缓流条件下成立。",
        "下游水位是缓流的下游控制条件。")

    gate = (params or {}).get("gate") or {}
    gate_qs = []
    if gate.get("on") or gate.get("s_pct") is not None:
        gate_qs = [
            {"type": "概念辨析", "q": "闸门关小以后，为什么闸上水位会升高？",
             "a": "过闸流量被压小，上游来水一时大于下泄，水量在闸前蓄积，水位抬升到新的平衡。",
             "why": "闸门是内边界，起节流作用；水位上升是水量平衡的结果。"},
            {"type": "工程应用", "q": "关闸削峰的代价是什么？",
             "a": "上游水位抬高、闸前库容被占用，防洪风险从下游转移到闸上。",
             "why": "削峰与蓄水是一体两面，调度要同时看下游峰值和上游最高水位。"},
            {"type": "数值计算", "q": "开度从 a₀ 关到 a₁，按线性关闭，t 时刻的开度怎么算？",
             "a": "a(t) = a₀ + (a₁ − a₀)·(t − t_close)/t_ramp（t 在关闭区间内）。",
             "why": "线性插值，区间外取端点值。"},
        ]

    def norm(t):
        return "".join(str(t or "").split())

    ex = [norm(e) for e in (exclude or [])]
    pool = [it for it in out if norm(it["q"]) not in ex]
    if not pool:
        pool = out
    if gate_qs:
        rest = [x for x in pool if x not in gate_qs]
        return (gate_qs[:2] + rest[:1])[:3]
    k = len(exclude or []) % max(len(pool), 1)
    pool = pool[k:] + pool[:k]
    return pool[:3]


def quiz(params, summary, exclude=None):
    """出 3 道题；exclude 是最近出过的题干，避免重复。"""
    ex = [q for q in (exclude or []) if q][-9:]
    user = ("【当前工况】%s\n\n【结果】%s\n\n【已出过的题】%s"
            % (_fmt_params(params), summary or "（还没有计算）",
               "；".join(ex) if ex else "（无）"))
    r = llm.chat([{"role": "system", "content": QUIZ_SYS},
                  {"role": "user", "content": user}],
                 max_tokens=900, temperature=random.uniform(0.7, 0.95))
    if "error" in r:
        return {"items": [], "error": r["error"]}
    try:
        data = llm._extract_json(r["text"])
        items = list(data.get("items") or [])
    except Exception:
        items = []
    for it in items:
        it.setdefault("type", "综合")

    # 后处理：模型可能无视“别重复”，这里做相似度去重；不足 3 题再补一次
    def _norm(t):
        return "".join(str(t or "").split())

    def _dup(a, b):
        import difflib
        return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio() > 0.85

    kept = []
    for it in items:
        if any(_dup(it.get("q", ""), ex) for ex in ex):
            continue
        if any(_dup(it.get("q", ""), k.get("q", "")) for k in kept):
            continue
        kept.append(it)
    if len(kept) < 3:
        ex2 = ex + [k.get("q", "") for k in kept]
        user2 = ("【当前工况】%s\n\n【结果】%s\n\n【已出过的题】%s\n\n"
                 "上面这些题一律不要再出，连改写都不要。"
                 % (_fmt_params(params), summary or "（还没有计算）",
                    "；".join(ex2) if ex2 else "（无）"))
        r2 = llm.chat([{"role": "system", "content": QUIZ_SYS},
                       {"role": "user", "content": user2}],
                      max_tokens=900, temperature=0.95)
        if "text" in r2:
            try:
                more = list(llm._extract_json(r2["text"]).get("items") or [])
            except Exception:
                more = []
            for it in more:
                it.setdefault("type", "综合")
                if any(_dup(it.get("q", ""), ex2) for ex in ex2):
                    continue
                if any(_dup(it.get("q", ""), k.get("q", "")) for k in kept):
                    continue
                kept.append(it)
                if len(kept) >= 3:
                    break
    if len(kept) < 3:
        fill = _local_quiz(params, summary, ex + [k.get("q", "") for k in kept])
        for it in fill:
            if len(kept) >= 3:
                break
            kept.append(it)

    # 含闸门时，至少 2 题要问闸门，避免“问闸门却出了网格题”
    if bool((params or {}).get("gate")):
        gate_pool = [
            {"type": "概念辨析", "q": "闸门关小以后，为什么闸上水位会升高？",
             "a": "过闸流量被压小，上游来水一时大于下泄，水量在闸前蓄积，水位抬升到新的平衡。",
             "why": "闸门是内边界，起节流作用。"},
            {"type": "工程应用", "q": "关闸削峰的代价是什么？",
             "a": "上游水位抬高、闸前库容被占用，防洪风险从下游转移到闸上。",
             "why": "削峰与蓄水是一体两面。"},
        ]
        have = sum(1 for it in kept if "闸" in (it.get("q") or ""))
        if have < 2:
            add = [q for q in gate_pool
                   if not any(_dup(q["q"], it.get("q", "")) for it in kept)]
            kept = (add + kept)[:3]
    return {"items": kept}


# ---------------- 猜你想问：按上一句做延伸 ----------------
_CHIP_MAP = [
    (("圣维南", "方程", "连续", "动量"), [
        "特征线 v±c 怎么用？", "扩散波和全方程差在哪？", "为什么保留惯性项？"]),
    (("绳套", "环", "多值"), [
        "环向由什么决定？", "为什么涨水段水位偏低？", "绳套面积代表什么？"]),
    (("洪峰", "削峰", "坦化", "变矮"), [
        "坦化为什么让峰值变低？", "峰现时间怎么算？", "下游水位会改变削峰吗？"]),
    (("回水", "顶托", "上溯"), [
        "回水长度怎么估算？", "缓流为什么能上溯？", "急流能上溯吗？"]),
    (("特征线", "传播", "波速", "c_k", "ck"), [
        "v+c 和 v−c 分别指什么？", "临界流时特征线怎样？", "怎么用 Fr 判断？"]),
    (("涌波", "溃坝", "间断"), [
        "Ritter 解是什么？", "本系统为什么算不了？", "需要什么方程？"]),
    (("模式", "全方程", "扩散波", "工程"), [
        "工程模式能算什么？", "扩散波什么时候够用？", "两者结果差多少？"]),
    (("出题", "练习", "题"), [
        "再来一批", "出计算题", "出概念题"]),
]

_DEFAULT_CHIPS = ["为什么洪峰变矮了？", "绳套为什么是逆时针？", "回水为什么能往上游传？",
                  "c_k 和峰现时间怎么算？", "扩散波和全方程差在哪？",
                  "请给我随机生成一份随机算例", "请给我随机生成一份随机闸门",
                  "工程模式能算什么？", "出题模式怎么用？",
                  "精度优先什么时候该勾？", "特征线怎么判断能不能上溯？"]
_chip_ptr = 0


def chips(text, params=None, summary="", limit=3):
    """依据上一句生成追问建议（规则，不花算力）；limit 可为展开模式放大。"""
    t = text or ""
    limit = max(3, min(int(limit or 3), 14))
    global _chip_ptr
    out = []
    for keys, pool in _CHIP_MAP:
        if any(k in t for k in keys):
            out.extend(list(pool))
            break
    if summary and "这个结果里最值得注意的是什么？" not in out:
        out.append("这个结果里最值得注意的是什么？")
    n = len(_DEFAULT_CHIPS)
    for k in range(n):
        c = _DEFAULT_CHIPS[(_chip_ptr + k) % n]
        if c not in out:
            out.append(c)
        if len(out) >= limit:
            break
    _chip_ptr = (_chip_ptr + 2) % n
    return {"chips": out[:limit]}
