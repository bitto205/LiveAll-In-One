"""
config.py — 本地配置持久化（key-value，存 config.json）
"""
import json, os

_FILE = "config.json"

def get(key: str, default=None):
    try:
        if os.path.exists(_FILE):
            return json.load(open(_FILE, encoding="utf-8")).get(key, default)
    except Exception:
        pass
    return default

def set(key: str, value):
    try:
        cfg = {}
        if os.path.exists(_FILE):
            cfg = json.load(open(_FILE, encoding="utf-8"))
        cfg[key] = value
        json.dump(cfg, open(_FILE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    except Exception:
        pass
