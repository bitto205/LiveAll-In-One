---
name: powershell-encoding
description: >-
  Prevent and repair UTF-8 corruption when using PowerShell on Windows source
  files. Use when editing Chinese-containing .cpp/.h via PowerShell, seeing
  hundreds of static/IDE errors after a shell rewrite, QStringLiteral
  unterminated-argument errors, BOM issues, or when the user mentions 编码,
  GBK, Set-Content, PowerShell encoding, or 静态检查暴增.
---

# PowerShell encoding（UTF-8 护栏）

## Why

LiveAIO sources are **UTF-8 without BOM** with many Chinese string literals.
Windows PowerShell often uses **system ANSI (GBK/936)**. Rewriting a file with
the wrong encoding truncates multi-byte sequences inside `QStringLiteral("…")`,
which cascades into **hundreds of parse / static-check errors**.

## Hard rules

1. **Never** rewrite Chinese source with:
   - `Set-Content` / `Out-File` / `Add-Content`
   - `>` / `>>` redirection onto `.cpp` / `.h` / `.md`
   - `Get-Content | … | Set-Content` round-trips
2. Prefer editor tools (`StrReplace`, `Write`) or **Python** with explicit UTF-8.
3. After any PowerShell touch of source, run the checker (below).
4. If IDE suddenly shows ~100+ errors on a previously fine TU, **check encoding first**.

## Safe patterns

```powershell
# Read UTF-8 text
$t = [IO.File]::ReadAllText("tools\leaf_tool.cpp", [Text.Encoding]::UTF8)

# Write UTF-8 *without* BOM
$utf8 = New-Object System.Text.UTF8Encoding $false
[IO.File]::WriteAllText("tools\leaf_tool.cpp", $t, $utf8)
```

```python
from pathlib import Path
Path("tools/leaf_tool.cpp").write_text(text, encoding="utf-8", newline="\r\n")
```

## Forbidden patterns

```powershell
# BAD — default encoding / BOM
Set-Content tools\leaf_tool.cpp $lines -Encoding UTF8   # often writes BOM
Get-Content ... | Set-Content ...                       # GBK round-trip
(Get-Content ...) | Where-Object { ... } | Set-Content ...
```

## Verify (mandatory)

```powershell
python scripts/check_utf8.py tools/leaf_tool.cpp util/widgets.cpp
```

Expect `OK … bom=False fffd=0 odd_quotes=0`. Any `BAD` / `ODD_QUOTE` → stop and repair before rebuilding.

## Repair playbook

1. Confirm corruption: `python scripts/check_utf8.py <file>` or look for `\uFFFD` / odd quotes.
2. Prefer `git checkout -- <file>` then **re-apply intentional edits** with StrReplace/Python.
3. If recovering mojibake, do **not** guess with GBK↔UTF-8 ping-pong unless you can prove round-trip against `git show HEAD:<file>` line prefixes; verify with the checker after each step.
4. Rebuild only after checker is green.

## Symptom → cause

| Symptom | Likely cause |
| --- | --- |
| 100–1000 IDE/static errors on one `.cpp` | Truncated `QStringLiteral` from encoding rewrite |
| `unterminated argument list … QStringLiteral` | Broken quotes / cut multi-byte char |
| Chinese shows as `` in console only | Console CP936 display; file may still be fine — verify with Python/`check_utf8.py` |
| File starts with `EF BB BF` | PowerShell `-Encoding UTF8` BOM |
