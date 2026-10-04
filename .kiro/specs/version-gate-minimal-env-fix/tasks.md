# Implementation Plan：version-gate-minimal-env-fix

## Overview

本實作計畫依已確認的 design.md 與 requirements.md，對既有 spec `version-consistency-gate`（30/30 完結）進行**設計修正**：剃除兩個 Derived_Version_Source 的執行值擷取（移除 `tomllib`/`import app`/`hatchling`/`sqlalchemy`），改以純文字結構檢查層 `check_structure` 於 commit 當下守住能力 A 接線，使閘門回到「只用標準庫、最小環境可跑、跨平台 CI 全綠」的定位。

實作語言為 **Python**（design 已使用具體 Python：frozen dataclass、type hints、hypothesis；不需再問）。

任務分組原則與依賴：

- **清理**（任務 1）移除舊 spec `version-consistency-gate/tasks.md` 誤加的「任務 15」整段，與本次程式修改互相獨立，可最先並行。
- **重寫 `scripts/check_version.py`**（任務 2–3）依 interface first：先定稿資料結構與全部函式簽章（骨架），再填各層實作（擷取 → 結構檢查 → 蒐集 → 判定 → 報告 → CLI）；每步建立在前一步之上，最終由 `_evaluate`/`run_check`/`main` 收斂為單一進入點。
- **連帶更新與新增測試**（任務 4–5）在 `check_version.py` 的受測實作就緒後進行；移除對已刪符號（`extract_pyproject_version`/`extract_app_version`/`is_derived`/3-tuple `SOURCE_SPECS`）的依賴，property 測試維持 ≥100 迭代、不弱化斷言。
- **舊 spec 設計紀錄**（任務 6）更新 `version-consistency-gate/design.md`，與程式修改獨立。
- **驗證收尾**（任務 7）本機 ruff/pytest/CLI/grep 全綠。
- **最終驗收**（任務 8）push 後盯 GitHub Actions CI 四個 job（ubuntu/windows × Py3.10/3.13）全綠——此為本修正完成與否的最終依據。

單檔 ≤ 500 行、單函式 ≤ 50 行、interface first、SoC/DRY、fail-fast、跨平台（Windows 11 + Linux）。測試放 `test/`、腳本放 `scripts/`。每個 property test 以註解標記 `Feature: version-consistency-gate, Property {n}: {property_text}`，hypothesis ≥100 迭代。

## Tasks

- [x] 1. 清理舊 spec：移除誤加的「任務 15」整段
  - 以可靠的檔案編輯工具（strReplace/fsWrite，**不**用 pwsh + `python -c`）開啟 `.kiro/specs/version-consistency-gate/tasks.md`
  - 刪除自 `- [ ] 15. 設計修正 — 剃除 Derived 執行值擷取、改以純文字結構檢查` 起、至其 `_Requirements: ...` 子項結束的整段「任務 15」區塊（含其上一個多餘空行），使任務 14 之後直接銜接 `## Notes` 段落
  - 編輯後**讀回該檔**核對：確認檔案以任務 14 作為最後一個任務、不再出現「任務 15 / 設計修正」字樣、`## Notes` 與 `## Task Dependency Graph` 段落完整保留，舊 spec 回到乾淨 30/30 完結
  - _Requirements: 10.1_

