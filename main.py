"""
main.py — 应用入口：QApplication + ListenerThread + 消息 Hub
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browsers")
import asyncio

from PySide6.QtWidgets import QApplication
from PySide6.QtCore    import QThread, Signal, QObject, QtMsgType, qInstallMessageHandler, Qt, QTimer


def _qt_msg_handler(msg_type, _, msg):  # _ = QMessageLogContext (required by Qt API)
    """过滤 DirectWrite/Fixedsys 无害警告，其余正常输出。"""
    if "Fixedsys" in msg or "CreateFontFaceFromHDC" in msg:
        return
    if msg_type in (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg):
        print(msg)
    elif msg_type == QtMsgType.QtWarningMsg:
        print(f"Qt Warning: {msg}", file=sys.stderr)
    else:
        print(f"Qt: {msg}", file=sys.stderr)


qInstallMessageHandler(_qt_msg_handler)

from pages.main_page import MainPage
import pages

logger = __import__("logging").getLogger(__name__)


class ListenerThread(QThread):
    message_received = Signal(object)
    status_changed   = Signal(bool)

    def __init__(self, live_id: str,
                 route: str      = "2",
                 state_file: str = "state.json",
                 headless: bool  = True,
                 debug: bool     = False):
        super().__init__()
        self._live_id    = live_id
        self._route      = route        # "1" = listener1，"2" = listener2
        self._state_file = state_file
        self._headless   = headless
        self._debug      = debug
        self._loop: asyncio.AbstractEventLoop | None = None

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._listen())
        except RuntimeError as e:
            if "Event loop stopped before Future completed" not in str(e):
                logger.error(f"ListenerThread error: {e}")
        except Exception as e:
            logger.error(f"ListenerThread error: {e}")
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            self._loop.close()

    async def _listen(self):
        if self._route == "4":
            from listener.listener4 import start_listener
            await start_listener(
                callback  = lambda msg: self.message_received.emit(msg),
                on_status = lambda c: self.status_changed.emit(c),
            )
            return
        if self._route == "3":
            from listener.listener3 import start_listener
            await start_listener(
                callback  = lambda msg: self.message_received.emit(msg),
                on_status = lambda c: self.status_changed.emit(c),
            )
            return
        if self._route == "1":
            from listener.listener1 import _run
        else:
            from listener.listener2 import _run
        await _run(
            live_id    = self._live_id,
            callback   = lambda msg: self.message_received.emit(msg),
            state_file = self._state_file,
            headless   = self._headless,
            debug      = self._debug,
            on_status  = lambda c: self.status_changed.emit(c),
        )

    def stop(self):
        """发出停止信号，不阻塞主线程。"""
        if self._loop and not self._loop.is_closed():
            if self._route == "3":
                import listener.listener3 as l3
                try:
                    fut = asyncio.run_coroutine_threadsafe(l3.shutdown(), self._loop)
                    fut.result(timeout=8)
                except Exception:
                    pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        self.quit()


class App(QObject):
    message_received = Signal(object)
    status_changed   = Signal(bool)

    def __init__(self, argv: list):
        super().__init__()
        self._qt     = QApplication(argv)
        self._thread: ListenerThread | None = None
        self._pending_connect: tuple[str, str] | None = None  # (live_id, route)
        self._stopping = False
        self._win = MainPage()

        self.message_received.connect(self._win.broadcast_message)
        self.status_changed.connect(self._win.broadcast_status)

        from pages.home_page import HomePage
        home = self._win.get_page(HomePage)
        if home:
            home.set_callbacks(
                on_connect    = self.connect,
                on_disconnect = self.disconnect,
            )

    def connect(self, live_id: str, route: str = "2"):
        """先结束当前 listener，待线程完全退出后再启动目标线路。"""
        from pages.home_page import HomePage
        home = self._win.get_page(HomePage)
        if home:
            home.preempt_other_listeners(route)

        self._pending_connect = (live_id, route)
        if self._thread and not self._thread.isRunning():
            self._thread = None
        if self._thread and self._thread.isRunning():
            if not self._stopping:
                self._stopping = True
                self._stop_listener(wait_callback=True)
            return
        self._start_listener()

    def _start_listener(self) -> None:
        pending = self._pending_connect
        if not pending:
            return
        live_id, route = pending
        self._pending_connect = None

        self._thread = ListenerThread(live_id, route=route)
        self._thread.message_received.connect(self.message_received)
        self._thread.status_changed.connect(self.status_changed)
        self._thread.start()

    def _stop_listener(self, *, wait_callback: bool) -> None:
        thread = self._thread
        if not thread:
            return
        self._thread = None
        if wait_callback:
            thread.finished.connect(self._on_listener_stopped, Qt.ConnectionType.SingleShotConnection)
        else:
            thread.finished.connect(thread.deleteLater)
        thread.stop()

    def _on_listener_stopped(self) -> None:
        self._stopping = False
        thread = self.sender()
        if isinstance(thread, QThread):
            thread.deleteLater()
        if self._pending_connect:
            self._start_listener()

    def disconnect(self):
        """停止当前 ListenerThread；若正在切换线路则取消待启动目标。"""
        self._pending_connect = None
        if not self._thread or not self._thread.isRunning():
            return
        if not self._stopping:
            self._stopping = True
            self._stop_listener(wait_callback=True)

    def disconnect_and_wait(self, timeout_ms: int = 5000) -> None:
        """退出前同步等待 listener 结束（仅用于应用关闭）。"""
        self._pending_connect = None
        if not self._thread:
            return
        if self._thread.isRunning():
            if not self._stopping:
                self._stopping = True
                self._stop_listener(wait_callback=False)
            self._thread.wait(timeout_ms)
        self._thread = None
        self._stopping = False

    def run(self) -> int:
        self._win.show()
        result = self._qt.exec()
        self.disconnect_and_wait()
        return result


def _ensure_admin() -> None:
    """以管理员身份重新启动，若已是管理员则直接返回。"""
    import ctypes
    if ctypes.windll.shell32.IsUserAnAdmin():
        return
    args = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 1)
    sys.exit(0)


if __name__ == "__main__":
    _ensure_admin()

    def _defer_save_location():
        try:
            from listener.listener4 import save_location
            save_location()
        except Exception:
            pass

    app = App(sys.argv)
    QTimer.singleShot(0, _defer_save_location)
    sys.exit(app.run())