"""弹幕机悬浮组件：尺寸、字体、对齐、折行均由皮肤定义。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
    QGraphicsOpacityEffect,
)

from resources.skin.base import ToolSkin, make_font, qt_alignment

_FADE_IN_MS = 300
_STAY_MS = 3000
_FADE_OUT_MS = 500


class DanmuBubbleBase(QWidget):
    """弹幕/礼物气泡基类：生命周期固定，外观委托皮肤。"""

    _SKIN_SURFACE = "bubble"

    def __init__(self, skin: ToolSkin, parent=None):
        super().__init__(parent)
        self._tool_id = skin.tool_id or "danmu"
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._skin_labels: list[tuple[QLabel, str, str]] = []

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)

    def _active_skin(self) -> ToolSkin:
        from resources.skin import get_active_skin
        return get_active_skin(self._tool_id)

    def _bind_skin_label(self, lbl: QLabel, role: str, extra: str = ""):
        self._skin_labels.append((lbl, role, extra))
        lbl.setStyleSheet(self._active_skin().stylesheet(role) + extra)

    def _apply_role_label(
        self,
        lbl: QLabel,
        role: str,
        text: str,
        *,
        width: int | None = None,
        word_wrap: bool | None = None,
    ):
        skin = self._active_skin()
        style = skin.role_style(self._SKIN_SURFACE, role)
        lbl.setText(text)
        lbl.setFont(make_font(style))
        lbl.setAlignment(qt_alignment(style.align, v_align=style.v_align))
        if width is not None:
            lbl.setFixedWidth(width)
        if word_wrap is not None:
            lbl.setWordWrap(word_wrap)
        elif style.word_wrap:
            lbl.setWordWrap(True)
        extra = style.style_extra
        if style.bold and "font-weight" not in extra:
            extra = (extra + " font-weight:600;").strip()
        self._bind_skin_label(lbl, role, (" " + extra) if extra else "")

    def _start_lifecycle(self):
        anim_in = QPropertyAnimation(self._effect, b"opacity", self)
        anim_in.setStartValue(0.0)
        anim_in.setEndValue(1.0)
        anim_in.setDuration(_FADE_IN_MS)
        anim_in.setEasingCurve(QEasingCurve.OutCubic)

        anim_out = QPropertyAnimation(self._effect, b"opacity", self)
        anim_out.setStartValue(1.0)
        anim_out.setEndValue(0.0)
        anim_out.setDuration(_FADE_OUT_MS)
        anim_out.setEasingCurve(QEasingCurve.InCubic)
        anim_out.finished.connect(self._remove)

        anim_in.finished.connect(
            lambda: QTimer.singleShot(_STAY_MS, anim_out.start)
        )
        anim_in.start()

    def _remove(self):
        self.hide()
        self.deleteLater()

    def refresh_skin(self):
        skin = self._active_skin()
        for lbl, role, extra in self._skin_labels:
            lbl.setStyleSheet(skin.stylesheet(role) + extra)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        try:
            self._active_skin().paint_surface(self, self._SKIN_SURFACE, p)
        except Exception:
            pass
        p.end()


class DanmuChatBubble(DanmuBubbleBase):
    """聊天/点赞/关注弹幕。"""

    def __init__(self, skin: ToolSkin, user: str, content: str, parent=None):
        super().__init__(skin, parent)
        self._build(user, content)
        self._start_lifecycle()

    def _build(self, user: str, content: str):
        skin = self._active_skin()
        body_style = skin.role_style(self._SKIN_SURFACE, "body")
        user_style = skin.role_style(self._SKIN_SURFACE, "user")

        body_font = make_font(body_style)
        user_font = make_font(user_style)
        fm_body = QFontMetrics(body_font)
        fm_user = QFontMetrics(user_font)

        cw = fm_body.horizontalAdvance("国")
        min_w = cw * body_style.min_chars
        max_w = cw * body_style.max_chars
        user_w = fm_user.horizontalAdvance(user)
        text_w = fm_body.horizontalAdvance(content)
        slack = max(body_style.slack_px, cw // 4)

        need_wrap = text_w > max_w
        content_w = max_w if need_wrap else max(min_w, user_w, text_w) + slack

        m = skin.metrics()
        h_margin = m.pad_h + m.fade_w
        self.setFixedWidth(content_w + h_margin * 2)

        row_align = qt_alignment(
            str(skin.layout_value(self._SKIN_SURFACE, "row_align", "center")),
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(h_margin, m.pad_v, h_margin, m.pad_v)
        lay.setSpacing(int(skin.layout_value(self._SKIN_SURFACE, "spacing", 2)))
        lay.setAlignment(row_align)

        id_lbl = QLabel()
        self._apply_role_label(id_lbl, "user", user, width=content_w)

        msg_lbl = QLabel()
        self._apply_role_label(
            msg_lbl, "body", content, width=content_w, word_wrap=need_wrap,
        )

        lay.addWidget(id_lbl)
        lay.addWidget(msg_lbl)


class DanmuGiftBubble(DanmuBubbleBase):
    """礼物弹幕。"""

    _SKIN_SURFACE = "gift_bubble"

    def __init__(self, skin: ToolSkin, msg, suffix: str = "", parent=None):
        super().__init__(skin, parent)
        self._build(msg, suffix)
        self._start_lifecycle()

    def _build(self, msg, suffix: str = ""):
        skin = self._active_skin()
        body_style = skin.role_style(self._SKIN_SURFACE, "body")
        user_style = skin.role_style(self._SKIN_SURFACE, "user")
        body_font = make_font(body_style)
        user_font = make_font(user_style)
        fm_body = QFontMetrics(body_font)
        fm_user = QFontMetrics(user_font)

        gift_line = f"送出了 {msg.gift} ×{msg.count}"
        text_slack = int(skin.layout_value(self._SKIN_SURFACE, "text_slack", 4))
        text_cw = max(
            fm_user.horizontalAdvance(msg.user),
            fm_body.horizontalAdvance(gift_line),
            fm_user.horizontalAdvance(suffix) if suffix else 0,
        ) + text_slack

        m = skin.metrics()
        icon_sz = m.gift_icon_size
        icon_gap = m.gift_icon_gap
        from tools.tool_common import load_gift_pixmap
        pixmap = load_gift_pixmap(msg.gift, icon_sz)
        h_margin = m.pad_h + m.fade_w
        inner_w = text_cw + ((icon_sz + icon_gap) if pixmap else 0)
        self.setFixedWidth(inner_w + h_margin * 2)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(h_margin, m.pad_v, h_margin, m.pad_v)
        outer.setSpacing(icon_gap)
        outer.setAlignment(Qt.AlignVCenter)
        if pixmap:
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(icon_sz, icon_sz)
            icon_lbl.setPixmap(pixmap)
            icon_lbl.setStyleSheet("background: transparent;")
            outer.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(int(skin.layout_value(self._SKIN_SURFACE, "spacing", 2)))

        id_lbl = QLabel()
        self._apply_role_label(id_lbl, "user", msg.user, width=text_cw)

        gift_lbl = QLabel()
        self._apply_role_label(
            gift_lbl, "body", gift_line, width=text_cw, word_wrap=False,
        )

        text_col.addWidget(id_lbl)
        text_col.addWidget(gift_lbl)

        if suffix:
            suf_lbl = QLabel()
            self._apply_role_label(suf_lbl, "suffix", suffix, width=text_cw)
            text_col.addWidget(suf_lbl)

        outer.addLayout(text_col)


def create_chat_bubble(
    skin: ToolSkin, parent: QWidget | None, user: str, content: str,
) -> DanmuBubbleBase:
    return DanmuChatBubble(skin, user, content, parent)


def create_gift_bubble(
    skin: ToolSkin, parent: QWidget | None, msg, suffix: str = "",
) -> DanmuBubbleBase:
    return DanmuGiftBubble(skin, msg, suffix, parent)
