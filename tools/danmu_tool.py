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
    QSpinBox, QLineEdit,
)
from PySide6.QtCore  import (
    Qt, QPoint, QRect, QEvent, Signal,
    QVariantAnimation, QEasingCurve,
    QPropertyAnimation, QTimer,
)
from PySide6.QtGui   import (
    QPainter, QColor, QPainterPath, QPen, QRegion,
)

import util.theme as _theme
from util.widgets import ThemedComboBox
from util.overlay_capture import enable_capture_transparency
from util.overlay_window import OverlayFrameMixin, show_tutorial_dialog

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

# 弹幕气泡组件见 resources/skin/danmu/components.py


def _danmu_skin():
    from resources.skin import get_active_skin
    return get_active_skin("danmu")


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
        for text, slot in [("─", self._win.minimize_overlay),
                            ("✕", self._win.close)]:
            btn = QPushButton(text)
            btn.setStyleSheet(btn_style)
            btn.setCursor(Qt.ArrowCursor)
            if text == "─":
                btn.setToolTip("收起边框并置底（保持渲染）")
            btn.clicked.connect(slot)
            btn_lay.addWidget(btn)

        self._btn_box.setVisible(False)   # 初始隐藏

        # 圆圈（左上角，始终置顶，不受 setMask 影响）
        self._circle = _CircleToggle(self)
        self._circle.move(_CIRCLE_OFF, _CIRCLE_OFF)
        self._circle.raise_()
        self._circle.clicked.connect(self._win._on_circle_toggle)

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
        self._win.restore_from_minimize()
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
class DanmuWindow(OverlayFrameMixin, QMainWindow):

    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.FramelessWindowHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        enable_capture_transparency(self)
        self._install_overlay_frame()
        self.setWindowTitle("弹幕机")
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
        from tools.tool_common import is_app_shutting_down
        if is_app_shutting_down():
            event.accept()
            return
        event.ignore()
        self.hide()
        self.closed.emit()

    # ── 1cm 安全边距（按屏幕 DPI 折算）──────────
    @staticmethod
    def _bubble_margin() -> int:
        from PySide6.QtWidgets import QApplication
        return max(20, int(QApplication.primaryScreen().logicalDotsPerInch() / 2.54))

    # ── 公共放置逻辑 ─────────────────────────
    def _place_and_show(self, bubble, bw: int, bh: int):
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

    def refresh_skin(self):
        for b in list(self._active_bubbles):
            try:
                if b.isVisible():
                    b.refresh_skin()
            except RuntimeError:
                pass
        self._root.update()

    def add_message(self, msg, suffix: str = ""):
        from util.models import ChatMessage, GiftMessage, LikeMessage, FollowMessage
        from resources.skin.danmu.components import create_chat_bubble, create_gift_bubble

        cw_widget = self._root._content
        skin = _danmu_skin()
        if isinstance(msg, ChatMessage):
            bubble = create_chat_bubble(skin, cw_widget, msg.user, msg.content + suffix)
        elif isinstance(msg, GiftMessage):
            bubble = create_gift_bubble(skin, cw_widget, msg, suffix)
        elif isinstance(msg, LikeMessage):
            bubble = create_chat_bubble(skin, cw_widget, msg.user, f"点了{msg.count}个赞" + suffix)
        elif isinstance(msg, FollowMessage):
            bubble = create_chat_bubble(skin, cw_widget, msg.user, "关注了" + suffix)
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
from tools import register_tool
from tools.tool_common import ToolSingleton, release_tool_singleton, unregister_tool_from_page


_DANMU_TOOL_W = 448
_DANMU_TOOL_H = 520
_DANMU_SIDE = 24       # 卡片距窗口左右等宽留白
_DANMU_SCROLL_GUTTER = 8  # 预留给竖向滚动条，避免内容区左右视觉不对称


