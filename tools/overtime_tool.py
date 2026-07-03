"""
tools/overtime_tool.py — 加班机（配置 + 设置 UI + 悬浮窗 + 控制面板）
"""
from __future__ import annotations

import sys, os, math, copy, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Callable

import config as _cfg
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QSizePolicy,
    QScrollArea, QStackedWidget, QLineEdit, QTextEdit,
)
from PySide6.QtCore import (
    Qt, QPoint, QRect, Signal,
    QVariantAnimation, QEasingCurve, QTimer,
)
from PySide6.QtGui import (
    QPainter, QColor, QPainterPath, QPen, QFont, QFontMetrics, QPixmap,
)

import pages.theme as _theme
from pages.widgets import ThemedComboBox
from tools import register_tool
from tools.tool_common import ToolSingleton, gift_names_cached, load_gift_pixmap

# ═══════════════════════════════════════════
# 配置 / 规则
# ═══════════════════════════════════════════

CFG_KEY = "overtime.settings"

UNITS = ("时", "分", "秒")
MODES = ("加", "减", "随机")
ALIGNS = ("居左", "居中", "居右")

_DEFAULT_RULE = {
    "gift": "小心心",
    "mode": "加",
    "value": 1,
    "unit": "分",
    "random_neg": 0,
    "random_pos": 1,
    "random_unit": "分",
}

# 默认礼物与规则模板（仅本文件；config 无 overtime.settings 时由 load_settings 回落到此）
DEFAULT_GIFTS = ("小心心", "玫瑰", "大啤酒", "鲜花", "热气球", "嘉年华")
SIM_DEFAULT_GIFT = "小心心"
SIM_USER = "LiveAIO"
SIM_USER_ID = "LiveAIO"
RULE_COUNT = 6


def make_sim_gift_message(gift: str, count: int):
    """构造模拟 GiftMessage（字段与 listener.models.GiftMessage 一致）。"""
    from listener.models import GiftMessage
    from gift.gift_info import get_gift_id

    name = (gift or SIM_DEFAULT_GIFT).strip() or SIM_DEFAULT_GIFT
    n = _clamp_int(count, 0, 9999)
    gid = get_gift_id(name) or 0
    return GiftMessage(
        user=SIM_USER,
        user_id=SIM_USER_ID,
        gift=name,
        gift_id=int(gid),
        count=n,
        repeat_end=1,
    )


def _default_rules() -> list[dict]:
    return [
        copy.deepcopy({**_DEFAULT_RULE, "gift": g})
        for g in DEFAULT_GIFTS
    ]


DEFAULT_SETTINGS: dict = {
    "hours": 2,
    "minutes": 0,
    "seconds": 0,
    "rules": _default_rules(),
    "custom_text": "可以输入自定义的文字",
    "custom_align": "居中",
}



def load_settings() -> dict:
    raw = _cfg.get(CFG_KEY)
    if not isinstance(raw, dict):
        return copy.deepcopy(DEFAULT_SETTINGS)
    out = copy.deepcopy(DEFAULT_SETTINGS)
    out["hours"] = _clamp_int(raw.get("hours"), 0, 999)
    out["minutes"] = _clamp_int(raw.get("minutes"), 0, 60)
    out["seconds"] = _clamp_int(raw.get("seconds"), 0, 60)
    if isinstance(raw.get("custom_text"), str):
        out["custom_text"] = raw["custom_text"][:80]
    align = raw.get("custom_align")
    if align in ALIGNS:
        out["custom_align"] = align
    rules = raw.get("rules")
    if isinstance(rules, list):
        merged: list[dict] = []
        for i in range(RULE_COUNT):
            if i < len(rules) and isinstance(rules[i], dict):
                merged.append(normalize_rule(rules[i]))
            else:
                merged.append(copy.deepcopy(_default_rules()[i]))
        out["rules"] = dedupe_rules(merged)
    return out


def dedupe_rules(rules: list[dict], *, prefer_index: int | None = None) -> list[dict]:
    """保证 6 条规则礼物互不相同；冲突时自动换成未占用的礼物。

    prefer_index: 优先保留该格的礼物，其它重复格自动换礼物。
    """
    items: list[dict] = []
    for i in range(RULE_COUNT):
        raw = rules[i] if i < len(rules) and isinstance(rules[i], dict) else {}
        items.append(normalize_rule(raw if raw else _default_rules()[i]))

    used: set[str] = set()
    out = [copy.deepcopy(r) for r in items]
    order = list(range(RULE_COUNT))
    if prefer_index is not None and 0 <= prefer_index < RULE_COUNT:
        order.remove(prefer_index)
        order.insert(0, prefer_index)

    for i in order:
        gift = out[i].get("gift", "").strip()
        if not gift or gift in used:
            gift = _next_unused_gift(used, prefer_index=i)
        out[i]["gift"] = gift
        used.add(gift)
    return out


def _next_unused_gift(used: set[str], *, prefer_index: int) -> str:
    pool = list(DEFAULT_GIFTS)
    if 0 <= prefer_index < len(pool) and pool[prefer_index] not in used:
        return pool[prefer_index]
    for g in pool:
        if g not in used:
            return g
    try:
        from gift.gift_info import all_gifts
        for g in sorted(all_gifts().keys()):
            if g not in used:
                return g
    except Exception:
        pass
    n = 1
    while True:
        fallback = f"礼物{n}"
        if fallback not in used:
            return fallback
        n += 1


def save_settings(data: dict) -> None:
    _cfg.set(CFG_KEY, copy.deepcopy(data))


def _clamp_int(v, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = lo
    return max(lo, min(hi, n))


def sanitize_digits(text: str, *, max_val: int, width: int = 3) -> str:
    digits = re.sub(r"\D", "", text or "")[:width]
    if not digits:
        return "0"
    return str(_clamp_int(int(digits), 0, max_val))


def normalize_rule(raw: dict) -> dict:
    r = copy.deepcopy(_DEFAULT_RULE)
    gift = raw.get("gift")
    if isinstance(gift, str) and gift.strip():
        r["gift"] = gift.strip()
    mode = raw.get("mode")
    if mode in MODES:
        r["mode"] = mode
    r["value"] = _clamp_int(raw.get("value"), 0, 999)
    unit = raw.get("unit")
    if unit in UNITS:
        r["unit"] = unit
    r["random_neg"] = _clamp_int(raw.get("random_neg"), 0, 999)
    r["random_pos"] = _clamp_int(raw.get("random_pos"), 0, 999)
    ru = raw.get("random_unit")
    if ru in UNITS:
        r["random_unit"] = ru
    return r


def total_seconds(h: int, m: int, s: int) -> int:
    h = _clamp_int(h, 0, 999)
    m = _clamp_int(m, 0, 60)
    s = _clamp_int(s, 0, 60)
    return h * 3600 + m * 60 + s


def split_seconds(total: int) -> tuple[int, int, int]:
    """剩余秒数 → (时, 分, 秒)，显示用，不低于 0。"""
    total = max(0, int(total))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return h, m, s


def find_rule_for_gift(rules: list[dict], gift_name: str) -> dict | None:
    """按礼物名匹配第一条规则（与设置页 6 格顺序一致）。"""
    if not gift_name:
        return None
    for rule in rules:
        if rule.get("gift") == gift_name:
            return rule
    return None


def gift_log_left(user: str, gift: str, count: int) -> str:
    user = (user or "观众").strip() or "观众"
    gift = (gift or "礼物").strip() or "礼物"
    count = max(1, int(count))
    if count > 1:
        return f"{user} 送出了 {gift} ×{count}"
    return f"{user} 送出了 {gift}"


def format_timer_display(h: int, m: int, s: int) -> str:
    """加班机倒计时：1:07:04 / 07:04。"""
    h = _clamp_int(h, 0, 999)
    m = _clamp_int(m, 0, 60)
    s = _clamp_int(s, 0, 60)
    if h >= 1:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def unit_to_seconds(value: int, unit: str) -> int:
    v = _clamp_int(value, 0, 999)
    if unit == "时":
        return v * 3600
    if unit == "分":
        return v * 60
    return v


def format_delta_log(delta_seconds: int) -> str:
    sign = "+" if delta_seconds >= 0 else "-"
    s = abs(int(delta_seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    parts: list[str] = []
    if h > 0:
        parts.append(f"{h}小时")
    if m > 0:
        parts.append(f"{m}分钟")
    if sec > 0 or not parts:
        parts.append(f"{sec}秒")
    return sign + "".join(parts)


def rule_slot_label(rule: dict) -> str:
    mode = rule.get("mode", "加")
    if mode == "随机":
        unit = rule.get("random_unit", "分")
        neg = _clamp_int(rule.get("random_neg"), 0, 999)
        pos = _clamp_int(rule.get("random_pos"), 0, 999)
        return f"随机-{neg}~+{pos}{unit}"
    sign = "+" if mode == "加" else "-"
    return f"{sign}{rule.get('value', 1)}{rule.get('unit', '分')}"


def rule_to_seconds(rule: dict, *, count: int = 1) -> int:
    """规则 → 秒数变化（带符号）；count 为礼物数量。"""
    count = max(1, _clamp_int(count, 1, 9999))
    mode = rule.get("mode", "加")
    if mode == "随机":
        import random
        neg = unit_to_seconds(rule.get("random_neg", 0), rule.get("random_unit", "分"))
        pos = unit_to_seconds(rule.get("random_pos", 0), rule.get("random_unit", "分"))
        return sum(random.randint(-neg, pos) for _ in range(count))
    sec = unit_to_seconds(rule.get("value", 0), rule.get("unit", "分"))
    signed = sec if mode == "加" else -sec
    return signed * count


def align_to_qt(align: str) -> int:
    from PySide6.QtCore import Qt
    if align == "居左":
        return int(Qt.AlignLeft | Qt.AlignVCenter)
    if align == "居右":
        return int(Qt.AlignRight | Qt.AlignVCenter)
    return int(Qt.AlignHCenter | Qt.AlignVCenter)

# ═══════════════════════════════════════════
# 设置页 UI
# ═══════════════════════════════════════════

# ── 固定尺寸（贴内容宽度，避免礼物格左右留白）────────────
_GIFT_ICON = 48
_GIFT_PICK = 48          # 礼物选择按钮：正方形
_GIFT_BTN_W = _GIFT_PICK
_GIFT_BTN_H = _GIFT_PICK
_GIFT_LEFT_W = _GIFT_BTN_W + 3 + _GIFT_ICON
_CTRL_H = 24
_CTRL_GAP = 2
_MODE_W = 56
_UNIT_W = 52
_VAL_W = 42
_VAL_W_TIME = 52
_RANDOM_ROW_W = 12 + _VAL_W + 24 + 12 + _VAL_W
_RIGHT_W = max(_MODE_W, _VAL_W, _RANDOM_ROW_W, _UNIT_W)
_MODULE_W = _GIFT_LEFT_W + 2 + _RIGHT_W + 8
_MODULE_H = _CTRL_H * 3 + _CTRL_GAP * 2 + 8
_GRID_GAP = 8
_SECTION_BODY_MX = 16   # _SectionBlock.content 左右各 8px
_GRID_CONTENT_W = _MODULE_W * 2 + _GRID_GAP
_PANEL_W = _GRID_CONTENT_W + _SECTION_BODY_MX
_PICKER_COLS = 4
_PICKER_ROWS = 4
_PICKER_CELL = 64
_PICKER_SCROLL_GUTTER = 16   # 给竖向滚动条留空，避免挡住第 4 列
_PICKER_W = (
    10 + _PICKER_CELL * _PICKER_COLS + 6 * (_PICKER_COLS - 1)
    + 10 + _PICKER_SCROLL_GUTTER
)
_PICKER_H = 10 + 34 + 8 + _PICKER_ROWS * (_PICKER_CELL + 18) + 10

_SIM_PICK_W = 90    # 模拟区「选择礼物」宽
_SIM_BTN_H = 32       # 比 icon 矮，避免边框贴边被裁切
_SIM_BTN_GAP = 12     # 按钮与 icon 间距


def _C():
    return _theme.get()


class _SettingsCombo(ThemedComboBox):
    """设置页下拉：细边框、略收紧内边距。"""

    def _refresh_style(self):
        C = _C()
        self._btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['card']};
                color: {C['text']};
                border: 1px solid {C['active_line']};
                border-radius: 4px;
                text-align: left;
                padding: 0 6px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                border-color: {C['active_line']};
                background: {C['hover']};
            }}
        """)


class _SectionBlock(QFrame):
    """分区：居中加粗标题（仿加班机），无蓝底。"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("OvertimeSection")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        bar = QLabel(title)
        bar.setObjectName("OvertimeSectionTitle")
        bar.setAlignment(Qt.AlignCenter)
        root.addWidget(bar)

        self.body = QFrame()
        self.body.setObjectName("OvertimeSectionBody")
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(8, 0, 8, 8)
        self.content.setSpacing(6)
        root.addWidget(self.body)


