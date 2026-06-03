"""
models.py
直播间消息数据类型定义
参考: saermart/DouyinLiveWebFetcher liveMan.py
"""

from dataclasses import dataclass, field
from typing import Literal


# ─────────────────────────────────────────────
# 弹幕
# ─────────────────────────────────────────────
@dataclass
class ChatMessage:
    type:    Literal["chat"] = field(default="chat", init=False)
    user:    str = ""        # 昵称
    user_id: str = ""        # 用户 ID
    content: str = ""        # 弹幕内容


# ─────────────────────────────────────────────
# 礼物
# ─────────────────────────────────────────────
@dataclass
class GiftMessage:
    type:    Literal["gift"] = field(default="gift", init=False)
    user:    str = ""        # 送礼用户昵称
    user_id: str = ""        # 送礼用户 ID
    gift:    str = ""        # 礼物名称
    gift_id: int = 0         # 礼物 ID
    count:   int = 1         # 连击数（combo_count）


# ─────────────────────────────────────────────
# 点赞
# ─────────────────────────────────────────────
@dataclass
class LikeMessage:
    type:    Literal["like"] = field(default="like", init=False)
    user:    str = ""        # 昵称
    user_id: str = ""        # 用户 ID
    count:   int = 1         # 点赞数量


# ─────────────────────────────────────────────
# 进入直播间
# ─────────────────────────────────────────────
@dataclass
class EnterMessage:
    type:    Literal["enter"] = field(default="enter", init=False)
    user:    str = ""        # 昵称
    user_id: str = ""        # 用户 ID


# ─────────────────────────────────────────────
# 关注
# ─────────────────────────────────────────────
@dataclass
class FollowMessage:
    type:    Literal["follow"] = field(default="follow", init=False)
    user:    str = ""        # 昵称
    user_id: str = ""        # 用户 ID


# ─────────────────────────────────────────────
# 在线人数统计
# ─────────────────────────────────────────────
@dataclass
class OnlineMessage:
    type:    Literal["online"] = field(default="online", init=False)
    current: int = 0         # 当前在线人数
    total:   int = 0         # 累计观看人数


# ─────────────────────────────────────────────
# 粉丝团消息
# ─────────────────────────────────────────────
@dataclass
class FansclubMessage:
    type:    Literal["fansclub"] = field(default="fansclub", init=False)
    user:    str = ""        # 昵称
    user_id: str = ""        # 用户 ID
    content: str = ""        # 粉丝团消息内容


# ─────────────────────────────────────────────
# 表情弹幕
# ─────────────────────────────────────────────
@dataclass
class EmojiChatMessage:
    type:           Literal["emoji"] = field(default="emoji", init=False)
    user:           str = ""   # 昵称
    user_id:        str = ""   # 用户 ID
    emoji_id:       str = ""   # 表情 ID
    default_content: str = ""  # 表情对应文字（如"哈哈哈"）


# ─────────────────────────────────────────────
# 直播间统计信息（display_long，如"1.2万人看过"）
# ─────────────────────────────────────────────
@dataclass
class RoomStatsMessage:
    type:         Literal["room_stats"] = field(default="room_stats", init=False)
    display_long: str = ""     # 展示文案


# ─────────────────────────────────────────────
# 直播间排行榜
# ─────────────────────────────────────────────
@dataclass
class RoomRankMessage:
    type:   Literal["rank"] = field(default="rank", init=False)
    ranks:  list = field(default_factory=list)  # 排行榜列表（原始 pb 对象）


# ─────────────────────────────────────────────
# 直播间控制（开播/下播）
# ─────────────────────────────────────────────
@dataclass
class ControlMessage:
    type:   Literal["control"] = field(default="control", init=False)
    status: int = 0            # 3 = 直播已结束


# ─────────────────────────────────────────────
# 联合类型（方便外部类型注解）
# ─────────────────────────────────────────────
LiveMessage = (
    ChatMessage
    | GiftMessage
    | LikeMessage
    | EnterMessage
    | FollowMessage
    | OnlineMessage
    | FansclubMessage
    | EmojiChatMessage
    | RoomStatsMessage
    | RoomRankMessage
    | ControlMessage
)