def _danmu_spin_qss(C: dict) -> str:
    bg = C["card"]
    arrow = C["text_muted"]
    return (
        f"QSpinBox {{ background: {bg}; color: {C['text']};"
        f" border: 1px solid {C['border']}; border-radius: 5px;"
        f" font-size: 12px; padding: 2px 4px; padding-right: 18px; min-height: 28px; }}"
        f"QSpinBox QLineEdit {{ background: {bg}; color: {C['text']};"
        f" border: none; padding: 0 2px;"
        f" selection-background-color: {C['active_line']}; }}"
        f"QSpinBox::up-button {{"
        f" subcontrol-origin: border; subcontrol-position: top right;"
        f" background: {C['hover']}; border: none; margin: 0; padding: 0;"
        f" width: 16px; height: 14px; border-top-right-radius: 4px; }}"
        f"QSpinBox::down-button {{"
        f" subcontrol-origin: border; subcontrol-position: bottom right;"
        f" background: {C['hover']}; border: none; margin: 0; padding: 0;"
        f" width: 16px; height: 14px; border-bottom-right-radius: 4px; }}"
        f"QSpinBox::up-button:hover, QSpinBox::down-button:hover {{"
        f" background: {C['active']}; }}"
        f"QSpinBox::up-arrow {{"
        f" image: none; width: 0; height: 0;"
        f" border-left: 3px solid transparent;"
        f" border-right: 3px solid transparent;"
        f" border-bottom: 4px solid {arrow}; margin-bottom: 1px; }}"
        f"QSpinBox::down-arrow {{"
        f" image: none; width: 0; height: 0;"
        f" border-left: 3px solid transparent;"
        f" border-right: 3px solid transparent;"
        f" border-top: 4px solid {arrow}; margin-top: 1px; }}"
    )


