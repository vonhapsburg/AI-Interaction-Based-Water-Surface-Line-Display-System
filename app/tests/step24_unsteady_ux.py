# -*- coding: utf-8 -*-
"""非恒定页交互完整度验收：示例算例、模式配色、auto 提示、回水反推、猜你想问、可拉伸输入。"""
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step24-unsteady-ux.txt")
lines = []
ok = total = 0


def check(label, cond, detail=""):
    global ok, total
    total += 1
    if cond:
        ok += 1
    lines.append(("%s %s" % ("✅" if cond else "❌", label))
                 + (("  —— " + str(detail)) if detail else ""))


def req(path, payload=None, timeout=120):
    if payload is None:
        r = urllib.request.Request(BASE + path)
    else:
        r = urllib.request.Request(
            BASE + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip().startswith("{") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def main():
    code, html = req("/unsteady.html")
    lines.append("=== 页面元素 ===")
    check("页面可访问", code == 200, code)
    if isinstance(html, str):
        check("有 5 个示例算例", html.count('class="preset') >= 5, html.count('class="preset'))
        check("示例含教学与工程两类", 'class="preset eng"' in html)
        check("模式徽标", 'id="modeBadge"' in html)
        check("工程模式回水提示条", 'id="engTip"' in html and "回水顶托" in html)
        check("auto 提示行", 'id="hnHint"' in html)
        check("回水预览按钮与画布", 'id="bwBtn"' in html and 'id="cBw"' in html)
        check("猜你想问", 'id="chatChips"' in html)
        check("输入框可拉伸（textarea）", '<textarea id="chatInput"' in html and "resize:vertical" in html)
        check("模式两色", 'data-mode="teaching"].on' in html and 'data-mode="engineering"].on' in html)
        check("猜你想问含机理题", "为什么洪峰变矮了" in html and "出几道题" in html)
        check("① 图悬停只显示当前帧", "该断面 z 范围" in html and "familySel" in html)
        check("① 图不再逐帧罗列", 'snaps.map((hs,k)=> "t="' not in html)
        check("精度开关在顶部", 'class="precisePill"' in html and html.find('precisePill') < html.find('class="wrap"'))
        check("121 帧时间轴", "frames: 121" in html)
        check("① 图只画当前帧", "淡蓝带" in html and "hsSel" in html)
        check("特征线有选中反馈", 'id="charSrc"' in html and 'classList.toggle("on"' in html)
        check("洪峰沿程在②卡片内", 'peakWrap' in html
              and html.find('peakTable') > html.find('cHydro'))
        check("回水与特征线自动刷新", "autoRefreshSide" in html)
        check("① 图可缩放", "famZoom" in html and 'id="famIn"' in html)
        check("公式速查已扩充", html.count('class="f"') >= 18)
        check("切换模式会自动重算", "caseRunning" in html and "rerunPending" in html)
        check("含闸门开关与参数", 'id="gateOn"' in html and 'id="gateBox"' in html)
        check("闸门结果卡与绘图", 'id="gateCard"' in html and "function drawGate" in html)
        check("闸门预设算例", "预泄腾库容" in html and "关闸削峰" in html)
        check("闸门图有悬停读数", 'id="ovlGate"' in html and 'registerHover("cGate"' in html)
        check("闸门图有图例与刻度", "最高闸上" in html and "水位 / 开度 (m)" in html)
        check("两个随机按钮", 'id="randBtn"' in html and 'id="randGateBtn"' in html)
        check("猜你想问点击可触发随机", 't.includes("随机闸门")' in html)
        check("聊天区可展开", 'id="chatExpand"' in html and 'chatExpanded' in html)
        check("聊天区有拖拽提示", 'chatResizeHint' in html)
        check("展开后请求更多建议", "chatExpanded ? 10 : 3" in html)
        check("展开后渲染全部建议", "r.chips.slice(0, chatExpanded ? 10 : 3)" in html)
        check("闸门支持多段开度（可增删行）",
              'id="gateSchedRows"' in html and "addGateSchedRow" in html
              and 'id="gateSchedAdd"' in html)
        check("对话可改闸门参数", "applyGatePatch" in html and "gate_set" in html)
        check("演进/调度两个标签", 'id="tabSchedule"' in html and "switchView" in html)
        check("调度页有方案对比", 'id="cmpBtn"' in html and "drawCompare" in html)
        check("调度页有 AI 参谋", 'id="aiBtn"' in html and "runAI" in html)
        check("AI 推荐可应用", "aiApply" in html and "need_clarify" in html)
        check("调度页有枚举寻优", 'id="optBtn"' in html and "runOpt" in html)
        check("调度页有 Pareto 与建议书", "drawPareto" in html and "report_md" in html)
        check("调度页有反事实", 'id="cfBtn"' in html and "counterfactual" in html)
        check("调度页有多轮会话", 'id="schedSend"' in html and 'id="schedLog"' in html)
        check("调度页会话可回放", 'id="schedReplay"' in html and "schedule/history" in html)
        check("调度页双栏布局", "schedGrid" in html and "actRow" in html)
        check("调度固定工程模式说明", "schedNote" in html)
        check("无定时覆盖建议", "setInterval(renderChips" not in html)

    if isinstance(html, str):
        check("模式选择器置顶（modebar）", 'class="modebar"' in html and 'id="modeSeg"' in html)
        check("对话记录可拉伸", "resize:vertical" in html and "chat-body" in html)
        check("猜你想问有标题", "猜你想问" in html)
        check("计算中遮罩", 'id="busy"' in html and "spin" in html)
        check("悬停提示层", 'id="tip"' in html and 'class="plot"' in html)
        check("公式速查卡", "公式速查" in html and "Preissmann" in html)
        check("随机算例按钮", 'id="randBtn"' in html)

    lines.append("")
    lines.append("=== 随机算例 ===")
    import json as _json
    got = 0
    for _ in range(3):
        code, jr = req("/api/unsteady/random", {})
        if code == 200 and jr.get("ok"):
            got += 1
            checks_ok = (jr["h_n"] > 0 and jr["mode"] in ("teaching", "engineering")
                         and jr["Qpeak"] > jr["Q0"] and jr["tb"] > jr["tp"]
                         and jr["te"] >= jr["tb"])
            if not checks_ok:
                got -= 1
                break
    check("随机算例 3 次都物理合理", got == 3, got)

    lines.append("")
    lines.append("=== 回水反推接口 ===")
    common = {"L": 500, "b": 5, "m": 0, "n": 0.015, "i0": 0.001, "Q": 10}
    code, j1 = req("/api/unsteady/backwater", dict(common, h_dn="auto"))
    check("auto 可用", code == 200 and j1.get("ok"), j1.get("error"))
    if j1.get("ok"):
        check("auto ≡ 正常水深", abs(j1["h_dn"] - j1["h_n"]) < 1e-9,
              (j1["h_n"], j1["h_dn"]))
        check("auto 不产生回水（长度 0）", abs(j1["backwater_len"]) < 1e-9,
              j1["backwater_len"])

    hn = j1["h_n"] if j1.get("ok") else 1.124
    code, j2 = req("/api/unsteady/backwater", dict(common, h_dn=round(1.5 * hn, 3)))
    check("抬高下游可用", code == 200 and j2.get("ok"), j2.get("error"))
    if j2.get("ok"):
        check("上游被抬高（反推成功）", j2["rise_up"] > 0.005, j2["rise_up"])
        check("型线为 M1 壅水", "M1" in j2["curve_types"], j2["curve_types"])
        check("给出回水长度", j2["backwater_len"] > 0, j2["backwater_len"])
        lines.append("  下游 +%.3f m → 上游 +%.3f m，回水 %.0f m"
                     % (j2["h_dn"] - j2["h_n"], j2["rise_up"], j2["backwater_len"]))

    code, j3 = req("/api/unsteady/backwater", dict(common, h_dn=round(0.9 * hn, 3)))
    check("降低下游可用", code == 200 and j3.get("ok"), j3.get("error"))
    if j3.get("ok"):
        check("上游被拉低（M2 降水）", j3["rise_up"] < 0 and "M2" in j3["curve_types"],
              (j3["rise_up"], j3["curve_types"]))

    lines.append("")
    lines.append("=" * 60)
    lines.append("通过 %d / %d" % (ok, total))
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", OUT)
    print("pass %d / %d" % (ok, total))


if __name__ == "__main__":
    main()
