# -*- coding: utf-8 -*-
"""并发测试：模型回答进行中，普通计算要等多久。"""
import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def post(path, payload, timeout=120):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8")
    return time.time() - t0, body


result = {}


def chat():
    try:
        result["chat"] = post("/api/turn", {"text": "我想研究一下水力学里关于缓坡和陡坡交界处水面线的一些有趣现象，你觉得应该从哪几个角度入手比较合适呢？请展开讲讲你的想法和建议"})
    except Exception as e:
        result["chat"] = (0, "ERR " + str(e))


th = threading.Thread(target=chat)
th.start()
time.sleep(0.4)

solve_payload = {"overrides": {
    "Q": 10,
    "reaches": [{"L": 300, "i": 0.001, "b": 5, "m": 0, "n": 0.015},
                {"L": 300, "i": 0.02, "b": 5, "m": 0, "n": 0.015}],
    "downstream": "free_outfall", "upstream": "none"}}
dt, body = post("/api/solve", solve_payload, timeout=120)
print("模型回答进行中，普通计算耗时: %.2f s" % dt)

th.join(120)
ct, cbody = result.get("chat", (0, ""))
print("模型回答耗时: %.2f s" % ct)
