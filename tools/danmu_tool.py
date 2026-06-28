"""
tools/danmu_tool.py — 弹幕机

DanmuTool   — 控制面板（普通窗口，注册为 Tool）
DanmuWindow — 透明悬浮弹幕窗（从控制面板打开/关闭）

动画原理：
  所有可隐藏内容（顶栏背景 + 三侧边框 + 按钮）由单一 _DanmuRoot 组件统一管理。
  paintEvent 以圆圈中心为原点用 QPainterPath 圆形 clip 绘制；
  按钮容器同步用 setMask(QRegion.Ellipse) 裁剪，保证完全同步，无先后差。
"""
import sys, os, math, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QSizePolicy,
    QGraphicsOpacityEffect, QSpinBox, QLineEdit,
)
from PySide6.QtCore  import (
    Qt, QPoint, QRect, QEvent, Signal,
    QVariantAnimation, QEasingCurve,
    QPropertyAnimation, QTimer,
)
from PySide6.QtGui   import (
    QPainter, QColor, QPainterPath, QPen, QRegion,
    QLinearGradient, QFont, QFontMetrics, QPixmap,
)

import theme as _theme

# ─────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────
_BORDER_W   = 2     # 三侧细边框宽度（px）
_TOPBAR_H   = 32    # 顶栏高度（px）
_RESIZE_HIT = 8     # 边缘 resize 感应宽度（px）
_CIRCLE_D   = 14    # 圆圈直径（px）
_CIRCLE_OFF = 5     # 圆圈距左上角偏移（px）
_ANIM_MS    = 280   # 动画时长（ms）
_BTN_W      = 32    # 每个控制按钮宽度（px）
# 顶栏拖动区左边距（为圆圈留空）
_DRAG_L     = _CIRCLE_OFF + _CIRCLE_D + 4

# 弹幕气泡渐变参数
_BUBBLE_FADE_W  = 22   # 左右淡出区宽度（文字区以外的延伸，px）
_BUBBLE_PAD_H   = 12   # 文字区水平内边距（px）
_BUBBLE_PAD_V   = 7    # 文字区垂直内边距（px）
# 礼物气泡图标参数
_GIFT_ICON_SIZE = 32   # 礼物图标边长（px，正方）
_GIFT_ICON_GAP  = 8    # 图标与文字区间距（px）


# ─────────────────────────────────────────────
# 气泡公共基类
# ─────────────────────────────────────────────
class _DanmuBubbleBase(QWidget):
    """
    弹幕/礼物气泡基类。
    提供：透明背景、无边框横向渐变（中心 50% → 边缘 0%）、淡入淡出生命周期。
    子类只需实现 _build() 并在 __init__ 中调用 _start_lifecycle()。
    """
    _FADE_IN_MS  = 300
    _STAY_MS     = 3000
    _FADE_OUT_MS = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)

    def _start_lifecycle(self):
        anim_in = QPropertyAnimation(self._effect, b"opacity", self)
        anim_in.setStartValue(0.0)
        anim_in.setEndValue(1.0)
        anim_in.setDuration(self._FADE_IN_MS)
        anim_in.setEasingCurve(QEasingCurve.OutCubic)

        anim_out = QPropertyAnimation(self._effect, b"opacity", self)
        anim_out.setStartValue(1.0)
        anim_out.setEndValue(0.0)
        anim_out.setDuration(self._FADE_OUT_MS)
        anim_out.setEasingCurve(QEasingCurve.InCubic)
        anim_out.finished.connect(self._remove)

        anim_in.finished.connect(
            lambda: QTimer.singleShot(self._STAY_MS, anim_out.start)
        )
        anim_in.start()
        self._anim_in  = anim_in
        self._anim_out = anim_out

    def _remove(self):
        self.hide()
        self.deleteLater()

    def paintEvent(self, _event):
        p = QPainter(self)
        w = self.width()
        h = self.height()
        f = _BUBBLE_FADE_W / w if w > 0 else 0.2

        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0.0,     QColor(0, 0, 0, 0))
        grad.setColorAt(f,       QColor(0, 0, 0, 128))
        grad.setColorAt(1.0 - f, QColor(0, 0, 0, 128))
        grad.setColorAt(1.0,     QColor(0, 0, 0, 0))

        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRect(0, 0, w, h)
        p.end()


# ─────────────────────────────────────────────
# 弹幕气泡（ChatMessage）
# ─────────────────────────────────────────────
class _DanmuBubble(_DanmuBubbleBase):
    """ID 行（上，居中）+ 内容行（下，居中，可换行）。"""

    def __init__(self, user: str, content: str, parent=None):
        super().__init__(parent)
        self._build(user, content)
        self._start_lifecycle()

    def _build(self, user: str, content: str):
        f13 = QFont("Microsoft YaHei"); f13.setPixelSize(13)
        f11 = QFont("Microsoft YaHei"); f11.setPixelSize(11)
        fm13, fm11 = QFontMetrics(f13), QFontMetrics(f11)

        cw       = fm13.horizontalAdvance("国")
        h_margin = _BUBBLE_PAD_H + _BUBBLE_FADE_W
        content_w = max(cw * 4, min(max(fm11.horizontalAdvance(user),
                                        fm13.horizontalAdvance(content)), cw * 16))
        # 固定宽度，让 adjustSize() 可按此宽度计算真实换行高度
        self.setFixedWidth(content_w + h_margin * 2)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(h_margin, _BUBBLE_PAD_V, h_margin, _BUBBLE_PAD_V)
        lay.setSpacing(2)
        lay.setAlignment(Qt.AlignHCenter)

        id_lbl = QLabel(user)
        id_lbl.setAlignment(Qt.AlignHCenter)
        id_lbl.setStyleSheet(
            "color: rgba(255,255,255,200); font-size: 11px;"
            " font-weight:600; background:transparent;"
        )

        msg_lbl = QLabel(content)
        msg_lbl.setAlignment(Qt.AlignHCenter)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(
            "color: white; font-size: 13px; background: transparent;"
        )

        lay.addWidget(id_lbl)
        lay.addWidget(msg_lbl)


