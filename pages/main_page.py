"""
main_page.py — 主窗口框架（白色系版本）

窗口: 整体圆角 10px，透明背景实现
侧边栏: 纯白卡片，锐角，右侧投影
导航按钮: 直角（无 border-radius）
窗口控制: 等宽等高矩形，无圆角
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pages
import pages.theme as _theme
import config as _cfg

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QHBoxLayout, QVBoxLayout, QStackedWidget,
    QLabel, QPushButton, QFrame, QSizePolicy, QScrollArea,
    QGraphicsDropShadowEffect, QSystemTrayIcon, QMenu,
)
from PySide6.QtCore  import Qt, QPropertyAnimation, QEasingCurve
from PySide6.QtGui   import QColor, QIcon, QPixmap, QPainter, QFont

from pages import get_pages, BasePage

# ─────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────
SIDEBAR_W_EXPANDED  = 220
SIDEBAR_W_COLLAPSED = 64
ANIM_MS             = 220
WIN_SHADOW_MARGIN   = 14


def _C() -> dict:
    """当前主题颜色字典，每次调用都读最新主题。"""
    return _theme.get()


class _ColorProxy:
    """让 C["key"] 在类方法里也能动态读当前主题，无需每次写 _C()["key"]。"""
    def __getitem__(self, key: str) -> str:
        return _theme.get()[key]


C = _ColorProxy()


def build_qss() -> str:
    C = _C()
    return f"""
/* ── 全局 ── */
QWidget {{
    background: transparent;
    color: {C["text"]};
    font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 14px;
    outline: none;
}}

/* ── 窗口主卡片（圆角、白色底）── */
#WindowCard {{
    background: {C["bg"]};
    border-radius: 10px;
    border: 1px solid {C["win_edge"]};
}}

/* ── 标题栏 ── */
#TitleBar {{
    background: {C["bg"]};
    border-radius: 10px 10px 0 0;
    border-bottom: 1px solid {C["border"]};
}}
#AppTitle {{
    color: {C["text_muted"]};
    font-size: 12px;
    background: transparent;
}}

/* ── 窗口控制按钮：等尺寸矩形，无圆角 ── */
#WinBtn {{
    background: transparent;
    border: none;
    border-radius: 0px;
    color: {C["text_muted"]};
    font-size: 13px;
    min-width: 46px; max-width: 46px;
    min-height: 36px; max-height: 36px;
}}
#WinBtn:hover {{
    background: {C["btn_hover"]};
    color: {C["text"]};
    border-radius: 0px;
}}
#WinBtn_close {{
    background: transparent;
    border: none;
    border-radius: 0 10px 0 0;
    color: {C["text_muted"]};
    font-size: 13px;
    min-width: 46px; max-width: 46px;
    min-height: 36px; max-height: 36px;
}}
#WinBtn_close:hover {{
    background: {C["close_hover"]};
    color: #ffffff;
    border-radius: 0 10px 0 0;
}}

/* ── 侧边栏（白色卡片，锐角）── */
#Sidebar {{
    background: {C["sidebar"]};
    border-radius: 0 0 0 10px;
}}
#SidebarDivider {{
    background: {C["win_edge"]};
    min-width: 1px;
    max-width: 1px;
}}

/* ── 导航按钮：直角 ── */
#ToggleBtn, #NavBtn, #SettingsBtn {{
    background: transparent;
    border: none;
    border-radius: 0px;
    text-align: left;
    color: {C["text_muted"]};
}}
#ToggleBtn:hover {{
    background: {C["hover"]};
    color: {C["text"]};
}}
#NavBtn:hover, #SettingsBtn:hover {{
    background: {C["hover"]};
    color: {C["text"]};
}}
#NavBtn[active=true], #SettingsBtn[active=true] {{
    background: {C["active"]};
    color: {C["text"]};
}}

