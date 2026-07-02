"""
tools/memo_tool.py — 备忘录工具

从 ToolsPage 点击打开，独立窗口。
顶部 Tab 切换：主页面 / 设置
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QFrame,
    QLineEdit, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui  import QColor

import theme as _theme
import config as _cfg
from widgets import ThemedToggle
from models import GiftMessage, FollowMessage, LikeMessage, FansclubMessage


# ─────────────────────────────────────────────
# 配置键前缀
# ─────────────────────────────────────────────
_K = {
    "gift_on":       "memo.gift.enabled",
    "gift_stack":    "memo.gift.stack",
    "gift_min_dia":  "memo.gift.min_diamonds",
    "follow_on":     "memo.follow.enabled",
    "like_on":       "memo.like.enabled",
    "like_stack":    "memo.like.stack",
}


# ─────────────────────────────────────────────
# 样式辅助
# ─────────────────────────────────────────────
def _C():
    return _theme.get()

def _qss():
    C = _C()
    return f"""
    QWidget {{ background: transparent; color: {C["text"]};
               font-family: "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
               font-size: 13px; }}
    #MemoRoot  {{ background: {C["bg"]}; }}
    #TopBar    {{ background: {C["sidebar"]}; border-bottom: 1px solid {C["border"]}; }}
    #TabBtn    {{ background: transparent; border: none;
                  border-bottom: 2px solid transparent;
                  padding: 0 16px; color: {C["text_muted"]}; font-size: 13px; }}
    #TabBtn:hover {{ background: {C["hover"]}; }}
    #TabBtn[active=true] {{ color: {C["text"]}; font-weight:600;
                            border-bottom: 2px solid {C["active_line"]}; }}
    #Card {{ background: {C["card"]}; border-radius: 10px;
             border: 1px solid {C["border"]}; }}
    #SectionTitle {{ font-size: 13px; font-weight:600; color: {C["text_muted"]}; }}
    QLineEdit {{ background: {C["card"]}; color: {C["text"]};
                 border: 1px solid {C["border"]}; border-radius: 6px;
                 padding: 0 10px; font-size: 13px; }}
    QLineEdit:focus {{ border-color: {C["active_line"]}; }}
    QScrollBar:vertical {{ background:transparent; width:4px; }}
    QScrollBar::handle:vertical {{ background:{C["border"]}; border-radius:2px; }}
    QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{ height:0; }}
    """


# _Toggle 已移至 widgets.ThemedToggle，下面直接使用


def _gift_diamonds(msg: GiftMessage) -> int | None:
    from gift.gift_info import get_diamonds, all_gifts
    d = get_diamonds(msg.gift)
    if d is not None:
        return d
    if msg.gift_id:
        for info in all_gifts().values():
            if info.get("gift_id") == msg.gift_id:
                return info.get("price")
    return None


def _row(label_text: str, widget: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(label_text)
    lbl.setStyleSheet("background:transparent;")
    row.addWidget(lbl)
    row.addStretch()
    row.addWidget(widget)
    return row


# ─────────────────────────────────────────────
# 列表条目
# ─────────────────────────────────────────────
class _MemoItem(QFrame):
    """
    单条备忘录条目。点击整行消除。
    stackable=True 的条目支持 add_count() 更新数量。
    """

    def __init__(self, text: str, stackable=False,
                 count=1, fmt: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("MemoItem")
        self.setCursor(Qt.PointingHandCursor)
        self._stackable = stackable
        self._count     = count
        self._fmt       = fmt           # e.g. "[{user}] 送了 {count} 个 [{gift}]"
        self._base_text = text          # 第一次的完整文字（不带数量）

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(8)

        self._lbl = QLabel(text)
        self._lbl.setWordWrap(True)
        self._lbl.setStyleSheet("background:transparent;")
        lay.addWidget(self._lbl, stretch=1)

        x_btn = QPushButton("×")
        x_btn.setFixedSize(24, 24)
        x_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                color: {_C()["text_muted"]}; font-size: 16px;
                border-radius: 4px;
            }}
            QPushButton:hover {{ background: {_C()["hover"]}; color: {_C()["text"]}; }}
        """)
        x_btn.clicked.connect(self._dismiss)
        lay.addWidget(x_btn)

        C = _C()
        self.setStyleSheet(f"""
            #MemoItem {{
                background: {C["card"]}; border-radius: 8px;
                border: 1px solid {C["border"]};
            }}
            #MemoItem:hover {{ background: {C["hover"]}; }}
        """)

    def add_count(self, delta: int):
        """叠加数量并更新显示文字。"""
        if not self._stackable or not self._fmt:
            return
        self._count += delta
        self._lbl.setText(self._fmt.format(count=self._count))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dismiss()

    def _dismiss(self):
        if self.parent() and hasattr(self.parent(), "layout"):
            self.setParent(None)
            self.deleteLater()