- [ ] 2. 重寫 scripts/check_version.py — 介面骨架與各層實作（interface first）
  - [x] 2.1 定稿資料結構、常數與全部函式簽章（骨架）
    - `VersionSource` 改為 `{name, version}`（**移除 `is_derived`**）
    - 新增 `StructureCheck` frozen dataclass：`{name, file_path, required: list[re.Pattern], forbidden: list[re.Pattern]}`
    - `CheckResult` **新增 `structure_errors: list[str]`** 欄位，其餘欄位（`authoritative`/`sources`/`ok`/`mismatches`/`invalid_format`/`extraction_error`）維持
    - 保留常數 `REPO_ROOT`、`SEMVER_PATTERN`、`HEADER_SCAN_LINES`；import 限 `re`/`sys`/`pathlib`/`dataclasses`/`collections.abc`
    - 先寫出擷取層 / 結構檢查層 / 蒐集層 / 判定層 / 報告層 / CLI 的型別註記簽章與 docstring（實作留待 2.2–2.6），確保 SoC 分層邊界清楚
    - _Requirements: 1.3, 4.1, 5.3, 6.1_

  - [x] 2.2 擷取層：移除 Derived 擷取、新增 `_read_source`、保留 3 個 Manual extractor
    - **刪除** `extract_pyproject_version`、`extract_app_version`、`_derive_pyproject_version_from_config`，連同 `import tomllib`、`import hatchling`、`import sqlalchemy`、任何 `import keeplink_mcp.*`
    - 新增 `_read_source(path: Path) -> str`：明確 `encoding="utf-8"` 讀檔，失敗 `raise ValueError`（DRY 共用讀檔）
    - 保留 `extract_init_version` / `extract_readme_version` / `extract_handoverbook_version`，各自 `read + regex`、以 `pathlib` 跨平台組路徑、fail-fast `raise ValueError`
    - _Requirements: 1.1, 1.2, 1.3, 4.4_

  - [x] 2.3 結構檢查層（新增）：`STRUCTURE_SPECS` 與 `check_structure`
    - 定義 `STRUCTURE_SPECS`：pyproject 規則（required `dynamic = ["version"]`、forbidden 行首 `^version = "..."` 以 `re.MULTILINE`，不誤傷 `requires-python`/`target-version`）；app.py 規則（required `version=__version__`、forbidden `version="<字面量>"`）
    - `check_structure(repo_root: Path) -> list[str]`：對每條 `StructureCheck` 讀檔（`encoding="utf-8"`、`pathlib` 組路徑），required 全命中、forbidden 全不命中；任一違規收集一條說明；**讀檔失敗（檔案不存在）亦收集為一條違規並繼續處理其餘規則**（不靜默略過、不提早中止）
    - 不解析 toml、不執行 app、不 import 任何專案程式碼；單函式 ≤ 50 行（必要時抽子函式）
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4_

  - [x] 2.4 蒐集層：`SOURCE_SPECS` 改 2-tuple、`collect_sources` 維持 true fail-fast
    - `SOURCE_SPECS` 由 3-tuple 改為 2-tuple `(顯示名, 擷取函式)`，恰 3 項全為 Manual，首筆為權威 `src/keeplink_mcp/__init__.py`，其餘為 `README.md`、`docs/handoverbook.md`；**不納入** pyproject/app metadata
    - `collect_sources(repo_root) -> tuple[list[VersionSource], str | None]`：依 2-tuple 順序逐一擷取，第一個例外即停止、不續掃、不累積失敗清單
    - _Requirements: 5.1, 5.2, 5.4, 8.5_

  - [x] 2.5 判定層：`check_consistency` 併入 `structure_errors`
    - `check_consistency(authoritative, sources, structure_errors) -> CheckResult`：比對每個來源 == 權威、皆合法 semver，將傳入的 `structure_errors` 放入結果；`ok` 僅在 無 mismatch、無 invalid_format、且 `structure_errors` 為空 時為 `True`；純資料轉換、無 I/O
    - `is_semver` 以 `SEMVER_PATTERN` 判定，維持不變
    - _Requirements: 6.1, 6.3, 6.4_

  - [x] 2.6 報告層 + CLI：新增結構違規分節、`_evaluate` 併入結構檢查、對外契約不變
    - `render_report`：維持既有分派優先序（擷取失敗 fail-fast > 一致 > 不一致/格式違規/結構違規）與既有格式契約；`structure_errors` 非空時**才**附上「能力 A 接線結構違規」分節並逐條列出，為空時不附
    - `_evaluate(repo_root)`：擷取成功後呼叫 `check_structure(repo_root)`，將結果傳入 `check_consistency`；擷取失敗走 true fail-fast 單一來源報告
    - `run_check` 回 exit code（0 一致 / 非零 其餘，含結構違規）；`main()` 開頭 `reconfigure(encoding="utf-8")`、no-args 走 `SOURCE_SPECS`、不一致/失敗寫 stderr 並 `flush()`、`sys.exit(run_check())`——CLI 契約/fail-fast/UTF-8 全部不變
    - _Requirements: 6.2, 6.5, 7.1, 7.2, 7.3, 8.1, 8.2, 8.3, 8.4, 8.5_

