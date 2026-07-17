"""皮肤媒体：静图 / 动画 webp 加载与播放（Pillow 逐帧 + DPR + UI 缩放）。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

ScaleMode = Literal["nearest", "smooth"]

# (path, logical_h, dpr_key, scale_mode, animated) → LoadedStill | LoadedAnim
_CACHE: dict[tuple, object] = {}


@dataclass(frozen=True)
class SkinImageSpec:
    file: str = ""
    logical_h: int = 16
    scale: ScaleMode | None = None  # None → 用皮肤 images_default
    animated: bool = False

    @classmethod
    def from_dict(cls, raw: dict | None) -> SkinImageSpec:
        if not raw:
            return cls()
        scale_raw = raw.get("scale")
        scale: ScaleMode | None = None
        if scale_raw is not None:
            s = str(scale_raw).lower()
            scale = "nearest" if s == "nearest" else "smooth"
        return cls(
            file=str(raw.get("file") or ""),
            logical_h=max(1, int(raw.get("logical_h") or 16)),
            scale=scale,
            animated=bool(raw.get("animated")),
        )


@dataclass
class LoadedStill:
    pixmap: QPixmap
    logical_w: int
    logical_h: int


@dataclass
class LoadedAnim:
    frames: list[QPixmap]
    delays: list[int]
    logical_w: int
    logical_h: int


def screen_dpr() -> float:
    scr = QApplication.primaryScreen()
    return float(scr.devicePixelRatio()) if scr else 1.0


def clear_media_cache() -> None:
    _CACHE.clear()


def _resample(scale: ScaleMode):
    from PIL import Image
    if scale == "nearest":
        return Image.Resampling.NEAREST
    return Image.Resampling.BILINEAR


def _dpr_key(dpr: float) -> float:
    return round(float(dpr), 3)


def _pil_rgba_to_pixmap(fr, dpr: float) -> QPixmap:
    """Pillow RGBA → QPixmap；显式 stride，避免 webp 行对齐问题。"""
    pw, ph = fr.size
    buf = fr.tobytes("raw", "RGBA")
    qimg = QImage(buf, pw, ph, pw * 4, QImage.Format.Format_RGBA8888).copy()
    pm = QPixmap.fromImage(qimg)
    pm.setDevicePixelRatio(float(dpr))
    return pm


def load_anim(
    path: str | Path,
    logical_h: int,
    dpr: float | None = None,
    scale: ScaleMode = "nearest",
    *,
    ui_scale: float = 1.0,
    use_cache: bool = True,
) -> LoadedAnim:
    """Pillow 逐帧解码 → 逻辑尺寸×ui_scale×DPR → QPixmap(devicePixelRatio)。"""
    path = str(path)
    dpr = float(dpr if dpr is not None else screen_dpr())
    lh = max(1, int(round(max(1, int(logical_h)) * max(0.25, float(ui_scale)))))
    key = (path, lh, _dpr_key(dpr), scale, True)
    if use_cache and key in _CACHE:
        cached = _CACHE[key]
        if isinstance(cached, LoadedAnim):
            return cached

    if not os.path.isfile(path):
        empty = LoadedAnim([], [100], lh, lh)
        if use_cache:
            _CACHE[key] = empty
        return empty

    from PIL import Image

    try:
        im = Image.open(path)
        n = max(1, int(getattr(im, "n_frames", 1) or 1))
        frames: list[QPixmap] = []
        delays: list[int] = []
        lw = lh
        resample = _resample(scale)
        for i in range(n):
            im.seek(i)
            fr = im.convert("RGBA")
            lw = max(1, round(fr.width * lh / max(1, fr.height)))
            pw = max(1, round(lw * dpr))
            ph = max(1, round(lh * dpr))
            if fr.size != (pw, ph):
                fr = fr.resize((pw, ph), resample)
            frames.append(_pil_rgba_to_pixmap(fr, dpr))
            delays.append(max(30, int(im.info.get("duration", 100) or 100)))
        anim = LoadedAnim(frames, delays, lw, lh)
    except Exception:
        anim = LoadedAnim([], [100], lh, lh)

    if use_cache:
        _CACHE[key] = anim
    return anim


def load_still(
    path: str | Path,
    logical_h: int,
    dpr: float | None = None,
    scale: ScaleMode = "smooth",
    *,
    ui_scale: float = 1.0,
    use_cache: bool = True,
) -> LoadedStill:
    path = str(path)
    dpr = float(dpr if dpr is not None else screen_dpr())
    lh = max(1, int(round(max(1, int(logical_h)) * max(0.25, float(ui_scale)))))
    key = (path, lh, _dpr_key(dpr), scale, False)
    if use_cache and key in _CACHE:
        cached = _CACHE[key]
        if isinstance(cached, LoadedStill):
            return cached

    anim = load_anim(
        path, logical_h, dpr, scale=scale, ui_scale=ui_scale, use_cache=use_cache,
    )
    pm = anim.frames[0] if anim.frames else QPixmap()
    still = LoadedStill(pm, anim.logical_w, anim.logical_h)
    if use_cache:
        _CACHE[key] = still
    return still


class SkinAnimPlayer(QObject):
    """按帧 delay 推进动画；frame_changed(index) 通知重绘。"""

    frame_changed = Signal(int)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._anim = LoadedAnim([], [100], 1, 1)
        self._i = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._on_tick: Callable[[int], None] | None = None

    @property
    def index(self) -> int:
        return self._i

    @property
    def frames(self) -> list[QPixmap]:
        return self._anim.frames

    @property
    def current(self) -> QPixmap:
        if not self._anim.frames:
            return QPixmap()
        return self._anim.frames[self._i % len(self._anim.frames)]

    @property
    def logical_w(self) -> int:
        return self._anim.logical_w

    @property
    def logical_h(self) -> int:
        return self._anim.logical_h

    def set_anim(self, anim: LoadedAnim):
        self.stop()
        self._anim = anim
        self._i = 0

    def set_on_tick(self, cb: Callable[[int], None] | None):
        self._on_tick = cb

    def start(self):
        if len(self._anim.frames) <= 1:
            return
        self._i = 0
        delay = self._anim.delays[0] if self._anim.delays else 100
        self._timer.start(delay)

    def stop(self):
        self._timer.stop()

    def _tick(self):
        if not self._anim.frames:
            return
        self._i = (self._i + 1) % len(self._anim.frames)
        delay = self._anim.delays[self._i % len(self._anim.delays)]
        self._timer.start(delay)
        self.frame_changed.emit(self._i)
        if self._on_tick is not None:
            self._on_tick(self._i)
