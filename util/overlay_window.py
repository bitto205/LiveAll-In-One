"""透明悬浮窗共用行为：边框动画、软最小化、可被其他窗口遮挡。"""
from __future__ import annotations

import math
import os
import re

from PySide6.QtCore import QAbstractAnimation, QEvent, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QLabel, QPushButton, QVBoxLayout, QMessageBox, QWidget,
)

from util.paths import app_root

_TUTORIAL_BTN_W = 44


class OverlayResizeFreeze:
    """拖拽缩放期间冻结整窗绘制；松手后恢复并回调 on_resume。

    数据逻辑可继续跑；UI 更新应检查 frozen，把待渲染项排队，resume 时再刷。
    已在播的 QAbstractAnimation 会 pause，松手后 resume。
    """

    def __init__(self, host: QWidget, on_resume):
        self._host = host
        self._on_resume = on_resume
        self._live = False
        self._paused: list[QAbstractAnimation] = []

    @property
    def frozen(self) -> bool:
        return self._live

    @property
    def live(self) -> bool:
        return self._live

    def begin(self) -> None:
        if self._live:
            return
        self._live = True
        self._paused = []
        for anim in self._host.findChildren(QAbstractAnimation):
            if anim.state() == QAbstractAnimation.Running:
                anim.pause()
                self._paused.append(anim)
        self._host.setUpdatesEnabled(False)

    def end(self) -> None:
        if not self._live:
            return
        self._live = False
        for anim in self._paused:
            try:
                if anim.state() == QAbstractAnimation.Paused:
                    anim.resume()
            except RuntimeError:
                pass
        self._paused = []
        self._host.setUpdatesEnabled(True)
        try:
            self._on_resume()
        finally:
            self._host.update()

    def begin_live(self) -> None:
        self.begin()

    def end_live(self) -> None:
        self.end()

    def request(self) -> None:
        if not self._live:
            self._on_resume()


ResizeLayoutGate = OverlayResizeFreeze


def tutorial_image_path() -> str | None:
    root = str(app_root())
    folder = os.path.join(root, "image")
    preferred = os.path.join(folder, "cb057d2246fcf7e3d23b54f3476bc83.png")
    if os.path.isfile(preferred):
        return preferred
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")):
                return os.path.join(folder, name)
    return None


def show_tutorial_dialog(parent=None) -> None:
    path = tutorial_image_path()
    if not path:
        QMessageBox.warning(parent, "教程", "未找到 image 文件夹中的教程图片。")
        return
    pix = QPixmap(path)
    if pix.isNull():
        QMessageBox.warning(parent, "教程", f"无法加载教程图片：{path}")
        return

    dlg = QDialog(parent)
    dlg.setWindowTitle("教程")
    dlg.setModal(False)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(12, 12, 12, 12)
    lay.setSpacing(10)

    screen = dlg.screen().availableGeometry() if dlg.screen() else None
    if screen:
        max_w = min(int(screen.width() * 0.68), 760)
        max_h = min(int(screen.height() * 0.62), 520)
    else:
        max_w, max_h = 720, 480

    scaled = pix.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    img = QLabel()
    img.setAlignment(Qt.AlignCenter)
    img.setPixmap(scaled)
    img.setFixedSize(scaled.size())
    lay.addWidget(img, alignment=Qt.AlignCenter)

    close_btn = QPushButton("关闭")
    close_btn.clicked.connect(dlg.close)
    lay.addWidget(close_btn, alignment=Qt.AlignCenter)

    dlg.adjustSize()
    dlg.show()


def create_tutorial_button(root, btn_style: str, parent_win) -> QPushButton:
    btn = QPushButton("教程", root)
    style = re.sub(r"min-width:\s*\d+px", f"min-width: {_TUTORIAL_BTN_W}px", btn_style)
    style = re.sub(r"max-width:\s*\d+px", f"max-width: {_TUTORIAL_BTN_W}px", style)
    btn.setStyleSheet(style)
    btn.setCursor(Qt.ArrowCursor)
    btn.setToolTip("查看使用教程")
    btn.clicked.connect(lambda: show_tutorial_dialog(parent_win))
    btn.setVisible(False)
    return btn


def layout_tutorial_button(
    btn: QPushButton,
    *,
    r: float,
    cx: float,
    cy: float,
    topbar_h: int,
    circle_off: int,
    circle_d: int,
) -> None:
    left = circle_off + circle_d + 4
    btn.setGeometry(left, 0, _TUTORIAL_BTN_W, topbar_h)
    need_r = math.hypot(left + _TUTORIAL_BTN_W / 2 - cx, topbar_h / 2 - cy)
    btn.setVisible(r >= need_r)


class OverlayFrameMixin:
    _root: object
    _anim: QVariantAnimation
    _shown: bool
    _anim_r: float

    def _install_overlay_frame(self) -> None:
        self._overlay_minimized = False
        self._cancelling_os_minimize = False

    def set_border_shown(self, shown: bool, *, animate: bool = True) -> None:
        if shown == self._shown:
            return
        self._shown = shown
        self._anim.stop()
        target = self._root.max_radius() if shown else 0.0
        if animate:
            self._anim.setStartValue(self._anim_r)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._anim_r = target
            self._root.set_radius(target)

    def toggle_border(self) -> None:
        self.set_border_shown(not self._shown, animate=True)

    def _on_circle_toggle(self) -> None:
        if self._overlay_minimized:
            self.restore_from_minimize()
            return
        self.toggle_border()

    def minimize_overlay(self) -> None:
        self.set_border_shown(False, animate=True)
        self._overlay_minimized = True
        self.lower()

    def restore_from_minimize(self) -> None:
        if not self._overlay_minimized:
            return
        self._overlay_minimized = False
        self.set_border_shown(True, animate=True)

    def _cancel_os_minimize(self) -> None:
        if self._cancelling_os_minimize:
            return
        self._cancelling_os_minimize = True
        try:
            self.showNormal()
            self.set_border_shown(False, animate=False)
            self._overlay_minimized = True
            self.lower()
        finally:
            self._cancelling_os_minimize = False

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMinimized:
                QTimer.singleShot(0, self._cancel_os_minimize)
            elif self._overlay_minimized:
                QTimer.singleShot(0, self.restore_from_minimize)
        elif event.type() == QEvent.ActivationChange and self.isActiveWindow():
            QTimer.singleShot(0, self.restore_from_minimize)
        super().changeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._overlay_minimized:
            QTimer.singleShot(0, self.restore_from_minimize)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        QTimer.singleShot(0, self.restore_from_minimize)
