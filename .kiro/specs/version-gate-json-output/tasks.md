# Implementation Plan: version-gate-json-output

## Overview

為 `scripts/check_version.py`（Version_Check_Script）新增 `--json` 輸出模式。採「判定/渲染解耦」策略：先將 `_evaluate` 回傳型別由 `tuple[int, str]` 重構為 `tuple[int, CheckResult]`（渲染決策上移至 `main()`），再新增純函式 `render_json` 與 `main()` 的 `--json` 旗標分支。核心擷取 / 結構檢查 / 一致性判定層完全不動，Default_Mode 行為逐字維持不變，僅使用標準庫（新增 `import json`）。各步驟遞進且最終在 `main()` 收斂接線，無孤立程式碼。

## Tasks

- [ ] 1. 重構 `_evaluate` 與 `run_check` 的回傳契約（判定/渲染解耦）
  - 在 `scripts/check_version.py` 將 `_evaluate(repo_root)` 的簽章與回傳由 `tuple[int, str]` 改為 `tuple[int, CheckResult]`：三個分支（擷取失敗 fail-fast / 一致 / 不一致）皆回傳 `(exit_code, result)` 的 `CheckResult` 物件本體，不再於此層呼叫 `render_report`
  - 同步更新 `_evaluate` docstring 說明渲染決策已上移至 `main()`
  - 調整 `run_check` 對應新回傳型別（語義不變，仍只取 `exit_code`、丟棄 `CheckResult`）
  - 不改動 `collect_sources` / `check_structure` / `check_consistency` / `render_report` 任何一行
  - _Requirements: 1.4, 4.1, 4.4_

- [ ] 2. 新增 `render_json` 序列化純函式
  - [ ] 2.1 在檔頂新增標準庫 `import json`（維持僅標準庫、不新增非標準庫 import），置於既有 import 區塊的字母序正確位置
    - _Requirements: 4.2_

  - [ ] 2.2 於報告層（`render_report` 附近）新增 `render_json(result: CheckResult) -> str`
    - 手工組裝 payload dict（不使用 `dataclasses.asdict`，以保證對外欄位名/順序為穩定契約、與 dataclass 內部解耦）：`ok` / `authoritative` / `sources`（list of `{name, version}`，同序）/ `mismatches`（`dict(...)`）/ `invalid_format`（`dict(...)`）/ `structure_errors`（`list(...)`）/ `extraction_error`
    - 以 `json.dumps(payload, ensure_ascii=False, indent=2)` 序列化：`ensure_ascii=False` 保留 CJK/emoji、`indent=2` 可讀縮排
    - 撰寫 docstring 說明對外契約穩定性與編碼決策
    - 維持單一函式 ≤ 50 行、單檔 ≤ 500 行
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.3_

  - [ ]* 2.3 為 `render_json` 撰寫 round-trip property test
    - **Property 1: render_json round-trip 欄位保真**
    - 於 `test/test_cli_json_output.py` 以 hypothesis 生成合法 `CheckResult`（字串欄位涵蓋 ASCII/CJK/emoji；`sources`/`mismatches`/`invalid_format`/`structure_errors` 涵蓋空與非空；`extraction_error` 涵蓋 `None` 與字串），斷言 `json.loads(render_json(r))` 鍵集合、各欄位值/型別/順序與原 `CheckResult` 相等
    - `@settings(max_examples=200)`（≥100 迭代）
    - 標註 `# Feature: version-gate-json-output, Property 1: render_json round-trip 欄位保真`
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.3**

- [ ] 3. 於 `main()` 新增 `--json` 旗標分支並接線
  - [ ] 3.1 在 `main()` 解析 `"--json" in sys.argv[1:]`
    - 有 `--json`：取 `_evaluate(REPO_ROOT)` 的 `(exit_code, result)`，一律以 `render_json(result)` 寫 stdout（不論 `ok`），仍保留開頭 stdout/stderr 的 UTF-8 reconfigure，最後 `sys.exit(exit_code)`
    - 無 `--json`：維持既有行為逐字不變（`render_report`；`exit 0` → stdout，否則 → stderr + flush → `sys.exit`）
    - 更新 `main()` docstring 說明兩模式分支
    - _Requirements: 1.1, 1.2, 1.3, 3.2, 4.1_

  - [ ]* 3.2 撰寫 `--json` 一致 repo 的 integration test
    - `test_json_mode_consistent_repo`：以 `subprocess`（`sys.executable`、`encoding="utf-8"`）呼叫 `check_version.py --json` 於當前一致 repo，斷言 exit 0、stdout 為合法 JSON、`ok==true`、`authoritative=="1.2.0"`、`sources` 含三個 Manual 來源（`__init__.py` / `README.md` / `docs/handoverbook.md`）、`mismatches`/`invalid_format`/`structure_errors` 皆空、`extraction_error` 為 `null`
    - _Requirements: 1.1, 1.2, 2.1, 2.4, 3.3_

  - [ ]* 3.3 撰寫 `render_json` 不一致情境單元測試
    - `test_render_json_inconsistent_unit`：建含 `mismatches` 與 `structure_errors` 的不一致 `CheckResult`，斷言 `json.loads(render_json(r))` 各欄位正確、`ok==false`
    - _Requirements: 1.3, 2.2, 2.5, 2.6_

  - [ ]* 3.4 撰寫 Default_Mode 回歸測試
    - `test_default_mode_regression`：不帶 `--json` 子行程呼叫，斷言 exit 0 且人讀報告含穩定標記（✅ 一致）
    - _Requirements: 4.1_

- [ ] 4. Checkpoint — 執行完整驗證
  - 執行 `ruff check scripts/ test/` 應乾淨；`pytest test/` 全綠
  - 以子行程實測 `python scripts/check_version.py --json`（輸出重導向到檔案後讀回判讀）：合法 JSON、`ok==true`、exit 0
  - 以子行程實測 `python scripts/check_version.py`（無 flag）行為逐字不變
  - grep 確認 `check_version.py` 仍無 `tomllib`/`hatchling`/`sqlalchemy`/`import keeplink_mcp`
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- 標記 `*` 的子任務為測試任務，可為 MVP 快速路徑略過；非測試的核心實作任務不得略過。
- 每個任務標註對應的具體 requirements 子項以利追溯。
- Property test 驗證 Property 1 的 round-trip 欄位保真；integration/unit test 驗證 CLI 契約與 Default_Mode 回歸。
- 本功能嚴格限縮於「新增輸出模式」：不動核心邏輯、不破壞既有契約、僅標準庫、單檔 ≤ 500 行 / 單一函式 ≤ 50 行、fail-fast 不變。

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2.1"] },
    { "id": 1, "tasks": ["2.2"] },
    { "id": 2, "tasks": ["3.1", "2.3"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4"] }
  ]
}
```