class _IntField(QLineEdit):
    """非负整数；失焦 clamp。"""

    valueCommitted = Signal()

    def __init__(self, *, max_val: int = 999, char_w: int = 3,
                 height: int | None = None, min_width: int | None = None,
                 parent=None):
        super().__init__(parent)
        self._max = max_val
        h = height if height is not None else _CTRL_H
        fm = QFontMetrics(QFont("Microsoft YaHei", 11))
        sample = "8" * char_w
        w = fm.horizontalAdvance(sample) + 16
        if min_width is not None:
            w = max(w, min_width)
        elif char_w >= 3:
            w = max(w, _VAL_W)
        else:
            w = max(w, 36)
        self.setFixedSize(w, h)
        self.setAlignment(Qt.AlignCenter)
        self.setText("0")
        self.textChanged.connect(self._strip)
        self.editingFinished.connect(self._commit)

    def _strip(self, text: str):
        cleaned = "".join(c for c in text if c.isdigit())
        if cleaned != text:
            self.blockSignals(True)
            self.setText(cleaned or "0")
            self.blockSignals(False)

    def _commit(self):
        val = sanitize_digits(self.text(), max_val=self._max)
        self.blockSignals(True)
        self.setText(val)
        self.blockSignals(False)
        self.valueCommitted.emit()

    def value(self) -> int:
        return int(sanitize_digits(self.text(), max_val=self._max))

    def set_value(self, n: int):
        self.setText(str(sanitize_digits(str(n), max_val=self._max)))


class _GiftPickerPopup(QFrame):
    """礼物选择栏：已挂在 6 格上的礼物不出现在列表中。"""

    giftSelected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setFixedSize(_PICKER_W, _PICKER_H)
        self._all_names = gift_names_cached()
        self._blocked: set[str] = set()
        self._show_all = False
        self._build()

    def _build(self):
        C = _C()
        self.setStyleSheet(f"""
            QFrame {{
                background: {C['card']};
                border: 1px solid {C['active_line']};
                border-radius: 4px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        sr = QHBoxLayout()
        sr.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索礼物")
        self._search.setFixedHeight(30)
        self._search.textChanged.connect(self._refresh_grid)
        sbtn = QPushButton("搜索")
        sbtn.setFixedSize(52, 30)
        sbtn.setCursor(Qt.PointingHandCursor)
        sbtn.clicked.connect(self._refresh_grid)
        sr.addWidget(self._search, 1)
        sr.addWidget(sbtn)
        lay.addLayout(sr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedHeight(_PICKER_ROWS * (_PICKER_CELL + 18))
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 2, 0)
        self._grid.setHorizontalSpacing(6)
        self._grid.setVerticalSpacing(6)
        scroll.setWidget(self._grid_host)
        scroll.setViewportMargins(0, 0, 2, 0)
        lay.addWidget(scroll)

    def _fuzzy_match(self, name: str, query: str) -> bool:
        q = query.strip().lower()
        if not q:
            return True
        if q in name.lower():
            return True
        i = 0
        for ch in name.lower():
            if i < len(q) and ch == q[i]:
                i += 1
        return i == len(q)

    def _refresh_grid(self):
        q = self._search.text()
        names = [
            n for n in self._all_names
            if n not in self._blocked and self._fuzzy_match(n, q)
        ]
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not names:
            hint = QLabel(
                "无匹配礼物" if self._show_all or self._search.text().strip()
                else "暂无其它礼物可选",
            )
            hint.setAlignment(Qt.AlignCenter)
            f = QFont("Microsoft YaHei")
            f.setPixelSize(11)
            hint.setFont(f)
            hint.setStyleSheet(f"color:{_C()['text_muted']};background:transparent;")
            self._grid.addWidget(hint, 0, 0, 1, _PICKER_COLS)
            return
        for i, name in enumerate(names):
            self._grid.addWidget(
                self._make_cell(name), i // _PICKER_COLS, i % _PICKER_COLS,
            )

    def _make_cell(self, name: str) -> QPushButton:
        C = _C()
        btn = QPushButton()
        btn.setFixedSize(_PICKER_CELL, _PICKER_CELL + 16)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px; padding: 0;
            }}
            QPushButton:hover {{
                background: transparent;
                border-color: {C['active_line']};
            }}
        """)
        lay = QVBoxLayout(btn)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        icon = QLabel()
        icon.setFixedSize(_PICKER_CELL - 4, _PICKER_CELL - 4)
        icon.setAlignment(Qt.AlignCenter)
        px = load_gift_pixmap(name, _PICKER_CELL - 8)
        if px:
            icon.setPixmap(px)
        icon.setStyleSheet("background:transparent;border:none;")
        name_lbl = QLabel(name)
        name_lbl.setFixedSize(_PICKER_CELL - 4, 14)
        name_lbl.setAlignment(Qt.AlignCenter)
        f = QFont("Microsoft YaHei")
        f.setPixelSize(10)
        name_lbl.setFont(f)
        name_lbl.setStyleSheet("background:transparent;border:none;")
        lay.addWidget(icon, 0, Qt.AlignHCenter)
        lay.addWidget(name_lbl, 0, Qt.AlignHCenter)
        btn.clicked.connect(lambda _=False, n=name: self._pick(n))
        return btn

    def _pick(self, name: str):
        self.giftSelected.emit(name)
        self.hide()

    def open_at(
        self,
        anchor: QWidget,
        *,
        hide: set[str] | None = None,
        show_all: bool = False,
    ):
        """show_all=True：模拟区，展示并可搜索全部礼物；否则 hide 为需隐藏的已占用礼物。"""
        self._show_all = show_all
        self._blocked = set() if show_all else set(hide or ())
        self.move(anchor.mapToGlobal(QPoint(anchor.width() + 6, 0)))
        self._search.clear()
        self._refresh_grid()
        self.show()
        self._search.setFocus()


