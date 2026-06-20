"""
main.py — 应用 Hub + 入口

职责：
    1. 启动 QApplication 和 MainPage
    2. 管理 ListenerThread（在独立线程跑 asyncio + Playwright）
    3. 作为数据中转：listener → App 信号 → 所有订阅者
       多个工具/页面只需 connect(app.message_received) 就能收到数据

启动：
    python main.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browsers")
import asyncio

from PySide6.QtWidgets import QApplication
from PySide6.QtCore    import QThread, Signal, QObject

from main_page import MainPage
import pages   # 触发所有 @register


# ─────────────────────────────────────────────
# ListenerThread — 在独立线程里跑 asyncio
# ─────────────────────────────────────────────
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
                import logging
                logging.getLogger(__name__).error(f"ListenerThread error: {e}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"ListenerThread error: {e}")
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
            self._loop.call_soon_threadsafe(self._loop.stop)
        self.quit()
        # 不调 wait()，否则阻塞 Qt 主线程导致 UI 卡住


# ─────────────────────────────────────────────
# App — 应用 Hub
# ─────────────────────────────────────────────
class App(QObject):
    """
    数据中转中心。

    所有对 listener 数据感兴趣的组件都连接这里的信号：
        app.message_received.connect(my_handler)
        app.status_changed.connect(my_status_handler)

    连接 / 断开直播间：
        app.connect("sanpan0.0")
        app.disconnect()
    """

    # ── 对外开放的信号（多个页面/工具可同时订阅）──
    message_received = Signal(object)
    status_changed   = Signal(bool)

    def __init__(self, argv: list):
        super().__init__()
        self._qt     = QApplication(argv)
        self._thread: ListenerThread | None = None

        # 创建主窗口
        self._win = MainPage()

        # 把 App 信号接到 MainPage 的广播方法（统一分发给所有已注册页面）
        self.message_received.connect(self._win.broadcast_message)
        self.status_changed.connect(self._win.broadcast_status)

        # 注入 connect/disconnect 到 HomePage
        from pages.home_page import HomePage
        home = self._win.get_page(HomePage)
        if home:
            home.set_callbacks(
                on_connect    = self.connect,
                on_disconnect = self.disconnect,
            )

    # ── 连接直播间 ────────────────────────────────
    def connect(self, live_id: str, route: str = "2"):
        """启动 ListenerThread，连接指定直播间。route='1' 用 listener1，'2' 用 listener2。"""
        self.disconnect()
        self._thread = ListenerThread(live_id, route=route)
        self._thread.message_received.connect(self.message_received)
        self._thread.status_changed.connect(self.status_changed)
        self._thread.start()

    # ── 断开直播间 ────────────────────────────────
    def disconnect(self):
        """停止当前 ListenerThread，不阻塞主线程。"""
        if self._thread:
            thread = self._thread
            self._thread = None
            # 线程结束后让 Qt 自动回收，主线程不等待
            thread.finished.connect(thread.deleteLater)
            thread.stop()

    # ── 运行 ──────────────────────────────────────
    def run(self) -> int:
        self._win.show()
        result = self._qt.exec()
        self.disconnect()   # 退出前清理
        return result


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────
if __name__ == "__main__":

    sys.exit(App(sys.argv).run())