/* ── 滚动区 ── */
#ScrollContainer {{ background: transparent; }}
QScrollArea       {{ background: transparent; border: none; }}
QScrollBar:vertical {{
    background: transparent; width: 4px;
}}
QScrollBar::handle:vertical {{
    background: {C["border"]}; border-radius: 2px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

/* ── 内容区 ── */
#ContentArea  {{ background: {C["bg"]}; border-radius: 0 0 10px 0; }}
#Card         {{ background: {C["card"]}; border-radius: 10px; border: 1px solid {C["border"]}; }}
#PageTitle    {{ font-size: 22px; font-weight: 600; color: {C["text"]}; }}
#PageSubtitle {{ font-size: 13px; color: {C["text_muted"]}; }}
"""


# ─────────────────────────────────────────────
# 自定义标题栏
# ─────────────────────────────────────────────
class CloseButton(QPushButton):
    """
    自绘关闭按钮：用 QPainter 保证右上角圆弧与窗口圆角精确契合。
    QSS border-radius 对 QPushButton hover 背景裁剪不可靠，
    因此完全绕过 QSS 背景渲染，手动绘制。
    """
    W, H, R = 46, 36, 10   # 宽、高、圆角半径（与 WindowCard 一致）

    def __init__(self, parent=None):
        super().__init__("✕", parent)
        self.setObjectName("WinBtn_close")
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.ArrowCursor)
        self.setFlat(True)
        self.setAttribute(Qt.WA_Hover)
        self.setStyleSheet("border: none; background: transparent;")

    def paintEvent(self, _event):
        from PySide6.QtGui import QPainter, QPainterPath, QColor
        C = _C()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # ── 背景 ──────────────────────────────────────
        if self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C["close_hover"]))

            # 只有右上角是圆弧，其余三角为直角
            path = QPainterPath()
            path.moveTo(0, 0)
            path.lineTo(self.W - self.R, 0)
            path.arcTo(self.W - self.R * 2, 0, self.R * 2, self.R * 2, 90, -90)
            path.lineTo(self.W, self.H)
            path.lineTo(0, self.H)
            path.closeSubpath()
            p.drawPath(path)

        # ── 文字 ──────────────────────────────────────
        color = QColor("#ffffff") if self.underMouse() else QColor(C["text_muted"])
        p.setPen(color)
        font = self.font()
        font.setPointSize(11)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignCenter, "✕")
        p.end()


# ─────────────────────────────────────────────
# 自定义标题栏
# ─────────────────────────────────────────────
class TitleBar(QWidget):
    HEIGHT = 36

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(self.HEIGHT)
        self._drag_pos = None
        self._build()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 0, 0)
        lay.setSpacing(0)

        title = QLabel("LiveAIO")
        title.setObjectName("AppTitle")
        lay.addWidget(title)
        lay.addStretch()

        # 三个等尺寸矩形按钮，无圆角（关闭右上角跟随窗口圆角）
        for text, obj_id, slot in [
            ("─", "WinBtn", self._on_min),
        ]:
            btn = QPushButton(text)
            btn.setObjectName(obj_id)
            btn.setCursor(Qt.ArrowCursor)
            btn.clicked.connect(slot)
            lay.addWidget(btn)

        close_btn = CloseButton()
        close_btn.clicked.connect(self._on_close)
        lay.addWidget(close_btn)

    def _on_min(self):   self.window().showMinimized()
    def _on_close(self): self.window().close()
    def _on_max(self):
        w = self.window()
        w.showNormal() if w.isMaximized() else w.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint()
                - self.window().frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.window().move(
                event.globalPosition().toPoint() - self._drag_pos
            )

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_max()


# ─────────────────────────────────────────────
# 导航按钮
# ─────────────────────────────────────────────
class NavButton(QPushButton):
    BTN_H   = 44
    ICON_PX = 18

    def __init__(self, icon: str, label: str,
                 obj_name: str = "NavBtn", parent=None):
        super().__init__(parent)
        self.setObjectName(obj_name)
        self.setFixedHeight(self.BTN_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 左侧激活指示条
        self._bar = QFrame()
        self._bar.setFixedWidth(3)
        self._bar.setStyleSheet("background: transparent;")

        self._icon = QLabel(icon)
        self._icon.setFixedWidth(SIDEBAR_W_COLLAPSED - 3)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet(
            f"font-size: {self.ICON_PX}px; background: transparent;"
            f" color: {C['text_muted']};"
        )

        self._label = QLabel(label)
        self._label.setStyleSheet(
            f"background: transparent; color: {C['text_muted']}; font-size: 14px;"
        )

        lay.addWidget(self._bar)
        lay.addWidget(self._icon)
        lay.addWidget(self._label)
        lay.addStretch()

    def set_expanded(self, val: bool):
        pass  # 不再控制可见性，由 Sidebar 裁剪处理

    def set_active(self, val: bool):
        self.setProperty("active", val)
        self.style().unpolish(self)
        self.style().polish(self)
        bar_color  = C["active_line"] if val else "transparent"
        txt_color  = C["text"]        if val else C["text_muted"]
        self._bar.setStyleSheet(f"background: {bar_color};")
        self._icon.setStyleSheet(
            f"font-size: {self.ICON_PX}px; background: transparent;"
            f" color: {txt_color};"
        )
        self._label.setStyleSheet(
            f"background: transparent; color: {txt_color}; font-size: 14px;"
        )


# ─────────────────────────────────────────────
# 侧边栏
# ─────────────────────────────────────────────
class Sidebar(QWidget):
    """
    收缩动画原理：
      内部 _panel 固定宽度 SIDEBAR_W_EXPANDED，不跟随 Sidebar 收缩。
      Sidebar 外壳做宽度动画，Qt 自动裁剪超出边界的子控件。
      → 文字完全静止，边界线像遮板一样从右向左扫过来盖住文字。
    """

    def __init__(self, page_metas, settings_cls, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_W_EXPANDED)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self._expanded      = True
        self._nav_btns:     list[NavButton] = []
        self._settings_btn: NavButton | None = None

        self._build(page_metas, settings_cls)
        self._setup_anim()

    def _build(self, page_metas, settings_cls):
        # ── 固定宽度内部面板，不参与布局伸缩 ──
        self._panel = QWidget(self)
        # 初始位置，resizeEvent 会维持它始终铺满且宽度固定
        self._panel.setGeometry(0, 0, SIDEBAR_W_EXPANDED, 600)

        outer = QVBoxLayout(self._panel)
        outer.setContentsMargins(0, 12, 0, 12)
        outer.setSpacing(0)

        # ── 折叠/展开按钮（图标宽度 = 完整折叠宽度，保证折叠后刚好只露图标）──
        self._toggle = QPushButton()
        self._toggle.setObjectName("ToggleBtn")
        self._toggle.setFixedHeight(44)
        self._toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.clicked.connect(self.toggle)

        t_lay = QHBoxLayout(self._toggle)
        t_lay.setContentsMargins(0, 0, 0, 0)
        t_lay.setSpacing(0)

        self._t_icon = QLabel("◀")
        self._t_icon.setFixedWidth(SIDEBAR_W_COLLAPSED)   # 恰好等于折叠宽度
        self._t_icon.setAlignment(Qt.AlignCenter)
        self._t_icon.setStyleSheet(
            f"font-size: 13px; background: transparent; color: {C['text_muted']};"
        )
        self._t_label = QLabel("收起")
        self._t_label.setStyleSheet(
            f"background: transparent; color: {C['text_muted']}; font-size: 13px;"
        )
        t_lay.addWidget(self._t_icon)
        t_lay.addWidget(self._t_label)
        t_lay.addStretch()

        outer.addWidget(self._toggle)
        outer.addSpacing(4)

        # ── 可滚动导航区（container 宽度固定，不随 ScrollArea 压缩）──
        scroll = QScrollArea()
        scroll.setWidgetResizable(False)           # 不压缩 container
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        container = QWidget()
        container.setObjectName("ScrollContainer")
        container.setFixedWidth(SIDEBAR_W_EXPANDED) # 宽度锁死，不被裁
        c_lay = QVBoxLayout(container)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setSpacing(1)

        for meta in page_metas:
            btn = NavButton(meta.icon, meta.name)
            self._nav_btns.append(btn)
            c_lay.addWidget(btn)

        c_lay.addStretch()
        scroll.setWidget(container)
        outer.addWidget(scroll, stretch=1)
        outer.addSpacing(4)

        # ── 设置按钮（固定底部）──
        self._settings_btn = NavButton(
            settings_cls.PAGE_ICON,
            settings_cls.PAGE_NAME,
            obj_name="SettingsBtn",
        )
        outer.addWidget(self._settings_btn)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 保持 _panel 始终铺满高度、宽度锁定为展开值
        if hasattr(self, "_panel"):
            self._panel.setGeometry(0, 0, SIDEBAR_W_EXPANDED, self.height())

    def _setup_anim(self):
        self._anim_min = QPropertyAnimation(self, b"minimumWidth")
        self._anim_max = QPropertyAnimation(self, b"maximumWidth")
        for a in (self._anim_min, self._anim_max):
            a.setDuration(ANIM_MS)
            a.setEasingCurve(QEasingCurve.InOutQuart)

    def toggle(self):
        self._expanded = not self._expanded
        start = self.width()
        end   = SIDEBAR_W_EXPANDED if self._expanded else SIDEBAR_W_COLLAPSED

        for a in (self._anim_min, self._anim_max):
            a.stop()
            a.setStartValue(start)
            a.setEndValue(end)
            a.start()

        # 只更新折叠图标方向，不动任何 label 的可见性
        self._t_icon.setText("◀" if self._expanded else "▶")

    def set_active_main(self, index: int):
        for i, btn in enumerate(self._nav_btns):
            btn.set_active(i == index)
        if self._settings_btn:
            self._settings_btn.set_active(False)

    def set_active_settings(self):
        for btn in self._nav_btns:
            btn.set_active(False)
        if self._settings_btn:
            self._settings_btn.set_active(True)

    @property
    def nav_buttons(self) -> list[NavButton]:
        return self._nav_btns

    @property
    def settings_button(self) -> NavButton | None:
        return self._settings_btn


# ─────────────────────────────────────────────
# 主窗口
# ─────────────────────────────────────────────
class MainPage(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LiveHelper")
        self.setMinimumSize(900 + WIN_SHADOW_MARGIN * 2,
                            600 + WIN_SHADOW_MARGIN * 2)
        self.resize(1200 + WIN_SHADOW_MARGIN * 2,
                    750  + WIN_SHADOW_MARGIN * 2)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._metas = get_pages()
        self._pages: list[BasePage] = [m.cls() for m in self._metas]

        settings_cls = pages.SETTINGS_PAGE
        self._settings_page: BasePage = settings_cls()
        self._settings_idx = len(self._pages)

        self._build_ui(settings_cls)
        self._connect()
        self._navigate_main(0)

        self.setStyleSheet(build_qss())
        _theme.on_change(lambda _: self.setStyleSheet(build_qss()))
        self._setup_tray()

    def _build_ui(self, settings_cls):
        # 最外层透明（留出阴影空间）
        root = QWidget()
        self.setCentralWidget(root)

        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(
            WIN_SHADOW_MARGIN, WIN_SHADOW_MARGIN,
            WIN_SHADOW_MARGIN, WIN_SHADOW_MARGIN,
        )

        # 窗口主卡片（实际可见的圆角窗口）
        self._card = QFrame()
        self._card.setObjectName("WindowCard")

        # 投影效果
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 55))
        self._card.setGraphicsEffect(shadow)

        root_lay.addWidget(self._card)

        # 卡片内布局
        card_lay = QVBoxLayout(self._card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)

        # 标题栏
        self._title_bar = TitleBar()
        card_lay.addWidget(self._title_bar)

        # 主体（侧边栏 + 内容区）
        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self._sidebar = Sidebar(self._metas, settings_cls)
        body_lay.addWidget(self._sidebar)

        # 物理分隔线：1px 宽，不会被子组件遮挡
        divider = QFrame()
        divider.setObjectName("SidebarDivider")
        body_lay.addWidget(divider)

        self._stack = QStackedWidget()
        self._stack.setObjectName("ContentArea")
        for page in self._pages:
            self._stack.addWidget(page)
        self._stack.addWidget(self._settings_page)
        body_lay.addWidget(self._stack)

        card_lay.addWidget(body)

    def _connect(self):
        for i, btn in enumerate(self._sidebar.nav_buttons):
            btn.clicked.connect(lambda _, idx=i: self._navigate_main(idx))
        if self._sidebar.settings_button:
            self._sidebar.settings_button.clicked.connect(self._navigate_settings)

    def _navigate_main(self, index: int):
        self._stack.setCurrentIndex(index)
        self._sidebar.set_active_main(index)

    def _navigate_settings(self):
        self._stack.setCurrentIndex(self._settings_idx)
        self._sidebar.set_active_settings()

    def broadcast_message(self, msg):
        for page in self._pages + [self._settings_page]:
            try: page.on_message(msg)
            except Exception: pass

    def broadcast_status(self, connected: bool):
        for page in self._pages + [self._settings_page]:
            try: page.on_status_change(connected)
            except Exception: pass

    def get_page(self, page_type: type):
        """按类型返回已注册的 page 实例，找不到返回 None。"""
        for page in self._pages:
            if isinstance(page, page_type):
                return page
        return None

    # ── 系统托盘 ──────────────────────────────────
    def _setup_tray(self):
        """创建系统托盘图标与右键菜单。"""
        # 用主题 active_line 色画一个简单图标
        size = 32
        pix  = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(_theme.get()["active_line"]))
        p.setPen(Qt.NoPen)
        p.drawEllipse(2, 2, size - 4, size - 4)
        p.setPen(QColor("#ffffff"))
        f = QFont()
        f.setBold(True)
        f.setPixelSize(14)
        p.setFont(f)
        p.drawText(pix.rect(), Qt.AlignCenter, "L")
        p.end()

        self._tray = QSystemTrayIcon(QIcon(pix), self)
        self._tray.setToolTip("LiveHelper")

        menu = QMenu()
        show_action = menu.addAction("显示主窗口")
        menu.addSeparator()
        quit_action = menu.addAction("退出")

        show_action.triggered.connect(self._restore_from_tray)
        quit_action.triggered.connect(QApplication.instance().quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.showNormal()
        self.activateWindow()

    def closeEvent(self, event):
        """关闭按钮：若开启"缩小到托盘"则隐藏窗口而非退出。"""
        if _cfg.get("minimize_to_tray", True):
            event.ignore()
            self.hide()
            if hasattr(self, "_tray"):
                self._tray.showMessage(
                    "LiveHelper",
                    "程序已最小化到任务栏，右键图标可退出",
                    QSystemTrayIcon.Information,
                    2000,
                )
        else:
            event.accept()


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainPage()
    win.show()
    sys.exit(app.exec())