class _GiftRuleModule(QFrame):
    """单个礼物规则（对应加班机上一格）。"""

    changed = Signal()

    def __init__(self, index: int, rule: dict, parent=None):
        super().__init__(parent)
        self._index = index
        self._rule = dict(rule)
        self._blocked_fn: Callable[[], set[str]] | None = None
        self._picker_cb: Callable[["_GiftRuleModule"], None] | None = None
        self.setFixedSize(_MODULE_W, _MODULE_H)
        self._build()
        self._load_rule(self._rule)

    def set_picker_callback(self, cb: Callable[["_GiftRuleModule"], None]):
        self._picker_cb = cb

    def pick_anchor(self) -> QPushButton:
        return self._pick_btn

    def set_blocked_fn(self, fn: Callable[[], set[str]]):
        self._blocked_fn = fn

    def current_gift(self) -> str:
        return self._rule.get("gift", "小心心")

    def _build(self):
        self.setObjectName("OvertimeGiftModule")
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        left_wrap = QWidget()
        left_wrap.setAttribute(Qt.WA_TranslucentBackground)
        left_wrap.setFixedSize(_GIFT_LEFT_W, _GIFT_ICON)
        left = QHBoxLayout(left_wrap)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(3)
        self._pick_btn = QPushButton("礼物\n选择")
        self._pick_btn.setFixedSize(_GIFT_BTN_W, _GIFT_BTN_H)
        self._pick_btn.setCursor(Qt.PointingHandCursor)
        self._pick_btn.clicked.connect(self._open_picker)
        left.addWidget(self._pick_btn)

        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(_GIFT_ICON, _GIFT_ICON)
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._icon_lbl.setObjectName("OvertimeGiftIcon")
        self._icon_lbl.setScaledContents(False)
        left.addWidget(self._icon_lbl)
        root.addWidget(left_wrap, 0, Qt.AlignTop)

        right_h = _CTRL_H * 3 + _CTRL_GAP * 2
        right_wrap = QWidget()
        right_wrap.setAttribute(Qt.WA_TranslucentBackground)
        right_wrap.setFixedSize(_RIGHT_W, right_h)
        right = QVBoxLayout(right_wrap)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(_CTRL_GAP)

        self._mode = _SettingsCombo()
        self._mode.addItems(list(MODES))
        self._mode.setFixedSize(_MODE_W, _CTRL_H)
        self._mode.currentTextChanged.connect(self._on_mode_change)
        right.addWidget(self._mode, 0, Qt.AlignLeft)

        self._row_stack = QStackedWidget()
        self._row_stack.setFixedSize(_RIGHT_W, _CTRL_H)

        self._simple = QWidget()
        sr = QHBoxLayout(self._simple)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(2)
        self._value = _IntField(max_val=999, char_w=3, min_width=_VAL_W)
        self._value.valueCommitted.connect(lambda: self._emit_change())
        sr.addWidget(self._value)
        self._row_stack.addWidget(self._simple)

        self._random = QWidget()
        rr = QHBoxLayout(self._random)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.setSpacing(1)
        rr.addWidget(self._lbl("-"))
        self._rand_neg = _IntField(max_val=999, char_w=3, min_width=_VAL_W)
        self._rand_neg.valueCommitted.connect(lambda: self._emit_change())
        rr.addWidget(self._rand_neg)
        rr.addWidget(self._lbl("到 "))
        rr.addWidget(self._lbl("+"))
        self._rand_pos = _IntField(max_val=999, char_w=3, min_width=_VAL_W)
        self._rand_pos.valueCommitted.connect(lambda: self._emit_change())
        rr.addWidget(self._rand_pos)
        self._row_stack.addWidget(self._random)

        right.addWidget(self._row_stack)

        self._unit_row = QWidget()
        self._unit_row.setFixedHeight(_CTRL_H)
        ur = QHBoxLayout(self._unit_row)
        ur.setContentsMargins(0, 0, 0, 0)
        ur.setSpacing(0)
        self._unit = _SettingsCombo()
        self._unit.addItems(list(UNITS))
        self._unit.setFixedSize(_UNIT_W, _CTRL_H)
        self._unit.currentTextChanged.connect(lambda _: self._emit_change())
        ur.addWidget(self._unit, 0, Qt.AlignLeft)
        right.addWidget(self._unit_row)

        root.addWidget(right_wrap, 0, Qt.AlignTop | Qt.AlignLeft)
        self._style_pick_btn()
        _theme.on_change(lambda _: (self._style_pick_btn(), self._style_icon()))

    @staticmethod
    def _lbl(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setStyleSheet("background:transparent;")
        lb.setFixedSize(
            12 if text in ("-", "+") else (24 if text == "到 " else 20),
            _CTRL_H,
        )
        f = QFont("Microsoft YaHei")
        f.setPixelSize(11)
        lb.setFont(f)
        lb.setAlignment(Qt.AlignCenter)
        return lb

    def _style_pick_btn(self):
        C = _C()
        self._pick_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C['text']};
                border: 1px solid {C['active_line']};
                border-radius: 3px; font-size: 10px; line-height: 1.05;
                padding: 0 1px;
            }}
            QPushButton:hover {{
                background: transparent; color: {C['active_line']};
            }}
        """)
        self._style_icon()

    def _style_icon(self):
        self._icon_lbl.setStyleSheet("""
            QLabel#OvertimeGiftIcon {
                background: transparent;
                border: none;
            }
        """)

    def _open_picker(self):
        if self._picker_cb:
            self._picker_cb(self)

    def apply_gift(self, name: str):
        blocked = self._blocked_fn() if self._blocked_fn else set()
        if name in blocked:
            return
        self._rule["gift"] = name
        self._show_icon(name)
        self._emit_change()

    def _show_icon(self, name: str):
        px = load_gift_pixmap(name, _GIFT_ICON - 6)
        if px:
            self._icon_lbl.setPixmap(px)
            self._icon_lbl.setText("")
        else:
            self._icon_lbl.setPixmap(QPixmap())
            self._icon_lbl.setText("·")

    def _on_mode_change(self, _t: str):
        self._sync_mode()
        self._emit_change()

    def _sync_mode(self):
        is_rand = self._mode.currentText() == "随机"
        self._row_stack.setCurrentIndex(1 if is_rand else 0)

    def _load_rule(self, rule: dict):
        self._rule = dict(rule)
        self._mode.blockSignals(True)
        self._unit.blockSignals(True)
        self._mode.setCurrentText(rule.get("mode", "加"))
        self._value.set_value(rule.get("value", 1))
        unit = rule.get("unit", "分")
        if rule.get("mode") == "随机":
            unit = rule.get("random_unit", unit)
        self._unit.setCurrentText(unit)
        self._rand_neg.set_value(rule.get("random_neg", 0))
        self._rand_pos.set_value(rule.get("random_pos", 1))
        self._mode.blockSignals(False)
        self._unit.blockSignals(False)
        self._show_icon(rule.get("gift", "小心心"))
        self._sync_mode()

    def to_rule(self) -> dict:
        unit = self._unit.currentText()
        return normalize_rule({
            "gift": self._rule.get("gift", "小心心"),
            "mode": self._mode.currentText(),
            "value": self._value.value(),
            "unit": unit,
            "random_neg": self._rand_neg.value(),
            "random_pos": self._rand_pos.value(),
            "random_unit": unit,
        })

    def _emit_change(self):
        self._rule = self.to_rule()
        self.changed.emit()


class OvertimeSimGiftWidget(QFrame):
    """设置页：模拟推送礼物（礼物选择 + 数量 + 推送）。"""

    pushClicked = Signal(object)  # GiftMessage

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("OvertimeSimGift")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._gift = SIM_DEFAULT_GIFT
        self._picker = _GiftPickerPopup(self)
        self._picker.giftSelected.connect(self._on_picked)
        self._build()
        self._show_icon(self._gift)
        _theme.on_change(lambda _: (self._style_pick_btn(), self._style_push_btn()))

    def _build(self):
        self.setFixedHeight(_GIFT_ICON)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        left_wrap = QWidget()
        left_wrap.setObjectName("OvertimeSimLeft")
        left_wrap.setAttribute(Qt.WA_TranslucentBackground)
        left_wrap.setFixedSize(
            _SIM_PICK_W + _SIM_BTN_GAP + _GIFT_ICON, _GIFT_ICON,
        )
        left = QHBoxLayout(left_wrap)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(_SIM_BTN_GAP)
        self._pick_btn = QPushButton("选择礼物")
        self._pick_btn.setObjectName("OvertimeSimPickBtn")
        self._pick_btn.setFlat(True)
        self._pick_btn.setAutoFillBackground(False)
        self._pick_btn.setFixedSize(_SIM_PICK_W, _SIM_BTN_H)
        self._pick_btn.setCursor(Qt.PointingHandCursor)
        self._pick_btn.clicked.connect(
            lambda: self._picker.open_at(self._pick_btn, show_all=True),
        )
        left.addWidget(self._pick_btn, 0, Qt.AlignVCenter)
        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(_GIFT_ICON, _GIFT_ICON)
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._icon_lbl.setObjectName("OvertimeSimGiftIcon")
        self._icon_lbl.setScaledContents(False)
        self._icon_lbl.setAutoFillBackground(False)
        self._icon_lbl.setAttribute(Qt.WA_TranslucentBackground)
        left.addWidget(self._icon_lbl, 0, Qt.AlignVCenter)
        root.addWidget(left_wrap)

        qty_row = QHBoxLayout()
        qty_row.setSpacing(6)
        qty_lbl = QLabel("数量")
        qty_lbl.setFixedHeight(_GIFT_ICON)
        qty_lbl.setAlignment(Qt.AlignCenter)
        self._count = _IntField(max_val=9999, char_w=4, min_width=52, height=28)
        self._count.set_value(1)
        qty_row.addWidget(qty_lbl)
        qty_row.addWidget(self._count)
        root.addLayout(qty_row)

        root.addStretch()

        self._push_btn = QPushButton("推送")
        self._push_btn.setFixedSize(72, 34)
        self._push_btn.setCursor(Qt.PointingHandCursor)
        self._push_btn.clicked.connect(self._on_push)
        root.addWidget(self._push_btn)
        self._style_pick_btn()
        self._style_push_btn()

    def set_push_enabled(self, enabled: bool):
        self._push_btn.setEnabled(enabled)
        self._push_btn.setToolTip(
            "" if enabled else "请先打开加班机悬浮窗",
        )

    def _on_picked(self, name: str):
        self._gift = name
        self._show_icon(name)

    def _on_push(self):
        count = self._count.value()
        if count <= 0:
            return
        self.pushClicked.emit(make_sim_gift_message(self._gift, count))

    def _show_icon(self, name: str):
        px = load_gift_pixmap(name, _GIFT_ICON - 6)
        if px:
            self._icon_lbl.setPixmap(px)
            self._icon_lbl.setText("")
        else:
            self._icon_lbl.setPixmap(QPixmap())
            self._icon_lbl.setText("·")

    def _style_pick_btn(self):
        C = _C()
        self._pick_btn.setStyleSheet(f"""
            QPushButton#OvertimeSimPickBtn {{
                background: transparent;
                background-color: transparent;
                color: {C['active_line']};
                border: 1.5px solid {C['active_line']};
                border-radius: 6px;
                font-size: 12px;
                font-weight: 600;
                padding: 0 10px;
                min-height: {_SIM_BTN_H}px;
                max-height: {_SIM_BTN_H}px;
            }}
            QPushButton#OvertimeSimPickBtn:hover {{
                background: transparent;
                background-color: transparent;
                color: {C['text']};
            }}
        """)
        self._icon_lbl.setStyleSheet("""
            QLabel#OvertimeSimGiftIcon {
                background: transparent;
                background-color: transparent;
                border: none;
            }
        """)

    def _style_push_btn(self):
        C = _C()
        self._push_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['active_line']}; color: #fff;
                border: none; border-radius: 6px;
                font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {C['hover']}; color: {C['text']}; }}
            QPushButton:disabled {{
                background: {C['border']}; color: {C['text_muted']};
            }}
        """)


