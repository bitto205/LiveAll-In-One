"""
theme.py — 全局色彩方案（4 套主题，config 键 theme）
"""
from __future__ import annotations

THEMES: dict[str, dict] = {

    "拿铁奶咖": {
        "bg":          "#eff1f5",
        "sidebar":     "#e6e9ef",
        "card":        "#ffffff",
        "hover":       "#dce0e8",
        "active":      "#ccd0da",
        "active_line": "#1e66f5",
        "text":        "#4c4f69",
        "text_muted":  "#5c5f77",
        "border":      "#ccd0da",
        "win_edge":    "#bcc0cc",
        "close_hover": "#d20f39",
        "btn_hover":   "#ccd0da",
    },

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

    "日晷护眼": {
        "bg":          "#fdf6e3",
        "sidebar":     "#eee8d5",
        "card":        "#ffffff",
        "hover":       "#e5dfc8",
        "active":      "#d9d2b5",
        "active_line": "#268bd2",
        "text":        "#586e75",
        "text_muted":  "#657b83",
        "border":      "#e2dcc8",
        "win_edge":    "#d9d2b5",
        "close_hover": "#dc322f",
        "btn_hover":   "#e2dcc8",
    },
}

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


def qss_outlined(C: dict | None = None, h: int = 36) -> str:
    C = C or get()
    return f"""
        QPushButton {{
            background: {C["card"]};
            color: {C["active_line"]};
            border: 1.5px solid {C["active_line"]};
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            min-height: {h}px;
            padding: 0 16px;
        }}
        QPushButton:hover {{
            background: {C["hover"]};
            border: 1.5px solid {C["active_line"]};
        }}
    """


def qss_disabled(C: dict | None = None, h: int = 36) -> str:
    C = C or get()
    return f"""
        QPushButton {{
            background: {C["border"]};
            color: {C["text_muted"]};
            border: none;
            border-radius: 8px;
            font-size: 13px;
            min-height: {h}px;
            padding: 0 16px;
        }}
    """


def qss_success(C: dict | None = None, h: int = 36) -> str:
    C = C or get()
    return f"""
        QPushButton {{
            background: {C["active_line"]};
            color: #ffffff;
            border: none;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            min-height: {h}px;
            padding: 0 16px;
        }}
    """


def qss_danger(C: dict | None = None, h: int = 36) -> str:
    C = C or get()
    return f"""
        QPushButton {{
            background: {C["close_hover"]};
            color: #ffffff;
            border: none;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            min-height: {h}px;
            padding: 0 16px;
        }}
        QPushButton:hover {{ background: {C["active"]}; }}
    """


def qss_back(C: dict | None = None) -> str:
    C = C or get()
    return f"""
        QPushButton {{
            background: transparent;
            color: {C["text_muted"]};
            border: none;
            font-size: 13px;
            padding: 4px 8px;
        }}
        QPushButton:hover {{
            color: {C["active_line"]};
        }}
    """


def qss_muted_label(C: dict | None = None, size: int = 13) -> str:
    C = C or get()
    return (
        f"background: transparent; font-size: {size}px;"
        f" color: {C['text_muted']};"
    )


def qss_error_label(C: dict | None = None, size: int = 13) -> str:
    C = C or get()
    return (
        f"background: transparent; font-size: {size}px;"
        f" color: {C['close_hover']};"
    )


def qss_accent_label(C: dict | None = None, size: int = 13) -> str:
    C = C or get()
    return (
        f"background: transparent; font-size: {size}px;"
        f" color: {C['active_line']};"
    )
