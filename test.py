"""
test.py — 弹幕机本地测试模拟器

启动完整的主程序 UI，绕过真实监听器，直接向消息总线注入模拟直播间数据。
支持：ChatMessage / GiftMessage / LikeMessage / FollowMessage

用法:
    python test.py
"""
import sys, os, random, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QTimer, QObject

from models import ChatMessage, GiftMessage, LikeMessage, FollowMessage
import main as _main


# ─────────────────────────────────────────────
# 素材池
# ─────────────────────────────────────────────
_NICK_PARTS = [
    # 前缀
    ["小", "大", "超级", "可爱的", "暴躁的", "神秘的", "快乐的",
     "摸鱼的", "努力的", "佛系", "豪横的", "迷路的"],
    # 主体
    ["猫咪", "老虎", "咸鱼王", "柠檬精", "夜猫子", "沙雕", "星际旅客",
     "小丸子", "天才", "吃货", "萌新", "大佬", "路人甲", "吃瓜群众",
     "小可爱", "炸弹侠"],
]

_CHAT_POOL = [
    "主播加油！", "好好看哦", "哈哈哈哈哈", "666666",
    "来了来了", "打卡打卡", "冲冲冲！", "主播唱首歌吧",
    "今天也很开心", "爱你呀", "求带带", "第一次来",
    "支持主播", "哇好厉害", "笑死我了", "太可爱了吧",
    "看了很久了", "冲鸭！", "主播辛苦了", "牛啊牛啊",
    "好的好的", "一直在这里", "天天来", "关注了！",
    "我超喜欢这个", "真的厉害", "哈哈哈哈太好笑了",
]


def _rand_nick() -> str:
    """随机昵称，≤15 字符。"""
    prefix = random.choice(_NICK_PARTS[0])
    body   = random.choice(_NICK_PARTS[1])
    nick   = prefix + body
    return nick[:15]


def _rand_uid() -> str:
    """7 位随机用户 ID。"""
    return str(random.randint(1000000, 9999999))


def _rand_chat() -> ChatMessage:
    base = random.choice(_CHAT_POOL)
    # 随机拼接补充，控制在 30 字以内
    suffix = random.choice(["", "！", "哈哈", "～", "呀", " 666"])
    text = (base + suffix)[:30]
    return ChatMessage(user=_rand_nick(), user_id=_rand_uid(), content=text)


def _rand_like() -> LikeMessage:
    return LikeMessage(
        user    = _rand_nick(),
        user_id = _rand_uid(),
        count   = random.randint(1, 100),
    )


def _rand_follow() -> FollowMessage:
    return FollowMessage(user=_rand_nick(), user_id=_rand_uid())


# ── 礼物表 ───────────────────────────────────
def _load_gifts() -> list[tuple[str, int]]:
    """从 gift/gift_info.json 读取礼物列表，返回 [(礼物名, gift_id), ...]。"""
    try:
        from gift.gift_info import all_gifts
        return [(name, info["gift_id"]) for name, info in all_gifts().items()]
    except Exception:
        return [("爱心", 463), ("玫瑰", 2001), ("嘉年华", 3)]


_GIFTS = _load_gifts()


def _rand_gift() -> GiftMessage:
    gift_name, gift_id = random.choice(_GIFTS)
    return GiftMessage(
        user       = _rand_nick(),
        user_id    = _rand_uid(),
        gift       = gift_name,
        gift_id    = gift_id,
        count      = random.randint(1, 100),
        repeat_end = 1,     # 标记为"连击结束帧"，让处理逻辑正常触发
    )


# ─────────────────────────────────────────────
# 模拟器
# ─────────────────────────────────────────────
class Simulator(QObject):
    """
    向 App.message_received 定时注入随机消息。
    权重：弹幕 50% / 点赞 30% / 关注 10% / 礼物 10%
    间隔：600~1400ms 随机，模拟真实波动。
    """

    _MAKERS = (
        [_rand_chat]   * 5 +
        [_rand_like]   * 3 +
        [_rand_follow] * 1 +
        [_rand_gift]   * 1
    )

    def __init__(self, app: "_main.App"):
        super().__init__()
        self._app = app

        # 通知 UI 进入"已连接"状态
        app.status_changed.emit(True)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._schedule()

    def _schedule(self):
        self._timer.start(random.randint(600, 1400))

    def _tick(self):
        msg = random.choice(self._MAKERS)()
        self._app.message_received.emit(msg)
        self._schedule()


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app  = _main.App(sys.argv)
    _sim = Simulator(app)       # 持有引用，防止被 GC
    sys.exit(app.run())