class OvertimeSettingsPanel(QWidget):
    applyRequested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = load_settings()
        self.setFixedWidth(_PANEL_W)
        self.setMinimumHeight(720)
        self._gift_picker = _GiftPickerPopup(self)
        self._gift_picker.giftSelected.connect(self._on_shared_gift_picked)
        self._gift_pick_target: _GiftRuleModule | None = None
        self._build()
        _theme.on_change(lambda _: self._apply_theme())

    def _apply_theme(self):
        self._style_apply_btn()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        top = QHBoxLayout()
        top.addStretch()
        self._apply_btn = QPushButton("应用")
        self._apply_btn.setFixedSize(72, 34)
        self._apply_btn.setCursor(Qt.PointingHandCursor)
        self._apply_btn.clicked.connect(self._on_apply)
        top.addWidget(self._apply_btn)
        root.addLayout(top)
        root.addWidget(self._block_time())
        root.addWidget(self._block_gifts())
        root.addWidget(self._block_custom())
        root.addStretch()
        self._style_apply_btn()

    def _style_apply_btn(self):
        C = _C()
        self._apply_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['active_line']}; color: #fff;
                border: none; border-radius: 6px;
                font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {C['hover']}; color: {C['text']}; }}
        """)

    def _block_time(self) -> _SectionBlock:
        sec = _SectionBlock("剩余时间设置")
        sec.setFixedWidth(_PANEL_W)
        row = QHBoxLayout()
        row.setSpacing(4)
        self._h = _IntField(max_val=999, char_w=3, height=28, min_width=_VAL_W_TIME)
        self._m = _IntField(max_val=60, char_w=2, height=28, min_width=40)
        self._s = _IntField(max_val=60, char_w=2, height=28, min_width=40)
        self._h.set_value(self._data["hours"])
        self._m.set_value(self._data["minutes"])
        self._s.set_value(self._data["seconds"])
        inner = QHBoxLayout()
        inner.setSpacing(4)
        for fld, suffix in (
            (self._h, "时"), (self._m, "分"), (self._s, "秒"),
        ):
            fld.valueCommitted.connect(self._save_time)
            inner.addWidget(fld)
            lb = QLabel(suffix)
            lb.setFixedHeight(28)
            lb.setAlignment(Qt.AlignCenter)
            inner.addWidget(lb)
        row.addStretch()
        row.addLayout(inner)
        row.addStretch()
        sec.content.addLayout(row)
        return sec

    def _block_gifts(self) -> _SectionBlock:
        sec = _SectionBlock("礼物设置")
        sec.setFixedWidth(_PANEL_W)
        grid_w = QGridLayout()
        grid_w.setHorizontalSpacing(_GRID_GAP)
        grid_w.setVerticalSpacing(6)
        grid_host = QWidget()
        grid_host.setFixedWidth(_GRID_CONTENT_W)
        gh = QHBoxLayout(grid_host)
        gh.setContentsMargins(0, 0, 0, 0)
        gh.addLayout(grid_w)
        self._rule_rows: list[_GiftRuleModule] = []
        for i, rule in enumerate(self._data["rules"]):
            mod = _GiftRuleModule(i, rule)
            mod.set_blocked_fn(self._assigned_gifts)
            mod.set_picker_callback(self._open_gift_picker_for)
            mod.changed.connect(lambda _=False, idx=i: self._save_rules(prefer_index=idx))
            self._rule_rows.append(mod)
            grid_w.addWidget(mod, i // 2, i % 2)
        sec.content.addWidget(grid_host)
        return sec

    def _open_gift_picker_for(self, mod: _GiftRuleModule):
        self._gift_pick_target = mod
        self._gift_picker.open_at(
            mod.pick_anchor(),
            hide=self._assigned_gifts(),
        )

    def _on_shared_gift_picked(self, name: str):
        if self._gift_pick_target:
            self._gift_pick_target.apply_gift(name)

    def _assigned_gifts(self) -> set[str]:
        """当前 6 格已挂载的礼物（选择窗中全部隐藏）。"""
        names: set[str] = set()
        for row in self._rule_rows:
            g = row.current_gift().strip()
            if g:
                names.add(g)
        return names

    def _block_custom(self) -> _SectionBlock:
        sec = _SectionBlock("自定义信息设置")
        sec.setFixedWidth(_PANEL_W)
        self._custom = QTextEdit()
        self._custom.setPlaceholderText("最多两行")
        self._custom.setFixedHeight(48)
        self._custom.setAcceptRichText(False)
        self._custom.setLineWrapMode(QTextEdit.WidgetWidth)
        self._custom.setPlainText(self._data.get("custom_text", ""))
        self._custom.textChanged.connect(self._save_custom)
        sec.content.addWidget(self._custom)

        ar = QHBoxLayout()
        ar.addWidget(QLabel("对齐"))
        self._align = _SettingsCombo()
        self._align.addItems(list(ALIGNS))
        self._align.setFixedSize(_UNIT_W + 20, _CTRL_H + 4)
        self._align.setCurrentText(self._data.get("custom_align", "居中"))
        self._align.currentTextChanged.connect(lambda _: self._save_custom())
        ar.addWidget(self._align)
        ar.addStretch()
        sec.content.addLayout(ar)
        return sec

    def _save_time(self):
        self._data["hours"] = self._h.value()
        self._data["minutes"] = self._m.value()
        self._data["seconds"] = self._s.value()
        save_settings(self._data)

    def _save_rules(self, prefer_index: int | None = None):
        self._data["rules"] = dedupe_rules(
            [r.to_rule() for r in self._rule_rows],
            prefer_index=prefer_index,
        )
        for row, rule in zip(self._rule_rows, self._data["rules"]):
            if row.current_gift() != rule.get("gift"):
                row._load_rule(rule)
        save_settings(self._data)

    def _save_custom(self):
        text = self._custom.toPlainText()
        lines = text.splitlines()
        if len(lines) > 2:
            text = "\n".join(lines[:2])
            self._custom.blockSignals(True)
            self._custom.setPlainText(text)
            self._custom.blockSignals(False)
        self._data["custom_text"] = text[:80]
        self._data["custom_align"] = self._align.currentText()
        save_settings(self._data)

    def _on_apply(self):
        self._save_time()
        self._save_rules()
        self._save_custom()
        self.applyRequested.emit(dict(self._data))

    def current_settings(self) -> dict:
        return dict(self._data)

# ═══════════════════════════════════════════
# 悬浮窗 + 控制面板
# ═══════════════════════════════════════════

# ─────────────────────────────────────────────
# 常量 / 配置
# ─────────────────────────────────────────────
_BORDER_W   = 2
_TOPBAR_H   = 32
_RESIZE_HIT = 8
_CIRCLE_D   = 14
_CIRCLE_OFF = 5
_ANIM_MS    = 280
_BTN_W      = 32

# 大组件 1x 逻辑宽；各行高度用 cm（随 scale 同比缩放）
_REF_BLOCK_W = 280

# 各行物理高度 / 间距（1x）
_CM_TIMER_W  = 4.0
_CM_TIMER_H  = 2.0
_CM_SLOT_H   = 1.5
_CM_CUSTOM_H = 1.2
_CM_LOG_H    = 1.2
_CM_ROW_GAP  = 0.3
_CM_TITLE_TIMER_GAP = 0.1   # 标题 ↔ 倒计时
_CM_GRID_GAP = 0.25
_CM_COL_GAP  = 0.2

_WINDOW_MARGIN_PX: int | None = None
_SCREEN_DPI: float | None = None

_DEFAULT_RULES = DEFAULT_SETTINGS["rules"]

_DEMO_TIMER = format_timer_display(0, 0, 0)
_DEMO_CUSTOM = DEFAULT_SETTINGS["custom_text"]

# 字号微调（1x 参考）
_GIFT_TEXT_REF = "中文字符占占"   # 礼物格宽度按 5 个汉字适配
_GIFT_TEXT_MAX_PX = 16           # 礼物名 / 自定义 1x 上限
_TITLE_MAX_PX = 13               # 标题 1x 上限
_LOG_MAX_PX = 13                 # 送礼日志 1x 上限
_TIMER_EDGE_PAD = 2              # 倒计时阴影/描边留白（px，随 scale）


def _screen_dpi() -> float:
    global _SCREEN_DPI
    if _SCREEN_DPI is None:
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        _SCREEN_DPI = float(screen.logicalDotsPerInch()) if screen else 96.0
    return _SCREEN_DPI


def _cm_to_px(cm: float) -> int:
    return max(1, int(round(_screen_dpi() / 2.54 * cm)))


def _window_margin_px() -> int:
    """窗口内容区边缘 → 大组件外框，固定 0.5cm（不随 scale）。"""
    global _WINDOW_MARGIN_PX
    if _WINDOW_MARGIN_PX is None:
        _WINDOW_MARGIN_PX = _cm_to_px(0.5)
    return _WINDOW_MARGIN_PX


def _ref_timer_w() -> int:
    return _cm_to_px(_CM_TIMER_W)


def _ref_timer_h() -> int:
    return _cm_to_px(_CM_TIMER_H)


def _ref_title_timer_gap() -> int:
    return _cm_to_px(_CM_TITLE_TIMER_GAP)


def _ref_title_row_h() -> int:
    """标题行 1x 高度（贴文字，供置底）。"""
    return max(_cm_to_px(0.35), _TITLE_MAX_PX + 6)


def _ref_title_timer_section_h() -> int:
    return _ref_title_row_h() + _ref_title_timer_gap() + _ref_timer_h()


def _ref_slot_row_h() -> int:
    return _cm_to_px(_CM_SLOT_H)


def _ref_custom_row_h() -> int:
    return _cm_to_px(_CM_CUSTOM_H)


def _ref_log_row_h() -> int:
    return _cm_to_px(_CM_LOG_H)


def _ref_row_gap() -> int:
    return _cm_to_px(_CM_ROW_GAP)


def _ref_grid_gap() -> int:
    return _cm_to_px(_CM_GRID_GAP)


def _ref_col_gap() -> int:
    return _cm_to_px(_CM_COL_GAP)


def _ref_slot_w() -> int:
    return (_ref_block_w() - _ref_col_gap()) // 2


def _ref_block_w() -> int:
    return _REF_BLOCK_W


def _ref_block_h() -> int:
    """大组件 1x 高度（各行均为 cm）。"""
    grid_h = 3 * _ref_slot_row_h() + 2 * _ref_grid_gap()
    row_heights = (
        _ref_title_timer_section_h(),
        grid_h,
        _ref_custom_row_h(),
        _ref_log_row_h(),
    )
    gap = _ref_row_gap()
    return sum(row_heights) + gap * (len(row_heights) - 1)


def _window_aspect_wh() -> float:
    """整窗拖拽比例 = 初始窗口（大组件 + 0.5cm 边距 + 顶栏）。"""
    dw, dh = _default_window_size()
    return dw / dh


def _default_window_size() -> tuple[int, int]:
    m = _window_margin_px()
    bw, bh = _ref_block_w(), _ref_block_h()
    return bw + 2 * m, _TOPBAR_H + bh + 2 * m


def _fit_font_pixel_size(
    text: str, max_w: int, max_h: int, *,
    bold: bool = False,
    max_px: int | None = None,
    min_px: int = 8,
    edge_pad: int = 1,
) -> int:
    max_w = max(1, max_w - edge_pad * 2)
    max_h = max(1, max_h - edge_pad * 2)
    lo, hi = min_px, max(8, max_h)
    if max_px is not None:
        hi = min(hi, max_px)
    if lo > hi:
        return max(min_px, hi)
    best = lo
    while lo <= hi:
        mid = (lo + hi) // 2
        f = QFont("Microsoft YaHei")
        f.setPixelSize(mid)
        if bold:
            f.setWeight(QFont.Bold)
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        if tw <= max_w and th <= max_h:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _gift_like_text_px(max_w: int, max_h: int, scale: float) -> int:
    """礼物名 / 自定义文字：偏小，且至少容得下 5 个汉字宽。"""
    cap = max(8, int(round(_GIFT_TEXT_MAX_PX * scale)))
    return _fit_font_pixel_size(
        _GIFT_TEXT_REF, max_w, max_h, max_px=cap,
    )


# ─────────────────────────────────────────────
# 白字 + 阴影标签
# ─────────────────────────────────────────────
class _ShadowLabel(QLabel):
    def __init__(self, text: str = "", parent=None, *, bold: bool = False):
        super().__init__(text, parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._bold = bold
        self._px = 13
        self._apply_font()

    def set_pixel_size(self, px: int):
        self._px = max(8, int(px))
        self._apply_font()

    def _apply_font(self):
        f = QFont("Microsoft YaHei")
        f.setPixelSize(self._px)
        if self._bold:
            f.setWeight(QFont.Bold)
        self.setFont(f)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setFont(self.font())
        rect = self.rect()
        flags = int(self.alignment())
        if self.wordWrap():
            flags |= int(Qt.TextWordWrap)
        for dx, dy in ((1, 1), (1, 0), (0, 1)):
            p.setPen(QColor(0, 0, 0, 160))
            p.drawText(rect.translated(dx, dy), flags, self.text())
        p.setPen(QColor(255, 255, 255))
        p.drawText(rect, flags, self.text())
        p.end()


class _GiftSlotWidget(QFrame):
    """单个礼物格：左 icon，右上行名、下行规则标签。"""

    def __init__(self, rule: dict, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._scale = 1.0
        self._gift_name = rule.get("gift", "小心心")
        self._time_label = rule_slot_label(rule)

        self._icon_lbl = QLabel()
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._icon_lbl.setStyleSheet("background: transparent;")
        self._name_lbl = _ShadowLabel(self._gift_name)
        self._name_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._time_lbl = _ShadowLabel(self._time_label)
        self._time_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        root.addWidget(self._icon_lbl)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        text_col.addWidget(self._name_lbl)
        text_col.addWidget(self._time_lbl)
        root.addLayout(text_col, 1)

        self.apply_scale(1.0)

    def set_rule(self, gift_name: str, time_label: str):
        self._gift_name = gift_name
        self._time_label = time_label
        self._name_lbl.setText(gift_name)
        self._time_lbl.setText(time_label)
        self.apply_scale(self._scale)

    def apply_scale(self, scale: float):
        s = max(0.5, scale)
        self._scale = s
        slot_w = int(round(_ref_slot_w() * s))
        slot_h = int(round(_ref_slot_row_h() * s))
        icon_side = max(14, int(round(slot_h * 0.82)))
        self.setFixedSize(slot_w, slot_h)
        self._icon_lbl.setFixedSize(icon_side, icon_side)
        px = load_gift_pixmap(self._gift_name, icon_side)
        if px:
            self._icon_lbl.setPixmap(px)
            self._icon_lbl.setText("")
        else:
            self._icon_lbl.setPixmap(QPixmap())
            self._icon_lbl.setText("·")
        text_w = max(20, slot_w - icon_side - int(round(6 * s)))
        text_h = max(8, (slot_h - int(round(2 * s))) // 2)
        name_px = _gift_like_text_px(text_w, text_h, s)
        self._name_lbl.set_pixel_size(name_px)
        time_px = _fit_font_pixel_size(
            self._time_lbl.text(), text_w, text_h,
            max_px=max(8, name_px),
        )
        self._time_lbl.set_pixel_size(time_px)


class _ResultRow(QWidget):
    def __init__(self, left: str = "", delta_seconds: int = 0, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._left_lbl = _ShadowLabel(left)
        self._left_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._right_lbl = _ShadowLabel("")
        self._right_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._left_lbl, 1)
        lay.addWidget(self._right_lbl, 0)
        self._scale = 1.0
        self.set_entry(left, delta_seconds)

    def set_entry(self, left: str, delta_seconds: int):
        self._left_lbl.setText(left)
        self._right_lbl.setText(
            format_delta_log(delta_seconds) if delta_seconds else "",
        )
        if self._scale > 0:
            self.apply_scale(self._scale)

    def apply_scale(self, scale: float):
        s = max(0.5, scale)
        self._scale = s
        h = int(round(_ref_log_row_h() * s))
        w = int(round(_ref_block_w() * s))
        self.setFixedHeight(h)
        pad = max(2, int(round(2 * s)))
        log_cap = max(8, int(round(_LOG_MAX_PX * s)))
        text_w = max(20, w // 2 - pad)
        px = _fit_font_pixel_size(
            self._left_lbl.text() or "占位", text_w, h - pad, max_px=log_cap,
        )
        self._left_lbl.set_pixel_size(px)
        right_text = self._right_lbl.text() or "+0秒"
        self._right_lbl.set_pixel_size(
            _fit_font_pixel_size(right_text, text_w, h - pad, max_px=log_cap),
        )


class _TimerBox(QFrame):
    """倒计时：1x 为 4cm×2cm，随大组件同比缩放。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._lbl = _ShadowLabel(_DEMO_TIMER, bold=True)
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.addWidget(self._lbl, 0, Qt.AlignHCenter | Qt.AlignTop)
        self._last_scale = 1.0
        self.apply_scale(1.0)

    def set_timer_text(self, text: str):
        self._lbl.setText(text)
        self.apply_scale(self._last_scale)

    def apply_scale(self, scale: float):
        s = max(0.5, scale)
        self._last_scale = s
        w = int(round(_ref_timer_w() * s))
        h = int(round(_ref_timer_h() * s))
        self.setFixedSize(w, h)
        edge = max(2, int(round(_TIMER_EDGE_PAD * s)))
        top = max(1, int(round(1 * s)))
        self._lay.setContentsMargins(edge, top, edge, edge)
        inner_w = max(1, w - edge * 2)
        inner_h = max(1, h - top - edge)
        px = _fit_font_pixel_size(
            self._lbl.text(), inner_w, inner_h, bold=True, edge_pad=1,
        )
        self._lbl.set_pixel_size(px)


