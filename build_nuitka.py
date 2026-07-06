#!/usr/bin/env python3
"""Nuitka 打包：产出 build/LiveAIO（无浏览器）与 build/LiveAIO-with-browsers。"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)

WORK = ROOT / "build" / "_nuitka_work"
DIST_NAME = "main.dist"  # Nuitka 以入口模块名命名
OUT_NO_BROWSER = ROOT / "build" / "LiveAIO"
OUT_WITH_BROWSER = ROOT / "build" / "LiveAIO-with-browsers"
BROWSERS_SRC = ROOT / "browsers"


def _rel_data(path: Path, dest: str) -> str:
    return f"{path.as_posix()}={dest}"


def nuitka_cmd() -> list[str]:
    cmd = [
        str(PYTHON), "-m", "nuitka",
        "--standalone",
        "--enable-plugin=pyside6",
        "--windows-console-mode=disable",
        f"--output-dir={WORK}",
        "--output-filename=LiveAIO.exe",
        "--assume-yes-for-downloads",
        "--nofollow-import-to=tkinter,unittest,test,pydoc",
        "--include-package=playwright",
        "--include-package-data=playwright",
        "--include-package=mitmproxy",
        "--include-package=mitmproxy_windows",
        "--include-package=cryptography",
        "--include-package=google.protobuf",
        "--include-package=listener",
        "--include-package=tools",
        "--include-package=pages",
        "--include-package=util",
        "--include-package=gift",
        "--include-data-dir=gift=gift",
        "--include-data-dir=image=image",
    ]

    shell = ROOT / "listener" / "proxy_shell.exe"
    if shell.is_file():
        cmd.append(f"--include-data-file={_rel_data(shell, 'listener/proxy_shell.exe')}")
    else:
        print("警告: listener/proxy_shell.exe 不存在，线路 4 patch 将不可用", file=sys.stderr)

    cfg = ROOT / "config.json"
    if cfg.is_file():
        cmd.append(f"--include-data-file={_rel_data(cfg, 'config.json')}")

    cmd.append(str(ROOT / "main.py"))
    return cmd


def run_nuitka() -> Path:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)

    print(">>> Nuitka 编译（耗时较长）…")
    subprocess.run(nuitka_cmd(), cwd=ROOT, check=True)

    dist = WORK / DIST_NAME
    if not dist.is_dir():
        raise SystemExit(f"未找到 Nuitka 输出目录: {dist}")
    return dist


def copy_dist(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    print(f"已输出: {dest}")


def add_browsers(dest: Path) -> None:
    if not BROWSERS_SRC.is_dir():
        raise SystemExit(f"未找到 browsers 目录: {BROWSERS_SRC}")
    browsers_dest = dest / "browsers"
    if browsers_dest.exists():
        shutil.rmtree(browsers_dest)
    print(f">>> 复制 browsers/ → {browsers_dest} …")
    shutil.copytree(BROWSERS_SRC, browsers_dest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Nuitka 打包 LiveAIO")
    parser.add_argument("--skip-compile", action="store_true", help="仅复制已有 _nuitka_work 产物")
    args = parser.parse_args()

    (ROOT / "build").mkdir(exist_ok=True)

    if args.skip_compile:
        dist = WORK / DIST_NAME
        if not dist.is_dir():
            raise SystemExit("无已有编译产物，请先完整运行本脚本")
    else:
        dist = run_nuitka()

    copy_dist(dist, OUT_NO_BROWSER)
    copy_dist(dist, OUT_WITH_BROWSER)
    add_browsers(OUT_WITH_BROWSER)

    print()
    print("完成:")
    print(f"  无浏览器: {OUT_NO_BROWSER}")
    print(f"  含浏览器: {OUT_WITH_BROWSER}")


if __name__ == "__main__":
    main()
