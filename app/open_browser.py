# -*- coding: utf-8 -*-
"""等 server.py 写出 .port 文件后，用系统默认浏览器打开页面。

这样即使 8000 被占用、服务退到 8001，浏览器也能打开正确地址。
"""
import os
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT_FILE = os.path.join(ROOT, ".port")


def main():
    try:
        os.remove(PORT_FILE)
    except OSError:
        pass
    for _ in range(160):                 # 最多等 80 秒
        if os.path.exists(PORT_FILE):
            try:
                with open(PORT_FILE, encoding="utf-8") as f:
                    port = f.read().strip() or "8000"
            except OSError:
                port = "8000"
            try:
                webbrowser.open("http://127.0.0.1:%s" % port)
            except Exception:
                pass
            return
        time.sleep(0.5)


if __name__ == "__main__":
    main()