- [x] 3. Checkpoint — 確認 check_version.py 可獨立運作
  - 於 repo 根目錄執行 `python scripts/check_version.py` 應回 exit 0；確認無 import 階段錯誤（最小環境可跑）
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. 連帶更新受影響的既有測試（移除已刪符號依賴）
  - [x] 4.1 更新 test_version_consistency.py 與 test_source_list_composition.py
    - `test_version_consistency.py`：移除對 `extract_pyproject_version`/`extract_app_version`/`is_derived`/3-tuple 的所有引用；一致性斷言改以 3 個 Manual 來源進行
    - `test_source_list_composition.py`：改斷言 `SOURCE_SPECS` 為恰 3 項 2-tuple、首筆為權威來源；移除 Derived/`is_derived` 斷言
    - _Requirements: 9.1, 9.2_

  - [x]* 4.2 更新 test_property1_consistency.py（Property 1，不弱化）
    - **Property 1（更新）：一致性判定納入結構檢查**
    - **Validates: Requirements 6.3, 6.4**
    - `check_consistency` 呼叫處補傳 `structure_errors` 參數；生成一致集傳 `[]` 應 `ok=True`、注入非空 `structure_errors` 或不一致/格式違規應 `ok=False` 且報告列出每個來源與其版本值
    - 註解標記 `Feature: version-consistency-gate, Property 1: ...`；hypothesis ≥100 迭代，不弱化既有斷言

  - [x]* 4.3 更新 test_property3_fail_fast.py（Property 3，2-tuple spy/mock）
    - **Property 3（沿用，實作微調）：True fail-fast 於首個擷取失敗即停止**
    - **Validates: Requirements 8.5**
    - 依 2-tuple `SOURCE_SPECS` 調整 spy/mock；斷言於首個失敗索引 k 停止、已成功來源恰 0..k-1、錯誤只提及索引 k、索引 k 之後 extractor 從未被呼叫
    - 註解標記 `Feature: version-consistency-gate, Property 3: ...`；hypothesis ≥100 迭代

  - [x] 4.4 更新 test_capability_a_structure.py 與 test_cli_contract.py
    - `test_capability_a_structure.py`：原 derivation-equality 測試（呼叫已刪的 `extract_pyproject_version`/`extract_app_version`）改為呼叫 `check_structure(repo_root)` 斷言對真實 repo 回空 list；既有純結構斷言保留
    - `test_cli_contract.py`：確認無對已刪符號的依賴；CLI 契約斷言維持不變
    - 另 grep 全 `test/` 目錄確認無任何殘留引用 `extract_pyproject_version`/`extract_app_version`/`is_derived`/3-tuple `SOURCE_SPECS`
    - _Requirements: 9.1, 9.5_

