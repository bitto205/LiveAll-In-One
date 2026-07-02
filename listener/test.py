"""
listener/test.py — 本地 UI 测试：绕过真实 listener，注入模拟弹幕。

用法: python listener/test.py

模拟环境：
  - 默认「已连接」
  - 线路 3/4：虚拟 patch / 无系统代理 / proxy_shell 视为运行中
"""
import sys
import os
import random

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PySide6.QtCore import QTimer, QObject

from listener.models import (
    ChatMessage, GiftMessage, LikeMessage, FollowMessage,
    EnterMessage, OnlineMessage, ControlMessage, FansclubMessage,
)
import main as _main


class _VirtualEnv:
    """测试用虚拟环境开关。"""
    route3_index_modified = False   # False = 未 patch，线路 3 可用
    route4_patched = True         # True = 已 patch，线路 4 可连
    system_proxy = False
    proxy_shell_running = True


_V = _VirtualEnv()


def _install_test_mocks() -> None:
    import listener.listener3 as l3
    import listener.listener4 as l4

    def _r3_status():
        from listener.listener4 import get_companion_path_fields
        return {
            **get_companion_path_fields(),
            "index_modified": _V.route3_index_modified,
            "system_proxy": _V.system_proxy,
        }

    l3.get_page_status = _r3_status
    l3._page_proxy_snapshot = False

    def _mock_run_page_check_r3():
        l3._page_proxy_snapshot = _V.system_proxy
        return _r3_status()

    l3.run_page_check = _mock_run_page_check_r3

    l4.is_patched = lambda: _V.route4_patched
    l4.is_index_js_modified = lambda: _V.route3_index_modified
    l4.is_proxy_shell_running = lambda: _V.proxy_shell_running
    l4._is_proxy_running = lambda: _V.proxy_shell_running

    _orig_build = l4._build_page_status

    def _page_status():
        s = _orig_build()
        s["is_patched"] = _V.route4_patched
        s["patch_needed"] = not _V.route4_patched
        s["index_modified"] = _V.route3_index_modified
        return s

    l4.get_page_status = _page_status
    l4.run_page_check = lambda: _page_status()


_NICK_PARTS = [
    ["小", "大", "超级", "可爱的", "暴躁的", "神秘的"],
    ["猫咪", "老虎", "咸鱼王", "柠檬精", "夜猫子", "沙雕"],
]

_CHAT_POOL = [
    "主播加油！", "666666", "来了来了", "哈哈哈哈哈",
    "", "   ", "x" * 50,
]


def _rand_nick() -> str:
    return (random.choice(_NICK_PARTS[0]) + random.choice(_NICK_PARTS[1]))[:15]


def _rand_uid() -> str:
    return str(random.randint(1000000, 9999999))


def _rand_chat() -> ChatMessage:
    base = random.choice(_CHAT_POOL)
    return ChatMessage(user=_rand_nick(), user_id=_rand_uid(), content=(base or "空弹幕")[:30])


def _rand_like() -> LikeMessage:
    return LikeMessage(user=_rand_nick(), user_id=_rand_uid(), count=random.randint(1, 100))


def _rand_follow() -> FollowMessage:
    return FollowMessage(user=_rand_nick(), user_id=_rand_uid())


def _rand_enter() -> EnterMessage:
    return EnterMessage(user=_rand_nick(), user_id=_rand_uid())


def _rand_online() -> OnlineMessage:
    return OnlineMessage(current=random.randint(1, 5000), total=random.randint(1000, 99999))


def _rand_control_end() -> ControlMessage:
    return ControlMessage(status=3)


def _rand_fansclub() -> FansclubMessage:
    return FansclubMessage(user=_rand_nick(), user_id=_rand_uid(), content="加入粉丝团")


def _load_gifts():
    try:
        from gift.gift_info import all_gifts
        return [(n, i["gift_id"]) for n, i in all_gifts().items()]
    except Exception:
        return [("小心心", 463), ("玫瑰", 2001)]


_GIFTS = _load_gifts()


def _rand_gift() -> GiftMessage:
    name, gid = random.choice(_GIFTS)
    return GiftMessage(
        user=_rand_nick(), user_id=_rand_uid(),
        gift=name, gift_id=gid,
        count=random.randint(1, 10),
        repeat_end=1,
    )


def _rand_gift_mid_combo() -> GiftMessage:
    """连击中间帧（repeat_end=0），多数 UI 应忽略。"""
    name, gid = random.choice(_GIFTS)
    return GiftMessage(
        user=_rand_nick(), user_id=_rand_uid(),
        gift=name, gift_id=gid, count=1, repeat_end=0,
    )


class Simulator(QObject):
    _MAKERS = (
        [_rand_chat] * 4
        + [_rand_like] * 2
        + [_rand_follow] * 1
        + [_rand_gift] * 2
        + [_rand_enter] * 1
        + [_rand_online] * 1
        + [_rand_fansclub] * 1
        + [_rand_gift_mid_combo] * 1
    )

    def __init__(self, app: "_main.App"):
        super().__init__()
        self._app = app
        app.status_changed.emit(True)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._schedule()

    def _schedule(self):
        self._timer.start(random.randint(500, 1800))

    def _tick(self):
        maker = random.choice(self._MAKERS)
        msg = maker()
        self._app.message_received.emit(msg)
        if random.random() < 0.02:
            self._app.message_received.emit(_rand_control_end())
        self._schedule()


if __name__ == "__main__":
    _install_test_mocks()
    app = _main.App(sys.argv)
    _sim = Simulator(app)
    sys.exit(app.run())
