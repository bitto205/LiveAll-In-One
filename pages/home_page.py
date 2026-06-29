import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QWidget, QScrollArea, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer

import theme as _theme
import config as _cfg
from base_page import BasePage, register


# ─────────────────────────────────────────────
# 顶部弹窗提示（Toast）
# ─────────────────────────────────────────────
class _Toast(QLabel):
    """
    浮在页面顶部中央的临时提示条，3 秒后自动消失。
    用法：toast.show_msg("保存成功")  /  toast.show_msg("出错了", error=True)
    """
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_msg(self, msg: str, error: bool = False, ms: int = 2500):
        C = _theme.get()
        bg = "#D20F39" if error else C["active_line"]
        self.setStyleSheet(f"""
            background: {bg};
            color: #ffffff;
            border-radius: 8px;
            padding: 8px 20px;
            font-size: 13px;
            font-weight: 600;
        """)
        self.setText(msg)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._timer.start(ms)

    def _reposition(self):
        pw = self.parent().width()
        self.move(max(0, (pw - self.width()) // 2), 12)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible():
            self._reposition()


# ─────────────────────────────────────────────
# 连接按钮状态
# ─────────────────────────────────────────────
class _Btn:
    IDLE       = "idle"
    CONNECTING = "connecting"
    CONNECTED  = "connected"
    ERROR      = "error"


# ─────────────────────────────────────────────
# 通用样式辅助
# ─────────────────────────────────────────────
def _style_outlined(C: dict, h: int = 36) -> str:
    """蓝框白底，hover 微微变灰 —— Step 1/2/3 通用按钮样式"""
    return f"""
        QPushButton {{
            background: {C["card"]};
            color: {C["active_line"]};
            border: 1.5px solid {C["active_line"]};
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            min-height: {h}px;
            padding: 0 16px;
        }}
        QPushButton:hover {{
            background: {C["hover"]};
            border: 1.5px solid {C["active_line"]};
        }}
    """

def _style_disabled(C: dict, h: int = 36) -> str:
    return f"""
        QPushButton {{
            background: {C["border"]};
            color: {C["text_muted"]};
            border: none;
            border-radius: 8px;
            font-size: 13px;
            min-height: {h}px;
            padding: 0 16px;
        }}
    """

def _style_danger(C: dict, h: int = 36) -> str:
    return f"""
        QPushButton {{
            background: #D20F39;
            color: #ffffff;
            border: none;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            min-height: {h}px;
            padding: 0 16px;
        }}
        QPushButton:hover {{ background: #B01030; }}
    """


# ─────────────────────────────────────────────
# 步骤卡片
# ─────────────────────────────────────────────
def _step_card(num: int, title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("Card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(20, 16, 20, 16)
    lay.setSpacing(12)

    C = _theme.get()
    header = QHBoxLayout()

    badge = QLabel(str(num))
    badge.setFixedSize(24, 24)
    badge.setAlignment(Qt.AlignCenter)
    badge.setStyleSheet(f"""
        background: {C['active_line']}; color: #fff;
        border-radius: 12px; font-size: 12px; font-weight: 700;
    """)

    t = QLabel(title)
    t.setStyleSheet(
        f"font-size: 14px; font-weight: 600;"
        f" color: {C['text']}; background: transparent;"
    )

    header.addWidget(badge)
    header.addSpacing(8)
    header.addWidget(t)
    header.addStretch()
    lay.addLayout(header)

    return card, lay


# ─────────────────────────────────────────────
# HomePage
# ─────────────────────────────────────────────
@register(icon="🏠", name="主页", order=0)
class HomePage(BasePage):

    def __init__(self):
        super().__init__()
        self._connect_cb    = None
        self._disconnect_cb = None
        self._btn_state     = _Btn.IDLE
        self._was_connecting = False
        self._build()
        self._refresh_login()
        _theme.on_change(lambda _: self._refresh_theme())

    # ── 外部注入 ──────────────────────────────
    def set_callbacks(self, on_connect, on_disconnect):
        self._connect_cb    = on_connect
        self._disconnect_cb = on_disconnect

    # ── 连接状态变化（来自 main.py 信号）────────
    def on_status_change(self, connected: bool):
        if connected:
            self._set_btn(_Btn.CONNECTED)
        else:
            if self._was_connecting:
                self._set_btn(_Btn.ERROR)
                self._conn_label.setText("⚠️  直播间已断开或没有连接")
                self._conn_label.setStyleSheet(
                    "background: transparent; font-size: 13px; color: #D20F39;"
                )
                self._conn_label.setVisible(True)
                self._toast.show_msg("直播间已断开", error=True)
            self._was_connecting = False

    # ── UI 构建 ───────────────────────────────
    def _build(self):
        # Toast 叠在页面最上层
        self._toast = _Toast(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(32, 32, 32, 32)
        lay.setSpacing(16)

        # 标题
        title = QLabel("主页")
        title.setObjectName("PageTitle")
        lay.addWidget(title)
        lay.addSpacing(4)

        # ── Step 1：登录 ──
        card1, c1 = _step_card(1, "登录")
        
        desc = QLabel("请用小号/非直播号登录，在连接过程中请勿用登陆账号进入任何直播间")
        desc.setStyleSheet(
            f"background: transparent; font-size: 12px;"
            f" color: {_theme.get()['text_muted']};"
        )
        c1.addWidget(desc)
        
        self._login_status = QLabel("检测中...")
        self._login_status.setStyleSheet(
            f"background: transparent; font-size: 13px;"
            f" color: {_theme.get()['text_muted']};"
        )
        # 始终叫"登录"，有效时禁用
        self._login_btn = QPushButton("登录")
        self._login_btn.setFixedHeight(34)
        self._login_btn.setCursor(Qt.PointingHandCursor)
        self._login_btn.clicked.connect(self._do_login)

        row1 = QHBoxLayout()
        row1.addWidget(self._login_status)
        row1.addStretch()
        row1.addWidget(self._login_btn)
        c1.addLayout(row1)
        lay.addWidget(card1)

        # ── Step 2：直播间 ID ──
        card2, c2 = _step_card(2, "直播间 ID")
        self._room_input = QLineEdit()
        self._room_input.setPlaceholderText("请输入直播间 ID，直播间id即为抖音号，可在抖音--我界面查看")
        self._room_input.setFixedHeight(36)
        self._room_input.setText(_cfg.get("live_id", ""))

        self._save_btn = QPushButton("保存")
        self._save_btn.setFixedHeight(36)
        self._save_btn.setMinimumWidth(80)
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.clicked.connect(self._save_id)

        row2 = QHBoxLayout()
        row2.addWidget(self._room_input)
        row2.addWidget(self._save_btn)
        c2.addLayout(row2)
        lay.addWidget(card2)

        # ── Step 3：线路选择 ──
        card3, c3 = _step_card(3, "线路选择")

        desc = QLabel("线路一/二需登录并填写直播间ID；线路三为WinDivert拦截；线路四需先Patch直播伴侣")
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"background: transparent; font-size: 12px;"
            f" color: {_theme.get()['text_muted']};"
        )
        c3.addWidget(desc)

        self._route = _cfg.get("route", "1")   # 默认线路一

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._route_btn1 = QPushButton("线路一")
        self._route_btn2 = QPushButton("线路二")
        self._route_btn3 = QPushButton("线路三")
        for btn in (self._route_btn1, self._route_btn2, self._route_btn3):
            btn.setFixedHeight(36)
            btn.setCursor(Qt.PointingHandCursor)
        self._route_btn1.clicked.connect(lambda: self._select_route("1"))
        self._route_btn2.clicked.connect(lambda: self._select_route("2"))
        self._route_btn3.clicked.connect(lambda: self._select_route("3"))
        btn_row.addWidget(self._route_btn1)
        btn_row.addWidget(self._route_btn2)
        btn_row.addWidget(self._route_btn3)
        btn_row.addStretch()
        c3.addLayout(btn_row)

        # ── 线路四（需先 Patch 直播伴侣）──
        patch_row = QHBoxLayout()
        patch_row.setSpacing(8)

        self._route_btn4 = QPushButton("线路四")
        self._route_btn4.setFixedHeight(36)
        self._route_btn4.setCursor(Qt.PointingHandCursor)
        self._route_btn4.clicked.connect(lambda: self._select_route("4"))

        self._patch_btn = QPushButton("Patch 直播伴侣")
        self._patch_btn.setFixedHeight(36)
        self._patch_btn.setCursor(Qt.PointingHandCursor)
        self._patch_btn.clicked.connect(self._do_patch)

        self._patch_lbl = QLabel("")
        self._patch_lbl.setStyleSheet("background: transparent; font-size: 12px;")

        patch_row.addWidget(self._route_btn4)
        patch_row.addWidget(self._patch_btn)
        patch_row.addWidget(self._patch_lbl)
        patch_row.addStretch()
        c3.addLayout(patch_row)

        self._check_patch_status()
        lay.addWidget(card3)

        # ── Step 4：连接 ──
        card4, c4 = _step_card(4, "连接直播间")
        self._conn_btn = QPushButton("连接直播间")
        self._conn_btn.setFixedHeight(44)
        self._conn_btn.setCursor(Qt.PointingHandCursor)
        self._conn_btn.clicked.connect(self._on_conn_click)

        self._conn_label = QLabel("")
        self._conn_label.setAlignment(Qt.AlignCenter)
        self._conn_label.setVisible(False)

        c4.addWidget(self._conn_btn)
        c4.addWidget(self._conn_label)
        lay.addWidget(card4)

        lay.addStretch()
        scroll.setWidget(inner)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        self._refresh_theme()

    # ── 登录 ──────────────────────────────────
    def _refresh_login(self):
        f = "state.json"
        if not os.path.exists(f):
            self._login_status.setText("❌ 未登录")
            self._set_login_btn(enabled=True)
            return
        try:
            cookies = json.load(open(f, encoding="utf-8")).get("cookies", [])
            now = time.time()
            for c in cookies:
                if c.get("name") == "sessionid":
                    exp = c.get("expires", -1)
                    if exp == -1:
                        self._login_status.setText("✅ 已登录")
                        self._set_login_btn(enabled=False)
                    elif exp > now:
                        self._login_status.setText(
                            f"✅ 已登录，还剩约 {int((exp-now)/86400)} 天"
                        )
                        self._set_login_btn(enabled=False)
                    else:
                        self._login_status.setText("⚠️ 登录已过期")
                        self._set_login_btn(enabled=True)
                    return
            self._login_status.setText("⚠️ 未找到登录凭证")
            self._set_login_btn(enabled=True)
        except Exception as e:
            self._login_status.setText(f"检测失败: {e}")
            self._set_login_btn(enabled=True)

    def _set_login_btn(self, enabled: bool):
        C = _theme.get()
        self._login_btn.setEnabled(enabled)
        self._login_btn.setStyleSheet(
            _style_outlined(C, h=34) if enabled else _style_disabled(C, h=34)
        )

    def _do_login(self):
        self._login_btn.setText("登录中...")
        self._login_btn.setEnabled(False)
        try:
            from listener.login import do_login
            do_login()
            self._refresh_login()
            self._toast.show_msg("登录成功")
        except Exception as e:
            self._login_status.setText(f"登录失败: {e}")
            self._set_login_btn(enabled=True)
            self._toast.show_msg(f"登录失败: {e}", error=True)
        finally:
            self._login_btn.setText("登录")

    # ── 保存直播间 ID ────────────────────────
    def _save_id(self):
        text = self._room_input.text().strip()
        if not text:
            self._toast.show_msg("请先输入直播间 ID", error=True)
            return
        _cfg.set("live_id", text)
        self._save_btn.setText("已保存")
        self._toast.show_msg("已保存")
        QTimer.singleShot(2000, lambda: self._save_btn.setText("保存"))

    # ── 线路选择 ─────────────────────────────────
    def _select_route(self, route: str):
        self._route = route
        _cfg.set("route", route)
        self._refresh_route_btns()

    def _refresh_route_btns(self):
        C = _theme.get()
        sel = f"""
            QPushButton {{
                background: {C["active_line"]}; color: #fff;
                border: 1.5px solid {C["active_line"]};
                border-radius: 8px; font-size: 13px; font-weight: 600;
                min-height: 36px; padding: 0 20px;
            }}
        """
        unsel = _style_outlined(C, h=36)
        self._route_btn1.setStyleSheet(sel if self._route == "1" else unsel)
        self._route_btn2.setStyleSheet(sel if self._route == "2" else unsel)
        self._route_btn3.setStyleSheet(sel if self._route == "3" else unsel)
        if self._route_btn4.isVisible():
            self._route_btn4.setStyleSheet(sel if self._route == "4" else unsel)
        C = _theme.get()
        warn_style = (f"QPushButton {{ background: {C['hover']}; color: {C['text']};"
                      f" border: 1.5px solid {C['border']}; border-radius: 8px;"
                      f" font-size: 13px; padding: 0 14px; }}"
                      f"QPushButton:hover {{ background: {C['active_line']}; color: #fff; }}")
        self._patch_btn.setStyleSheet(warn_style)

    # ── 线路四 patch ─────────────────────────
    def _check_patch_status(self):
        try:
            from listener.listener4 import get_route4_status
            s = get_route4_status()
        except Exception:
            s = {}

        ready = bool(
            s.get("index_js_found")
            and s.get("is_patched")
            and s.get("exe_in_place")
            and s.get("ca_installed")
        )

        self._route_btn4.setVisible(ready)
        self._patch_btn.setVisible(not ready)
        self._patch_lbl.setVisible(not ready)

        if not ready:
            C = _theme.get()
            if not s.get("companion_installed"):
                msg = "未检测到直播伴侣"
                self._patch_btn.setEnabled(False)
                self._patch_btn.setStyleSheet(_style_disabled(C, h=36))
            elif not s.get("is_patched"):
                msg = "点击 Patch 以启用线路四"
                self._patch_btn.setEnabled(True)
            elif not s.get("exe_in_place"):
                msg = "proxy_shell.exe 不存在，请检查软件完整性"
                self._patch_btn.setEnabled(False)
                self._patch_btn.setStyleSheet(_style_disabled(C, h=36))
            elif not s.get("ca_installed"):
                msg = "CA 证书未安装，重新 Patch 可修复"
                self._patch_btn.setEnabled(True)
            else:
                msg = ""
            self._patch_lbl.setText(msg)

        if not ready and self._route == "4":
            self._select_route("1")
        self._refresh_route_btns()

    def _do_patch(self):
        self._patch_btn.setText("Patch 中...")
        self._patch_btn.setEnabled(False)
        try:
            from listener.listener4 import patch_companion
            ok, msg = patch_companion()
            if ok:
                self._patch_lbl.setText("")
                self._toast.show_msg(msg)
                self._check_patch_status()
            else:
                self._patch_lbl.setStyleSheet(
                    "background: transparent; font-size: 12px; color: #D20F39;")
                self._patch_lbl.setText(msg)
                self._toast.show_msg(msg, error=True)
        except Exception as e:
            self._toast.show_msg(f"Patch 失败: {e}", error=True)
        finally:
            self._patch_btn.setText("Patch 直播伴侣")
            self._patch_btn.setEnabled(True)

    # ── 连接/断开 ────────────────────────────
    def _on_conn_click(self):
        if self._btn_state == _Btn.IDLE or self._btn_state == _Btn.ERROR:
            if self._route in ("3", "4"):
                live_id = ""
            else:
                live_id = self._room_input.text().strip()
                if not live_id:
                    self._toast.show_msg("请先填写直播间 ID", error=True)
                    return
            self._set_btn(_Btn.CONNECTING)
            self._was_connecting = True
            if self._connect_cb:
                self._connect_cb(live_id, self._route)

        elif self._btn_state == _Btn.CONNECTED:
            self._set_btn(_Btn.IDLE)
            self._was_connecting = False
            if self._disconnect_cb:
                self._disconnect_cb()
            self._toast.show_msg("已断开连接")

    def _set_btn(self, state: str):
        self._btn_state = state
        C = _theme.get()

        if state == _Btn.IDLE:
            self._conn_btn.setText("连接直播间")
            self._conn_btn.setEnabled(True)
            self._conn_btn.setStyleSheet(_style_outlined(C, h=44))
            self._conn_label.setVisible(False)

        elif state == _Btn.CONNECTING:
            self._conn_btn.setText("连接中...")
            self._conn_btn.setEnabled(False)
            self._conn_btn.setStyleSheet(_style_disabled(C, h=44))
            self._conn_label.setVisible(False)

        elif state == _Btn.CONNECTED:
            self._conn_btn.setText("断开连接")
            self._conn_btn.setEnabled(True)
            self._conn_btn.setStyleSheet(_style_danger(C, h=44))
            self._conn_label.setText("✅  已连接")
            self._conn_label.setStyleSheet(
                f"background: transparent; font-size: 13px;"
                f" color: {C['active_line']};"
            )
            self._conn_label.setVisible(True)
            self._toast.show_msg("直播间连接成功 🎉")

        elif state == _Btn.ERROR:
            self._conn_btn.setText("连接直播间")
            self._conn_btn.setEnabled(True)
            self._conn_btn.setStyleSheet(_style_outlined(C, h=44))

    # ── 主题刷新 ─────────────────────────────
    def _refresh_theme(self):
        C = _theme.get()

        self._room_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C["card"]}; color: {C["text"]};
                border: 1px solid {C["border"]}; border-radius: 6px;
                padding: 0 10px; font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {C["active_line"]}; }}
        """)

        self._save_btn.setStyleSheet(_style_outlined(C, h=36))
        self._set_login_btn(enabled=self._login_btn.isEnabled())
        self._set_btn(self._btn_state)
        self._refresh_route_btns()

    # Toast 跟随父容器尺寸
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_toast"):
            self._toast._reposition()