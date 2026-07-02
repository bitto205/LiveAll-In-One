"""页面基类与 @register 注册表。"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget


class _PageMeta:
    def __init__(self, cls: type, icon: str, name: str,
                 order: int, section: str):
        self.cls     = cls
        self.icon    = icon
        self.name    = name
        self.order   = order
        self.section = section


_REGISTRY: list[_PageMeta] = []


def register(icon: str, name: str, order: int = 99,
             section: str = "main"):
    def decorator(cls):
        _REGISTRY.append(_PageMeta(cls, icon, name, order, section))
        return cls
    return decorator


def get_pages() -> list[_PageMeta]:
    main   = sorted([p for p in _REGISTRY if p.section == "main"],
                    key=lambda p: p.order)
    bottom = sorted([p for p in _REGISTRY if p.section == "bottom"],
                    key=lambda p: p.order)
    return main + bottom


class BasePage(QWidget):
    def on_message(self, msg):
        pass

    def on_status_change(self, connected: bool):
        pass


class BaseSetting(QWidget):
    name:  str = ""
    order: int = 99

    def build_section(self, title: str):
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
