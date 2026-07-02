"""
widgets.py — 通用主题 Widget 库

收录与当前主题自动联动的自定义控件。
所有控件通过 theme.on_change() 注册回调，切换主题即时生效。

使用:
    from pages.widgets import ThemedComboBox

    combo = ThemedComboBox()
    combo.addItems(["选项A", "选项B", "选项C"])
    combo.setFixedHeight(34)
    combo.setMinimumWidth(160)
    combo.currentTextChanged.connect(on_change)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (
    QWidget, QFrame, QHBoxLayout, QVBoxLayout,
    QPushButton,
)
from PySide6.QtCore  import Qt, QPoint, Signal, Property, QPropertyAnimation, QEasingCurve
from PySide6.QtGui   import QPainter, QColor, QPen, QFont, QBitmap
from PySide6.QtCore  import QRectF

import pages.theme as _theme
import config as _cfg


# ─────────────────────────────────────────────
# ThemedComboBox — 主题自适应下拉选择框
# ─────────────────────────────────────────────

class _DropPopup(QFrame):
    """
    完全自制的下拉弹窗。

    原理：
      Qt.FramelessWindowHint + WA_TranslucentBackground 设在构造函数（第一次
      show 之前），所以透明度完全生效。paintEvent 手绘圆角背景（Antialiasing），
      圆角以外区域物理透明，彻底解决"矩形背景透出"和"锯齿"两个问题。

    边框：active_line 颜色，2px 粗，圆角与选择框一致。
    """

    def __init__(self):
        super().__init__(
            None,
            Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._radius = 8
        self._border  = 2

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(4, 4, 4, 4)
        self._lay.setSpacing(2)

    def paintEvent(self, _event):
        C = _theme.get()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # 圆角填充背景
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["card"]))
        p.drawRoundedRect(self.rect(), self._radius, self._radius)

        # active_line 颜色边框
        inset = self._border / 2
        pen = QPen(QColor(C["active_line"]))
        pen.setWidthF(self._border)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(
            QRectF(inset, inset,
                   self.width()  - self._border,
                   self.height() - self._border),
            self._radius, self._radius,
        )
        p.end()

    def set_items(self, items: list[str], current: str,
                  on_select, width: int, C: dict):
        """清空并重建选项按钮，当前项置顶。"""
        while self._lay.count():
            item = self._lay.takeAt(0)
            if w := item.widget():
                w.deleteLater()

        ordered = [current] + [t for t in items if t != current]
        for text in ordered:
            btn = QPushButton(text)
            btn.setFlat(True)
            btn.setCursor(Qt.PointingHandCursor)
            is_cur = (text == current)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {C["text"]};
                    border: none;
                    border-radius: 5px;
                    text-align: left;
                    padding: 0 10px;
                    height: 34px;
                    font-weight: {"600" if is_cur else "400"};
                }}
                QPushButton:hover {{ background: {C["hover"]}; }}
            """)
            btn.clicked.connect(lambda _, t=text: (self.hide(), on_select(t)))
            self._lay.addWidget(btn)

        self.adjustSize()
        self.setFixedWidth(width)