class _TitleTimerSection(QWidget):
    """标题（置底）+ 0.1cm + 倒计时（置顶）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._title_wrap = QWidget()
        self._title_wrap.setAttribute(Qt.WA_TranslucentBackground)
        self._title_lbl = _ShadowLabel("距离下播时间")
        self._title_lbl.setAlignment(Qt.AlignCenter)
        tw_lay = QVBoxLayout(self._title_wrap)
        tw_lay.setContentsMargins(0, 0, 0, 0)
        tw_lay.setSpacing(0)
        tw_lay.addStretch(1)
        tw_lay.addWidget(self._title_lbl, 0, Qt.AlignHCenter | Qt.AlignBottom)

        self._timer_box = _TimerBox()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(_ref_title_timer_gap())
        lay.addWidget(self._title_wrap)
        lay.addWidget(self._timer_box, 0, Qt.AlignHCenter)

    @property
    def title_lbl(self) -> _ShadowLabel:
        return self._title_lbl

    @property
    def timer_box(self) -> _TimerBox:
        return self._timer_box

    def apply_scale(self, scale: float, block_w: int, pad: int):
        s = max(0.5, scale)
        section_gap = max(1, int(round(_ref_title_timer_gap() * s)))
        self.layout().setSpacing(section_gap)

        title_cap = max(8, int(round(_TITLE_MAX_PX * s)))
        title_px = _fit_font_pixel_size(
            self._title_lbl.text(), block_w - pad, _ref_title_row_h(),
            max_px=title_cap,
        )
        self._title_lbl.set_pixel_size(title_px)
        title_row_h = QFontMetrics(self._title_lbl.font()).height() + max(
            1, int(round(2 * s)),
        )
        self._title_wrap.setFixedHeight(title_row_h)

        self._timer_box.apply_scale(s)
        self.setFixedHeight(title_row_h + section_gap + self._timer_box.height())


class _OvertimeBlock(QWidget):
    """加班机大组件：所有子项固定在其内部；窗口尺寸只引用本组件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._gift_slots: list[_GiftSlotWidget] = []
        self._result_row: _ResultRow | None = None
        self._scale = 1.0
        self._settings = load_settings()
        self._build()

    def _build(self):
        self._root_lay = QVBoxLayout(self)
        self._root_lay.setContentsMargins(0, 0, 0, 0)
        self._root_lay.setSpacing(_ref_row_gap())

        self._title_timer = _TitleTimerSection()
        self._root_lay.addWidget(self._title_timer)

        self._grid_wrap = QWidget()
        self._grid_wrap.setAttribute(Qt.WA_TranslucentBackground)
        self._grid = QGridLayout(self._grid_wrap)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(_ref_col_gap())
        self._grid.setVerticalSpacing(_ref_grid_gap())
        for i, rule in enumerate(self._settings.get("rules", _DEFAULT_RULES)):
            slot = _GiftSlotWidget(rule)
            self._gift_slots.append(slot)
            self._grid.addWidget(slot, i // 2, i % 2, Qt.AlignLeft)
        self._root_lay.addWidget(self._grid_wrap)

        custom = self._settings.get("custom_text", _DEMO_CUSTOM)
        align = self._settings.get("custom_align", "居中")
        self._custom_lbl = _ShadowLabel(custom)
        self._custom_lbl.setAlignment(
            Qt.AlignmentFlag(align_to_qt(align)) | Qt.AlignTop,
        )
        self._custom_lbl.setWordWrap(True)
        self._root_lay.addWidget(self._custom_lbl)

        self._result_row = _ResultRow()
        self._root_lay.addWidget(self._result_row)

        self.apply_scale(1.0)
        self.apply_settings(self._settings)

    def set_remaining_display(self, total_seconds: int):
        h, m, s = split_seconds(total_seconds)
        self._title_timer.timer_box.set_timer_text(
            format_timer_display(h, m, s),
        )

    def set_gift_log(self, left: str, delta_seconds: int):
        if self._result_row:
            self._result_row.set_entry(left, delta_seconds)

    def apply_settings(self, settings: dict):
        self._settings = settings
        rules = settings.get("rules", _DEFAULT_RULES)
        for slot, rule in zip(self._gift_slots, rules):
            slot.set_rule(
                rule.get("gift", "小心心"),
                rule_slot_label(rule),
            )
        text = settings.get("custom_text", _DEMO_CUSTOM)
        align = settings.get("custom_align", "居中")
        self._custom_lbl.setText(text)
        self._custom_lbl.setAlignment(
            Qt.AlignmentFlag(align_to_qt(align)) | Qt.AlignTop,
        )
        if self._scale > 0:
            self.apply_scale(self._scale)

    def apply_scale(self, scale: float):
        s = max(0.5, scale)
        self._scale = s
        gap = max(1, int(round(_ref_row_gap() * s)))
        col_gap = max(1, int(round(_ref_col_gap() * s)))
        grid_gap = max(1, int(round(_ref_grid_gap() * s)))

        self._root_lay.setSpacing(gap)
        self._grid.setHorizontalSpacing(col_gap)
        self._grid.setVerticalSpacing(grid_gap)

        bw = int(round(_ref_block_w() * s))
        pad = max(2, int(round(2 * s)))

        self._title_timer.apply_scale(s, bw, pad)

        grid_h = int(round((3 * _ref_slot_row_h() + 2 * _ref_grid_gap()) * s))
        self._grid_wrap.setFixedHeight(grid_h)
        for slot in self._gift_slots:
            slot.apply_scale(s)

        custom_h = int(round(_ref_custom_row_h() * s))
        self._custom_lbl.setFixedHeight(custom_h)
        self._custom_lbl.set_pixel_size(_gift_like_text_px(
            bw - pad, custom_h - pad, s,
        ))

        if self._result_row:
            self._result_row.apply_scale(s)

        self.setFixedSize(
            bw,
            int(round(_ref_block_h() * s)),
        )


class _CircleToggle(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(_CIRCLE_D, _CIRCLE_D)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("background: transparent; border: none;")
        self.setToolTip("显示/隐藏边框")

    def paintEvent(self, _event):
        C = _theme.get()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["border"]))
        p.drawEllipse(0, 0, _CIRCLE_D, _CIRCLE_D)
        p.end()


