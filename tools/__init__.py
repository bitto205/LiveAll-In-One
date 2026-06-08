"""
tools/__init__.py — 工具注册表

新增工具只需两步：
  1. 在 tools/ 下新建 xxx_tool.py，用 @register_tool 装饰工具类
  2. 在这里加一行 import

tools_page.py 读 get_tools() 自动渲染工具卡片，不需要改其他任何地方。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────
# 注册表
# ─────────────────────────────────────────────
class _ToolMeta:
    def __init__(self, cls: type, name: str, desc: str,
                 icon: str, order: int):
        self.cls   = cls
        self.name  = name
        self.desc  = desc
        self.icon  = icon
        self.order = order


_REGISTRY: list[_ToolMeta] = []


def register_tool(name: str, desc: str = "",
                  icon: str = "🔧", order: int = 99):
    """
    工具注册装饰器。

    用法::

        @register_tool(name="备忘录", desc="记录直播事件", icon="📋", order=0)
        class MemoTool(QMainWindow): ...
    """
    def decorator(cls):
        _REGISTRY.append(_ToolMeta(cls, name, desc, icon, order))
        return cls
    return decorator


def get_tools() -> list[_ToolMeta]:
    """按 order 排序后返回所有已注册工具。"""
    return sorted(_REGISTRY, key=lambda t: t.order)


# ─────────────────────────────────────────────
# 注册所有工具（在这里 import 触发 @register_tool）
# ─────────────────────────────────────────────
from tools.memo_tool import MemoTool   # noqa  触发装饰器

__all__ = ["MemoTool", "get_tools", "register_tool"]