import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QScrollArea, QWidget
)
from PySide6.QtCore import Qt

import theme as _theme
from base_page import BasePage, BaseSetting, register


@register(icon="⚒", name="工具", order=1)
class ToolsPage(BasePage):
    def __init__(self):
        super().__init__()
        self._open_wins: dict[str, object] = {}   # name → window
        self._build()
        _theme.on_change(lambda _: self._rebuild())

    def _build(self):
        import tools as _tools

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(32, 32, 32, 32)
        lay.setSpacing(16)

        title = QLabel("工具")
        title.setObjectName("PageTitle")
        sub = QLabel("直播辅助工具集")
        sub.setObjectName("PageSubtitle")
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(8)

        # 从注册表读取，自动生成卡片
        for meta in _tools.get_tools():
            lay.addWidget(self._make_card(meta))

        lay.addStretch()
        scroll.setWidget(inner)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _make_card(self, meta) -> QFrame:
        C = _theme.get()
        card = QFrame()
        card.setObjectName("Card")
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(20, 16, 20, 16)
        c_lay.setSpacing(8)

        row = QHBoxLayout()
        name_lbl = QLabel(f"{meta.icon}  {meta.name}")
        name_lbl.setStyleSheet(
            f"background:transparent; font-size:15px; font-weight:600;"
            f" color:{C['text']};"
        )
        open_btn = QPushButton("打开")
        open_btn.setFixedHeight(34)
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{C["active_line"]};
                border:1.5px solid {C["active_line"]}; border-radius:8px;
                font-size:13px; font-weight:600; padding:0 16px;
            }}
            QPushButton:hover {{ background:{C["hover"]};
                                 border:1.5px solid {C["active_line"]}; }}
        """)
        open_btn.clicked.connect(lambda _, m=meta: self._open_tool(m))
        row.addWidget(name_lbl)
        row.addStretch()
        row.addWidget(open_btn)
        c_lay.addLayout(row)

        if meta.desc:
            desc = QLabel(meta.desc)
            desc.setStyleSheet(
                f"background:transparent; font-size:12px; color:{C['text_muted']};"
            )
            c_lay.addWidget(desc)

        return card

    def _open_tool(self, meta):
        name = meta.name
        win  = self._open_wins.get(name)
        if win is None or not win.isVisible():
            win = meta.cls()
            self._open_wins[name] = win
        win.show()
        win.raise_()
        win.activateWindow()

    def _rebuild(self):
        """主题切换时重建 UI。"""
        # 清除旧布局
        while self.layout().count():
            item = self.layout().takeAt(0)
            if w := item.widget():
                w.deleteLater()
        self._build()

    # ── 消息转发给所有已打开的工具 ──────────────
    def on_message(self, msg):
        for name, win in self._open_wins.items():
            if win and win.isVisible() and hasattr(win, "process_message"):
                try:
                    win.process_message(msg)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(
                        f"[ToolsPage] {name}.process_message 异常: {e}", exc_info=True
                    )


class ToolsSettings(BaseSetting):
    name  = "工具设置"
    order = 20

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)
        t = QLabel("工具设置")
        t.setObjectName("SettingPageTitle")
        lay.addWidget(t)
        card, inner = self.build_section("备忘录")
        inner.addWidget(QLabel("详细设置请在工具内调整"))
        lay.addWidget(card)
        lay.addStretch()