class _OvertimeRoot(QWidget):
    _EDGE_CURSORS = {
        "l":  Qt.SizeHorCursor,
        "r":  Qt.SizeHorCursor,
        "b":  Qt.SizeVerCursor,
        "bl": Qt.SizeBDiagCursor,
        "br": Qt.SizeFDiagCursor,
    }

    def __init__(self, win: "OvertimeWindow", parent=None):
        super().__init__(parent)
        self._win = win
        self._r = 0.0
        self._drag_anchor: QPoint | None = None
        self._resize_data: tuple | None = None
        self.setMouseTracking(True)
        self.setStyleSheet("background: transparent;")
        self._build()

    def _build(self):
        C = _theme.get()

        self._btn_box = QWidget(self)
        btn_lay = QHBoxLayout(self._btn_box)
        btn_lay.setContentsMargins(0, 0, 0, 0)
        btn_lay.setSpacing(0)

        btn_style = f"""
            QPushButton {{
                background: transparent; border: none;
                color: {C['text_muted']}; font-size: 12px;
                min-width: {_BTN_W}px; max-width: {_BTN_W}px;
                min-height: {_TOPBAR_H}px; max-height: {_TOPBAR_H}px;
            }}
            QPushButton:hover {{
                background: {C['btn_hover']}; color: {C['text']};
            }}
        """
        for text, slot in [("─", lambda: self._win.showMinimized()),
                           ("✕", self._win.close)]:
            btn = QPushButton(text)
            btn.setStyleSheet(btn_style)
            btn.setCursor(Qt.ArrowCursor)
            btn.clicked.connect(slot)
            btn_lay.addWidget(btn)

        self._btn_box.setVisible(False)

        self._circle = _CircleToggle(self)
        self._circle.move(_CIRCLE_OFF, _CIRCLE_OFF)
        self._circle.raise_()
        self._circle.clicked.connect(self._win.toggle_border)

        self._content = QWidget(self)
        self._content.setAttribute(Qt.WA_TranslucentBackground)
        self._content.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._content.setStyleSheet("background: transparent;")

        self._block = _OvertimeBlock(self._content)

    def layout_block(self):
        cw, ch = self._content.width(), self._content.height()
        if cw <= 0 or ch <= 0:
            return
        m = _window_margin_px()
        avail_w = max(1, cw - 2 * m)
        avail_h = max(1, ch - 2 * m)

        s = max(0.5, min(avail_w / _ref_block_w(), avail_h / _ref_block_h()))
        bw = int(round(_ref_block_w() * s))
        bh = int(round(_ref_block_h() * s))
        bx = m + (avail_w - bw) // 2
        by = m + (avail_h - bh) // 2

        self._block.setGeometry(bx, by, bw, bh)
        self._block.apply_scale(s)

    @property
    def block(self) -> _OvertimeBlock:
        return self._block

    def _cx(self) -> float:
        return _CIRCLE_OFF + _CIRCLE_D / 2.0

    def _cy(self) -> float:
        return _CIRCLE_OFF + _CIRCLE_D / 2.0

    def max_radius(self) -> float:
        cx, cy = self._cx(), self._cy()
        return max(
            math.hypot(cx, cy),
            math.hypot(self.width() - cx, cy),
            math.hypot(cx, self.height() - cy),
            math.hypot(self.width() - cx, self.height() - cy),
        )

    def set_radius(self, r: float):
        self._r = r
        btn_far = math.hypot(
            self.width() - self._cx(),
            _TOPBAR_H - self._cy(),
        )
        self._btn_box.setVisible(r >= btn_far)
        self.update()

    def paintEvent(self, _event):
        if self._r <= 0:
            return
        C = _theme.get()
        cx, cy, r = self._cx(), self._cy(), self._r

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        clip = QPainterPath()
        clip.addEllipse(cx - r, cy - r, r * 2, r * 2)
        p.setClipPath(clip)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["sidebar"]))
        p.drawRect(0, 0, self.width(), _TOPBAR_H)

        pen = QPen(QColor(C["sidebar"]), _BORDER_W)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        hw = max(1, _BORDER_W // 2)
        p.drawLine(hw, 0, hw, self.height())
        p.drawLine(self.width() - hw, 0, self.width() - hw, self.height())
        p.drawLine(0, self.height() - hw, self.width(), self.height() - hw)
        p.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        btn_total = _BTN_W * 2
        self._btn_box.setGeometry(
            self.width() - btn_total, 0, btn_total, _TOPBAR_H
        )
        self._content.setGeometry(
            0, _TOPBAR_H, self.width(), self.height() - _TOPBAR_H
        )
        self.layout_block()
        from PySide6.QtCore import QAbstractAnimation
        if (self._win._shown and
                self._win._anim.state() != QAbstractAnimation.Running):
            r = self.max_radius()
            self._r = r
            self._win._anim_r = r
        self.set_radius(self._r)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()

        edge = self._edge_at(pos)
        if edge:
            self._resize_data = (
                edge,
                QRect(self._win.geometry()),
                event.globalPosition().toPoint(),
            )
            return

        if pos.y() < _TOPBAR_H and self._r > _TOPBAR_H * 0.5:
            self._drag_anchor = (
                event.globalPosition().toPoint() - self._win.pos()
            )

    def mouseMoveEvent(self, event):
        gpos = event.globalPosition().toPoint()
        pos = event.position().toPoint()

        if event.buttons() == Qt.LeftButton:
            if self._resize_data:
                self._do_resize(gpos)
                return
            if self._drag_anchor:
                self._win.move(gpos - self._drag_anchor)
                return

        self.setCursor(self._EDGE_CURSORS.get(
            self._edge_at(pos), Qt.ArrowCursor
        ))

    def mouseReleaseEvent(self, _event):
        self._drag_anchor = None
        self._resize_data = None
        self.setCursor(Qt.ArrowCursor)

    def _edge_at(self, pos: QPoint) -> str | None:
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        hit = _RESIZE_HIT
        left = x < hit
        right = x > w - hit
        bot = y > h - hit
        if left and bot:
            return "bl"
        if right and bot:
            return "br"
        if left:
            return "l"
        if right:
            return "r"
        if bot:
            return "b"
        return None

    def _do_resize(self, gpos: QPoint):
        edge, start_geo, start_gpos = self._resize_data
        dx = gpos.x() - start_gpos.x()
        dy = gpos.y() - start_gpos.y()
        aspect = self._win.aspect_ratio()
        min_w = self._win.minimumWidth()
        min_h = self._win.minimumHeight()

        geo = QRect(start_geo)
        w, h = geo.width(), geo.height()

        if edge in ("br", "r"):
            new_w = max(min_w, w + dx)
            new_h = max(min_h, int(round(new_w / aspect)))
            new_w = int(round(new_h * aspect))
        elif edge in ("bl", "l"):
            new_w = max(min_w, w - dx)
            new_h = max(min_h, int(round(new_w / aspect)))
            new_w = int(round(new_h * aspect))
            geo.setLeft(geo.right() - new_w)
        elif edge == "b":
            new_h = max(min_h, h + dy)
            new_w = max(min_w, int(round(new_h * aspect)))
            new_h = int(round(new_w / aspect))
        else:
            return

        if edge in ("br", "r", "b"):
            geo.setRight(geo.left() + new_w)
            geo.setBottom(geo.top() + new_h)
        elif edge in ("bl", "l"):
            geo.setBottom(geo.top() + new_h)

        if geo.width() >= min_w and geo.height() >= min_h:
            self._win.setGeometry(geo)


class OvertimeWindow(QMainWindow):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        dw, dh = _default_window_size()
        self.setMinimumSize(int(dw * 0.75), int(dh * 0.75))
        self.resize(dw, dh)

        self._shown = True
        self._anim_r = 0.0
        self._first_show = True

        self._root = _OvertimeRoot(self)
        self.setCentralWidget(self._root)

        self._settings = load_settings()
        self._remaining_seconds = 0
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)

        self._setup_anim()
        _theme.on_change(lambda _: self._root.update())

    @property
    def block(self) -> _OvertimeBlock:
        return self._root.block

    @staticmethod
    def aspect_ratio() -> float:
        return _window_aspect_wh()

    def _setup_anim(self):
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_r)
        self._anim.finished.connect(self._on_anim_done)

    def toggle_border(self):
        self._shown = not self._shown
        self._anim.stop()
        target = self._root.max_radius() if self._shown else 0.0
        self._anim.setStartValue(self._anim_r)
        self._anim.setEndValue(target)
        self._anim.start()

    def showEvent(self, event):
        super().showEvent(event)
        w, h = _default_window_size()
        self.resize(w, h)
        if self._first_show:
            self._first_show = False
            QTimer.singleShot(0, self._init_shown)
        else:
            QTimer.singleShot(0, lambda: (
                self.apply_settings(load_settings()),
            ))
        self._tick.start()

    def hideEvent(self, event):
        self._tick.stop()
        super().hideEvent(event)

    def _init_shown(self):
        r = self._root.max_radius()
        if r <= 0:
            r = 800.0
        self._anim_r = r
        self._root.set_radius(r)
        self.apply_settings(load_settings())
        self._root.layout_block()
        self._tick.start()

    def apply_settings(self, settings: dict | None = None):
        if settings is None:
            settings = load_settings()
        self._settings = settings
        self._remaining_seconds = total_seconds(
            settings.get("hours", 0),
            settings.get("minutes", 0),
            settings.get("seconds", 0),
        )
        self._root.block.apply_settings(settings)
        self._refresh_timer_display()
        self._root.layout_block()
        if self.isVisible():
            self._tick.start()
        else:
            self._tick.stop()

    def handle_gift(self, msg) -> bool:
        rules = self._settings.get("rules", _DEFAULT_RULES)
        rule = find_rule_for_gift(rules, msg.gift)
        if not rule:
            return False
        delta = rule_to_seconds(rule, count=msg.count)
        if delta == 0:
            return False
        self._remaining_seconds = max(0, self._remaining_seconds + delta)
        self._refresh_timer_display()
        self._root.block.set_gift_log(
            gift_log_left(msg.user, msg.gift, msg.count),
            delta,
        )
        return True

    def _refresh_timer_display(self):
        self._root.block.set_remaining_display(self._remaining_seconds)

    def _on_tick(self):
        if self._remaining_seconds <= 0:
            return
        self._remaining_seconds -= 1
        self._refresh_timer_display()

    def _on_r(self, r: float):
        self._anim_r = r
        self._root.set_radius(r)

    def _on_anim_done(self):
        pass

    def closeEvent(self, event):
        from tools.tool_common import is_app_shutting_down
        if is_app_shutting_down():
            event.accept()
            self.closed.emit()
            return
        event.ignore()
        self.hide()
        self.closed.emit()


