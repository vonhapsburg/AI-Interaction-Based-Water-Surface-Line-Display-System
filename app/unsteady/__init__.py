# -*- coding: utf-8 -*-
"""非恒定流（扩散波）模块。

与恒定流内核 gvf_core 完全独立：
  · 不导入、不修改 gvf_core；
  · 只通过 /api/unsteady/run 对外提供计算；
  · 单独页面 /unsteady.html 展示，不影响主界面。
"""
from . import solver  # noqa: F401

__all__ = ["solver"]
