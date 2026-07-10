"""Playwright 浏览器目录精简：只保留 headless shell，删冗余与隐私相关文件。"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_KEEP_LOCALES = frozenset({
    "zh-CN.pak",
    "zh-CN_FEMININE.pak",
    "zh-CN_MASCULINE.pak",
    "zh-CN_NEUTER.pak",
    "en-US.pak",
    "en-US_FEMININE.pak",
    "en-US_MASCULINE.pak",
    "en-US_NEUTER.pak",
})

# headless shell 内可整目录删除的项
_SHELL_DIR_NAMES = (
    "hyphen-data",
    "PrivacySandboxAttestationsPreloaded",
    "resources",
)

# browsers/ 下只保留 headless shell，其余 Playwright 附带物直接删
_ROOT_REMOVE_DIR_GLOBS = (
    "ffmpeg-*",
    "winldd-*",
    "chromium-*",
)

# 运行/调试残留，不应随包分发
_PRIVACY_FILES = ("debug.log",)


def _dir_size_mb(path: Path) -> float:
    if not path.is_dir():
        return 0.0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / (1024 * 1024), 1)


def _shell_dirs(browsers: Path) -> list[Path]:
    return [
        d / sub
        for d in browsers.glob("chromium_headless_shell-*")
        for sub in ("chrome-headless-shell-win64", "chrome-headless-shell-win32")
        if (d / sub).is_dir()
    ]


def trim_playwright_browsers(browsers_dir: str | Path | None = None) -> float:
    """精简 browsers/。返回节省的 MB（约数）。"""
    if browsers_dir is None:
        root = Path(__file__).resolve().parent.parent / "browsers"
    else:
        root = Path(browsers_dir)
    if not root.is_dir():
        return 0.0

    before = _dir_size_mb(root)

    for pattern in _ROOT_REMOVE_DIR_GLOBS:
        for d in root.glob(pattern):
            if not d.is_dir():
                continue
            if pattern == "chromium-*" and "headless_shell" in d.name:
                continue
            shutil.rmtree(d, ignore_errors=True)
            logger.info("已删除浏览器目录: %s", d.name)

    for shell in _shell_dirs(root):
        for name in _SHELL_DIR_NAMES:
            target = shell / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                logger.info("已删除: %s/%s", shell.name, name)

        locales = shell / "locales"
        if locales.is_dir():
            for f in locales.iterdir():
                if f.is_file() and f.name not in _KEEP_LOCALES:
                    try:
                        f.unlink()
                    except OSError:
                        pass

        for name in _PRIVACY_FILES:
            f = shell / name
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass

    saved = round(before - _dir_size_mb(root), 1)
    if saved > 0:
        logger.info("浏览器精简: %.1f MB -> %.1f MB（省 %.1f MB）", before, _dir_size_mb(root), saved)
    return saved
