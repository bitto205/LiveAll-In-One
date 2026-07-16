"""LiveAIO 主程序（提权完成后加载）。"""
from __future__ import annotations

import asyncio
import sys

from PySide6.QtCore import QObject, QThread, QTimer, QtMsgType, Signal, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from util.overlay_capture import prepare_app_alpha_format
from util.log_util import ensure_console_logging, ensure_startup_log, get_tagged_logger
from util.paths import app_root, state_file as default_state_file

APP_NAME = "LiveAIO"
logger = get_tagged_logger("main", __name__)


def _qt_msg_handler(msg_type, _, msg):
    if "Fixedsys" in msg or "CreateFontFaceFromHDC" in msg:
        return
    if msg_type in (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg):
        print(msg)
    elif msg_type == QtMsgType.QtWarningMsg:
        print(f"Qt Warning: {msg}", file=sys.stderr)
    else:
        print(f"Qt: {msg}", file=sys.stderr)


def _set_process_name(name: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        SetProcessDescription = ctypes.windll.kernel32.SetProcessDescription
        SetProcessDescription.argtypes = (wintypes.HANDLE, wintypes.LPCWSTR)
        SetProcessDescription.restype = wintypes.HRESULT
        SetProcessDescription(
            ctypes.windll.kernel32.GetCurrentProcess(), name,
        )
    except Exception:
        pass


class ListenerThread(QThread):
    message_received = Signal(object)
    status_changed = Signal(bool)

    def __init__(
        self,
        live_id: str,
        route: str = "2",
        state_file: str = "",
        headless: bool = True,
        debug: bool = False,
        force_system: bool = False,
    ):
        super().__init__()
        self._live_id = live_id
        self._route = route
        self._state_file = state_file or str(default_state_file())
        self._headless = headless
        self._debug = debug
        self._force_system = force_system
        self._loop: asyncio.AbstractEventLoop | None = None

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            ensure_console_logging()
            logger.info("Listener 线程启动，线路=%s", self._route)
            self._loop.run_until_complete(self._listen())
        except RuntimeError as e:
            if "Event loop stopped before Future completed" not in str(e):
                logger.error("Listener 线程异常: %s", e)
        except Exception as e:
            logger.error("Listener 线程异常: %s", e)
        finally:
            if self._route == "3":
                try:
                    from listener.listener3 import _teardown_local_redirector
                    if self._loop and not self._loop.is_closed():
                        self._loop.run_until_complete(_teardown_local_redirector())
                except Exception:
                    pass
            try:
                if self._loop and not self._loop.is_closed():
                    pending = asyncio.all_tasks(self._loop)
                    for t in pending:
                        t.cancel()
                    if pending:
                        self._loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
            except Exception:
                pass
            try:
                if self._loop and not self._loop.is_closed():
                    self._loop.close()
            except Exception:
                pass

    async def _listen(self):
        if self._route == "4":
            from listener.listener4 import start_listener
            await start_listener(
                callback=lambda msg: self.message_received.emit(msg),
                on_status=lambda c: self.status_changed.emit(c),
            )
            return
        if self._route == "3":
            from listener.listener3 import start_listener
            await start_listener(
                callback=lambda msg: self.message_received.emit(msg),
                on_status=lambda c: self.status_changed.emit(c),
            )
            return
        if self._route == "1":
            from listener.listener1 import _run
        else:
            from listener.listener2 import _run
        await _run(
            live_id=self._live_id,
            callback=lambda msg: self.message_received.emit(msg),
            state_file=self._state_file,
            headless=self._headless,
            debug=self._debug,
            on_status=lambda c: self.status_changed.emit(c),
            force_system=self._force_system,
        )

    def stop(self):
        if self._route == "4":
            try:
                from listener.listener4 import request_listener_stop
                if request_listener_stop():
                    return
            except Exception:
                pass
        if self._loop and not self._loop.is_closed():
            if self._route == "3":
                import listener.listener3 as l3
                try:
                    fut = asyncio.run_coroutine_threadsafe(l3.shutdown(), self._loop)
                    fut.result(timeout=8)
                except Exception:
                    pass
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)


