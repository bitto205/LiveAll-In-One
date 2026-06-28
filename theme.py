"""
theme.py — 全局色彩方案

五套专业级配色，均来自主流设计社区：
  · Catppuccin Latte  — github.com/catppuccin（官方浅色）
  · Catppuccin Mocha  — github.com/catppuccin（官方深色）
  · Nord Light        — nordtheme.com（极地浅色）
  · Nord Dark         — nordtheme.com（极地深色）
  · Solarized         — ethanschoonover.com/solarized（经典护眼浅色）

切换主题：theme.set_theme(name) → 触发所有 on_change 回调
读取颜色：theme.get()[key]
"""
from __future__ import annotations

# ─────────────────────────────────────────────
# 方案定义
# ─────────────────────────────────────────────
THEMES: dict[str, dict] = {

    # ── 拿铁奶咖（Catppuccin Latte）────────────
    "拿铁奶咖": {
        "bg":          "#eff1f5",
        "sidebar":     "#e6e9ef",
        "card":        "#ffffff",
        "hover":       "#dce0e8",
        "active":      "#ccd0da",
        "active_line": "#1e66f5",
        "text":        "#4c4f69",
        "text_muted":  "#5c5f77",   # subtext1，对比度 5.2:1（原 subtext0 4.1:1）
        "border":      "#ccd0da",
        "win_edge":    "#bcc0cc",
        "close_hover": "#d20f39",
        "btn_hover":   "#ccd0da",
    },

    # ── 深焙摩卡（Catppuccin Mocha）────────────
    "深焙摩卡": {
        "bg":          "#1e1e2e",
        "sidebar":     "#181825",
        "card":        "#313244",
        "hover":       "#45475a",
        "active":      "#585b70",
        "active_line": "#89b4fa",
        "text":        "#cdd6f4",
        "text_muted":  "#a6adc8",
        "border":      "#313244",
        "win_edge":    "#11111b",
        "close_hover": "#f38ba8",
        "btn_hover":   "#45475a",
    },

    # ── 极夜深蓝（Nord Dark）──────────────────
    "极夜深蓝": {
        "bg":          "#2e3440",
        "sidebar":     "#252b35",
        "card":        "#3b4252",
        "hover":       "#434c5e",
        "active":      "#4c566a",
        "active_line": "#88c0d0",
        "text":        "#eceff4",
        "text_muted":  "#d8dee9",
        "border":      "#434c5e",
        "win_edge":    "#1d2430",
        "close_hover": "#bf616a",
        "btn_hover":   "#434c5e",
    },

    # ── 日晷护眼（Solarized Light）────────────
    "日晷护眼": {
        "bg":          "#fdf6e3",
        "sidebar":     "#eee8d5",
        "card":        "#ffffff",
        "hover":       "#e5dfc8",
        "active":      "#d9d2b5",
        "active_line": "#268bd2",
        "text":        "#586e75",
        "text_muted":  "#657b83",   # base00，对比度 3.9:1（原 base1 仅 2.4:1）
        "border":      "#e2dcc8",
        "win_edge":    "#d9d2b5",
        "close_hover": "#dc322f",
        "btn_hover":   "#e2dcc8",
    },
}

# ─────────────────────────────────────────────
# 运行时状态
# ─────────────────────────────────────────────
def _load_saved() -> str:
    try:
        import config
        name = config.get("theme")
        if name in THEMES:
            return name
    except Exception:
        pass
    return "拿铁奶咖"


_current: str = _load_saved()
_callbacks: list = []


def get() -> dict:
    return THEMES[_current]


def current_name() -> str:
    return _current


def names() -> list[str]:
    return list(THEMES.keys())


def on_change(callback):
    _callbacks.append(callback)


def set_theme(name: str):
    global _current
    if name not in THEMES:
        return
    _current = name
    try:
        import config
        config.set("theme", name)
    except Exception:
        pass
    for cb in _callbacks:
        try:
            cb(name)
        except Exception:
            pass