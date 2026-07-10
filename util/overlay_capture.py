"""悬浮窗窗口采集：为 Windows 保留 Alpha 通道（本机渲染不变）。

直播伴侣 10.5+ / OBS 等在窗口采集里勾选「允许窗口透明」时，
需要窗口表面带 Alpha。Qt 在 Windows 上默认可能没有，导致采集变黑。

参考：
- OBS win-capture allow_transparency + WGC
- Qt Forum: QOpenGLWidget 引导后 deleteLater 可保留 Alpha 通道
- 抖音开放平台「自定义透明画布」：伴侣勾选「允许窗口透明」
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QWidget

_app_prepared = False


def prepare_app_alpha_format() -> None:
    """在 QApplication 创建前调用一次。"""
    global _app_prepared
    if _app_prepared:
        return
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    _app_prepared = True


class _AlphaBootstrap(QObject):
    """窗口首次显示时注入 1×1 OpenGL 子控件再销毁，引导 Alpha 通道。"""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        self._done = False
        window.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._window and event.type() == QEvent.Show and not self._done:
            self._done = True
            QTimer.singleShot(0, self._bootstrap)
        return False

    def _bootstrap(self):
        if sys.platform != "win32":
            return
        try:
            from PySide6.QtOpenGLWidgets import QOpenGLWidget
        except ImportError:
            return
        w = self._window
        if not w.isVisible():
            return
        gl = QOpenGLWidget(w)
        gl.setFixedSize(1, 1)
        gl.setAttribute(Qt.WA_TransparentForMouseEvents)
        gl.move(-10, -10)
        gl.show()
        QTimer.singleShot(0, gl.deleteLater)


def enable_capture_transparency(window: QWidget) -> None:
    """透明悬浮窗启用（不改变 WA_TranslucentBackground 渲染）。"""
    prepare_app_alpha_format()
    if sys.platform != "win32":
        return
    if getattr(window, "_capture_alpha_ready", False):
        return
    window._capture_alpha_ready = True
    window._capture_alpha_bootstrap = _AlphaBootstrap(window)
