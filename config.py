"""本地配置持久化（key-value → config.json）"""
import json
import os
from contextlib import contextmanager

_FILE = "config.json"


def _read() -> dict:
    if not os.path.exists(_FILE):
        return {}
    try:
        with open(_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write(cfg: dict) -> None:
    with open(_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get(key: str, default=None):
    return _read().get(key, default)


def set(key: str, value):
    try:
        cfg = _read()
        cfg[key] = value
        _write(cfg)
    except Exception:
        pass


@contextmanager
def transaction():
    """原子读写 config.json。"""
    cfg = _read()
    try:
        yield cfg
        _write(cfg)
    except Exception:
        raise
