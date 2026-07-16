"""透明窗皮肤：按工具隔离。资源在 resources/skin/，实现在本包。"""
from __future__ import annotations

from util.paths import skin_root
from util.skin.base import SkinMetrics, ToolSkin
from util.skin.registry import (
    get_active_skin,
    get_skin,
    list_skins,
    set_active_skin,
    skin_tool_dir,
)

__all__ = [
    "SkinMetrics",
    "ToolSkin",
    "get_active_skin",
    "get_skin",
    "list_skins",
    "set_active_skin",
    "skin_root",
    "skin_tool_dir",
]
