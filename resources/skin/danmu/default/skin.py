"""弹幕机默认皮肤：横向黑渐变底。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget

from resources.skin.base import ToolSkin


class DanmuDefaultSkin(ToolSkin):
    tool_id = "danmu"

    def paint_surface(self, widget: QWidget, surface: str, painter: QPainter) -> None:
        if surface not in ("bubble", "gift_bubble"):
            return
        m = self.metrics()
        w = widget.width()
        h = widget.height()
        fade = m.fade_w / w if w > 0 else 0.2

        mid = self.color("gradient_mid", QColor(0, 0, 0, 128))
        edge = self.color("gradient_edge", QColor(0, 0, 0, 0))

        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0.0, edge)
        grad.setColorAt(fade, mid)
        grad.setColorAt(1.0 - fade, mid)
        grad.setColorAt(1.0, edge)

        painter.setPen(Qt.NoPen)
        painter.setBrush(grad)
        painter.drawRect(0, 0, w, h)
