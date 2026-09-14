---
name: deliberate-execution
description: >-
  Deliberate multi-step workflow: think longer, read before editing, write
  TodoWrite plans, change carefully, then verify. Use for any non-trivial coding,
  build/scripts, architecture, debug/F5, refactors, IPC/contracts, or when the
  user mentions 公约, 先看再改, 多思考, todo, 验证, or asks to slow down / plan first.
  Prefer this over rushing edits.
---

# Deliberate execution（公约）

目的：多思考，先看再改，改完验证，写好 todo，拉长执行时间。宁可慢半拍，也不要盲改。

## When to apply

Apply on **any non-trivial task**. Skip only for tiny one-liners (typo, single-path fix, pure Q&A with no edits).

If unsure whether it is trivial: **treat it as non-trivial**.

## Mandatory loop

```text
Think → Read → Plan/Todo → Change → Verify → Report
```

Do **not** skip ahead to Change.

### 1. Think（拉长思考）

Before tools that mutate files:

- Restate the goal in one sentence (internal).
- List constraints (contracts, single-exe/multi-dll, paths, gitignore).
- Name risks (wrong path, breaking F5, deleting data, half-migrated APIs).
- Prefer reading 2–3 more files over guessing.

Do not start writing code in the same beat you first understood the request.

### 2. Read（先看再改）

- Open the real files, configs, and contracts involved.
- Confirm paths exist (`Test-Path` / list dir) before pointing tools at them.
- Prefer repo truth (`CONTRACT.md`, code) over memory from older chats.
- If something is missing, say so; do not invent “it must be there.”

### 3. Plan + Todo

For non-trivial work, call `TodoWrite` **before** the first edit:

- 2+ concrete steps (not vague “fix it”).
- One `in_progress` at a time; mark `completed` as you go.
- Update the list when scope changes instead of silently drifting.

Tiny tasks may skip TodoWrite; everything else must not.

### 4. Change

- Smallest diff that satisfies the goal.
- Match existing style; no drive-by refactors.
- Do not commit unless the user asked.

### 5. Verify（改完验证）

After edits, prove something:

- Run the relevant command (build script, `go test`/`go build`, linter, `python -c` import, `Test-Path`, task dry-run).
- If any source was touched via **PowerShell** (`Set-Content` / redirection / bulk rewrite), run `python scripts/check_utf8.py <files…>` first — see skill `powershell-encoding`. Encoding corruption cascades into hundreds of static/IDE errors.
- If the environment lacks a tool, say what was verified and what was blocked.
- Fix failures before declaring done; do not leave “should work.”

### 6. Report

- Short verdict first.
- What changed, how verified, what remains.
- No fake confidence.

## Anti-patterns

- Edit first, search later.
- “Remembering” build scripts / paths that are not on disk.
- Closing todos without verification.
- Parallel huge rewrites with no plan.
- Treating “extension installed” as “binary on PATH.”
- Rewriting UTF-8 Chinese sources with PowerShell `Set-Content` / `>` (GBK/BOM corruption → hundreds of parse errors).

## LiveAIO notes (when in this repo)

- Production: one `LiveAIO.exe` + `LiveAIOCore/Pages/Tools.dll`; `main.go` is debug shell only.
- Build entry: `scripts/build.ps1` (not legacy `build/build.ps1` unless restored).
- Gift assets: `resources/gift/` + `resources/gift/icon/`.
- Contracts: `core/CONTRACT.md`, `core/CPP_UI_CONTRACT.md`, `core/DESIGN_LOCKS.md`.
- IDE：Cursor clangd 靠根目录 `compile_commands.json`（build 会导出）+ `.clangd`。unity 子 `.cpp`（被 `*_main.cpp` include）打开时会假报 unused-parameter 等，已在 `.clangd` 里 Suppress；若仍见数百条，Reload Window。
