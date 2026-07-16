"""皮肤类型与抽象接口。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class SkinMetrics:
    fade_w: int = 22
    pad_h: int = 12
    pad_v: int = 7
    gift_icon_size: int = 32
    gift_icon_gap: int = 8
    extra: Mapping[str, Any] | None = None


class ToolSkin:
    """单个工具的一套皮肤。不同 tool_id 的皮肤不可互换。"""

    tool_id: str = ""
    skin_id: str = "default"
    name: str = "默认"
    version: int = 1

    def __init__(self, root: Path, meta: dict[str, Any]):
        self.root = root
        self.meta = meta
        self.skin_id = str(meta.get("id") or root.name)
        self.name = str(meta.get("name") or self.skin_id)
        self.version = int(meta.get("version") or 1)
        tool = meta.get("tool")
        if tool:
            self.tool_id = str(tool)

    def metrics(self) -> SkinMetrics:
        m = self.meta.get("metrics") or {}
        return SkinMetrics(
            fade_w=int(m.get("fade_w", 22)),
            pad_h=int(m.get("pad_h", 12)),
            pad_v=int(m.get("pad_v", 7)),
            gift_icon_size=int(m.get("gift_icon_size", 32)),
            gift_icon_gap=int(m.get("gift_icon_gap", 8)),
            extra=m.get("extra"),
        )

    def supports(self, surface: str) -> bool:
        surfaces = self.meta.get("surfaces")
        if not surfaces:
            return True
        return surface in surfaces

    def color(self, key: str, default: QColor | None = None) -> QColor:
        raw = (self.meta.get("colors") or {}).get(key)
        if raw is None:
            return default or QColor(255, 255, 255)
        return _parse_color(raw, default or QColor(255, 255, 255))

    def stylesheet(self, role: str) -> str:
        styles = self.meta.get("styles") or {}
        if role in styles:
            return str(styles[role])
        c = self.color(role, QColor(255, 255, 255))
        return (
            f"color: rgba({c.red()},{c.green()},{c.blue()},{c.alpha()});"
            " background: transparent;"
        )

    def paint_surface(
        self,
        widget: QWidget,
        surface: str,
        painter: QPainter,
    ) -> None:
        return

    def paint_text_shadow_style(self) -> tuple[QColor, QColor]:
        colors = self.meta.get("colors") or {}
        fill = _parse_color(colors.get("text_fill"), QColor(255, 255, 255))
        shadow = _parse_color(colors.get("text_shadow"), QColor(0, 0, 0, 160))
        return fill, shadow


def _parse_color(raw: Any, default: QColor) -> QColor:
    if raw is None:
        return QColor(default)
    if isinstance(raw, str):
        c = QColor(raw)
        return c if c.isValid() else QColor(default)
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        a = int(raw[3]) if len(raw) > 3 else 255
        return QColor(int(raw[0]), int(raw[1]), int(raw[2]), a)
    if isinstance(raw, dict):
        return QColor(
            int(raw.get("r", 255)),
            int(raw.get("g", 255)),
            int(raw.get("b", 255)),
            int(raw.get("a", 255)),
        )
    return QColor(default)
