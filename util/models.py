"""直播间消息数据类型（基于当前抖音 Webcast 实际样本）"""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ChatMessage:
    type:    Literal["chat"] = field(default="chat", init=False)
    user:    str = ""
    user_id: str = ""
    content: str = ""


@dataclass
class GiftMessage:
    type:       Literal["gift"] = field(default="gift", init=False)
    user:       str = ""
    user_id:    str = ""
    gift:       str = ""
    gift_id:    int = 0
    count:      int = 1
    repeat_end: int = -1   # 1=连击结束  0=中间帧  -1=未知


@dataclass
class LikeMessage:
    type:    Literal["like"] = field(default="like", init=False)
    user:    str = ""
    user_id: str = ""
    count:   int = 1


@dataclass
class EnterMessage:
    type:    Literal["enter"] = field(default="enter", init=False)
    user:    str = ""
    user_id: str = ""


@dataclass
class FollowMessage:
    type:    Literal["follow"] = field(default="follow", init=False)
    user:    str = ""
    user_id: str = ""
    action: int = 0
    share_type: int = 0
    share_target: str = ""
    follow_count: int = 0


@dataclass
class OnlineMessage:
    type:    Literal["online"] = field(default="online", init=False)
    current: int = 0
    total:   int = 0


@dataclass
class FansclubMessage:
    type:    Literal["fansclub"] = field(default="fansclub", init=False)
    user:    str = ""
    user_id: str = ""
    content: str = ""


@dataclass
class EmojiChatMessage:
    type:           Literal["emoji"] = field(default="emoji", init=False)
    user:           str = ""
    user_id:        str = ""
    emoji_id:       str = ""
    default_content: str = ""


@dataclass
class RoomStatsMessage:
    type:         Literal["room_stats"] = field(default="room_stats", init=False)
    display_long: str = ""
    display_short: str = ""
    display_middle: str = ""
    display_value: int = 0
    total: int = 0
    display_type: int = 0


@dataclass
class RoomRankMessage:
    type:   Literal["rank"] = field(default="rank", init=False)
    ranks:  list[Any] = field(default_factory=list)


@dataclass
class ControlMessage:
    type:   Literal["control"] = field(default="control", init=False)
    status: int = 0            # 3 = 直播已结束


@dataclass
class ChatLikeMessage:
    type: Literal["chat_like"] = field(default="chat_like", init=False)
    common: bytes = b""
    ext: bytes = b""


@dataclass
class GiftSortMessage:
    type: Literal["gift_sort"] = field(default="gift_sort", init=False)
    scene: int = 0
    sort_type: str = ""


@dataclass
class InRoomBannerMessage:
    type: Literal["in_room_banner"] = field(default="in_room_banner", init=False)
    banner_type: int = 0
    data: str = ""


@dataclass
class InteractEffectMessage:
    type: Literal["interact_effect"] = field(default="interact_effect", init=False)
    effect_id: int = 0
    value: str = ""
    value_type: str = ""
    width: int = 0
    height: int = 0
    duration: int = 0
    extra_json: str = ""


@dataclass
class RanklistHourEntranceMessage:
    type: Literal["ranklist_hour_entrance"] = field(default="ranklist_hour_entrance", init=False)
    detail: bytes = b""


@dataclass
class RoomCommentTopicMessage:
    type: Literal["room_comment_topic"] = field(default="room_comment_topic", init=False)
    icon_url: str = ""
    topic_type: int = 0
    ext: bytes = b""


@dataclass
class RoomMessage:
    type: Literal["room_message"] = field(default="room_message", init=False)
    text: str = ""
    source: str = ""
    room_data: bytes = b""
    ext: bytes = b""


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
    | ChatLikeMessage
    | GiftSortMessage
    | InRoomBannerMessage
    | InteractEffectMessage
    | RanklistHourEntranceMessage
    | RoomCommentTopicMessage
    | RoomMessage
)