class ThemedComboBox(QWidget):
    """
    主题自适应下拉选择框。

    特性：
      · 颜色随 theme 即时更新（无需重新点击）
      · 下拉弹窗顶部对齐选择框，向四周延伸 _OVERSHOOT px 完全盖住原框
      · 当前选项置顶，其余保持原顺序
      · 圆角平滑无锯齿，边框 active_line 颜色 2px

    用法::

        combo = ThemedComboBox()
        combo.addItems(["A", "B", "C"])
        combo.setCurrentText("B")
        combo.currentTextChanged.connect(my_callback)
    """

    currentTextChanged = Signal(str)
    _OVERSHOOT = 2      # popup 向四周各延伸的像素数

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items:   list[str] = []
        self._current: str = ""
        self._popup    = _DropPopup()

        self._btn = QPushButton(self)
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.clicked.connect(self._toggle)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._btn)

        # 初始化样式 + 主题变更时自动刷新
        self._refresh_style()
        _theme.on_change(lambda _: self._refresh_style())

    # ── 样式 ────────────────────────────────────
    def _refresh_style(self):
        C = _theme.get()
        self._btn.setStyleSheet(f"""
            QPushButton {{
                background: {C["card"]};
                color: {C["text"]};
                border: 2px solid {C["active_line"]};
                border-radius: 6px;
                text-align: left;
                padding: 0 10px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                border-color: {C["active_line"]};
                background: {C["hover"]};
            }}
        """)

    # ── 数据 API ────────────────────────────────
    def addItems(self, items: list[str]):
        self._items = list(items)
        if items:
            self._set_current(items[0], emit=False)

    def currentText(self) -> str:
        return self._current

    def setCurrentText(self, text: str):
        if text in self._items:
            self._set_current(text, emit=False)

    # ── 尺寸代理 ────────────────────────────────
    def setFixedHeight(self, h: int):
        self._btn.setFixedHeight(h)
        super().setFixedHeight(h)

    def setMinimumWidth(self, w: int):
        self._btn.setMinimumWidth(w)
        super().setMinimumWidth(w)

    # ── 内部 ────────────────────────────────────
    def _set_current(self, text: str, emit=True):
        old = self._current
        self._current = text
        self._btn.setText(text + "   ▾")
        if emit and old != text:
            self.currentTextChanged.emit(text)

    def _toggle(self):
        if self._popup.isVisible():
            self._popup.hide()
            return

        C  = _theme.get()
        o  = self._OVERSHOOT
        w  = self.width() + o * 2

        self._popup.set_items(self._items, self._current, self._select, w, C)
        self._popup.move(self.mapToGlobal(QPoint(-o, -o)))
        self._popup.show()

    def _select(self, text: str):
        self._set_current(text)


# ─────────────────────────────────────────────
# ThemedToggle — 滑条式开关
# ─────────────────────────────────────────────
class ThemedToggle(QWidget):
    """
    iOS/Material 风格滑条开关，自动读写 config.json。

    用法::

        toggle = ThemedToggle("my.setting.key", default=True)
        toggle.toggled.connect(on_changed)
        print(toggle.value())
    """
    toggled = Signal(bool)

    def __init__(self, cfg_key: str, default: bool = True, parent=None):
        super().__init__(parent)
        self._key   = cfg_key
        self._value = bool(_cfg.get(cfg_key, default))
        self.setFixedSize(48, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)

        # 圆圈位置：0.0=左(关) 1.0=右(开)
        self._pos = 1.0 if self._value else 0.0

        self._anim = QPropertyAnimation(self, b"circle_pos")
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)

    # ── Qt Property（供动画驱动）────────────────
    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, v: float):
        self._pos = v
        self.update()

    circle_pos = Property(float, _get_pos, _set_pos)

    # ── 绘制 ────────────────────────────────────
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        C   = _theme.get()
        w, h = self.width(), self.height()
        m   = 3                          # 圆圈边距
        d   = h - m * 2                  # 圆圈直径
        r   = h / 2                      # 背景圆角

        # 背景颜色：在 active_line 和 border 之间插值
        c_on  = QColor(C["active_line"])
        c_off = QColor(C["border"])
        t     = self._pos
        bg = QColor(
            int(c_off.red()   + (c_on.red()   - c_off.red())   * t),
            int(c_off.green() + (c_on.green() - c_off.green()) * t),
            int(c_off.blue()  + (c_on.blue()  - c_off.blue())  * t),
        )

        p.setBrush(bg)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, w, h, r, r)

        # 圆圈
        x = m + self._pos * (w - m * 2 - d)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(int(x), m, d, d)
        p.end()

    # ── 交互 ────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._value = not self._value
            _cfg.set(self._key, self._value)

            self._anim.stop()
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(1.0 if self._value else 0.0)
            self._anim.start()

            self.toggled.emit(self._value)

    def value(self) -> bool:
        return self._value

    def setValue(self, v: bool):
        if v == self._value:
            return
        self._value = v
        _cfg.set(self._key, v)
        self._pos = 1.0 if v else 0.0
        self.update()
