"""加班机默认皮肤：白字 + 黑阴影。"""
from __future__ import annotations

from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from resources.skin.base import ToolSkin


class OvertimeDefaultSkin(ToolSkin):
    tool_id = "overtime"

    def paint_surface(self, widget: QWidget, surface: str, painter: QPainter) -> None:
        if surface == "panel":
            return
