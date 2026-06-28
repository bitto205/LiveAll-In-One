"""
tools/tray_registry.py — 系统托盘菜单扩展注册表

各工具通过 register_tray() 注册子菜单，
main_page.py 在构建托盘时自动将所有注册项合并进来。

用法（在工具模块末尾调用）：
    from tools.tray_registry import register_tray, TrayAction

    register_tray("工具名", lambda: [
        TrayAction("已关闭", lambda: None,
                   text_when_active="运行中", is_active=lambda: ..., disabled=True),
        TrayAction("启动XXX", callback_start,
                   text_when_active="关闭XXX", is_active=lambda: ...),
        TrayAction("退出XXX", callback_quit),
    ])
"""
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class TrayAction:
    text: str                                       # 默认显示文字
    callback: Callable                              # 点击回调
    text_when_active: str = ""                      # is_active()=True 时的文字
    is_active: Callable[[], bool] | None = None     # 返回当前是否"激活"
    disabled: bool = False                          # True = 不可点击（状态文字用）


@dataclass
class TrayEntry:
    name: str                                       # 子菜单名称
    get_actions: Callable[[], list[TrayAction]]     # 每次显示前调用，返回最新列表


_TRAY_REGISTRY: list[TrayEntry] = []


def register_tray(name: str, get_actions: Callable[[], list[TrayAction]]):
    """注册一个托盘子菜单。"""
    _TRAY_REGISTRY.append(TrayEntry(name=name, get_actions=get_actions))