# ─────────────────────────────────────────────
# 礼物气泡（GiftMessage）
# ─────────────────────────────────────────────
_GIFT_ICONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gift", "icon",
)


class _DanmuGiftBubble(_DanmuBubbleBase):
    """
    左边礼物图标（有则显示，正方 _GIFT_ICON_SIZE px），
    右边两行左对齐文字：昵称 / 送出了 礼物名 × 数量。
    无图标时仅显示双行文字。
    """

    def __init__(self, msg, suffix: str = "", parent=None):
        super().__init__(parent)
        self._build(msg, suffix)
        self._start_lifecycle()

    # ── 图标加载 ────────────────────────────
    @staticmethod
    def _load_icon(gift_id: int, gift_name: str) -> "QPixmap | None":
        try:
            from gift.gift_info import get_icon_path
            path = get_icon_path(gift_name)
        except Exception:
            path = None
        if not path:
            # 保底：按 gift_id 在图标目录直接查找
            for ext in (".webp", ".png", ".jpg"):
                p = os.path.join(_GIFT_ICONS_DIR, f"{gift_id}{ext}")
                if os.path.exists(p):
                    path = p
                    break
        if path:
            px = QPixmap(path)
            if not px.isNull():
                return px
        return None

    # ── UI 构建 ──────────────────────────────
    def _build(self, msg, suffix: str = ""):
        f13 = QFont("Microsoft YaHei"); f13.setPixelSize(13)
        f11 = QFont("Microsoft YaHei"); f11.setPixelSize(11)
        fm13, fm11 = QFontMetrics(f13), QFontMetrics(f11)
        gift_line  = f"送出了 {msg.gift} ×{msg.count}"
        text_cw    = max(fm11.horizontalAdvance(msg.user),
                         fm13.horizontalAdvance(gift_line),
                         fm11.horizontalAdvance(suffix) if suffix else 0)

        pixmap   = self._load_icon(msg.gift_id, msg.gift)
        h_margin = _BUBBLE_PAD_H + _BUBBLE_FADE_W
        inner_w  = text_cw + ((_GIFT_ICON_SIZE + _GIFT_ICON_GAP) if pixmap else 0)
        self.setFixedWidth(inner_w + h_margin * 2)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(h_margin, _BUBBLE_PAD_V, h_margin, _BUBBLE_PAD_V)
        outer.setSpacing(_GIFT_ICON_GAP)
        outer.setAlignment(Qt.AlignVCenter)
        if pixmap:
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(_GIFT_ICON_SIZE, _GIFT_ICON_SIZE)
            icon_lbl.setPixmap(
                pixmap.scaled(_GIFT_ICON_SIZE, _GIFT_ICON_SIZE,
                              Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            icon_lbl.setStyleSheet("background: transparent;")
            outer.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        id_lbl = QLabel(msg.user)
        id_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        id_lbl.setStyleSheet(
            "color: rgba(255,255,255,200); font-size: 11px;"
            " font-weight:600; background:transparent;"
        )

        gift_lbl = QLabel(f"送出了 {msg.gift} ×{msg.count}")
        gift_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        gift_lbl.setStyleSheet(
            "color: white; font-size: 13px; background: transparent;"
        )

        text_col.addWidget(id_lbl)
        text_col.addWidget(gift_lbl)

        if suffix:
            suf_lbl = QLabel(suffix)
            suf_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            suf_lbl.setStyleSheet(
                "color: rgba(255,255,255,200); font-size: 11px; background:transparent;"
            )
            text_col.addWidget(suf_lbl)

        outer.addLayout(text_col)


# ─────────────────────────────────────────────
# 圆圈切换按钮（始终可见，绝对定位，不参与波纹）
# ─────────────────────────────────────────────
class _CircleToggle(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(_CIRCLE_D, _CIRCLE_D)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("background: transparent; border: none;")
        self.setToolTip("显示/隐藏边框")

    def paintEvent(self, _event):
        C = _theme.get()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["border"]))
        p.drawEllipse(0, 0, _CIRCLE_D, _CIRCLE_D)
        p.end()


# ─────────────────────────────────────────────
# 弹幕窗主体组件
#
# 整个可显示/隐藏区域（顶栏背景 + 三侧边框 + 按钮）
# 统一由 paintEvent + setMask 驱动，以圆圈为圆心做波纹展开/收缩。
# ─────────────────────────────────────────────
class _DanmuRoot(QWidget):

    _EDGE_CURSORS = {
        "l":  Qt.SizeHorCursor,
        "r":  Qt.SizeHorCursor,
        "b":  Qt.SizeVerCursor,
        "bl": Qt.SizeBDiagCursor,
        "br": Qt.SizeFDiagCursor,
    }

    def __init__(self, win: "DanmuWindow", parent=None):
        super().__init__(parent)
        self._win          = win
        self._r            = 0.0   # 当前波纹半径（像素）
        self._drag_anchor: QPoint | None = None
        self._resize_data: tuple  | None = None
        self.setMouseTracking(True)
        self.setStyleSheet("background: transparent;")
        self._build()

    # ── UI 构建 ───────────────────────────────
    def _build(self):
        C = _theme.get()

        # 按钮容器（绝对定位，right side of topbar）
        self._btn_box = QWidget(self)
        btn_lay = QHBoxLayout(self._btn_box)
        btn_lay.setContentsMargins(0, 0, 0, 0)
        btn_lay.setSpacing(0)

        btn_style = f"""
            QPushButton {{
                background: transparent; border: none;
                color: {C['text_muted']}; font-size: 12px;
                min-width: {_BTN_W}px; max-width: {_BTN_W}px;
                min-height: {_TOPBAR_H}px; max-height: {_TOPBAR_H}px;
            }}
            QPushButton:hover {{
                background: {C['btn_hover']}; color: {C['text']};
            }}
        """
        for text, slot in [("─", lambda: self._win.showMinimized()),
                            ("✕", self._win.close)]:
            btn = QPushButton(text)
            btn.setStyleSheet(btn_style)
            btn.setCursor(Qt.ArrowCursor)
            btn.clicked.connect(slot)
            btn_lay.addWidget(btn)

        self._btn_box.setVisible(False)   # 初始隐藏

        # 圆圈（左上角，始终置顶，不受 setMask 影响）
        self._circle = _CircleToggle(self)
        self._circle.move(_CIRCLE_OFF, _CIRCLE_OFF)
        self._circle.raise_()
        self._circle.clicked.connect(self._win.toggle_border)

        # 弹幕内容区（topbar 以下，绝对定位，鼠标事件透传）
        self._content = QWidget(self)
        self._content.setAttribute(Qt.WA_TranslucentBackground)
        self._content.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._content.setStyleSheet("background: transparent;")

    # ── 圆圈中心（本组件坐标系）────────────────
    def _cx(self) -> float:
        return _CIRCLE_OFF + _CIRCLE_D / 2.0

    def _cy(self) -> float:
        return _CIRCLE_OFF + _CIRCLE_D / 2.0

    # ── 最大所需半径（到最远角的距离）────────────
    def max_radius(self) -> float:
        cx, cy = self._cx(), self._cy()
        return max(
            math.hypot(cx,              cy),
            math.hypot(self.width()-cx, cy),
            math.hypot(cx,              self.height()-cy),
            math.hypot(self.width()-cx, self.height()-cy),
        )

    # ── 动画驱动：设置当前波纹半径 ─────────────
    def set_radius(self, r: float):
        self._r = r

        # 按钮盒右上角（最远角）到圆心的距离
        btn_far = math.hypot(
            self.width() - self._cx(),
            _TOPBAR_H    - self._cy(),
        )
        # 不使用 setMask：完全用 setVisible 控制，避免 mask↔无mask 切换闪现
        # 圆覆盖到按钮区域才显示，否则隐藏
        self._btn_box.setVisible(r >= btn_far)

        self.update()

    # ── 绘制：顶栏背景 + 三侧边框（圆形 clip）──
    def paintEvent(self, _event):
        if self._r <= 0:
            return
        C  = _theme.get()
        cx = self._cx()
        cy = self._cy()
        r  = self._r

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # 圆形裁剪路径（与 setMask 圆同心同径）
        clip = QPainterPath()
        clip.addEllipse(cx - r, cy - r, r * 2, r * 2)
        p.setClipPath(clip)

        # 顶栏背景（实心 sidebar 色）
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["sidebar"]))
        p.drawRect(0, 0, self.width(), _TOPBAR_H)

        # 三侧细边框（与顶栏同色）
        pen = QPen(QColor(C["sidebar"]), _BORDER_W)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        hw = max(1, _BORDER_W // 2)
        p.drawLine(hw,                0,               hw,                self.height())
        p.drawLine(self.width() - hw, 0,               self.width() - hw, self.height())
        p.drawLine(0,                 self.height()-hw, self.width(),      self.height()-hw)

        p.end()

    # ── 尺寸变化时重新定位按钮容器 ─────────────
    def resizeEvent(self, event):
        super().resizeEvent(event)
        btn_total = _BTN_W * 2
        self._btn_box.setGeometry(
            self.width() - btn_total, 0, btn_total, _TOPBAR_H
        )
        self._content.setGeometry(
            0, _TOPBAR_H, self.width(), self.height() - _TOPBAR_H
        )
        # 展开状态且动画未运行：把 _r 更新到新尺寸的 max_radius，
        # 否则窗口变大后旧半径覆盖不到新边角，边框/顶栏右侧消失
        from PySide6.QtCore import QAbstractAnimation
        if (self._win._shown and
                self._win._anim.state() != QAbstractAnimation.Running):
            r = self.max_radius()
            self._r = r
            self._win._anim_r = r
        self.set_radius(self._r)

    # ── 鼠标事件：拖动 + 边缘 resize ─────────
    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()

        edge = self._edge_at(pos)
        if edge:
            self._resize_data = (
                edge,
                QRect(self._win.geometry()),
                event.globalPosition().toPoint(),
            )
            return

        # 顶栏区域 + 顶栏已展开 → 拖动
        if pos.y() < _TOPBAR_H and self._r > _TOPBAR_H * 0.5:
            self._drag_anchor = (
                event.globalPosition().toPoint() - self._win.pos()
            )

    def mouseMoveEvent(self, event):
        gpos = event.globalPosition().toPoint()
        pos  = event.position().toPoint()

        if event.buttons() == Qt.LeftButton:
            if self._resize_data:
                self._do_resize(gpos)
                return
            if self._drag_anchor:
                self._win.move(gpos - self._drag_anchor)
                return

        self.setCursor(self._EDGE_CURSORS.get(
            self._edge_at(pos), Qt.ArrowCursor
        ))

    def mouseReleaseEvent(self, _event):
        self._drag_anchor = None
        self._resize_data = None
        self.setCursor(Qt.ArrowCursor)

    def _edge_at(self, pos: QPoint) -> str | None:
        x, y  = pos.x(), pos.y()
        w, h  = self.width(), self.height()
        hit   = _RESIZE_HIT
        left  = x < hit
        right = x > w - hit
        bot   = y > h - hit
        if left  and bot: return "bl"
        if right and bot: return "br"
        if left:          return "l"
        if right:         return "r"
        if bot:           return "b"
        return None

    def _do_resize(self, gpos: QPoint):
        edge, start_geo, start_gpos = self._resize_data
        dx  = gpos.x() - start_gpos.x()
        dy  = gpos.y() - start_gpos.y()
        geo = QRect(start_geo)
        if "r" in edge: geo.setRight(geo.right()   + dx)
        if "l" in edge: geo.setLeft(geo.left()     + dx)
        if "b" in edge: geo.setBottom(geo.bottom() + dy)
        if (geo.width()  >= self._win.minimumWidth() and
                geo.height() >= self._win.minimumHeight()):
            self._win.setGeometry(geo)


# ─────────────────────────────────────────────
# 透明悬浮弹幕窗
# ─────────────────────────────────────────────
class DanmuWindow(QMainWindow):

    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(200, 150)
        self._restore_geometry()      # 优先用上次保存的位置/大小

        self._shown         = True    # 初始展开
        self._anim_r        = 0.0
        self._first_show    = True    # 第一次 show 后初始化到展开状态
        self._active_bubbles: list[_DanmuBubble] = []

        self._root = _DanmuRoot(self)
        self.setCentralWidget(self._root)

        self._setup_anim()
        _theme.on_change(lambda _: self._root.update())

    def _setup_anim(self):
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_r)
        self._anim.finished.connect(self._on_anim_done)

    def toggle_border(self):
        self._shown = not self._shown
        self._anim.stop()

        target = self._root.max_radius() if self._shown else 0.0
        self._anim.setStartValue(self._anim_r)
        self._anim.setEndValue(target)
        self._anim.start()

    def showEvent(self, event):
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            # 布局完成后再初始化（singleShot 0 确保 resize 已经发生）
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._init_shown)

    def _init_shown(self):
        r = self._root.max_radius()
        if r <= 0:
            r = 800.0   # 布局异常时的保底值
        self._anim_r = r
        self._root.set_radius(r)
        self._root._btn_box.clearMask()   # 展开完成，去掉圆形 mask

    def _on_r(self, r: float):
        self._anim_r = r
        self._root.set_radius(r)

    def _on_anim_done(self):
        pass   # set_radius 已经通过 setVisible 处理好，无需额外操作

    _GEO_KEY = "danmu_window_geometry"

    def _restore_geometry(self):
        import config as _cfg
        from PySide6.QtWidgets import QApplication
        saved = _cfg.get(self._GEO_KEY)
        if not saved:
            self.resize(420, 320)
            return
        # 确保位置在某个屏幕上（防止换显示器后窗口飞出）
        x, y, w, h = saved["x"], saved["y"], saved["w"], saved["h"]
        screens = QApplication.screens()
        on_screen = any(s.availableGeometry().contains(QRect(x, y, 1, 1)) for s in screens)
        if on_screen:
            self.setGeometry(x, y, w, h)
        else:
            self.resize(w, h)   # 保留大小，位置让 Qt 默认

    def hideEvent(self, event):
        super().hideEvent(event)
        import config as _cfg
        geo = self.geometry()
        _cfg.set(self._GEO_KEY, {
            "x": geo.x(), "y": geo.y(),
            "w": geo.width(), "h": geo.height(),
        })

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.closed.emit()

    # ── 1cm 安全边距（按屏幕 DPI 折算）──────────
    @staticmethod
    def _bubble_margin() -> int:
        from PySide6.QtWidgets import QApplication
        return max(20, int(QApplication.primaryScreen().logicalDotsPerInch() / 2.54))

    # ── 公共放置逻辑 ─────────────────────────
    def _place_and_show(self, bubble: "_DanmuBubbleBase", bw: int, bh: int):
        cw_widget = self._root._content
        M         = self._bubble_margin()
        cw_area, ch_area = cw_widget.width(), cw_widget.height()

        x_min, y_min = M, M
        x_max = cw_area - M - bw
        y_max = ch_area - M - bh

        if x_max < x_min or y_max < y_min:
            bubble.deleteLater()
            return

        step = 20
        xs = list(range(x_min, x_max + 1, step))
        ys = list(range(y_min, y_max + 1, step))
        if xs[-1] != x_max: xs.append(x_max)
        if ys[-1] != y_max: ys.append(y_max)

        candidates = [(x, y) for x in xs for y in ys]
        random.shuffle(candidates)

        def _alive_geo(b):
            try:
                return b.geometry() if b.isVisible() else None
            except RuntimeError:
                return None

        active_geos = [g for b in self._active_bubbles if (g := _alive_geo(b))]

        placed_pos = None
        for x, y in candidates:
            r = QRect(x, y, bw, bh)
            if not any(r.intersects(g) for g in active_geos):
                placed_pos = (x, y)
                break

        if placed_pos is None:
            bubble.deleteLater()
            return

        bubble.setFixedSize(bw, bh)
        bubble.move(*placed_pos)
        self._active_bubbles.append(bubble)
        bubble.destroyed.connect(lambda _=None, b=bubble: self._on_bubble_gone(b))
        bubble.show()

    def add_message(self, msg, suffix: str = ""):
        from models import ChatMessage, GiftMessage, LikeMessage, FollowMessage

        cw_widget = self._root._content
        if isinstance(msg, ChatMessage):
            bubble = _DanmuBubble(msg.user, msg.content + suffix, cw_widget)
        elif isinstance(msg, GiftMessage):
            bubble = _DanmuGiftBubble(msg, suffix, cw_widget)
        elif isinstance(msg, LikeMessage):
            bubble = _DanmuBubble(msg.user, f"点了{msg.count}个赞" + suffix, cw_widget)
        elif isinstance(msg, FollowMessage):
            bubble = _DanmuBubble(msg.user, "关注了" + suffix, cw_widget)
        else:
            return

        bubble.adjustSize()
        self._place_and_show(bubble, bubble.width(), bubble.height())

    def _on_bubble_gone(self, bubble):
        try:
            self._active_bubbles.remove(bubble)
        except ValueError:
            pass