@register_tool(name="加班机", desc="透明悬浮加班显示窗口", icon="⏱", order=2)
class OvertimeTool(ToolSingleton, QMainWindow):

    def __init__(self, parent=None):
        if not ToolSingleton.guard_init(self):
            return
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("加班机")
        self.setMinimumSize(_PANEL_W + 40, 760)
        self.resize(_PANEL_W + 48, 780)
        self._overtime_win: OvertimeWindow | None = None
        self._open_btn: QPushButton | None = None
        self._cur_nav = 0
        self._overtime_tab_built = False
        self._overtime_win_pending = False
        self._build()
        self.setStyleSheet(self._qss())
        _theme.on_change(lambda _: (
            self.setStyleSheet(self._qss()),
            self._navigate(self._cur_nav),
            self._refresh_btn(),
        ))

    def _qss(self) -> str:
        C = _theme.get()
        return f"""
        QWidget         {{ background: {C['bg']}; color: {C['text']};
                           font-family: "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
                           font-size: 13px; }}
        #OvertimeNavBar {{ background: {C['sidebar']};
                           border-bottom: 1px solid {C['border']}; }}
        #OvertimeNavBtn {{ background: transparent; border: none;
                           border-bottom: 2px solid transparent;
                           padding: 0 16px; color: {C['text_muted']}; font-size: 13px; }}
        #OvertimeNavBtn:hover {{ background: {C['hover']}; color: {C['text']}; }}
        #OvertimeNavBtn[active=true] {{ background: transparent; color: {C['text']};
                                        font-weight: 600;
                                        border-bottom: 2px solid {C['active_line']}; }}
        #OvertimeContent {{ background: {C['bg']}; }}
        #OvertimeCard   {{ background: {C['card']}; border-radius: 10px;
                           border: 1px solid {C['border']}; }}
        #OvertimeSection {{ background: transparent; border: none; }}
        #OvertimeSectionTitle {{ background: transparent; color: {C['text']};
                                 font-size: 13px; font-weight: 600; padding: 4px 0; }}
        #OvertimeSectionBody {{ background: transparent; border: none; }}
        #OvertimeSimGift {{ background: transparent; border: none; }}
        #OvertimeSimLeft {{ background: transparent; border: none; }}
        #OvertimeSimPickBtn {{
            background: transparent; background-color: transparent;
        }}
        #OvertimeSimGiftIcon {{ background: transparent; border: none; }}
        #OvertimeGiftModule {{ background: {C['bg']};
                               border: 1px solid {C['border']}; border-radius: 4px; }}
        #OvertimeGiftModule > QWidget {{ background: transparent; border: none; }}
        #OvertimePageTitle {{ font-size: 20px; font-weight: 600; color: {C['text']}; }}
        QLabel          {{ background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 4px; }}
        QScrollBar::handle:vertical {{ background: {C['border']}; border-radius: 2px;
                                       min-height: 20px; }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{ height: 0; }}
        QLineEdit, QTextEdit {{
            background: {C['card']}; color: {C['text']};
            border: 1px solid {C['border']}; border-radius: 6px;
            padding: 4px 8px; font-size: 13px;
        }}
        QLineEdit:focus, QTextEdit:focus {{ border-color: {C['active_line']}; }}
        """

    def _make_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("OvertimeCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)
        return card

    def _build_general_panel(self, lay):
        """第一页：启动悬浮窗等通用设置。"""
        C = _theme.get()
        lay.addWidget(self._page_title("设置"))

        card = self._make_card()
        cl = card.layout()
        row = QHBoxLayout()
        lbl = QLabel("悬浮加班窗")
        lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        row.addWidget(lbl)
        row.addStretch()
        self._open_btn = QPushButton("打开加班机")
        self._open_btn.setFixedHeight(34)
        self._open_btn.setCursor(Qt.PointingHandCursor)
        self._open_btn.clicked.connect(self._toggle_overtime_win)
        row.addWidget(self._open_btn)
        cl.addLayout(row)
        desc = QLabel("透明悬浮窗，叠加在直播软件上方显示加班倒计时")
        desc.setStyleSheet(f"font-size: 12px; color: {C['text_muted']};")
        cl.addWidget(desc)
        lay.addWidget(card)

        sim_card = self._make_card()
        scl = sim_card.layout()
        sim_title = QLabel("模拟送礼")
        sim_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        scl.addWidget(sim_title)
        self._sim_widget = OvertimeSimGiftWidget()
        self._sim_widget.pushClicked.connect(self._on_sim_gift)
        scl.addWidget(self._sim_widget)
        sim_desc = QLabel("向已打开的加班机推送模拟礼物，用于本地测试规则与倒计时")
        sim_desc.setStyleSheet(f"font-size: 12px; color: {C['text_muted']};")
        scl.addWidget(sim_desc)
        lay.addWidget(sim_card)
        lay.addStretch()
        self._refresh_btn()

    def _build_overtime_panel(self, lay):
        """第二页：剩余时间、礼物规则、自定义文字。"""
        lay.addWidget(self._page_title("加班机"))
        self._settings_panel = OvertimeSettingsPanel()
        self._settings_panel.applyRequested.connect(self._on_settings_apply)
        lay.addWidget(self._settings_panel)

    @staticmethod
    def _page_title(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("OvertimePageTitle")
        return lbl

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        main_lay = QVBoxLayout(root)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        topbar = QWidget()
        topbar.setObjectName("OvertimeNavBar")
        topbar.setFixedHeight(46)
        tb_lay = QHBoxLayout(topbar)
        tb_lay.setContentsMargins(8, 0, 8, 0)
        tb_lay.setSpacing(0)

        self._stack = QStackedWidget()
        self._nav_btns: list[QPushButton] = []

        for i, name in enumerate(("设置", "加班机")):
            nav_btn = QPushButton(name)
            nav_btn.setObjectName("OvertimeNavBtn")
            nav_btn.setFixedHeight(46)
            nav_btn.setCursor(Qt.PointingHandCursor)
            nav_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            nav_btn.clicked.connect(lambda _, idx=i: self._navigate(idx))
            self._nav_btns.append(nav_btn)
            tb_lay.addWidget(nav_btn)

        # 第一页：设置（轻量，立即构建）
        inner0 = QWidget()
        lay0 = QVBoxLayout(inner0)
        lay0.setContentsMargins(12, 12, 12, 12)
        lay0.setSpacing(16)
        self._build_general_panel(lay0)
        scroll0 = QScrollArea()
        scroll0.setWidgetResizable(True)
        scroll0.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll0.setObjectName("OvertimeContent")
        scroll0.setWidget(inner0)
        self._stack.addWidget(scroll0)

        # 第二页：占位，首次进入时再构建（避免打开工具窗卡顿）
        self._overtime_placeholder = QWidget()
        ph_lay = QVBoxLayout(self._overtime_placeholder)
        ph_lay.setContentsMargins(12, 12, 12, 12)
        loading = QLabel("加载中…")
        loading.setObjectName("OvertimePageTitle")
        ph_lay.addWidget(loading)
        ph_lay.addStretch()
        self._stack.addWidget(self._overtime_placeholder)

        tb_lay.addStretch()
        main_lay.addWidget(topbar)
        main_lay.addWidget(self._stack)

        self._navigate(0)
        self._refresh_btn()

    def _ensure_overtime_tab(self):
        if self._overtime_tab_built:
            return
        self._overtime_tab_built = True
        idx = self._stack.indexOf(self._overtime_placeholder)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(12, 12, 12, 12)
        inner_lay.setSpacing(16)
        self._build_overtime_panel(inner_lay)
        inner_lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("OvertimeContent")
        scroll.setWidget(inner)
        self._stack.removeWidget(self._overtime_placeholder)
        self._overtime_placeholder.deleteLater()
        self._overtime_placeholder = None
        self._stack.insertWidget(idx, scroll)

    def _navigate(self, index: int):
        self._cur_nav = index
        if index == 1 and not self._overtime_tab_built:
            self._stack.setCurrentIndex(1)
            QTimer.singleShot(0, self._deferred_overtime_tab)
        else:
            self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_btns):
            btn.setProperty("active", i == index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _deferred_overtime_tab(self):
        self._ensure_overtime_tab()
        if self._cur_nav == 1:
            self._stack.setCurrentIndex(1)

    def _on_settings_apply(self, data: dict):
        save_settings(data)
        if self._overtime_win is not None:
            self._overtime_win.apply_settings(data)

    def _on_sim_gift(self, msg):
        from listener.models import GiftMessage
        if not isinstance(msg, GiftMessage):
            return
        self.process_message(msg)

    def _toggle_overtime_win(self):
        if self._overtime_win_pending:
            return
        if self._overtime_win is None:
            self._overtime_win_pending = True
            if self._open_btn:
                self._open_btn.setEnabled(False)
                self._open_btn.setText("打开中…")
            QTimer.singleShot(0, self._create_overtime_win)
            return

        if self._overtime_win.isVisible():
            self._overtime_win.hide()
        else:
            self._overtime_win.apply_settings(load_settings())
            self._overtime_win.show()
            self._overtime_win.activateWindow()

        self._refresh_btn()

    def _create_overtime_win(self):
        self._overtime_win_pending = False
        try:
            if self._overtime_win is None:
                self._overtime_win = OvertimeWindow()
                self._overtime_win.closed.connect(self._refresh_btn)
            self._overtime_win.apply_settings(load_settings())
            self._overtime_win.show()
            self._overtime_win.activateWindow()
        finally:
            self._refresh_btn()

    def _refresh_btn(self):
        is_open = (
            self._overtime_win is not None and self._overtime_win.isVisible()
        )
        if getattr(self, "_sim_widget", None):
            self._sim_widget.set_push_enabled(is_open and not self._overtime_win_pending)
        if not self._open_btn:
            return
        C = _theme.get()
        if self._overtime_win_pending:
            self._open_btn.setText("打开中…")
            self._open_btn.setEnabled(False)
            return
        self._open_btn.setText("关闭加班机" if is_open else "打开加班机")
        if is_open:
            self._open_btn.setStyleSheet("""
                QPushButton {
                    background: #D20F39; color: #fff;
                    border: 1.5px solid transparent;
                    border-radius: 8px; font-size: 13px; font-weight: 600;
                    padding: 0 16px;
                }
                QPushButton:hover { background: #B01030; }
            """)
        else:
            self._open_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C['card']}; color: {C['active_line']};
                    border: 1.5px solid {C['active_line']};
                    border-radius: 8px; font-size: 13px; font-weight: 600;
                    padding: 0 16px;
                }}
                QPushButton:hover {{ background: {C['hover']}; }}
            """)

    def toggle_overtime_window(self):
        self._toggle_overtime_win()

    def overtime_window_active(self) -> bool:
        return (
            self._overtime_win is not None and self._overtime_win.isVisible()
        )

    def process_message(self, msg):
        from listener.models import GiftMessage
        if not isinstance(msg, GiftMessage):
            return
        win = self._overtime_win
        if win is None or not win.isVisible():
            return
        win.handle_gift(msg)

    def closeEvent(self, event):
        from tools.tool_common import is_app_shutting_down
        if is_app_shutting_down():
            event.accept()
            return
        event.ignore()
        self.hide()


def _tray_register():
    from tools.tray_registry import register_tray, TrayAction

    def _is_active() -> bool:
        return OvertimeTool().overtime_window_active()

    def _toggle():
        inst = OvertimeTool()
        if _is_active():
            if inst._overtime_win:
                inst._overtime_win.hide()
            inst.hide()
        else:
            inst.toggle_overtime_window()

    def _open_settings():
        t = OvertimeTool()
        t.show()
        t.activateWindow()

    register_tray("加班机", lambda: [
        TrayAction("已关闭", lambda: None,
                   text_when_active="运行中", is_active=_is_active,
                   disabled=True),
        TrayAction("启动加班机", _toggle,
                   text_when_active="关闭加班机", is_active=_is_active),
        TrayAction("打开设置页面", _open_settings),
    ])


_tray_register()
