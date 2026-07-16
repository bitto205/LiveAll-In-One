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
_ROOT_REMOVE_PREFIXES = (
    "ffmpeg-",
    "winldd-",
    "chromium-",  # 不含 chromium_headless_shell-
)

# 运行/调试残留，不应随包分发
_PRIVACY_FILES = ("debug.log",)


def _shell_dirs(browsers: Path) -> list[Path]:
    out: list[Path] = []
    try:
        children = list(browsers.iterdir())
    except OSError:
        return out
    for d in children:
        if not d.is_dir() or not d.name.startswith("chromium_headless_shell-"):
            continue
        for sub in ("chrome-headless-shell-win64", "chrome-headless-shell-win32"):
            path = d / sub
            if path.is_dir():
                out.append(path)
    return out


def _root_junk_dirs(browsers: Path) -> list[Path]:
    junk: list[Path] = []
    try:
        children = list(browsers.iterdir())
    except OSError:
        return junk
    for d in children:
        if not d.is_dir():
            continue
        name = d.name
        if name.startswith("chromium_headless_shell-"):
            continue
        if any(name.startswith(p) for p in _ROOT_REMOVE_PREFIXES):
            junk.append(d)
    return junk


def _shell_needs_trim(shell: Path) -> bool:
    for name in _SHELL_DIR_NAMES:
        if (shell / name).is_dir():
            return True
    locales = shell / "locales"
    if locales.is_dir():
        try:
            for f in locales.iterdir():
                if f.is_file() and f.name not in _KEEP_LOCALES:
                    return True
        except OSError:
            pass
    for name in _PRIVACY_FILES:
        if (shell / name).is_file():
            return True
    return False


def _needs_trim(browsers: Path) -> bool:
    """浅层探测：有垃圾才 trim，避免每次启动全树 rglob 算体积。"""
    if _root_junk_dirs(browsers):
        return True
    return any(_shell_needs_trim(s) for s in _shell_dirs(browsers))


def trim_playwright_browsers(browsers_dir: str | Path | None = None) -> float:
    """精简 browsers/。返回大致节省的 MB（仅在实际删除时估算，可能为 0）。"""
    if browsers_dir is None:
        root = Path(__file__).resolve().parent.parent / "browsers"
    else:
        root = Path(browsers_dir)
    if not root.is_dir():
        return 0.0

    if not _needs_trim(root):
        return 0.0

    removed = 0
    for d in _root_junk_dirs(root):
        shutil.rmtree(d, ignore_errors=True)
        removed += 1
        logger.info("已删除浏览器目录: %s", d.name)

    for shell in _shell_dirs(root):
        for name in _SHELL_DIR_NAMES:
            target = shell / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                removed += 1
                logger.info("已删除: %s/%s", shell.name, name)

        locales = shell / "locales"
        if locales.is_dir():
            try:
                for f in locales.iterdir():
                    if f.is_file() and f.name not in _KEEP_LOCALES:
                        try:
                            f.unlink()
                            removed += 1
                        except OSError:
                            pass
            except OSError:
                pass

        for name in _PRIVACY_FILES:
            f = shell / name
            if f.is_file():
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass

    if removed:
        logger.info("浏览器精简完成（删除 %d 项）", removed)
    return float(removed)
