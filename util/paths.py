"""运行时根目录（开发目录 / Nuitka 打包目录）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _is_compiled() -> bool:
    if getattr(sys, "frozen", False):
        return True
    try:
        import __main__
        return hasattr(__main__, "__compiled__")
    except Exception:
        return False


def app_root() -> Path:
    """项目根目录：开发时为仓库根；打包后为 exe 所在目录。"""
    if _is_compiled():
        return Path(os.path.abspath(sys.argv[0])).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_file() -> Path:
    return app_root() / "config.json"
