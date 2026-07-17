"""透明窗皮肤：按工具隔离。资源与实现都在 resources/skin/。"""
from __future__ import annotations

from util.paths import skin_root
from resources.skin.base import SkinMetrics, ToolSkin, RoleTextStyle
from resources.skin.registry import (
    get_active_skin,
    get_skin,
    list_skins,
    register_skin,
    set_active_skin,
    skin_tool_dir,
)

__all__ = [
    "SkinMetrics",
    "RoleTextStyle",
    "ToolSkin",
    "get_active_skin",
    "get_skin",
    "list_skins",
    "register_skin",
    "set_active_skin",
    "skin_root",
    "skin_tool_dir",
]
