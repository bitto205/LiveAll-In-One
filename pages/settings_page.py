import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pages.theme as _theme
import config as _cfg

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget,
    QLabel, QPushButton, QFrame, QSizePolicy, QScrollArea,
)
from PySide6.QtCore import Qt

from pages import BasePage, BaseSetting
from pages.widgets import ThemedComboBox, ThemedToggle


def _C() -> dict:
    return _theme.get()


def build_setting_qss() -> str:
    C = _C()
    return f"""
#SettingsSidebar {{
    background: {C["sidebar"]};
    border-bottom: 1px solid {C["border"]};
}}
#SettingNavBtn {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 0 16px;
    color: {C["text_muted"]};
    font-size: 13px;
}}
#SettingNavBtn:hover {{
    background: {C["hover"]};
    color: {C["text"]};
    border-bottom: 2px solid transparent;
}}
#SettingNavBtn[active=true] {{
    background: transparent;
    color: {C["text"]};
    font-weight: 600;
    border-bottom: 2px solid {C["active_line"]};
}}
#SettingContent  {{ background: {C["bg"]}; }}
#SettingCard     {{ background: {C["card"]}; border-radius: 10px;
                    border: 1px solid {C["border"]}; }}
#SettingCardTitle   {{ font-size: 13px; font-weight: 600; color: {C["text_muted"]}; }}
#SettingPageTitle   {{ font-size: 20px; font-weight: 600; color: {C["text"]}; }}
QScrollBar:vertical {{ background: transparent; width: 4px; }}
QScrollBar::handle:vertical {{ background: {C["border"]}; border-radius: 2px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


# ─────────────────────────────────────────────
# 顶部 Tab 按钮
# ─────────────────────────────────────────────
class SettingNavBtn(QPushButton):
    H = 46

    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self.setObjectName("SettingNavBtn")
        self.setFixedHeight(self.H)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def set_active(self, val: bool):
        self.setProperty("active", val)
        self.style().unpolish(self)
        self.style().polish(self)


# ─────────────────────────────────────────────
# 内置面板：系统
# ─────────────────────────────────────────────
class SystemSettings(BaseSetting):
    name  = "系统"
    order = 0

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        t = QLabel("系统")
        t.setObjectName("SettingPageTitle")
        lay.addWidget(t)

        card, inner = self.build_section("颜色主题")

        row = QHBoxLayout()
        row.setSpacing(12)

        lbl = QLabel("系统颜色主题")
        lbl.setStyleSheet("background: transparent; font-size: 14px;")
        row.addWidget(lbl)
        row.addStretch()

        self._combo = ThemedComboBox()
        self._combo.addItems(_theme.names())
        self._combo.setCurrentText(_theme.current_name())
        self._combo.setFixedHeight(34)
        self._combo.setMinimumWidth(160)
        self._combo.currentTextChanged.connect(_theme.set_theme)
        row.addWidget(self._combo)

        inner.addLayout(row)
        lay.addWidget(card)

        # 行为卡片
        card2, inner2 = self.build_section("行为")

        row2 = QHBoxLayout()
        row2.setSpacing(12)
        lbl2 = QLabel("关闭时缩小到任务栏")
        lbl2.setStyleSheet("background: transparent; font-size: 14px;")
        row2.addWidget(lbl2)
        row2.addStretch()

        self._tray_toggle = ThemedToggle("minimize_to_tray", default=True)
        row2.addWidget(self._tray_toggle)

        inner2.addLayout(row2)
        lay.addWidget(card2)

        lay.addStretch()



# ─────────────────────────────────────────────
# 内置面板：账号
# ─────────────────────────────────────────────
class AccountSettings(BaseSetting):
    name  = "账号"
    order = 1

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        t = QLabel("账号")
        t.setObjectName("SettingPageTitle")
        lay.addWidget(t)

        card, inner = self.build_section("登录状态")
        self._status = QLabel("检测中...")
        self._status.setObjectName("PageSubtitle")
        inner.addWidget(self._status)

        self._btn = QPushButton("重新登录")
        self._btn.setFixedHeight(34)
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setStyleSheet(f"""
            QPushButton {{
                background: {_C()["active_line"]}; color: #fff;
                border: none; border-radius: 6px;
                font-size: 13px; padding: 0 16px;
            }}
            QPushButton:hover {{ background: #7B9FFF; }}
        """)
        self._btn.clicked.connect(self._do_login)
        inner.addWidget(self._btn)
        lay.addWidget(card)
        lay.addStretch()

        self._refresh()

    def _refresh(self):
        import json, time
        f = "state.json"
        if not os.path.exists(f):
            self._status.setText("未登录")
            return
        try:
            cookies = json.load(open(f)).get("cookies", [])
            now = time.time()
            for c in cookies:
                if c.get("name") == "sessionid":
                    exp = c.get("expires", -1)
                    if exp == -1:
                        self._status.setText("✅  已登录")
                    elif exp > now:
                        self._status.setText(
                            f"✅  已登录，还剩约 {int((exp-now)/86400)} 天"
                        )
                    else:
                        self._status.setText("⚠️  登录已过期")
                    return
            self._status.setText("⚠️  未找到登录凭证")
        except Exception as e:
            self._status.setText(f"检测失败: {e}")

    def _do_login(self):
        try:
            from listener.login import do_login
            self._btn.setText("登录中...")
            self._btn.setEnabled(False)
            do_login()
            self._refresh()
        except Exception as e:
            self._status.setText(f"登录失败: {e}")
        finally:
            self._btn.setText("重新登录")
            self._btn.setEnabled(True)


# ─────────────────────────────────────────────
# SettingsPage 主体
# ─────────────────────────────────────────────
class SettingsPage(BasePage):
    PAGE_ICON = "⚙"
    PAGE_NAME = "设置"
    PAGE_SIZE = 5

    def __init__(self):
        super().__init__()
        self._nav_btns: list[SettingNavBtn] = []
        self._panels:   list[QWidget]       = []
        self._cur_page  = 0
        self._build()
        self._navigate(0)

        self.setStyleSheet(build_setting_qss())
        _theme.on_change(lambda _: self.setStyleSheet(build_setting_qss()))

    def _build(self):
        import pages as _pages

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        topbar = QWidget()
        topbar.setObjectName("SettingsSidebar")
        topbar.setFixedHeight(46)
        tb_lay = QHBoxLayout(topbar)
        tb_lay.setContentsMargins(8, 0, 8, 0)
        tb_lay.setSpacing(0)

        self._prev_btn = QPushButton("← 上一页")
        self._prev_btn.setObjectName("SettingNavBtn")
        self._prev_btn.setFixedHeight(46)
        self._prev_btn.setCursor(Qt.PointingHandCursor)
        self._prev_btn.clicked.connect(self._prev_page)
        tb_lay.addWidget(self._prev_btn)

        self._tab_container = QWidget()
        self._tab_layout = QHBoxLayout(self._tab_container)
        self._tab_layout.setContentsMargins(0, 0, 0, 0)
        self._tab_layout.setSpacing(0)
        tb_lay.addWidget(self._tab_container)

        tb_lay.addStretch()

        self._next_btn = QPushButton("下一页 →")
        self._next_btn.setObjectName("SettingNavBtn")
        self._next_btn.setFixedHeight(46)
        self._next_btn.setCursor(Qt.PointingHandCursor)
        self._next_btn.clicked.connect(self._next_page)
        tb_lay.addWidget(self._next_btn)

        lay.addWidget(topbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("SettingContent")

        self._stack = QStackedWidget()
        self._stack.setObjectName("SettingContent")

        for cls in _pages.SETTINGS:
            panel = cls()
            btn   = SettingNavBtn(cls.name)
            btn.clicked.connect(
                lambda _, i=len(self._nav_btns): self._navigate(i)
            )
            self._nav_btns.append(btn)
            self._panels.append(panel)
            self._stack.addWidget(panel)

        scroll.setWidget(self._stack)
        lay.addWidget(scroll)

        self._update_page()

    def _total_pages(self) -> int:
        import math
        return max(1, math.ceil(len(self._nav_btns) / self.PAGE_SIZE))

    def _update_page(self):
        while self._tab_layout.count():
            item = self._tab_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        start = self._cur_page * self.PAGE_SIZE
        for btn in self._nav_btns[start : start + self.PAGE_SIZE]:
            self._tab_layout.addWidget(btn)
            btn.show()

        has_prev = self._cur_page > 0
        has_next = self._cur_page < self._total_pages() - 1

        self._prev_btn.setEnabled(has_prev)
        self._next_btn.setEnabled(has_next)

        C = _C()
        for btn, has in [(self._prev_btn, has_prev), (self._next_btn, has_next)]:
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none;"
                f" border-bottom: 2px solid transparent; padding: 0 10px;"
                f" font-size: 13px;"
                f" color: {C['text'] if has else C['text_muted']}; }}"
                f"QPushButton:hover {{ background: {C['hover'] if has else 'transparent'}; }}"
            )

    def _prev_page(self):
        if self._cur_page > 0:
            self._cur_page -= 1
            self._update_page()

    def _next_page(self):
        if self._cur_page < self._total_pages() - 1:
            self._cur_page += 1
            self._update_page()

    def _navigate(self, index: int):
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_btns):
            btn.set_active(i == index)