@register_tool(name="弹幕机", desc="透明悬浮弹幕显示窗口", icon="💬", order=1)
class DanmuTool(ToolSingleton, QMainWindow):
    """弹幕机控制面板（单例）。"""

    # config 键名
    _SWITCH_KEYS = {
        "chat":   "danmu_chat_on",
        "gift":   "danmu_gift_on",
        "follow": "danmu_follow_on",
        "like":   "danmu_like_on",
    }

    def __init__(self, parent=None):
        if not ToolSingleton.guard_init(self):
            return
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setWindowTitle("设置")
        self.setFixedSize(_DANMU_TOOL_W, _DANMU_TOOL_H)
        self._danmu_win: DanmuWindow | None = None
        self._switch_btns: dict[str, QPushButton] = {}
        self._gift_spin:      QSpinBox    | None = None
        self._like_spin:      QSpinBox    | None = None
        self._like_accum_btn: QPushButton | None = None
        self._like_accum:     dict        = {}   # uid → {"user": str, "count": int}
        self._suffix_edits:   dict        = {}   # key → QLineEdit
        self._apply_btns:    list[QPushButton] = []
        self._active:         dict        = {}
        self._build()
        self._load_active()
        self._cur_nav  = 0
        self.setStyleSheet(self._qss())
        self._refresh_switches(sync_from_config=True)
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
        #DanmuContent   {{ background: {C['bg']}; border: none; }}
        #DanmuContent > QWidget > QWidget {{
            background: {C['bg']};
        }}
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
        QLineEdit {{
            background: {C['card']}; color: {C['text']};
            border: 1px solid {C['border']}; border-radius: 5px;
            font-size: 12px; padding: 2px 6px;
            selection-background-color: {C['active_line']};
        }}
        {_danmu_spin_qss(C)}
        """

    # ── 布局助手 ─────────────────────────────────
    def _make_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("DanmuCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
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
        self._suffix_edits[key] = edit
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(edit)
        card_lay.addLayout(row)

    def _make_apply_btn(self, handler) -> QPushButton:
        btn = QPushButton("应用")
        btn.setFixedSize(72, 34)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(handler)
        self._apply_btns.append(btn)
        return btn

    def _add_apply_row(self, lay: QVBoxLayout, handler) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        row.addStretch(1)
        row.addWidget(self._make_apply_btn(handler))
        lay.addLayout(row)

    def _style_apply_btn(self, btn: QPushButton) -> None:
        C = _theme.get()
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['active_line']}; color: #fff;
                border: none; border-radius: 6px;
                font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {C['hover']}; color: {C['text']}; }}
        """)

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
        title.setAlignment(Qt.AlignHCenter)
        lay.addWidget(title)

        card = self._make_card()
        cl   = card.layout()
        row  = QHBoxLayout()
        lbl  = QLabel("悬浮弹幕窗")
        lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        row.addWidget(lbl)
        row.addStretch()
        tut_btn = QPushButton("教程")
        tut_btn.setFixedHeight(34)
        tut_btn.setCursor(Qt.PointingHandCursor)
        tut_btn.setToolTip("查看使用教程")
        tut_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['card']}; color: {C['text_muted']};
                border: 1.5px solid {C['border']};
                border-radius: 8px; font-size: 13px; font-weight: 600;
                padding: 0 14px;
            }}
            QPushButton:hover {{ background: {C['hover']}; color: {C['text']}; }}
        """)
        tut_btn.clicked.connect(lambda: show_tutorial_dialog(self))
        row.addWidget(tut_btn)
        row.addSpacing(8)
        self._open_btn = QPushButton("打开弹幕窗")
        self._open_btn.setFixedHeight(34)
        self._open_btn.setCursor(Qt.PointingHandCursor)
        self._open_btn.clicked.connect(self._toggle_danmu_win)
        row.addWidget(self._open_btn)
        cl.addLayout(row)
        desc = QLabel(
            "透明悬浮窗，叠加在直播软件上方显示弹幕。"
            "窗口采集请在直播伴侣素材设置-高级设置-选择绿幕抠图（10.5+）。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 12px; color: {C['text_muted']};")
        cl.addWidget(desc)
        lay.addWidget(card)

        theme_card = self._make_card()
        tcl = theme_card.layout()
        trow = QHBoxLayout()
        trow.setSpacing(12)
        tlbl = QLabel("弹幕外观主题")
        tlbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        trow.addWidget(tlbl)
        trow.addStretch()
        from resources.skin import get_active_skin, list_skins
        skins = list_skins("danmu")
        self._skin_name_to_id = {s["name"]: s["id"] for s in skins}
        self._skin_combo = ThemedComboBox()
        names = [s["name"] for s in skins] or ["默认"]
        if not self._skin_name_to_id:
            self._skin_name_to_id = {"默认": "default"}
        self._skin_combo.addItems(names)
        active = get_active_skin("danmu")
        if active.name in names:
            self._skin_combo.setCurrentText(active.name)
        else:
            self._skin_combo.setCurrentText(names[0])
        self._skin_combo.setFixedHeight(34)
        self._skin_combo.setMinimumWidth(160)
        self._skin_combo.currentTextChanged.connect(self._on_skin_changed)
        trow.addWidget(self._skin_combo)
        tcl.addLayout(trow)
        lay.addWidget(theme_card)

    def _on_skin_changed(self, name: str):
        from resources.skin import set_active_skin
        sid = self._skin_name_to_id.get(name, "default")
        set_active_skin("danmu", sid)
        if self._danmu_win is not None and self._danmu_win.isVisible():
            self._danmu_win.refresh_skin()

    def _build_chat_panel(self, lay):
        lay.addWidget(self._page_title("弹幕"))
        card = self._make_card()
        cl   = card.layout()
        self._add_toggle_row(cl, "chat", "消息弹幕", "显示观众发送的聊天弹幕")
        cl.addWidget(self._make_sep())
        self._add_suffix_row(cl, "chat")
        lay.addWidget(card)
        self._add_apply_row(lay, self._apply_chat)

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
        spin.setFixedSize(110, 28)
        spin.setValue(_cfg.get("danmu_gift_min_diamonds", 0))
        self._gift_spin = spin
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(spin)
        cl.addLayout(row)
        cl.addWidget(self._make_sep())
        self._add_suffix_row(cl, "gift")
        lay.addWidget(card)
        self._add_apply_row(lay, self._apply_gift)

    def _build_follow_panel(self, lay):
        lay.addWidget(self._page_title("关注"))
        card = self._make_card()
        cl   = card.layout()
        self._add_toggle_row(cl, "follow", "关注弹幕", "显示新关注通知弹幕")
        cl.addWidget(self._make_sep())
        self._add_suffix_row(cl, "follow")
        lay.addWidget(card)
        self._add_apply_row(lay, self._apply_follow)

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
        spin.setFixedSize(90, 28)
        spin.setValue(_cfg.get("danmu_like_threshold", 1))
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
        self._add_apply_row(lay, self._apply_like)

    @staticmethod
    def _page_title(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("DanmuPageTitle")
        lbl.setAlignment(Qt.AlignHCenter)
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
            inner.setObjectName("DanmuPage")
            inner_lay = QVBoxLayout(inner)
            inner_lay.setContentsMargins(
                _DANMU_SIDE + _DANMU_SCROLL_GUTTER // 2,
                20,
                _DANMU_SIDE + _DANMU_SCROLL_GUTTER // 2,
                20,
            )
            inner_lay.setSpacing(16)
            builder(inner_lay)
            inner_lay.addStretch()

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            scroll.setObjectName("DanmuContent")
            scroll.setWidget(inner)
            self._stack.addWidget(scroll)

        tb_lay.addStretch()
        main_lay.addWidget(topbar)
        main_lay.addWidget(self._stack)

        self._navigate(0)
        self._refresh_btn()

    # ── 导航 ─────────────────────────────────────
    def _navigate(self, index: int):
        self._cur_nav = index
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_btns):
            btn.setProperty("active", i == index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _load_active(self) -> None:
        import config as _cfg
        self._active = {
            "danmu_chat_on": bool(_cfg.get("danmu_chat_on", True)),
            "danmu_chat_suffix": _cfg.get("danmu_chat_suffix", "") or "",
            "danmu_gift_on": bool(_cfg.get("danmu_gift_on", True)),
            "danmu_gift_min_diamonds": int(_cfg.get("danmu_gift_min_diamonds", 0) or 0),
            "danmu_gift_suffix": _cfg.get("danmu_gift_suffix", "") or "",
            "danmu_follow_on": bool(_cfg.get("danmu_follow_on", True)),
            "danmu_follow_suffix": _cfg.get("danmu_follow_suffix", "") or "",
            "danmu_like_on": bool(_cfg.get("danmu_like_on", True)),
            "danmu_like_threshold": max(1, int(_cfg.get("danmu_like_threshold", 1) or 1)),
            "danmu_like_accumulate": bool(_cfg.get("danmu_like_accumulate", False)),
            "danmu_like_suffix": _cfg.get("danmu_like_suffix", "") or "",
        }

    def _apply_chat(self) -> None:
        import config as _cfg
        on = self._switch_btns["chat"].text() == "已开启"
        suffix = self._suffix_edits["chat"].text()
        _cfg.set("danmu_chat_on", on)
        _cfg.set("danmu_chat_suffix", suffix)
        self._active["danmu_chat_on"] = on
        self._active["danmu_chat_suffix"] = suffix

    def _apply_gift(self) -> None:
        import config as _cfg
        on = self._switch_btns["gift"].text() == "已开启"
        min_d = self._gift_spin.value()
        suffix = self._suffix_edits["gift"].text()
        _cfg.set("danmu_gift_on", on)
        _cfg.set("danmu_gift_min_diamonds", min_d)
        _cfg.set("danmu_gift_suffix", suffix)
        self._active["danmu_gift_on"] = on
        self._active["danmu_gift_min_diamonds"] = min_d
        self._active["danmu_gift_suffix"] = suffix

    def _apply_follow(self) -> None:
        import config as _cfg
        on = self._switch_btns["follow"].text() == "已开启"
        suffix = self._suffix_edits["follow"].text()
        _cfg.set("danmu_follow_on", on)
        _cfg.set("danmu_follow_suffix", suffix)
        self._active["danmu_follow_on"] = on
        self._active["danmu_follow_suffix"] = suffix

    def _apply_like(self) -> None:
        import config as _cfg
        on = self._switch_btns["like"].text() == "已开启"
        threshold = max(1, self._like_spin.value())
        accum = self._like_accum_btn.text() == "累加：已开启"
        suffix = self._suffix_edits["like"].text()
        old_th = self._active.get("danmu_like_threshold", 1)
        old_acc = self._active.get("danmu_like_accumulate", False)
        _cfg.set("danmu_like_on", on)
        _cfg.set("danmu_like_threshold", threshold)
        _cfg.set("danmu_like_accumulate", accum)
        _cfg.set("danmu_like_suffix", suffix)
        self._active["danmu_like_on"] = on
        self._active["danmu_like_threshold"] = threshold
        self._active["danmu_like_accumulate"] = accum
        self._active["danmu_like_suffix"] = suffix
        if threshold != old_th or accum != old_acc:
            self._like_accum.clear()

    def _active_on(self, cfg_key: str) -> bool:
        return bool(self._active.get(cfg_key, True))

    def _active_suffix(self, cfg_key: str) -> str:
        return self._active.get(cfg_key, "") or ""

    # ── spin / accumulate 辅助 ──────────────────
    def _spin_qss(self, C: dict) -> str:
        return _danmu_spin_qss(C)

    def _like_user_key(self, msg) -> str:
        uid = (getattr(msg, "user_id", None) or "").strip()
        if uid:
            return uid
        name = (getattr(msg, "user", None) or "").strip()
        return name or "(未知)"

    def _on_toggle_accum(self):
        import config as _cfg
        btn = self._like_accum_btn
        on = btn.text() == "累加：已开启"
        new_on = not on
        btn.setText("累加：已关闭" if on else "累加：已开启")
        old_acc = self._active.get("danmu_like_accumulate", False)
        _cfg.set("danmu_like_accumulate", new_on)
        self._active["danmu_like_accumulate"] = new_on
        if new_on != old_acc:
            self._like_accum.clear()
        self._refresh_switches()

    def _send_to_danmu(self, msg):
        if not (self._danmu_win and self._danmu_win.isVisible()):
            return
        from util.models import ChatMessage, GiftMessage, FollowMessage, LikeMessage
        if isinstance(msg, ChatMessage):
            suffix = self._active_suffix("danmu_chat_suffix")
        elif isinstance(msg, GiftMessage):
            suffix = self._active_suffix("danmu_gift_suffix")
        elif isinstance(msg, FollowMessage):
            suffix = self._active_suffix("danmu_follow_suffix")
        elif isinstance(msg, LikeMessage):
            suffix = self._active_suffix("danmu_like_suffix")
        else:
            suffix = ""
        self._danmu_win.add_message(msg, suffix)

    def _toggle_danmu_win(self):
        if self._danmu_win is None:
            self._danmu_win = DanmuWindow()
            self._danmu_win.closed.connect(self._on_overlay_closed)

        if self._danmu_win.isVisible():
            self._danmu_win.hide()
        else:
            self._danmu_win.show()
            self._danmu_win.activateWindow()

        self._refresh_btn()

    def _on_overlay_closed(self):
        self._refresh_btn()
        self._try_release_tool()

    def _is_tool_active(self) -> bool:
        if self.isVisible():
            return True
        return self._danmu_win is not None and self._danmu_win.isVisible()

    def _cleanup_for_release(self) -> None:
        if self._danmu_win is not None:
            self._danmu_win.blockSignals(True)
            self._danmu_win.deleteLater()
            self._danmu_win = None

    def _try_release_tool(self) -> None:
        from tools.tool_common import is_app_shutting_down
        if is_app_shutting_down() or self._is_tool_active():
            return
        release_tool_singleton(DanmuTool, cleanup=lambda inst: inst._cleanup_for_release())
        unregister_tool_from_page("弹幕机")

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
    def _on_toggle(self, key: str):
        btn = self._switch_btns[key]
        on = btn.text() == "已开启"
        btn.setText("已关闭" if on else "已开启")
        self._refresh_switches()

    def _refresh_switches(self, sync_from_config: bool = False):
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
            if sync_from_config:
                on = bool(_cfg.get(self._SWITCH_KEYS[key], True))
                btn.setText("已开启" if on else "已关闭")
            else:
                on = btn.text() == "已开启"
            btn.setStyleSheet(on_style if on else off_style)

        sqss = self._spin_qss(C)
        eqss = (
            f"QLineEdit {{ background: {C['card']}; color: {C['text']};"
            f" border: 1px solid {C['border']}; border-radius: 5px;"
            f" font-size: 12px; padding: 2px 6px;"
            f" selection-background-color: {C['active_line']}; }}"
        )
        if self._gift_spin:
            self._gift_spin.setStyleSheet(sqss)
        if self._like_spin:
            self._like_spin.setStyleSheet(sqss)
        for edit in self._suffix_edits.values():
            edit.setStyleSheet(eqss)
        if self._like_accum_btn:
            if sync_from_config:
                accum_on = bool(_cfg.get("danmu_like_accumulate", False))
                self._like_accum_btn.setText("累加：已开启" if accum_on else "累加：已关闭")
            else:
                accum_on = self._like_accum_btn.text() == "累加：已开启"
            self._like_accum_btn.setStyleSheet(on_style if accum_on else off_style)
        for btn in self._apply_btns:
            self._style_apply_btn(btn)

    def process_message(self, msg):
        from util.models import ChatMessage, GiftMessage, FollowMessage, LikeMessage

        if isinstance(msg, ChatMessage):
            if not self._active_on("danmu_chat_on"):
                return
            self._send_to_danmu(msg)

        elif isinstance(msg, GiftMessage):
            if not self._active_on("danmu_gift_on"):
                return
            min_d = int(self._active.get("danmu_gift_min_diamonds", 0) or 0)
            if min_d > 0:
                from resources.gift.gift_info import get_diamonds
                if (get_diamonds(msg.gift) or 0) < min_d:
                    return
            self._send_to_danmu(msg)

        elif isinstance(msg, FollowMessage):
            if not self._active_on("danmu_follow_on"):
                return
            self._send_to_danmu(msg)

        elif isinstance(msg, LikeMessage):
            if not self._active_on("danmu_like_on"):
                return
            threshold = max(1, int(self._active.get("danmu_like_threshold", 1) or 1))
            if self._active.get("danmu_like_accumulate", False):
                user_key = self._like_user_key(msg)
                entry = self._like_accum.setdefault(
                    user_key, {"user": msg.user, "count": 0}
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

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_btn()

    def closeEvent(self, event):
        from tools.tool_common import is_app_shutting_down
        if is_app_shutting_down():
            event.accept()
            return
        event.ignore()
        self.hide()
        self._try_release_tool()


# ─────────────────────────────────────────────
# 模块注册（ToolSingleton 由 tools/__init__.py import 触发）
# ─────────────────────────────────────────────
