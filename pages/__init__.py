"""
pages/__init__.py — 页面与设置面板注册入口
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pages.home_page     import HomePage
from pages.tools_page    import ToolsPage, ToolsSettings
from pages.settings_page import SettingsPage, SystemSettings

SETTINGS_PAGE = SettingsPage

SETTINGS: list[type] = sorted(
    [SystemSettings, ToolsSettings],
    key=lambda cls: cls.order,
)

__all__ = [
    "HomePage", "ToolsPage",
    "SettingsPage", "SETTINGS_PAGE", "SETTINGS",
]