# ─────────────────────────────────────────────
# 设置页
# ─────────────────────────────────────────────
class _SettingsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        # ── 礼物 ──
        lay.addWidget(self._section(
            "礼物",
            [
                ("启用", ThemedToggle(_K["gift_on"])),
                ("叠加", ThemedToggle(_K["gift_stack"])),
                ("最低钻石数", self._make_diamond_input()),
            ]
        ))

        # ── 关注 ──
        lay.addWidget(self._section("关注", [
            ("启用", ThemedToggle(_K["follow_on"])),
        ]))

        # ── 点赞 ──
        lay.addWidget(self._section("点赞", [
            ("启用", ThemedToggle(_K["like_on"])),
            ("叠加", ThemedToggle(_K["like_stack"])),
        ]))

        # ── 自定义 ──
        custom_card = QFrame()
        custom_card.setObjectName("Card")
        c_lay = QVBoxLayout(custom_card)
        c_lay.setContentsMargins(16, 14, 16, 14)
        c_lay.setSpacing(10)
        title = QLabel("自定义")
        title.setObjectName("SectionTitle")
        c_lay.addWidget(title)
        row = QHBoxLayout()
        self._custom_input = QLineEdit()
        self._custom_input.setFixedHeight(34)
        self._custom_input.setPlaceholderText("输入备忘内容...")
        self._custom_input.returnPressed.connect(self._add_custom)
        add_btn = QPushButton("添加")
        add_btn.setFixedHeight(34)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add_custom)
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {_C()["active_line"]};
                border: 1.5px solid {_C()["active_line"]}; border-radius: 6px;
                font-size: 13px; font-weight:600; padding: 0 14px;
            }}
            QPushButton:hover {{ background: {_C()["hover"]}; }}
        """)
        row.addWidget(self._custom_input)
        row.addWidget(add_btn)
        c_lay.addLayout(row)
        lay.addWidget(custom_card)
        lay.addStretch()

        scroll.setWidget(inner)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    # ── 辅助 ──────────────────────────────────
    def _section(self, title: str, rows: list) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        t = QLabel(title)
        t.setObjectName("SectionTitle")
        lay.addWidget(t)
        for label, widget in rows:
            lay.addLayout(_row(label, widget))
        return card

    def _make_diamond_input(self) -> QWidget:
        from PySide6.QtWidgets import QHBoxLayout
        wrap = QWidget()
        wrap.setStyleSheet("background:transparent;")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        inp = QLineEdit(str(_cfg.get(_K["gift_min_dia"], 0)))
        inp.setFixedSize(80, 30)
        inp.setAlignment(Qt.AlignCenter)
        inp.setPlaceholderText("0")

        hint = QLabel("0 = 不过滤")
        hint.setStyleSheet(f"background:transparent; color:{_C()['text_muted']}; font-size:11px;")

        def _save():
            try:
                val = int(inp.text().strip())
                val = max(0, val)
            except ValueError:
                val = 0
            inp.setText(str(val))
            _cfg.set(_K["gift_min_dia"], val)

        inp.editingFinished.connect(_save)
        row.addWidget(inp)
        row.addWidget(hint)
        row.addStretch()
        return wrap

    def _spin(self, attr: str, default: int) -> QLineEdit:
        w = QLineEdit(str(default))
        w.setFixedSize(72, 34)
        w.setAlignment(Qt.AlignCenter)
        setattr(self, f"_{attr}", w)
        return w

    def _add_custom(self):
        text = self._custom_input.text().strip()
        if not text:
            return
        # 把自定义文字传给主窗口
        win = self.window()
        if hasattr(win, "_add_item"):
            win._add_item("custom", text)
        self._custom_input.clear()




# ─────────────────────────────────────────────
# 主页面（备忘录列表）
# ─────────────────────────────────────────────
class _MainTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)

        # 顶部操作栏
        top = QHBoxLayout()
        clear_btn = QPushButton("清空全部")
        clear_btn.setFixedHeight(30)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {_C()["text_muted"]};
                border: 1px solid {_C()["border"]}; border-radius: 6px;
                font-size: 12px; padding: 0 12px;
            }}
            QPushButton:hover {{ color: #D20F39; border-color: #D20F39; }}
        """)
        clear_btn.clicked.connect(self.clear_all)
        top.addStretch()
        top.addWidget(clear_btn)
        lay.addLayout(top)

        # 列表区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")

        self._container = QWidget()
        self._list_lay  = QVBoxLayout(self._container)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(6)
        self._list_lay.addStretch()

        self._scroll.setWidget(self._container)
        lay.addWidget(self._scroll)

    def add_item(self, item: _MemoItem):
        """将条目插入到列表顶部（stretch 之前）。"""
        count = self._list_lay.count()
        self._list_lay.insertWidget(count - 1, item)
        # 滚动到顶部
        self._scroll.verticalScrollBar().setValue(0)

    def clear_all(self):
        while self._list_lay.count() > 1:   # 保留最后的 stretch
            item = self._list_lay.takeAt(0)
            if w := item.widget():
                w.deleteLater()