class App(QObject):
    message_received = Signal(object)
    status_changed = Signal(bool)

    def __init__(self, argv: list):
        super().__init__()
        self._qt = QApplication(argv)
        self._qt.setApplicationName(APP_NAME)
        self._qt.setApplicationDisplayName(APP_NAME)
        self._qt.setOrganizationName(APP_NAME)
        import config as _cfg
        self._qt.setQuitOnLastWindowClosed(not _cfg.get("minimize_to_tray", True))
        _set_process_name(APP_NAME)
        self._thread: ListenerThread | None = None
        self._pending_connect: tuple[str, str] | None = None
        self._stopping = False

        from pages.main_page import MainPage
        self._win = MainPage()

        self.message_received.connect(self._win.broadcast_message)
        self.status_changed.connect(self._win.broadcast_status)

        from pages.home_page import HomePage
        home = self._win.get_page(HomePage)
        if home:
            home.set_callbacks(
                on_connect=self.connect,
                on_disconnect=self.stop_listener,
            )

    def connect(self, live_id: str, route: str = "2"):
        from pages.home_page import HomePage
        home = self._win.get_page(HomePage)
        if home:
            home.preempt_other_listeners(route)

        self._pending_connect = (live_id, route)
        logger.info("请求连接 listener，线路=%s", route)
        if self._thread and not self._thread.isRunning():
            self._thread = None
        if self._thread and self._thread.isRunning():
            if home:
                home.begin_listener_switch(route)
            if not self._stopping:
                self._stopping = True
                self._stop_listener()
            return
        if home:
            home.end_listener_switch()
        self._start_listener()

    def _start_listener(self) -> None:
        pending = self._pending_connect
        if not pending:
            return
        live_id, route = pending
        self._pending_connect = None

        import config as _cfg
        force_system = bool(_cfg.get("use_system_browser", False))
        self._thread = ListenerThread(live_id, route=route, force_system=force_system)
        self._thread.message_received.connect(self.message_received)
        self._thread.status_changed.connect(self.status_changed)
        self._thread.finished.connect(self._on_listener_finished)
        self._thread.start()

    def _on_listener_finished(self) -> None:
        thread = self.sender()
        if not isinstance(thread, QThread):
            return
        pending = self._pending_connect
        if self._thread is thread:
            self._thread = None
        self._stopping = False
        if thread.isRunning():
            thread.wait(3000)
        thread.deleteLater()
        from pages.home_page import HomePage
        home = self._win.get_page(HomePage)
        if home:
            home.end_listener_switch()
            if not pending:
                home.clear_listener_state()
        if pending:
            self._start_listener()

    def _stop_listener(self) -> None:
        thread = self._thread
        if not thread or not thread.isRunning():
            return
        self._stopping = True
        thread.stop()

    def stop_listener(self, *, switching: bool = False) -> None:
        if not switching:
            self._pending_connect = None
        if self._stopping:
            return
        logger.info("请求断开 listener%s", "（切换线路）" if switching else "")
        self._stop_listener()

    def disconnect(self, *, switching: bool = False) -> None:
        self.stop_listener(switching=switching)

    def disconnect_and_wait(self, timeout_ms: int = 5000) -> None:
        self._pending_connect = None
        thread = self._thread
        if not thread:
            return
        if thread.isRunning():
            self._stopping = True
            self._thread = None
            thread.stop()
            thread.wait(timeout_ms)
        self._thread = None
        self._stopping = False

    def run(self) -> int:
        self._win.show()
        result = self._qt.exec()
        self.disconnect_and_wait()
        return result


def _warmup_background() -> None:
    from util.playwright_bootstrap import configure_playwright_browsers, log_browser_mode
    configure_playwright_browsers(app_root())
    log_browser_mode()


def _defer_save_location() -> None:
    try:
        from listener.listener4 import save_location
        save_location()
    except Exception:
        pass


def run_app() -> int:
    ensure_startup_log()
    prepare_app_alpha_format()
    qInstallMessageHandler(_qt_msg_handler)
    logger.info("%s 启动", APP_NAME)

    from util.browser_trim import trim_playwright_browsers
    trim_playwright_browsers(app_root() / "browsers")

    app = App(sys.argv)
    QTimer.singleShot(0, _warmup_background)
    QTimer.singleShot(0, _defer_save_location)
    return app.run()
