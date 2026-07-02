import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QWidget, QScrollArea, QStackedWidget,
    QGridLayout, QFileDialog,
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal

import pages.theme as _theme
import config as _cfg
from pages import BasePage, register


class _Toast(QLabel):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_msg(self, msg: str, error: bool = False, ms: int = 2500):
        C = _theme.get()
        bg = C["close_hover"] if error else C["active_line"]
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


class _Btn:
    IDLE       = "idle"
    CONNECTING = "connecting"
    CONNECTED  = "connected"
    ERROR      = "error"


def _preempt_msg(by_route: str) -> str:
    return f"listener{by_route}停止连接直播间"


def _apply_listener_preempt(page, by_route: str, home) -> None:
    msg = _preempt_msg(by_route)
    page._preempted = True
    page._was_connecting = False
    page.set_conn_state(_Btn.ERROR)
    page._conn_label.setText(f"⚠️  {msg}")
    page._conn_label.setStyleSheet(_theme.qss_error_label())
    page._conn_label.setVisible(True)
    home.toast.show_msg(msg, error=True)


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


def _scroll_page(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
    scroll.setWidget(content)
    return scroll


def _pick_companion_dir(home) -> None:
    from listener.listener4 import get_manual_companion_dir, set_manual_companion_dir
    start = get_manual_companion_dir() or ""
    path = QFileDialog.getExistingDirectory(home, "选择直播伴侣安装目录", start)
    if not path:
        return
    ok, msg = set_manual_companion_dir(path)
    home.toast.show_msg(msg, error=not ok)
    if ok:
        from listener.listener3 import run_page_check
        from listener.listener4 import run_page_check as run_r4
        run_page_check()
        run_r4()
        if home._route3_page:
            home._route3_page.refresh_status()
        if home._route4_page:
            home._route4_page.refresh_status()


def _refresh_companion_path_btn(btn: QPushButton, in_registry: bool) -> None:
    if in_registry:
        btn.hide()
        return
    btn.show()
    from listener.listener4 import get_manual_companion_dir
    btn.setText("更换路径" if get_manual_companion_dir() else "指定路径")
    btn.setStyleSheet(_theme.qss_outlined(h=36))


_ROUTE_META = {
    "1": {
        "title": "线路一",
        "badge": "JS Hook",
        "desc": "该线路需要登录抖音，在直播间连接期间请勿用登录账号进入任何直播间",
    },
    "2": {
        "title": "线路二",
        "badge": "WSS",
        "desc": "该线路需要登录抖音，在直播间连接期间请勿用登录账号进入任何直播间",
    },
    "3": {
        "title": "线路三",
        "badge": "WinDivert",
        "desc": "\n该线路需要监听直播伴侣，无需登录抖音，但不能同时运行任何代理软件。在连接直播间以后，请勿关闭直播伴侣或者关播，否则需要重新开播才能连接",
    },
    "4": {
        "title": "线路四",
        "badge": "Proxy Shell",
        "desc": "该线路需要监听直播伴侣，无需登录抖音。但是需要patch直播伴侣",
    },
}


class ConnPageMixin:
    """线路页共享：抢占中断、断开回调。"""

    def stop_by_preempt(self, by_route: str) -> None:
        _apply_listener_preempt(self, by_route, self._home)

    def on_status_change(self, connected: bool, *, success_toast: str = "直播间连接成功 🎉"):
        if connected:
            self._preempted = False
            self.set_conn_state(_Btn.CONNECTED)
            self._home.toast.show_msg(success_toast)
        else:
            if self._preempted:
                self._preempted = False
                return
            if self._was_connecting:
                self.set_conn_state(_Btn.ERROR)
                self._conn_label.setText("⚠️  直播间已断开或没有连接")
                self._conn_label.setStyleSheet(_theme.qss_error_label())
                self._conn_label.setVisible(True)
                self._home.toast.show_msg("直播间已断开", error=True)
            self._was_connecting = False

    def disconnect_listener(self):
        self._home._connected_route = None
        if self._home._disconnect_cb:
            self._home._disconnect_cb()


class _LoginThread(QThread):
    """在后台跑 Playwright 登录，避免阻塞 Qt 主线程。"""
    finished = Signal(bool, str)

    def run(self):
        try:
            from listener.login import do_login
            ok = do_login()
            self.finished.emit(ok, "" if ok else "登录未完成或已取消")
        except Exception as e:
            self.finished.emit(False, str(e))


class _RoutePickerPage(QWidget):
    def __init__(self, on_pick):
        super().__init__()
        self._on_pick = on_pick
        self._cards: dict[str, QFrame] = {}
        self._card_labels: dict[str, dict[str, QLabel]] = {}
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 32)
        outer.setSpacing(20)

        title = QLabel("选择线路")
        title.setObjectName("PageTitle")
        outer.addWidget(title)

        hint = QLabel("请先选择监听方案，再进入对应配置页完成连接。")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"background: transparent; font-size: 13px;"
            f" color: {_theme.get()['text_muted']};"
        )
        outer.addWidget(hint)
        outer.addSpacing(8)

        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setSpacing(16)
        grid.setContentsMargins(0, 0, 0, 0)

        for i, route in enumerate(("1", "2", "3", "4")):
            card = self._make_card(route)
            self._cards[route] = card
            grid.addWidget(card, i // 2, i % 2)

        outer.addWidget(grid_w)
        outer.addStretch()

    def _make_card(self, route: str) -> QFrame:
        meta = _ROUTE_META[route]
        card = QFrame()
        card.setObjectName("Card")
        card.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)

        top = QHBoxLayout()
        name = QLabel(meta["title"])
        badge = QLabel(meta["badge"])
        badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(name)
        top.addStretch()
        top.addWidget(badge)
        lay.addLayout(top)

        desc = QLabel(meta["desc"])
        desc.setWordWrap(True)
        lay.addWidget(desc)

        self._card_labels[route] = {
            "name": name, "badge": badge, "desc": desc,
        }

        card.mousePressEvent = lambda e, r=route: self._on_pick(r)  # type: ignore
        return card

    def refresh_theme(self):
        last = _cfg.get("route", "1")
        C = _theme.get()
        label_style = (
            f"font-size: 16px; font-weight: 700;"
            f" color: {C['text']}; background: transparent;"
        )
        badge_style = (
            f"background: transparent; color: {C['text_muted']};"
            f" font-size: 11px; font-weight: 600;"
        )
        desc_style = (
            f"font-size: 12px; color: {C['text_muted']};"
            f" background: transparent;"
        )
        for route, labels in self._card_labels.items():
            labels["name"].setStyleSheet(label_style)
            labels["badge"].setStyleSheet(badge_style)
            labels["desc"].setStyleSheet(desc_style)

        for route, card in self._cards.items():
            selected = route == last
            border = C["active_line"] if selected else C["border"]
            card.setStyleSheet(f"""
                QFrame#Card {{
                    background: {C["card"]};
                    border: 1.5px solid {border};
                    border-radius: 10px;
                }}
                QFrame#Card:hover {{
                    background: {C["hover"]};
                    border-color: {C["active_line"]};
                }}
            """)

