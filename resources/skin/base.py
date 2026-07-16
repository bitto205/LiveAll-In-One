"""皮肤类型与抽象接口。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class SkinMetrics:
    fade_w: int = 22
    pad_h: int = 12
    pad_v: int = 7
    gift_icon_size: int = 32
    gift_icon_gap: int = 8
    extra: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RoleTextStyle:
    """单段文字的字体、对齐、折行等，由皮肤 JSON 定义。"""

    font_family: str = "Microsoft YaHei"
    pixel_size: int = 13
    bold: bool = False
    align: str = "center"
    v_align: str = "vcenter"
    word_wrap: bool = False
    min_px: int = 8
    max_px: int = 13
    fit_ref: str = ""
    style_extra: str = ""
    min_chars: int = 4
    max_chars: int = 16
    slack_px: int = 2

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> RoleTextStyle:
        if not raw:
            return cls()
        return cls(
            font_family=str(raw.get("font_family") or "Microsoft YaHei"),
            pixel_size=int(raw.get("pixel_size") or 13),
            bold=bool(raw.get("bold")),
            align=str(raw.get("align") or "center"),
            v_align=str(raw.get("v_align") or "vcenter"),
            word_wrap=bool(raw.get("word_wrap")),
            min_px=int(raw.get("min_px") or 8),
            max_px=int(raw.get("max_px") or raw.get("pixel_size") or 13),
            fit_ref=str(raw.get("fit_ref") or ""),
            style_extra=str(raw.get("style_extra") or ""),
            min_chars=int(raw.get("min_chars") or 4),
            max_chars=int(raw.get("max_chars") or 16),
            slack_px=int(raw.get("slack_px") or 2),
        )


def qt_alignment(align: str, *, v_align: str = "vcenter") -> int:
    h_map = {
        "left": Qt.AlignLeft,
        "center": Qt.AlignHCenter,
        "right": Qt.AlignRight,
    }
    v_map = {
        "top": Qt.AlignTop,
        "vcenter": Qt.AlignVCenter,
        "bottom": Qt.AlignBottom,
    }
    return int(h_map.get(align, Qt.AlignHCenter) | v_map.get(v_align, Qt.AlignVCenter))


def make_font(style: RoleTextStyle, *, pixel_size: int | None = None) -> QFont:
    f = QFont(style.font_family)
    f.setPixelSize(pixel_size if pixel_size is not None else style.pixel_size)
    if style.bold:
        f.setWeight(QFont.Bold)
    return f


def fit_font_pixel_size(
    text: str,
    max_w: int,
    max_h: int,
    *,
    font_family: str = "Microsoft YaHei",
    bold: bool = False,
    max_px: int | None = None,
    min_px: int = 8,
    edge_pad: int = 1,
) -> int:
    max_w = max(1, max_w - edge_pad * 2)
    max_h = max(1, max_h - edge_pad * 2)
    lo, hi = min_px, max(8, max_h)
    if max_px is not None:
        hi = min(hi, max_px)
    if lo > hi:
        return max(min_px, hi)
    best = lo
    sample = text or "国"
    while lo <= hi:
        mid = (lo + hi) // 2
        f = QFont(font_family)
        f.setPixelSize(mid)
        if bold:
            f.setWeight(QFont.Bold)
        fm = QFontMetrics(f)
        if fm.horizontalAdvance(sample) <= max_w and fm.height() <= max_h:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


class ToolSkin:
    """单个工具的一套皮肤。不同 tool_id 的皮肤不可互换。"""

    tool_id: str = ""
    skin_id: str = "default"
    name: str = "默认"
    version: int = 1

    def __init__(self, root: Path, meta: dict[str, Any]):
        self.root = root
        self.meta = meta
        self.skin_id = str(meta.get("id") or root.name)
        self.name = str(meta.get("name") or self.skin_id)
        self.version = int(meta.get("version") or 1)
        tool = meta.get("tool")
        if tool:
            self.tool_id = str(tool)

    def metrics(self) -> SkinMetrics:
        m = self.meta.get("metrics") or {}
        return SkinMetrics(
            fade_w=int(m.get("fade_w", 22)),
            pad_h=int(m.get("pad_h", 12)),
            pad_v=int(m.get("pad_v", 7)),
            gift_icon_size=int(m.get("gift_icon_size", 32)),
            gift_icon_gap=int(m.get("gift_icon_gap", 8)),
            extra=m.get("extra"),
        )

    def surface_layout(self, surface: str) -> dict[str, Any]:
        layouts = self.meta.get("layouts") or {}
        raw = layouts.get(surface)
        return dict(raw) if isinstance(raw, dict) else {}

    def role_style(self, surface: str, role: str) -> RoleTextStyle:
        surf = self.surface_layout(surface)
        roles = surf.get("roles") or {}
        raw = roles.get(role) if isinstance(roles, dict) else None
        return RoleTextStyle.from_dict(raw if isinstance(raw, dict) else None)

    def layout_value(self, surface: str, key: str, default: Any = None) -> Any:
        return self.surface_layout(surface).get(key, default)

    def fit_role_pixel_size(
        self,
        surface: str,
        role: str,
        text: str,
        max_w: int,
        max_h: int,
        *,
        scale: float = 1.0,
        edge_pad: int = 1,
    ) -> int:
        style = self.role_style(surface, role)
        cap = max(style.min_px, int(round(style.max_px * scale)))
        sample = text or style.fit_ref or "国"
        return fit_font_pixel_size(
            sample,
            max_w,
            max_h,
            font_family=style.font_family,
            bold=style.bold,
            max_px=cap,
            min_px=style.min_px,
            edge_pad=edge_pad,
        )

    def supports(self, surface: str) -> bool:
        surfaces = self.meta.get("surfaces")
        if not surfaces:
            return True
        return surface in surfaces

    def color(self, key: str, default: QColor | None = None) -> QColor:
        raw = (self.meta.get("colors") or {}).get(key)
        if raw is None:
            return default or QColor(255, 255, 255)
        return _parse_color(raw, default or QColor(255, 255, 255))

    def stylesheet(self, role: str) -> str:
        styles = self.meta.get("styles") or {}
        if role in styles:
            return str(styles[role])
        c = self.color(role, QColor(255, 255, 255))
        return (
            f"color: rgba({c.red()},{c.green()},{c.blue()},{c.alpha()});"
            " background: transparent;"
        )

    def paint_surface(self, widget: QWidget, surface: str, painter: QPainter) -> None:
        return

    def paint_text_shadow_style(self) -> tuple[QColor, QColor]:
        colors = self.meta.get("colors") or {}
        fill = _parse_color(colors.get("text_fill"), QColor(255, 255, 255))
        shadow = _parse_color(colors.get("text_shadow"), QColor(0, 0, 0, 160))
        return fill, shadow


def _parse_color(raw: Any, default: QColor) -> QColor:
    if raw is None:
        return QColor(default)
    if isinstance(raw, str):
        c = QColor(raw)
        return c if c.isValid() else QColor(default)
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        a = int(raw[3]) if len(raw) > 3 else 255
        return QColor(int(raw[0]), int(raw[1]), int(raw[2]), a)
    if isinstance(raw, dict):
        return QColor(
            int(raw.get("r", 255)),
            int(raw.get("g", 255)),
            int(raw.get("b", 255)),
            int(raw.get("a", 255)),
        )
    return QColor(default)