# ─────────────────────────────────────────────
# 主窗口
# ─────────────────────────────────────────────
from tools import register_tool


@register_tool(name="备忘录", desc="将礼物、关注、点赞记录为可消除的列表条目",
               icon="📋", order=0)
class MemoTool(QMainWindow):
    """
    备忘录工具窗口。
    外部调用 process_message(msg) 传入直播消息。
    """

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("备忘录")
        self.setMinimumSize(380, 560)
        self.resize(420, 620)

        # key → _MemoItem，用于叠加查找
        self._item_map: dict[str, _MemoItem] = {}

        self._build()
        self.setStyleSheet(_qss())
        _theme.on_change(lambda _: self.setStyleSheet(_qss()))

    # ── UI 构建 ───────────────────────────────
    def _build(self):
        root = QWidget()
        root.setObjectName("MemoRoot")
        self.setCentralWidget(root)

        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 顶部 Tab 栏
        topbar = QWidget()
        topbar.setObjectName("TopBar")
        topbar.setFixedHeight(44)
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(8, 0, 8, 0)
        tb.setSpacing(0)

        self._tabs = []
        for i, name in enumerate(["主页面", "设置"]):
            btn = QPushButton(name)
            btn.setObjectName("TabBtn")
            btn.setFixedHeight(44)
            btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, idx=i: self._switch(idx))
            self._tabs.append(btn)
            tb.addWidget(btn)
        tb.addStretch()
        lay.addWidget(topbar)

        # 内容区
        from PySide6.QtWidgets import QStackedWidget
        self._stack = QStackedWidget()
        self._main_tab     = _MainTab()
        self._settings_tab = _SettingsTab()
        self._stack.addWidget(self._main_tab)
        self._stack.addWidget(self._settings_tab)
        lay.addWidget(self._stack)

        self._switch(0)

    def _switch(self, idx: int):
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._tabs):
            btn.setProperty("active", i == idx)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ── 消息处理 ──────────────────────────────
    def process_message(self, msg):
        if isinstance(msg, GiftMessage):
            self._handle_gift(msg)
        elif isinstance(msg, FollowMessage):
            self._handle_follow(msg)
        elif isinstance(msg, LikeMessage):
            self._handle_like(msg)
        elif isinstance(msg, FansclubMessage):
            self._handle_fansclub(msg)

    def _handle_gift(self, msg):
        if not _cfg.get(_K["gift_on"], True):
            return

        # 钻石过滤
        min_dia = _cfg.get(_K["gift_min_dia"], 0)
        if min_dia > 0:
            diamonds = _gift_diamonds(msg)
            if diamonds is not None and diamonds < min_dia:
                return

        stack = _cfg.get(_K["gift_stack"], True)
        key   = f"gift:{msg.user}:{msg.gift}"
        fmt   = f"[{msg.user}] 送了 {{count}} 个 {msg.gift}"
        text  = fmt.format(count=msg.count)

        if stack and key in self._item_map:
            w = self._item_map[key]
            if w.parent():
                w.add_count(msg.count)
                return
            else:
                del self._item_map[key]

        item = _MemoItem(text, stackable=stack, count=msg.count, fmt=fmt)
        self._item_map[key] = item
        self._main_tab.add_item(item)

    def _handle_follow(self, msg):
        if not _cfg.get(_K["follow_on"], True):
            return
        text = f"[{msg.user}] 关注了"
        self._main_tab.add_item(_MemoItem(text))

    def _handle_fansclub(self, msg):
        if not _cfg.get(_K["follow_on"], True):   # 和关注共用同一开关
            return
        text = f"[{msg.user}] 加入了粉丝团"
        self._main_tab.add_item(_MemoItem(text))

    def _handle_like(self, msg):
        if not _cfg.get(_K["like_on"], True):
            return
        stack = _cfg.get(_K["like_stack"], True)
        key   = f"like:{msg.user}"
        fmt   = f"[{msg.user}] 点了 {{count}} 个赞"
        text  = fmt.format(count=msg.count)

        if stack and key in self._item_map:
            w = self._item_map[key]
            if w.parent():
                w.add_count(msg.count)
                return
            else:
                del self._item_map[key]

        item = _MemoItem(text, stackable=stack, count=msg.count, fmt=fmt)
        self._item_map[key] = item
        self._main_tab.add_item(item)

    def _add_item(self, key: str, text: str):
        """自定义条目（由 _SettingsTab 调用）。"""
        item = _MemoItem(text)
        if key != "custom":
            self._item_map[key] = item
        self._main_tab.add_item(item)
        # 自动切换到主页面
        self._switch(0)