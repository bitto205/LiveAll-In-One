"""主页子模块共享：Toast、连接状态、伴侣路径、布局辅助。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QWidget, QScrollArea, QFileDialog,
)
from PySide6.QtCore import Qt, QTimer

import theme as _theme
import config as _cfg


class Toast(QLabel):
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


class Btn:
    IDLE       = "idle"
    CONNECTING = "connecting"
    CONNECTED  = "connected"
    ERROR      = "error"


def preempt_msg(by_route: str) -> str:
    return f"listener{by_route}停止连接直播间"


def apply_listener_preempt(page, by_route: str, home) -> None:
    msg = preempt_msg(by_route)
    page._preempted = True
    page._was_connecting = False
    page.set_conn_state(Btn.ERROR)
    page._conn_label.setText(f"⚠️  {msg}")
    page._conn_label.setStyleSheet(_theme.qss_error_label())
    page._conn_label.setVisible(True)
    home.toast.show_msg(msg, error=True)


def step_card(num: int, title: str) -> tuple[QFrame, QVBoxLayout]:
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


def scroll_page(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
    scroll.setWidget(content)
    return scroll


def pick_companion_dir(home) -> None:
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


def refresh_companion_path_btn(btn: QPushButton, in_registry: bool) -> None:
    if in_registry:
        btn.hide()
        return
    btn.show()
    from listener.listener4 import get_manual_companion_dir
    btn.setText("更换路径" if get_manual_companion_dir() else "指定路径")
    btn.setStyleSheet(_theme.qss_outlined(h=36))


ROUTE_META = {
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
        apply_listener_preempt(self, by_route, self._home)

    def on_status_change(self, connected: bool, *, success_toast: str = "直播间连接成功 🎉"):
        if connected:
            self._preempted = False
            self.set_conn_state(Btn.CONNECTED)
            self._home.toast.show_msg(success_toast)
        else:
            if self._preempted:
                self._preempted = False
                return
            if self._was_connecting:
                self.set_conn_state(Btn.ERROR)
                self._conn_label.setText("⚠️  直播间已断开或没有连接")
                self._conn_label.setStyleSheet(_theme.qss_error_label())
                self._conn_label.setVisible(True)
                self._home.toast.show_msg("直播间已断开", error=True)
            self._was_connecting = False

    def disconnect_listener(self):
        self._home._connected_route = None
        if self._home._disconnect_cb:
            self._home._disconnect_cb()
