"""加班机悬浮文字组件：字体、对齐、阴影由皮肤定义。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QLabel

from resources.skin.base import ToolSkin, make_font, qt_alignment

_OVERLAY_SURFACE = "overlay"


class OvertimeTextLabel(QLabel):
    """白字 + 阴影；像素大小由皮肤 fit 或 set_pixel_size 设置。"""

    def __init__(
        self,
        role: str,
        text: str = "",
        parent=None,
        *,
        tool_id: str = "overtime",
        align: str | None = None,
        v_align: str | None = None,
        word_wrap: bool | None = None,
    ):
        super().__init__(text, parent)
        self._tool_id = tool_id
        self._role = role
        self.setAttribute(Qt.WA_TranslucentBackground)
        style = self._active_skin().role_style(_OVERLAY_SURFACE, role)
        self._px = style.pixel_size
        if align is not None or v_align is not None:
            self.setAlignment(qt_alignment(
                align or style.align,
                v_align=v_align or style.v_align,
            ))
        else:
            self.setAlignment(qt_alignment(style.align, v_align=style.v_align))
        if word_wrap is not None:
            self.setWordWrap(word_wrap)
        elif style.word_wrap:
            self.setWordWrap(True)
        self._apply_font()

    def _active_skin(self) -> ToolSkin:
        from resources.skin import get_active_skin
        return get_active_skin(self._tool_id)

    def set_pixel_size(self, px: int):
        self._px = max(8, int(px))
        self._apply_font()

    def fit_to_box(self, text: str, max_w: int, max_h: int, *, scale: float = 1.0):
        px = self._active_skin().fit_role_pixel_size(
            _OVERLAY_SURFACE, self._role, text, max_w, max_h, scale=scale,
        )
        self.set_pixel_size(px)

    def _apply_font(self):
        style = self._active_skin().role_style(_OVERLAY_SURFACE, self._role)
        self.setFont(make_font(style, pixel_size=self._px))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setFont(self.font())
        rect = self.rect()
        flags = int(self.alignment())
        if self.wordWrap():
            flags |= int(Qt.TextWordWrap)
        try:
            fill, shadow = self._active_skin().paint_text_shadow_style()
        except Exception:
            fill, shadow = QColor(255, 255, 255), QColor(0, 0, 0, 160)
        for dx, dy in ((1, 1), (1, 0), (0, 1)):
            p.setPen(shadow)
            p.drawText(rect.translated(dx, dy), flags, self.text())
        p.setPen(fill)
        p.drawText(rect, flags, self.text())
        p.end()

    def refresh_skin(self):
        self._apply_font()
        self.update()
