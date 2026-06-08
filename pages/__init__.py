"""
pages/__init__.py — 唯一的注册表

新增 page：在这里 import 并加入 PAGES（如需）和 SETTINGS（如需设置面板）。
main_page.py 和 settings_page.py 都只从这里取数据，不直接 import 具体文件。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 页面注册（触发 @register）──
from pages.home_page     import HomePage
from pages.tools_page    import ToolsPage, ToolsSettings
from pages.settings_page import SettingsPage, SystemSettings

# ── main_page.py 读这个 ──
SETTINGS_PAGE = SettingsPage

# ── settings_page.py 读这个：按 order 排列所有设置面板 ──
SETTINGS: list[type] = sorted(
    [SystemSettings, ToolsSettings],
    key=lambda cls: cls.order,
)

__all__ = [
    "HomePage", "ToolsPage",
    "SettingsPage", "SETTINGS_PAGE", "SETTINGS",
]