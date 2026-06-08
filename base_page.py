"""
base_page.py — 页面基类与注册表

每个 page 文件只需：
    from base_page import BasePage, register

    @register(icon="⌂", name="主页", order=0)
    class HomePage(BasePage):
        ...
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget


# ─────────────────────────────────────────────
# 注册表
# ─────────────────────────────────────────────
class _PageMeta:
    def __init__(self, cls: type, icon: str, name: str,
                 order: int, section: str):
        self.cls     = cls
        self.icon    = icon
        self.name    = name
        self.order   = order
        self.section = section   # "main" | "bottom"


_REGISTRY: list[_PageMeta] = []


def register(icon: str, name: str, order: int = 99,
             section: str = "main"):
    """
    页面注册装饰器。

    用法:
        @register(icon="⌂", name="主页", order=0)
        class HomePage(BasePage): ...

    参数:
        icon     显示在导航栏的 Unicode 图标
        name     显示在导航栏的文字
        order    排列顺序（越小越靠前）
        section  "main" = 普通导航项 | "bottom" = 固定在底部（如设置）
    """
    def decorator(cls):
        _REGISTRY.append(_PageMeta(cls, icon, name, order, section))
        return cls
    return decorator


def get_pages() -> list[_PageMeta]:
    """按 order 排序后返回所有已注册页面。"""
    main   = sorted([p for p in _REGISTRY if p.section == "main"],
                    key=lambda p: p.order)
    bottom = sorted([p for p in _REGISTRY if p.section == "bottom"],
                    key=lambda p: p.order)
    return main + bottom


# ─────────────────────────────────────────────
# 页面基类
# ─────────────────────────────────────────────
class BasePage(QWidget):
    """所有 page 继承此类，实现需要的方法即可。"""

    def on_message(self, msg):
        """收到直播消息时触发（listener 广播）。"""
        pass

    def on_status_change(self, connected: bool):
        """WebSocket 连接状态变化时触发。"""
        pass


# ─────────────────────────────────────────────
# 设置面板基类
# ─────────────────────────────────────────────
class BaseSetting(QWidget):
    """
    所有设置面板继承此类。
    name  = 在设置侧边栏显示的分类名
    order = 排列顺序
    """
    name:  str = ""
    order: int = 99

    def build_section(self, title: str):
        """
        便捷方法：创建带标题的卡片容器。
        返回 (card_widget, inner_layout)。
        """
        from PySide6.QtWidgets import QVBoxLayout, QLabel
        card = QWidget()
        card.setObjectName("SettingCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(12)
        lbl = QLabel(title)
        lbl.setObjectName("SettingCardTitle")
        lay.addWidget(lbl)
        return card, lay