# -*- coding: utf-8 -*-
"""Quick UTF-8 integrity probe for source files after PowerShell edits."""
from pathlib import Path
import sys

paths = sys.argv[1:] or ["tools/leaf_tool.cpp"]
rc = 0
for p in paths:
    data = Path(p).read_bytes()
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = data.decode("utf-8")
        ok = True
        err = ""
    except UnicodeDecodeError as e:
        ok = False
        err = str(e)
        text = ""
    fffd = text.count("\ufffd") if ok else -1
    # odd-quote QStringLiteral lines (rough)
    odd = 0
    if ok:
        for i, line in enumerate(text.splitlines(), 1):
            if "QStringLiteral" in line and line.count('"') % 2 != 0:
                odd += 1
                print(f"ODD_QUOTE {p}:{i}")
    # NSIS Unicode 脚本需要 UTF-8 BOM；其它源码仍要求无 BOM。
    allow_bom = Path(p).suffix.lower() == ".nsi"
    status = "OK" if ok and (allow_bom or not bom) and fffd == 0 and odd == 0 else "BAD"
    if status != "OK":
        rc = 1
    print(f"{status} {p} bom={bom} fffd={fffd} odd_quotes={odd} {err}")
sys.exit(rc)