- [x] 5. 新增結構檢查測試（正例 + 反例 + 新增 Property）
  - [x] 5.1 新增結構檢查 example 測試（正例 + 反例）
    - 新測試檔放 `test/`（例如 `test_structure_check.py`）
    - 正例：對真實 `pyproject.toml` / `src/keeplink_mcp/api/app.py` 斷言 `check_structure(repo_root)` 回空 list
    - 反例：以合成內容建立「`pyproject.toml` 含行首靜態 `version = "x"`」「`app.py` 含 `version="字面量"`」「目標檔缺失」情境，斷言回非空且含對應違規說明；並斷言 `requires-python`/`target-version` 不被誤判
    - _Requirements: 2.4, 3.4, 4.3, 9.5_

  - [x]* 5.2 新增 Property 6 property test（結構檢查正確性）
    - **Property 6（新增）：結構檢查正確性**
    - **Validates: Requirements 2.1, 2.2, 2.4, 3.1, 3.2, 3.4, 4.2, 4.3**
    - 生成策略：對各目標檔生成「全 required 命中且全 forbidden 不命中」「至少一 required 缺失」「至少一 forbidden 命中」「目標檔缺失」等內容組合，寫入臨時 repo 結構；斷言 `check_structure` 回空 **當且僅當** 全部通過，否則回非空、為每條違規各收一條說明、且不因任一違規提早中止其餘規則處理
    - 註解標記 `Feature: version-consistency-gate, Property 6: ...`；hypothesis ≥100 迭代

  - [x]* 5.3 新增 Property 7 property test（結構違規報告分節）
    - **Property 7（新增）：結構違規報告分節**
    - **Validates: Requirements 7.1, 7.2**
    - 生成策略：生成帶空/非空 `structure_errors` 的 `CheckResult`，斷言 `render_report` 於非空時含「能力 A 接線結構違規」分節並出現每條違規文字、於空時不含該分節
    - 註解標記 `Feature: version-consistency-gate, Property 7: ...`；hypothesis ≥100 迭代

- [x] 6. 舊 spec 設計修正紀錄（不動既有 requirements 編號）
  - 於 `.kiro/specs/version-consistency-gate/design.md` 新增一段「設計修正紀錄」，說明本次剃除 Derived 執行值擷取（移除 `tomllib`/`import app`/`hatchling`/`sqlalchemy`）與改以純文字結構檢查 `check_structure` 的改法、動機（CI 四 job 失敗根因）與對外契約不變
  - 僅新增紀錄段落，**不更動** `version-consistency-gate` 既有 requirements 的編號與需求意圖
  - _Requirements: 10.2, 10.3_

- [x] 7. 驗證收尾（本機）
  - 執行 `ruff check scripts/ test/` 並修正所有告警至乾淨
  - 執行 `pytest test/` 確認全部通過（含 property tests ≥100 迭代）
  - 於 repo 根目錄執行 `python scripts/check_version.py` 確認回 exit code 0
  - 對 `scripts/check_version.py` 執行 grep 確認**不含** `tomllib`、`import keeplink_mcp`、`hatchling`、`sqlalchemy` 任一字樣
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 11.1, 11.2, 11.3, 11.4_

- [-] 8. 最終驗收 — push 後盯 CI 四個 job 全綠
  - 將本修正 push 至遠端分支
  - 盯 GitHub Actions CI_Pipeline，確認 CI_Matrix 四個 job（`ubuntu-latest` + `windows-latest` × Python `3.10` + `3.13`）全部標記為通過——此為本修正完成與否的**最終依據**（本機測試不算數）
  - _Requirements: 11.5_

## Notes

- 標記 `*` 的子任務為測試相關（property）可選；清理（任務 1）、核心實作（任務 2）、更新既有/結構 example 測試（4.1/4.4/5.1）、驗證收尾（任務 7）與最終驗收（任務 8）為核心任務，**不得**略過。
- 每個任務標註對應的 requirements 子條款以利追溯。
- Property 編號延續既有 `version-consistency-gate`：Property 1 更新、Property 3 沿用、Property 6/7 新增；Property 2/4/5 不受本修正影響，無對應任務。
- 任務 1（清理舊 spec）與任務 6（舊 spec 設計紀錄）皆只動 `version-consistency-gate/` 下的 md，與程式修改互不衝突。
- Checkpoint（任務 3、7）確保增量驗證。

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2.1", "6"] },
    { "id": 1, "tasks": ["2.2", "2.3"] },
    { "id": 2, "tasks": ["2.4", "2.5"] },
    { "id": 3, "tasks": ["2.6"] },
    { "id": 4, "tasks": ["4.1", "4.4", "5.1"] },
    { "id": 5, "tasks": ["4.2", "4.3", "5.2", "5.3"] },
    { "id": 6, "tasks": ["7"] },
    { "id": 7, "tasks": ["8"] }
  ]
}
```