# ─────────────────────────────────────────────
# 控制面板（注册为 Tool）
# ─────────────────────────────────────────────
def _reg(name, desc="", icon="🔧", order=99):
    def deco(cls):
        from tools import _REGISTRY, _ToolMeta
        _REGISTRY.append(_ToolMeta(cls, name, desc, icon, order))
        return cls
    return deco


@_reg(name="弹幕机", desc="透明悬浮弹幕显示窗口", icon="💬", order=1)
class DanmuTool(QMainWindow):
    """弹幕机控制面板（单例）。"""

    _instance: "DanmuTool | None" = None

    def __new__(cls, parent=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # config 键名
    _SWITCH_KEYS = {
        "chat":   "danmu_chat_on",
        "gift":   "danmu_gift_on",
        "follow": "danmu_follow_on",
        "like":   "danmu_like_on",
    }

    def __init__(self, parent=None):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("弹幕机")
        self.setMinimumSize(360, 460)
        self.resize(380, 520)
        self._danmu_win: DanmuWindow | None = None
        self._switch_btns: dict[str, QPushButton] = {}
        self._gift_spin:      QSpinBox    | None = None
        self._like_spin:      QSpinBox    | None = None
        self._like_accum_btn: QPushButton | None = None
        self._like_accum:     dict        = {}   # uid → {"user": str, "count": int}
        self._suffix_edits:   dict        = {}   # key → QLineEdit
        self._build()
        self._cur_nav  = 0
        self.setStyleSheet(self._qss())
        _theme.on_change(lambda _: (
            self.setStyleSheet(self._qss()),
            self._refresh_switches(),
            self._navigate(self._cur_nav),
        ))

    def _qss(self) -> str:
        C = _theme.get()
        return f"""
        QWidget         {{ background: {C['bg']}; color: {C['text']};
                           font-family: "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
                           font-size: 13px; }}
        #DanmuNavBar    {{ background: {C['sidebar']};
                           border-bottom: 1px solid {C['border']}; }}
        #DanmuNavBtn    {{ background: transparent; border: none;
                           border-bottom: 2px solid transparent;
                           padding: 0 16px; color: {C['text_muted']}; font-size: 13px; }}
        #DanmuNavBtn:hover {{ background: {C['hover']}; color: {C['text']}; }}
        #DanmuNavBtn[active=true] {{ background: transparent; color: {C['text']};
                                     font-weight: 600;
                                     border-bottom: 2px solid {C['active_line']}; }}
        #DanmuContent   {{ background: {C['bg']}; }}
        #DanmuCard      {{ background: {C['card']}; border-radius: 10px;
                           border: 1px solid {C['border']}; }}
        #DanmuPageTitle {{ font-size: 20px; font-weight: 600; color: {C['text']}; }}
        #DanmuTip       {{ font-size: 12px; color: {C['text_muted']}; }}
        QLabel          {{ background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 4px; }}
        QScrollBar::handle:vertical {{ background: {C['border']}; border-radius: 2px;
                                       min-height: 20px; }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{ height: 0; }}
        """

    # ── 布局助手 ─────────────────────────────────
    def _make_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("DanmuCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)
        return card

    def _make_sep(self) -> QFrame:
        C = _theme.get()
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background: {C['border']}; max-height: 1px;")
        return sep

    def _add_suffix_row(self, card_lay, key: str):
        """在卡片布局里添加后缀输入行。"""
        import config as _cfg
        row = QHBoxLayout()
        lbl = QLabel("弹幕后缀")
        edit = QLineEdit()
        edit.setMaxLength(10)
        edit.setPlaceholderText("最多10字")
        edit.setFixedWidth(150)
        edit.setText(_cfg.get(f"danmu_{key}_suffix", ""))
        edit.textChanged.connect(lambda t, k=key: _cfg.set(f"danmu_{k}_suffix", t))
        self._suffix_edits[key] = edit
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(edit)
        card_lay.addLayout(row)

    def _add_toggle_row(self, card_lay, key: str, name: str, tip: str):
        """在卡片布局里添加一行开关（名称+tip+按钮）。"""
        row = QHBoxLayout()
        row.setSpacing(0)
        left = QVBoxLayout()
        left.setSpacing(3)
        n = QLabel(name)
        n.setStyleSheet("font-size: 14px; font-weight: 600;")
        t = QLabel(tip)
        t.setObjectName("DanmuTip")
        left.addWidget(n)
        left.addWidget(t)
        btn = QPushButton()
        btn.setFixedHeight(28)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _, k=key: self._on_toggle(k))
        self._switch_btns[key] = btn
        row.addLayout(left)
        row.addStretch()
        row.addWidget(btn)
        card_lay.addLayout(row)

    # ── 各 tab 面板构建 ──────────────────────────
    def _build_settings_panel(self, lay):
        C = _theme.get()
        title = QLabel("设置")
        title.setObjectName("DanmuPageTitle")
        lay.addWidget(title)

        card = self._make_card()
        cl   = card.layout()
        row  = QHBoxLayout()
        lbl  = QLabel("悬浮弹幕窗")
        lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        row.addWidget(lbl)
        row.addStretch()
        self._open_btn = QPushButton("打开弹幕窗")
        self._open_btn.setFixedHeight(34)
        self._open_btn.setCursor(Qt.PointingHandCursor)
        self._open_btn.clicked.connect(self._toggle_danmu_win)
        row.addWidget(self._open_btn)
        cl.addLayout(row)
        desc = QLabel("透明悬浮窗，叠加在直播软件上方显示弹幕")
        desc.setStyleSheet(f"font-size: 12px; color: {C['text_muted']};")
        cl.addWidget(desc)
        lay.addWidget(card)

    def _build_chat_panel(self, lay):
        lay.addWidget(self._page_title("弹幕"))
        card = self._make_card()
        cl   = card.layout()
        self._add_toggle_row(cl, "chat", "消息弹幕", "显示观众发送的聊天弹幕")
        cl.addWidget(self._make_sep())
        self._add_suffix_row(cl, "chat")
        lay.addWidget(card)

    def _build_gift_panel(self, lay):
        import config as _cfg
        lay.addWidget(self._page_title("礼物"))
        card = self._make_card()
        cl   = card.layout()
        self._add_toggle_row(cl, "gift", "礼物弹幕", "显示观众送出礼物的弹幕")
        cl.addWidget(self._make_sep())

        row = QHBoxLayout()
        lbl = QLabel("最低金额")
        spin = QSpinBox()
        spin.setRange(0, 999999)
        spin.setSuffix(" 钻")
        spin.setFixedWidth(110)
        spin.setValue(_cfg.get("danmu_gift_min_diamonds", 0))
        spin.valueChanged.connect(lambda v: _cfg.set("danmu_gift_min_diamonds", v))
        self._gift_spin = spin
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(spin)
        cl.addLayout(row)
        cl.addWidget(self._make_sep())
        self._add_suffix_row(cl, "gift")
        lay.addWidget(card)

    def _build_follow_panel(self, lay):
        lay.addWidget(self._page_title("关注"))
        card = self._make_card()
        cl   = card.layout()
        self._add_toggle_row(cl, "follow", "关注弹幕", "显示新关注通知弹幕")
        cl.addWidget(self._make_sep())
        self._add_suffix_row(cl, "follow")
        lay.addWidget(card)

    def _build_like_panel(self, lay):
        import config as _cfg
        lay.addWidget(self._page_title("点赞"))
        card = self._make_card()
        cl   = card.layout()
        self._add_toggle_row(cl, "like", "点赞弹幕", "显示观众点赞弹幕")
        cl.addWidget(self._make_sep())

        row = QHBoxLayout()
        lbl = QLabel("数量阈值")
        spin = QSpinBox()
        spin.setRange(1, 99999)
        spin.setFixedWidth(90)
        spin.setValue(_cfg.get("danmu_like_threshold", 1))
        spin.valueChanged.connect(lambda v: _cfg.set("danmu_like_threshold", v))
        self._like_spin = spin
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(spin)
        cl.addLayout(row)

        cl.addWidget(self._make_sep())

        row2 = QHBoxLayout()
        lbl2 = QLabel("累加模式")
        lbl2.setStyleSheet("font-size: 14px; font-weight: 600;")
        t2   = QLabel("按用户累计点赞数，达到阈值后发送弹幕")
        t2.setObjectName("DanmuTip")
        left2 = QVBoxLayout()
        left2.setSpacing(3)
        left2.addWidget(lbl2)
        left2.addWidget(t2)
        accum_btn = QPushButton()
        accum_btn.setFixedHeight(28)
        accum_btn.setCursor(Qt.PointingHandCursor)
        accum_btn.clicked.connect(self._on_toggle_accum)
        self._like_accum_btn = accum_btn
        row2.addLayout(left2)
        row2.addStretch()
        row2.addWidget(accum_btn)
        cl.addLayout(row2)
        cl.addWidget(self._make_sep())
        self._add_suffix_row(cl, "like")
        lay.addWidget(card)

    @staticmethod
    def _page_title(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("DanmuPageTitle")
        return lbl

    # ── 主构建入口 ───────────────────────────────
    def _build(self):
        from PySide6.QtWidgets import QScrollArea, QStackedWidget
        root = QWidget()
        self.setCentralWidget(root)
        main_lay = QVBoxLayout(root)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # ── 顶部导航栏 ──
        topbar = QWidget()
        topbar.setObjectName("DanmuNavBar")
        topbar.setFixedHeight(46)
        tb_lay = QHBoxLayout(topbar)
        tb_lay.setContentsMargins(8, 0, 8, 0)
        tb_lay.setSpacing(0)

        self._stack:    QStackedWidget     = QStackedWidget()
        self._nav_btns: list[QPushButton]  = []

        _TABS = [
            ("设置", self._build_settings_panel),
            ("弹幕", self._build_chat_panel),
            ("礼物", self._build_gift_panel),
            ("关注", self._build_follow_panel),
            ("点赞", self._build_like_panel),
        ]
        for i, (tab_name, builder) in enumerate(_TABS):
            nav_btn = QPushButton(tab_name)
            nav_btn.setObjectName("DanmuNavBtn")
            nav_btn.setFixedHeight(46)
            nav_btn.setCursor(Qt.PointingHandCursor)
            nav_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            nav_btn.clicked.connect(lambda _, idx=i: self._navigate(idx))
            self._nav_btns.append(nav_btn)
            tb_lay.addWidget(nav_btn)

            inner = QWidget()
            inner_lay = QVBoxLayout(inner)
            inner_lay.setContentsMargins(24, 24, 24, 24)
            inner_lay.setSpacing(16)
            builder(inner_lay)
            inner_lay.addStretch()

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setObjectName("DanmuContent")
            scroll.setWidget(inner)
            self._stack.addWidget(scroll)

        tb_lay.addStretch()
        main_lay.addWidget(topbar)
        main_lay.addWidget(self._stack)

        self._navigate(0)
        self._refresh_btn()
        self._refresh_switches()

    # ── 导航 ─────────────────────────────────────
    def _navigate(self, index: int):
        self._cur_nav = index
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_btns):
            btn.setProperty("active", i == index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ── spin / accumulate 辅助 ──────────────────
    def _spin_qss(self, C: dict) -> str:
        return (
            f"QSpinBox {{ background: {C['hover']}; color: {C['text']};"
            f" border: 1px solid {C['border']}; border-radius: 5px;"
            f" font-size: 12px; padding: 2px 4px; }}"
            f"QSpinBox::up-button {{ border: none; width: 16px; }}"
            f"QSpinBox::down-button {{ border: none; width: 16px; }}"
        )

    def _on_toggle_accum(self):
        import config as _cfg
        new_val = not _cfg.get("danmu_like_accumulate", False)
        _cfg.set("danmu_like_accumulate", new_val)
        if not new_val:
            self._like_accum.clear()
        self._refresh_switches()

    def _send_to_danmu(self, msg):
        if not (self._danmu_win and self._danmu_win.isVisible()):
            return
        import config as _cfg
        from models import ChatMessage, GiftMessage, FollowMessage, LikeMessage
        if isinstance(msg, ChatMessage):
            suffix = _cfg.get("danmu_chat_suffix", "")
        elif isinstance(msg, GiftMessage):
            suffix = _cfg.get("danmu_gift_suffix", "")
        elif isinstance(msg, FollowMessage):
            suffix = _cfg.get("danmu_follow_suffix", "")
        elif isinstance(msg, LikeMessage):
            suffix = _cfg.get("danmu_like_suffix", "")
        else:
            suffix = ""
        self._danmu_win.add_message(msg, suffix)

    def _toggle_danmu_win(self):
        if self._danmu_win is None:
            self._danmu_win = DanmuWindow()
            self._danmu_win.closed.connect(self._refresh_btn)

        if self._danmu_win.isVisible():
            self._danmu_win.hide()
        else:
            self._danmu_win.show()
            self._danmu_win.activateWindow()

        self._refresh_btn()

    def _refresh_btn(self):
        C       = _theme.get()
        is_open = self._danmu_win is not None and self._danmu_win.isVisible()
        self._open_btn.setText("关闭弹幕窗" if is_open else "打开弹幕窗")
        if is_open:
            self._open_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #D20F39; color: #fff;
                    border: 1.5px solid transparent;
                    border-radius: 8px; font-size: 13px; font-weight: 600;
                    padding: 0 16px;
                }}
                QPushButton:hover {{ background: #B01030; }}
            """)
        else:
            self._open_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C['card']}; color: {C['active_line']};
                    border: 1.5px solid {C['active_line']};
                    border-radius: 8px; font-size: 13px; font-weight: 600;
                    padding: 0 16px;
                }}
                QPushButton:hover {{ background: {C['hover']}; }}
            """)

    # ── 开关读写 ─────────────────────────────
    def _switch_on(self, key: str) -> bool:
        import config as _cfg
        return _cfg.get(self._SWITCH_KEYS[key], True)

    def _on_toggle(self, key: str):
        import config as _cfg
        cfg_key = self._SWITCH_KEYS[key]
        _cfg.set(cfg_key, not _cfg.get(cfg_key, True))
        self._refresh_switches()

    def _refresh_switches(self):
        import config as _cfg
        C = _theme.get()
        on_style = (
            f"QPushButton {{ background: {C['active_line']}; color: #fff;"
            f" border: 1.5px solid transparent; border-radius: 8px;"
            f" font-size: 12px; font-weight: 600; padding: 0 14px; }}"
            f"QPushButton:hover {{ background: {C['active_line']}; }}"
        )
        off_style = (
            f"QPushButton {{ background: transparent; color: {C['text_muted']};"
            f" border: 1.5px solid {C['border']}; border-radius: 8px;"
            f" font-size: 12px; padding: 0 14px; }}"
            f"QPushButton:hover {{ background: {C['hover']}; }}"
        )
        for key, btn in self._switch_btns.items():
            on = self._switch_on(key)
            btn.setText("已开启" if on else "已关闭")
            btn.setStyleSheet(on_style if on else off_style)

        sqss = self._spin_qss(C)
        eqss = (
            f"QLineEdit {{ background: {C['hover']}; color: {C['text']};"
            f" border: 1px solid {C['border']}; border-radius: 5px;"
            f" font-size: 12px; padding: 2px 6px; }}"
        )
        if self._gift_spin:
            self._gift_spin.setStyleSheet(sqss)
        if self._like_spin:
            self._like_spin.setStyleSheet(sqss)
        for edit in self._suffix_edits.values():
            edit.setStyleSheet(eqss)
        if self._like_accum_btn:
            accum_on = _cfg.get("danmu_like_accumulate", False)
            self._like_accum_btn.setText("累加：已开启" if accum_on else "累加：已关闭")
            self._like_accum_btn.setStyleSheet(on_style if accum_on else off_style)

    def process_message(self, msg):
        from models import ChatMessage, GiftMessage, FollowMessage, LikeMessage
        import config as _cfg

        if isinstance(msg, ChatMessage):
            if not self._switch_on("chat"):
                return
            self._send_to_danmu(msg)

        elif isinstance(msg, GiftMessage):
            if not self._switch_on("gift"):
                return
            min_d = _cfg.get("danmu_gift_min_diamonds", 0)
            if min_d > 0:
                from gift.gift_info import get_diamonds
                if (get_diamonds(msg.gift) or 0) < min_d:
                    return
            self._send_to_danmu(msg)

        elif isinstance(msg, FollowMessage):
            if not self._switch_on("follow"):
                return
            self._send_to_danmu(msg)

        elif isinstance(msg, LikeMessage):
            if not self._switch_on("like"):
                return
            threshold = max(1, _cfg.get("danmu_like_threshold", 1))
            if _cfg.get("danmu_like_accumulate", False):
                entry = self._like_accum.setdefault(
                    msg.user_id, {"user": msg.user, "count": 0}
                )
                entry["user"]   = msg.user
                entry["count"] += msg.count
                if entry["count"] >= threshold:
                    fire = LikeMessage(
                        user=msg.user, user_id=msg.user_id,
                        count=entry["count"],
                    )
                    entry["count"] = 0
                    self._send_to_danmu(fire)
            else:
                if msg.count < threshold:
                    return
                self._send_to_danmu(msg)

    def toggle_danmu_window(self):
        """切换悬浮弹幕窗显示/隐藏（供托盘调用）。"""
        self._toggle_danmu_win()

    def danmu_window_active(self) -> bool:
        """悬浮弹幕窗当前是否可见（供托盘标签切换）。"""
        return self._danmu_win is not None and self._danmu_win.isVisible()

    def closeEvent(self, event):
        """关闭时隐藏而非销毁（保持单例可用）。"""
        event.ignore()
        self.hide()


# ─────────────────────────────────────────────
# 托盘注册（模块导入时执行）
# ─────────────────────────────────────────────
def _tray_register():
    from tools.tray_registry import register_tray, TrayAction

    def _is_active() -> bool:
        return DanmuTool().danmu_window_active()

    def _toggle():
        inst = DanmuTool()
        if _is_active():
            # 关闭：隐藏悬浮窗 + 控制面板，不留任何可见后台
            if inst._danmu_win:
                inst._danmu_win.hide()
            inst.hide()
        else:
            # 启动：只打开悬浮窗
            inst.toggle_danmu_window()

    def _open_settings():
        t = DanmuTool()
        t.show()
        t.activateWindow()

    register_tray("弹幕机", lambda: [
        TrayAction("已关闭", lambda: None,
                   text_when_active="运行中", is_active=_is_active,
                   disabled=True),
        TrayAction("启动弹幕机", _toggle,
                   text_when_active="关闭弹幕机", is_active=_is_active),
        TrayAction("打开设置页面", _open_settings),
    ])


_tray_register()