# ─────────────────────────────────────────────
# 子页面：线路一 / 二（网页登录流程）
# ─────────────────────────────────────────────
class _WebRoutePage(QWidget, ConnPageMixin):
    def __init__(self, route: str, home: "HomePage"):
        super().__init__()
        self._route = route
        self._home = home
        self._btn_state = _Btn.IDLE
        self._was_connecting = False
        self._preempted = False
        self._login_thread: _LoginThread | None = None
        meta = _ROUTE_META[route]
        self._title_text = meta["title"]
        self._subtitle = meta["desc"]
        self._build()

    def _build(self):
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(32, 24, 32, 32)
        lay.setSpacing(16)

        self._back_btn = QPushButton("← 返回选择线路")
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.clicked.connect(self._home.show_picker)
        lay.addWidget(self._back_btn)

        title = QLabel(self._title_text)
        title.setObjectName("PageTitle")
        lay.addWidget(title)

        sub = QLabel(self._subtitle)
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"background: transparent; font-size: 13px;"
            f" color: {_theme.get()['text_muted']};"
        )
        lay.addWidget(sub)
        lay.addSpacing(4)

        # Step 1：登录
        card1, c1 = _step_card(1, "登录")
        desc = QLabel("请用小号/非直播号登录，连接过程中请勿用该账号进入任何直播间")
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"background: transparent; font-size: 12px;"
            f" color: {_theme.get()['text_muted']};"
        )
        c1.addWidget(desc)
        self._login_status = QLabel("进入线路后检测登录状态")
        self._login_status.setStyleSheet(
            f"background: transparent; font-size: 13px;"
            f" color: {_theme.get()['text_muted']};"
        )
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

        # Step 2：直播间 ID
        card2, c2 = _step_card(2, "直播间 ID")
        self._room_input = QLineEdit()
        self._room_input.setPlaceholderText(
            "请输入直播间 ID（抖音号，可在抖音「我」界面查看）"
        )
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

        # Step 3：连接
        card3, c3 = _step_card(3, "连接直播间")
        self._conn_btn = QPushButton("连接直播间")
        self._conn_btn.setFixedHeight(44)
        self._conn_btn.setCursor(Qt.PointingHandCursor)
        self._conn_btn.clicked.connect(self._on_conn_click)
        self._conn_label = QLabel("")
        self._conn_label.setAlignment(Qt.AlignCenter)
        self._conn_label.setVisible(False)
        c3.addWidget(self._conn_btn)
        c3.addWidget(self._conn_label)
        lay.addWidget(card3)

        lay.addStretch()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(_scroll_page(inner))

        self.refresh_theme()

    def refresh_login(self):
        from listener.login import get_login_ui_state
        text, btn_enabled = get_login_ui_state()
        self._login_status.setText(text)
        self._set_login_btn(enabled=btn_enabled)

    def _set_login_btn(self, enabled: bool):
        C = _theme.get()
        self._login_btn.setEnabled(enabled)
        self._login_btn.setStyleSheet(
            _theme.qss_outlined(C, h=34) if enabled else _theme.qss_disabled(C, h=34)
        )

    def _do_login(self):
        if self._login_thread and self._login_thread.isRunning():
            return
        self._login_btn.setText("登录中...")
        self._login_btn.setEnabled(False)
        self._login_thread = _LoginThread(self)
        self._login_thread.finished.connect(self._on_login_done)
        self._login_thread.start()

    def _on_login_done(self, ok: bool, err: str):
        self._login_btn.setText("登录")
        if ok:
            self.refresh_login()
            self._home.toast.show_msg("登录成功")
        else:
            self._login_status.setText(f"登录失败: {err}" if err else "登录已取消")
            self._set_login_btn(enabled=True)
            self._home.toast.show_msg(err or "登录已取消", error=True)

    def _save_id(self):
        text = self._room_input.text().strip()
        if not text:
            self._home.toast.show_msg("请先输入直播间 ID", error=True)
            return
        _cfg.set("live_id", text)
        self._save_btn.setText("已保存")
        self._home.toast.show_msg("已保存")
        QTimer.singleShot(2000, lambda: self._save_btn.setText("保存"))

    def _on_conn_click(self):
        if self._btn_state in (_Btn.IDLE, _Btn.ERROR):
            live_id = self._room_input.text().strip()
            if not live_id:
                self._home.toast.show_msg("请先填写直播间 ID", error=True)
                return
            self.set_conn_state(_Btn.CONNECTING)
            self._was_connecting = True
            _cfg.set("route", self._route)
            if self._home._connect_cb:
                self._home._connect_cb(live_id, self._route)
        elif self._btn_state == _Btn.CONNECTED:
            self.set_conn_state(_Btn.IDLE)
            self._was_connecting = False
            self._home._connected_route = None
            if self._home._disconnect_cb:
                self._home._disconnect_cb()
            self._home.toast.show_msg("已断开连接")

    def set_conn_state(self, state: str):
        self._btn_state = state
        C = _theme.get()
        if state == _Btn.IDLE:
            self._conn_btn.setText("连接直播间")
            self._conn_btn.setEnabled(True)
            self._conn_btn.setStyleSheet(_theme.qss_outlined(C, h=44))
            self._conn_label.setVisible(False)
        elif state == _Btn.CONNECTING:
            self._conn_btn.setText("连接中...")
            self._conn_btn.setEnabled(False)
            self._conn_btn.setStyleSheet(_theme.qss_disabled(C, h=44))
            self._conn_label.setVisible(False)
        elif state == _Btn.CONNECTED:
            self._conn_btn.setText("断开连接")
            self._conn_btn.setEnabled(True)
            self._conn_btn.setStyleSheet(_theme.qss_danger(C, h=44))
            self._conn_label.setText("✅  已连接")
            self._conn_label.setStyleSheet(_theme.qss_accent_label())
            self._conn_label.setVisible(True)
        elif state == _Btn.ERROR:
            self._conn_btn.setText("连接直播间")
            self._conn_btn.setEnabled(True)
            self._conn_btn.setStyleSheet(_theme.qss_outlined(C, h=44))

    def refresh_theme(self):
        C = _theme.get()
        self._back_btn.setStyleSheet(_theme.qss_back(C))
        self._room_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C["card"]}; color: {C["text"]};
                border: 1px solid {C["border"]}; border-radius: 6px;
                padding: 0 10px; font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {C["active_line"]}; }}
        """)
        self._save_btn.setStyleSheet(_theme.qss_outlined(C, h=36))
        self._set_login_btn(enabled=self._login_btn.isEnabled())
        self.set_conn_state(self._btn_state)


# ─────────────────────────────────────────────
# 子页面：线路三（Unpatch + 环境检测）
# ─────────────────────────────────────────────
class _Route3Page(QWidget, ConnPageMixin):
    _HINT = (
        "处于 patch 或者代理状态中时线路 3 不可用，"
        "在连接直播间后请勿在中途打开任何代理软件"
    )
    _CONN_HINT = "请在直播开始之前点击连接直播间"

    def __init__(self, home: "HomePage"):
        super().__init__()
        self._home = home
        self._btn_state = _Btn.IDLE
        self._was_connecting = False
        self._preempted = False
        self._env_checked = False
        self._build()

    def _build(self):
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(32, 24, 32, 32)
        lay.setSpacing(16)

        self._back_btn = QPushButton("← 返回选择线路")
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.clicked.connect(self._home.show_picker)
        lay.addWidget(self._back_btn)

        title = QLabel(_ROUTE_META["3"]["title"])
        title.setObjectName("PageTitle")
        lay.addWidget(title)

        sub = QLabel(_ROUTE_META["3"]["desc"])
        sub.setWordWrap(True)
        self._sub = sub
        lay.addWidget(sub)

        card = QFrame()
        card.setObjectName("Card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(20, 16, 20, 20)
        card_lay.setSpacing(12)

        self._hint = QLabel(self._HINT)
        self._hint.setWordWrap(True)
        card_lay.addWidget(self._hint)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self._action_btn = QPushButton("Unpatch")
        self._action_btn.setFixedHeight(36)
        self._action_btn.setMinimumWidth(120)
        self._action_btn.setCursor(Qt.PointingHandCursor)
        self._action_btn.clicked.connect(self._do_unpatch)
        btn_row.addWidget(self._action_btn)

        self._path_btn = QPushButton("指定路径")
        self._path_btn.setFixedHeight(36)
        self._path_btn.setCursor(Qt.PointingHandCursor)
        self._path_btn.clicked.connect(lambda: _pick_companion_dir(self._home))
        btn_row.addWidget(self._path_btn)

        self._warn = QLabel("当前系统代理占用，请关闭代理后重启本软件")
        self._warn.setWordWrap(True)
        self._warn.hide()
        btn_row.addWidget(self._warn, stretch=1)
        btn_row.addStretch()
        card_lay.addLayout(btn_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        card_lay.addWidget(self._status_lbl)
        lay.addWidget(card)

        conn_card = QFrame()
        conn_card.setObjectName("Card")
        conn_lay = QVBoxLayout(conn_card)
        conn_lay.setContentsMargins(20, 16, 20, 20)
        conn_lay.setSpacing(12)

        self._conn_hint = QLabel(self._CONN_HINT)
        self._conn_hint.setWordWrap(True)
        conn_lay.addWidget(self._conn_hint)

        self._conn_btn = QPushButton("连接直播间")
        self._conn_btn.setFixedHeight(44)
        self._conn_btn.setCursor(Qt.PointingHandCursor)
        self._conn_btn.clicked.connect(self._on_conn_click)
        conn_lay.addWidget(self._conn_btn)

        self._conn_label = QLabel("")
        self._conn_label.setAlignment(Qt.AlignCenter)
        self._conn_label.setVisible(False)
        conn_lay.addWidget(self._conn_label)

        lay.addWidget(conn_card)
        lay.addStretch()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(_scroll_page(inner))

        self.refresh_theme()

    def _do_unpatch(self):
        self._action_btn.setText("还原中...")
        self._action_btn.setEnabled(False)
        try:
            from listener.listener4 import unpatch_companion
            ok, msg = unpatch_companion()
            if ok:
                self._home.toast.show_msg(msg)
            else:
                self._home.toast.show_msg(msg, error=True)
        except Exception as e:
            self._home.toast.show_msg(f"Unpatch 失败: {e}", error=True)
        finally:
            self._action_btn.setText("Unpatch")
            from listener.listener3 import run_page_check
            run_page_check()
            self.refresh_status()
            if self._home._route4_page:
                from listener.listener4 import run_page_check as run_r4
                run_r4()
                self._home._route4_page.refresh_status()

    def refresh_status(self):
        self._env_checked = True
        from listener.listener3 import get_page_status
        s = get_page_status()
        C = _theme.get()
        patched = s["index_modified"]
        proxy = s["system_proxy"]
        _refresh_companion_path_btn(self._path_btn, s["companion_in_registry"])

        if s.get("manual_path_invalid"):
            self._action_btn.setEnabled(False)
            self._action_btn.setStyleSheet(_theme.qss_disabled(C, h=36))
            self._warn.hide()
            self._status_lbl.setText("该指定目录无效")
            return

        if not s["companion_installed"] or not s["index_js_found"]:
            self._action_btn.setEnabled(False)
            self._action_btn.setStyleSheet(_theme.qss_disabled(C, h=36))
            self._warn.hide()
            self._status_lbl.setText("未检测到直播伴侣，请指定安装路径")
            return

        if proxy:
            # 页面检测时系统代理开启 → 不可点 + 红字
            self._action_btn.setEnabled(False)
            self._action_btn.setStyleSheet(_theme.qss_disabled(C, h=36))
            self._warn.show()
            self._status_lbl.setText("")
        elif patched:
            # 已 patch、无代理 → 可点 Unpatch
            self._action_btn.setEnabled(True)
            self._action_btn.setStyleSheet(_theme.qss_outlined(C, h=36))
            self._warn.hide()
            self._status_lbl.setText("")
        else:
            # 未 patch、无代理 → 不可点（当前无需还原）
            self._action_btn.setEnabled(False)
            self._action_btn.setStyleSheet(_theme.qss_disabled(C, h=36))
            self._warn.hide()
            self._status_lbl.setText("当前未 patch，无需 Unpatch")

        self._refresh_conn_btn()

    def _route3_ready(self) -> bool:
        from listener.listener3 import get_page_status
        s = get_page_status()
        if s.get("manual_path_invalid"):
            return False
        if not s["companion_installed"] or not s["index_js_found"]:
            return False
        if s["system_proxy"] or s["index_modified"]:
            return False
        return True

    def _on_conn_click(self):
        if self._btn_state in (_Btn.IDLE, _Btn.ERROR):
            if not self._route3_ready():
                self._home.toast.show_msg("请先完成环境检测（Unpatch / 关闭代理 / 指定伴侣路径）", error=True)
                return
            self.set_conn_state(_Btn.CONNECTING)
            self._was_connecting = True
            _cfg.set("route", "3")
            if self._home._connect_cb:
                self._home._connect_cb("", "3")
        elif self._btn_state == _Btn.CONNECTING:
            self.set_conn_state(_Btn.IDLE)
            self._was_connecting = False
            self._home._connected_route = None
            if self._home._disconnect_cb:
                self._home._disconnect_cb()
            self._home.toast.show_msg("已取消连接")

    def set_conn_state(self, state: str):
        self._btn_state = state
        self._refresh_conn_btn()

    def _refresh_conn_btn(self):
        C = _theme.get()
        state = self._btn_state
        ready = self._route3_ready() if self._env_checked else False

        if state == _Btn.IDLE:
            self._conn_btn.setText("连接直播间")
            self._conn_btn.setEnabled(ready)
            self._conn_btn.setStyleSheet(
                _theme.qss_outlined(C, h=44) if ready else _theme.qss_disabled(C, h=44)
            )
            self._conn_label.setVisible(False)
        elif state == _Btn.CONNECTING:
            self._conn_btn.setText("连接中,等待开播...点击取消连接")
            self._conn_btn.setEnabled(True)
            self._conn_btn.setStyleSheet(_theme.qss_outlined(C, h=44))
            self._conn_label.setVisible(False)
        elif state == _Btn.CONNECTED:
            self._conn_btn.setText("连接成功")
            self._conn_btn.setEnabled(False)
            self._conn_btn.setStyleSheet(_theme.qss_success(C, h=44))
            self._conn_label.setVisible(False)
        elif state == _Btn.ERROR:
            self._conn_btn.setText("连接直播间")
            self._conn_btn.setEnabled(ready)
            self._conn_btn.setStyleSheet(
                _theme.qss_outlined(C, h=44) if ready else _theme.qss_disabled(C, h=44)
            )

    def refresh_theme(self):
        C = _theme.get()
        self._back_btn.setStyleSheet(_theme.qss_back(C))
        self._sub.setStyleSheet(
            f"background: transparent; font-size: 13px;"
            f" color: {C['text_muted']};"
        )
        self._hint.setStyleSheet(
            f"background: transparent; font-size: 13px;"
            f" color: {C['text_muted']};"
        )
        self._warn.setStyleSheet(
            f"background: transparent; font-size: 13px;"
            f" color: {C['close_hover']};"
        )
        self._status_lbl.setStyleSheet(
            f"background: transparent; font-size: 12px;"
            f" color: {C['text_muted']};"
        )
        self._conn_hint.setStyleSheet(
            f"background: transparent; font-size: 13px;"
            f" color: {C['text_muted']};"
        )
        self._refresh_conn_btn()
        if self._env_checked:
            self.refresh_status()


# ─────────────────────────────────────────────
# 子页面：线路四（Patch）
# ─────────────────────────────────────────────
class _Route4Page(QWidget, ConnPageMixin):
    _HINT = "线路 4 需要 patch 后才能使用"
    _CONN_HINT = "请先启动直播伴侣并开播后再连接"

    def __init__(self, home: "HomePage"):
        super().__init__()
        self._home = home
        self._btn_state = _Btn.IDLE
        self._was_connecting = False
        self._preempted = False
        self._env_checked = False
        self._build()

    def _build(self):
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(32, 24, 32, 32)
        lay.setSpacing(16)

        self._back_btn = QPushButton("← 返回选择线路")
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.clicked.connect(self._home.show_picker)
        lay.addWidget(self._back_btn)

        title = QLabel(_ROUTE_META["4"]["title"])
        title.setObjectName("PageTitle")
        lay.addWidget(title)

        sub = QLabel(_ROUTE_META["4"]["desc"])
        sub.setWordWrap(True)
        self._sub = sub
        lay.addWidget(sub)

        card = QFrame()
        card.setObjectName("Card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(20, 16, 20, 20)
        card_lay.setSpacing(12)

        self._hint = QLabel(self._HINT)
        self._hint.setWordWrap(True)
        card_lay.addWidget(self._hint)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self._action_btn = QPushButton("Patch")
        self._action_btn.setFixedHeight(36)
        self._action_btn.setMinimumWidth(120)
        self._action_btn.setCursor(Qt.PointingHandCursor)
        self._action_btn.clicked.connect(self._do_patch)
        btn_row.addWidget(self._action_btn)

        self._path_btn = QPushButton("指定路径")
        self._path_btn.setFixedHeight(36)
        self._path_btn.setCursor(Qt.PointingHandCursor)
        self._path_btn.clicked.connect(lambda: _pick_companion_dir(self._home))
        btn_row.addWidget(self._path_btn)
        btn_row.addStretch()
        card_lay.addLayout(btn_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        card_lay.addWidget(self._status_lbl)

        lay.addWidget(card)

        conn_card = QFrame()
        conn_card.setObjectName("Card")
        conn_lay = QVBoxLayout(conn_card)
        conn_lay.setContentsMargins(20, 16, 20, 20)
        conn_lay.setSpacing(12)

        self._conn_hint = QLabel(self._CONN_HINT)
        self._conn_hint.setWordWrap(True)
        conn_lay.addWidget(self._conn_hint)

        self._conn_btn = QPushButton("连接直播间")
        self._conn_btn.setFixedHeight(44)
        self._conn_btn.setCursor(Qt.PointingHandCursor)
        self._conn_btn.clicked.connect(self._on_conn_click)
        conn_lay.addWidget(self._conn_btn)

        self._conn_label = QLabel("")
        self._conn_label.setAlignment(Qt.AlignCenter)
        self._conn_label.setVisible(False)
        conn_lay.addWidget(self._conn_label)

        lay.addWidget(conn_card)
        lay.addStretch()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(_scroll_page(inner))

        self.refresh_theme()

    def _do_patch(self):
        self._action_btn.setText("Patch 中...")
        self._action_btn.setEnabled(False)
        try:
            from listener.listener4 import patch_companion
            ok, msg = patch_companion()
            if ok:
                self._home.toast.show_msg(msg)
            else:
                self._home.toast.show_msg(msg, error=True)
        except Exception as e:
            self._home.toast.show_msg(f"Patch 失败: {e}", error=True)
        finally:
            self._action_btn.setText("Patch")
            from listener.listener4 import run_page_check
            run_page_check()
            self.refresh_status()
            if self._home._route3_page:
                from listener.listener3 import run_page_check as run_r3
                run_r3()
                self._home._route3_page.refresh_status()

    def refresh_status(self):
        self._env_checked = True
        from listener.listener4 import get_page_status
        s = get_page_status()
        C = _theme.get()
        _refresh_companion_path_btn(self._path_btn, s["companion_in_registry"])

        if s.get("manual_path_invalid"):
            self._action_btn.setEnabled(False)
            self._action_btn.setStyleSheet(_theme.qss_disabled(C, h=36))
            self._status_lbl.setText("该指定目录无效")
            return

        if s["patch_needed"]:
            self._action_btn.setEnabled(True)
            self._action_btn.setStyleSheet(_theme.qss_outlined(C, h=36))
            parts = []
            if not s["exe_identical"]:
                parts.append("exe 与发布包不一致或缺失")
            if not s["index_exact"]:
                parts.append("index.js 与期望 patch 不一致")
            self._status_lbl.setText("；".join(parts) if parts else "")
        else:
            self._action_btn.setEnabled(False)
            self._action_btn.setStyleSheet(_theme.qss_disabled(C, h=36))
            if s["is_patched"]:
                self._status_lbl.setText("Patch 已完成，请重启直播伴侣")
            elif not s["companion_installed"]:
                self._status_lbl.setText("未检测到直播伴侣，请指定安装路径")
            elif not s["index_js_found"]:
                self._status_lbl.setText("未找到直播伴侣 index.js")
            else:
                self._status_lbl.setText("")

        self._refresh_conn_btn()

    def _route4_ready(self) -> bool:
        from listener.listener4 import get_page_status, is_proxy_shell_running
        s = get_page_status()
        if s.get("manual_path_invalid"):
            return False
        if not s["companion_installed"] or not s["index_js_found"]:
            return False
        if s["patch_needed"] or not s["is_patched"]:
            return False
        return is_proxy_shell_running()

    def _on_conn_click(self):
        if self._btn_state in (_Btn.IDLE, _Btn.ERROR):
            if not self._route4_ready():
                from listener.listener4 import get_page_status, is_proxy_shell_running
                s = get_page_status()
                if s.get("manual_path_invalid"):
                    self._home.toast.show_msg("该指定目录无效", error=True)
                elif s["patch_needed"] or not s["is_patched"]:
                    self._home.toast.show_msg("请先完成 Patch 并重启直播伴侣", error=True)
                elif not is_proxy_shell_running():
                    self._home.toast.show_msg("未检测到 proxy_shell 运行，请先启动直播伴侣并开播", error=True)
                else:
                    self._home.toast.show_msg("环境未就绪", error=True)
                return
            self.set_conn_state(_Btn.CONNECTING)
            self._was_connecting = True
            _cfg.set("route", "4")
            if self._home._connect_cb:
                self._home._connect_cb("", "4")
        elif self._btn_state == _Btn.CONNECTED:
            self.set_conn_state(_Btn.IDLE)
            self._was_connecting = False
            self._home._connected_route = None
            if self._home._disconnect_cb:
                self._home._disconnect_cb()
            self._home.toast.show_msg("已断开连接")

    def set_conn_state(self, state: str):
        self._btn_state = state
        self._refresh_conn_btn()

    def _refresh_conn_btn(self):
        C = _theme.get()
        state = self._btn_state
        ready = self._route4_ready() if self._env_checked else False

        if state == _Btn.IDLE:
            self._conn_btn.setText("连接直播间")
            self._conn_btn.setEnabled(ready)
            self._conn_btn.setStyleSheet(
                _theme.qss_outlined(C, h=44) if ready else _theme.qss_disabled(C, h=44)
            )
            self._conn_label.setVisible(False)
        elif state == _Btn.CONNECTING:
            self._conn_btn.setText("连接中...")
            self._conn_btn.setEnabled(False)
            self._conn_btn.setStyleSheet(_theme.qss_disabled(C, h=44))
            self._conn_label.setVisible(False)
        elif state == _Btn.CONNECTED:
            self._conn_btn.setText("断开连接")
            self._conn_btn.setEnabled(True)
            self._conn_btn.setStyleSheet(_theme.qss_danger(C, h=44))
            self._conn_label.setText("✅  已连接")
            self._conn_label.setStyleSheet(
                f"background: transparent; font-size: 13px;"
                f" color: {C['active_line']};"
            )
            self._conn_label.setVisible(True)
        elif state == _Btn.ERROR:
            self._conn_btn.setText("连接直播间")
            self._conn_btn.setEnabled(ready)
            self._conn_btn.setStyleSheet(
                _theme.qss_outlined(C, h=44) if ready else _theme.qss_disabled(C, h=44)
            )

    def refresh_theme(self):
        C = _theme.get()
        self._back_btn.setStyleSheet(_theme.qss_back(C))
        self._sub.setStyleSheet(
            f"background: transparent; font-size: 13px;"
            f" color: {C['text_muted']};"
        )
        self._hint.setStyleSheet(
            f"background: transparent; font-size: 13px;"
            f" color: {C['text_muted']};"
        )
        self._status_lbl.setStyleSheet(
            f"background: transparent; font-size: 12px;"
            f" color: {C['text_muted']};"
        )
        self._conn_hint.setStyleSheet(
            f"background: transparent; font-size: 13px;"
            f" color: {C['text_muted']};"
        )
        self._refresh_conn_btn()
        if self._env_checked:
            self.refresh_status()

# ─────────────────────────────────────────────
# HomePage
# ─────────────────────────────────────────────
@register(icon="🏠", name="主页", order=0)
class HomePage(BasePage):

    _IDX_PICKER = 0
    _IDX_ROUTE  = {"1": 1, "2": 2, "3": 3, "4": 4}

    def __init__(self):
        super().__init__()
        self._connect_cb      = None
        self._disconnect_cb   = None
        self._connected_route: str | None = None
        self._route3_page: _Route3Page | None = None
        self._route4_page: _Route4Page | None = None
        self._build()
        _theme.on_change(lambda _: self._refresh_theme())

    @property
    def toast(self) -> _Toast:
        return self._toast

    def set_callbacks(self, on_connect, on_disconnect):
        self._connect_cb    = on_connect
        self._disconnect_cb = on_disconnect

    def get_active_listener_route(self) -> str | None:
        """当前正在连接或已连接的线路（四线路互斥）。"""
        for r, page in self._web_pages.items():
            if page._btn_state in (_Btn.CONNECTING, _Btn.CONNECTED):
                return r
        if self._route3_page and self._route3_page._btn_state in (_Btn.CONNECTING, _Btn.CONNECTED):
            return "3"
        if self._route4_page and self._route4_page._btn_state in (_Btn.CONNECTING, _Btn.CONNECTED):
            return "4"
        return None

    def preempt_other_listeners(self, new_route: str) -> None:
        """新线路连接前，中断其他正在运行/等待中的 listener。"""
        for r, page in self._web_pages.items():
            if r != new_route and page._btn_state in (_Btn.CONNECTING, _Btn.CONNECTED):
                page.stop_by_preempt(new_route)
        if (
            self._route3_page
            and new_route != "3"
            and self._route3_page._btn_state in (_Btn.CONNECTING, _Btn.CONNECTED)
        ):
            self._route3_page.stop_by_preempt(new_route)
        if (
            self._route4_page
            and new_route != "4"
            and self._route4_page._btn_state in (_Btn.CONNECTING, _Btn.CONNECTED)
        ):
            self._route4_page.stop_by_preempt(new_route)
        active = self.get_active_listener_route()
        if active and active != new_route:
            self._connected_route = None

    def _reset_other_listener_pages(self, active_route: str | None) -> None:
        """非当前线路且非抢占报错态的页面恢复为空闲。"""
        for r, page in self._web_pages.items():
            if r != active_route and page._btn_state != _Btn.ERROR:
                page.set_conn_state(_Btn.IDLE)
                page._was_connecting = False
                page._preempted = False
        if (
            self._route3_page
            and active_route != "3"
            and self._route3_page._btn_state != _Btn.ERROR
        ):
            self._route3_page.set_conn_state(_Btn.IDLE)
            self._route3_page._was_connecting = False
            self._route3_page._preempted = False
        if (
            self._route4_page
            and active_route != "4"
            and self._route4_page._btn_state != _Btn.ERROR
        ):
            self._route4_page.set_conn_state(_Btn.IDLE)
            self._route4_page._was_connecting = False
            self._route4_page._preempted = False

    def is_live_connected(self) -> bool:
        if self.get_active_listener_route():
            return True
        if self._connected_route:
            return True
        return False

    def show_picker(self):
        self._stack.setCurrentIndex(self._IDX_PICKER)
        self._picker.refresh_theme()

    def _enter_route(self, route: str):
        _cfg.set("route", route)
        self._stack.setCurrentIndex(self._IDX_ROUTE[route])
        if route in self._web_pages:
            self._web_pages[route].refresh_login()
        elif route == "3" and self._route3_page:
            from listener.listener4 import sync_companion_dir_from_registry
            sync_companion_dir_from_registry()
            if not self.is_live_connected():
                from listener.listener3 import run_page_check
                run_page_check()
            self._route3_page.refresh_status()
        elif route == "4" and self._route4_page:
            from listener.listener4 import sync_companion_dir_from_registry
            sync_companion_dir_from_registry()
            if not self.is_live_connected():
                from listener.listener4 import run_page_check
                run_page_check()
            self._route4_page.refresh_status()

    def _on_route_picked(self, route: str):
        self._enter_route(route)

    def on_status_change(self, connected: bool):
        if connected:
            self._connected_route = _cfg.get("route")
        route = self._connected_route
        if route and route in self._web_pages:
            if connected:
                self._web_pages[route].on_status_change(True)
            else:
                self._web_pages[route].on_status_change(False)
                self._connected_route = None
        elif route == "3" and self._route3_page:
            if connected:
                self._route3_page.on_status_change(True)
            else:
                self._route3_page.on_status_change(False)
                self._connected_route = None
        elif route == "4" and self._route4_page:
            if connected:
                self._route4_page.on_status_change(True)
            else:
                self._route4_page.on_status_change(False)
                self._connected_route = None
        self._reset_other_listener_pages(route if connected else None)

    def _build(self):
        self._toast = _Toast(self)

        self._stack = QStackedWidget()
        self._picker = _RoutePickerPage(self._on_route_picked)
        self._stack.addWidget(self._picker)  # 0

        self._web_pages: dict[str, _WebRoutePage] = {}
        for route in ("1", "2"):
            page = _WebRoutePage(route, self)
            self._web_pages[route] = page
            self._stack.addWidget(page)

        self._route3_page = _Route3Page(self)
        self._route4_page = _Route4Page(self)
        self._stack.addWidget(self._route3_page)
        self._stack.addWidget(self._route4_page)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._stack)

        self._refresh_theme()
        self.show_picker()

    def _refresh_theme(self):
        self._picker.refresh_theme()
        for page in self._web_pages.values():
            page.refresh_theme()
        if self._route3_page:
            self._route3_page.refresh_theme()
        if self._route4_page:
            self._route4_page.refresh_theme()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_toast"):
            self._toast._reposition()
