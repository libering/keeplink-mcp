# Requirements Document

## Introduction

本 spec 是對既有已完成 spec `version-consistency-gate`（30/30 完結）的**設計修正**，非新功能。既有能力 B 的核心腳本 `scripts/check_version.py` 在 GitHub Actions CI 四個 job（`ubuntu-latest` + `windows-latest` × Python 3.10/3.13）全數失敗，根因為腳本去擷取 Derived 來源（`pyproject.toml`、FastAPI app metadata）的執行值，為此引入 `tomllib`（3.11+ 才進標準庫，3.10 無此模組）與 import 整個 app（連帶 import SQLAlchemy asyncio，乾淨 CI 缺 `greenlet`），違反閘門「只用標準庫、最小環境可跑」的定位。

本修正以奧卡姆剃刀剃除兩個 Derived_Version_Source 的執行值擷取，改以**純文字結構檢查**（required/forbidden regex）在 commit 當下驗證能力 A 接線未被改回寫死，並維持對外契約（CLI exit code 語義、Version_Report 格式、fail-fast、Windows UTF-8）不變。本修正遵守專案硬規則：單檔 ≤ 500 行、單函式 ≤ 50 行、interface first、SoC/DRY、fail-fast、跨平台（Windows 11 + Linux）、不新增需連外網或非標準庫的執行前提。

本 spec **不更動** `version-consistency-gate` 的既有 requirements 編號與需求意圖；僅修正能力 B 的實作詮釋（由「3-tuple + 執行值擷取」改為「結構檢查驗證 Derived 接線 + 來源清單只含 Manual 比對」）。

### 明確不在範圍（Out of Scope）

- 不改變 `version-consistency-gate` 既有需求的語義意圖（能力 A 仍讓 pyproject/app 由權威 `__version__` 推導）。
- 不新增版本功能、不改 CLI 對外契約語義、不改報告的對外格式契約。
- 不改寫已推送的 git 歷史。

## Glossary

- **Version_Check_Script**：既有檢查腳本 `scripts/check_version.py`，本 spec 修正其內部實作。
- **Authoritative_Version**：版本真相唯一權威來源，定義為 `src/keeplink_mcp/__init__.py` 的 `__version__`。
- **Manual_Version_Source**：以人工維護、需檢查機制把關與 Authoritative_Version 一致的版本來源。本修正後納管來源縮為 3 個，全為 Manual：`src/keeplink_mcp/__init__.py`（權威）、`README.md` 頂部版本字串、`docs/handoverbook.md` 頂部當前版本標記。
- **Derived_Version_Source**：其版本值由 Authoritative_Version 於建置/執行期推導的來源（`pyproject.toml`、FastAPI app metadata）。本修正後**不再擷取其執行值**，改以結構檢查驗證其接線。
- **Minimal_Env**：閘門執行環境定位——僅 Python 標準庫（`re` / `sys` / `pathlib` / `dataclasses` / `collections.abc`），不 import 專案 runtime、不用 `tomllib`、不 import `app` / `sqlalchemy` / `hatchling`。
- **Structure_Check_Layer**：新增的純文字結構檢查層 `check_structure(repo_root)`，以 required/forbidden regex 驗證能力 A 接線未被改回寫死，不執行 app、不解析 toml。
- **StructureCheck**：單一結構規則資料結構 `{name, file_path, required, forbidden}`。
- **STRUCTURE_SPECS**：集中維護的 StructureCheck 清單。
- **VersionSource**：單一納管來源的擷取結果 `{name, version}`（本修正移除 `is_derived` 欄位）。
- **SOURCE_SPECS**：集中維護的來源規格清單，本修正由 3-tuple `(name, is_derived, extractor)` 改為 2-tuple `(name, extractor)`，首筆為權威來源。
- **CheckResult**：一致性判定結果，本修正新增 `structure_errors` 欄位。
- **Structure_Error**：Structure_Check_Layer 偵測到的能力 A 接線違規說明字串。
- **Version_Report**：Version_Check_Script 輸出的檢查結果文字；本修正於不一致情境新增「能力 A 接線結構違規」分節。
- **Semver_Format**：語意化版本格式，`^\d+\.\d+\.\d+$`。
- **Pre_Commit_Hook**：安裝於本機 git 的 `pre-commit` 掛鉤，於 commit 建立前呼叫 Version_Check_Script。
- **CI_Pipeline**：GitHub Actions 工作流程（`.github/workflows/ci.yml`）。
- **CI_Matrix**：CI_Pipeline 的作業系統與 Python 版本矩陣，為 `ubuntu-latest` + `windows-latest` × Python `3.10` + `3.13` 共四個 job。
- **Capability_A_Wiring_Guard**：對能力 A 接線正確性的保障，由 commit 階段的 Structure_Check_Layer 與 pytest 階段的 `test_capability_a_structure` 雙重把關。

## Requirements

### Requirement 1: 剃除 Derived 執行值擷取（最小環境定位）

**User Story:** As a KeepLink 維護者, I want 檢查腳本不再擷取 Derived 來源的執行值, so that 閘門回到「只用標準庫、最小環境可跑」的定位，不在 CI 乾淨環境崩潰。

#### Acceptance Criteria

1. THE Version_Check_Script SHALL 移除 `extract_pyproject_version`、`extract_app_version` 與 `_derive_pyproject_version_from_config` 三個函式。
2. THE Version_Check_Script SHALL 不含 `import tomllib`、`import hatchling`、`import sqlalchemy` 與任何對 `keeplink_mcp` 專案 runtime 模組的 import。
3. THE Version_Check_Script SHALL 僅 import Python 標準庫模組（限 `re`、`sys`、`pathlib`、`dataclasses`、`collections.abc`）。
4. WHEN 於 Python 3.10 的乾淨環境（僅標準庫、未安裝專案 runtime 依賴）執行 Version_Check_Script 時, THE Version_Check_Script SHALL 不因缺少 `tomllib` / `greenlet` / `sqlalchemy` / 專案 runtime 而於 import 階段失敗。

---

### Requirement 2: pyproject.toml 動態版本接線的純文字結構檢查（補法甲）

**User Story:** As a KeepLink 維護者, I want 以純文字結構檢查驗證 pyproject 動態版本接線未被改回寫死, so that 不需解析 toml 或執行 hatchling 即可在 commit 當下守住能力 A。

#### Acceptance Criteria

1. THE Structure_Check_Layer SHALL 以純文字 required regex 驗證 `pyproject.toml` 含 `dynamic = ["version"]` 的動態版本宣告。
2. THE Structure_Check_Layer SHALL 以純文字 forbidden regex 驗證 `pyproject.toml` 不含行首靜態 `version = "<字面量>"` 宣告，且 SHALL 不誤判 `requires-python` 或 `target-version` 等非版本欄位為違規。
3. THE Structure_Check_Layer SHALL 不解析 toml、不執行 hatchling、不 import 任何專案程式碼即完成前述驗證。
4. IF `pyproject.toml` 缺少必要的動態版本接線或出現禁止的靜態 `version` 寫死, THEN THE Structure_Check_Layer SHALL 於回傳的 Structure_Error 清單收集一條對應該違規的說明。

---

### Requirement 3: app.py 權威版本接線的純文字結構檢查（補法甲）

**User Story:** As a KeepLink 維護者, I want 以純文字結構檢查驗證 app metadata 引用權威版本的接線未被改回寫死, so that 不需 import app 或 SQLAlchemy 即可在 commit 當下守住能力 A。

#### Acceptance Criteria

1. THE Structure_Check_Layer SHALL 以純文字 required regex 驗證 `src/keeplink_mcp/api/app.py` 含 `version=__version__` 的引用接線。
2. THE Structure_Check_Layer SHALL 以純文字 forbidden regex 驗證 `src/keeplink_mcp/api/app.py` 不含 `version="<字面量>"` 的寫死版本。
3. THE Structure_Check_Layer SHALL 不 import `app`、不 import `sqlalchemy`、不執行任何專案程式碼即完成前述驗證。
4. IF `src/keeplink_mcp/api/app.py` 缺少必要的 `version=__version__` 接線或出現禁止的字面量版本, THEN THE Structure_Check_Layer SHALL 於回傳的 Structure_Error 清單收集一條對應該違規的說明。

---

### Requirement 4: 結構檢查層的介面與讀檔行為

**User Story:** As a KeepLink 維護者, I want 結構檢查層以集中維護的規則清單運作並對缺檔 fail-fast, so that 能力 A 接線的驗證規則可追溯、缺檔不被靜默。

#### Acceptance Criteria

1. THE Structure_Check_Layer SHALL 以集中維護的 STRUCTURE_SPECS 清單定義所有待驗證的 StructureCheck 規則。
2. WHEN Structure_Check_Layer 執行且所有 StructureCheck 的 required regex 全部命中且 forbidden regex 全部不命中時, THE Structure_Check_Layer SHALL 回傳空的 Structure_Error 清單。
3. IF 某 StructureCheck 的目標檔案無法被讀取（例如檔案不存在）, THEN THE Structure_Check_Layer SHALL 將該情況收集為一條 Structure_Error 並繼續處理其餘 StructureCheck（缺檔視為接線被破壞，不靜默略過）。
4. THE Structure_Check_Layer SHALL 以作業系統無關的方式（透過 `pathlib` 組路徑、明確 `encoding="utf-8"` 讀檔）解析與讀取目標檔案。

---

### Requirement 5: 納管來源縮為 3 個 Manual 來源

**User Story:** As a KeepLink 維護者, I want 比對用的納管來源只保留 3 個手動維護來源, so that 檢查腳本的來源清單與最小環境定位一致、不再比對 Derived 執行值。

#### Acceptance Criteria

1. THE Version_Check_Script SHALL 將納管比對來源縮為恰 3 個 Manual_Version_Source：`src/keeplink_mcp/__init__.py`、`README.md`、`docs/handoverbook.md`。
2. THE SOURCE_SPECS SHALL 以 2-tuple `(顯示名, 擷取函式)` 定義每個來源，並以 `src/keeplink_mcp/__init__.py` 為首筆權威來源。
3. THE VersionSource SHALL 不含 `is_derived` 欄位，僅保留 `name` 與 `version`。
4. THE Version_Check_Script SHALL 不於 SOURCE_SPECS 納入 `pyproject.toml` 推導版本或 FastAPI app metadata 作為比對來源。

---

### Requirement 6: 一致性判定併入結構檢查

**User Story:** As a KeepLink 維護者, I want 結構違規與版本不一致一起決定檢查結果, so that 能力 A 接線被改壞時，閘門在 commit 當下即判為失敗，不退到 CI pytest 才抓（補 G2 缺口）。

#### Acceptance Criteria

1. THE CheckResult SHALL 新增 `structure_errors` 欄位，承載 Structure_Check_Layer 回傳的 Structure_Error 清單。
2. WHEN `_evaluate` 於擷取成功後執行時, THE Version_Check_Script SHALL 呼叫 `check_structure(repo_root)` 並將其結果傳入 `check_consistency`。
3. THE `check_consistency` SHALL 僅在無版本不一致、無格式違規、且 `structure_errors` 為空時使 CheckResult 的 `ok` 為 `True`。
4. IF `structure_errors` 為非空清單, THEN THE Version_Check_Script SHALL 使 CheckResult 的 `ok` 為 `False` 並使 `run_check` 回傳非零 exit code。
5. WHILE Pre_Commit_Hook 於 commit 建立前呼叫 Version_Check_Script, THE Capability_A_Wiring_Guard SHALL 透過 Structure_Check_Layer 於該次 commit 當下即對能力 A 接線違規生效並阻擋 commit。

---

### Requirement 7: 報告層呈現結構違規

**User Story:** As a KeepLink 維護者, I want 檢查報告在結構違規時明確列出接線問題, so that 我能立即知道能力 A 的哪條接線被改壞。

#### Acceptance Criteria

1. WHEN CheckResult 的 `structure_errors` 為非空時, THE Version_Report SHALL 包含「能力 A 接線結構違規」分節並列出每一條 Structure_Error 說明。
2. WHILE CheckResult 的 `structure_errors` 為空清單, THE Version_Report SHALL 不附上「能力 A 接線結構違規」分節。
3. THE Version_Report SHALL 維持既有分派優先序（擷取失敗 fail-fast > 一致 > 不一致/格式違規/結構違規），不改變既有來源一致與不一致情境的報告格式契約。

---

### Requirement 8: 對外契約不變

**User Story:** As a hook/CI/未來 git skill 的整合者, I want 檢查腳本的對外行為在本修正後維持不變, so that 既有呼叫端無需調整即可繼續依 exit code 與輸出判斷結果。

#### Acceptance Criteria

1. THE Version_Check_CLI SHALL 維持 no-args 情境以內部集中維護的 SOURCE_SPECS 完成預設檢查。
2. THE Version_Check_CLI SHALL 維持 exit code 語義：`0` 表示一致、非零表示不一致/格式違規/結構違規/擷取失敗。
3. IF 檢查結果為不一致或檢查失敗, THEN THE Version_Check_CLI SHALL 將 Version_Report 輸出至標準錯誤或標準輸出（emission 義務）。
4. THE Version_Check_CLI SHALL 於 `main()` 開頭以 `reconfigure(encoding="utf-8")` 並於輸出後 `flush()`，確保 Version_Report 可被呼叫端以正確編碼可靠擷取，不於 Windows 非 UTF-8 console 發生 `UnicodeEncodeError`（capturability 義務）。
5. IF 掃描過程中遇到第一個無法擷取版本的來源, THEN THE Version_Check_Script SHALL 立即停止對其餘來源的檢查、以非零 exit code 結束，並於 Version_Report 僅指出該第一個無法擷取的來源（true fail-fast，維持既有語義）。

---

### Requirement 9: 既有測試連帶更新

**User Story:** As a KeepLink 維護者, I want 受影響的既有測試隨實作同步更新, so that 測試不再依賴已刪符號，且 property 測試不被弱化。

#### Acceptance Criteria

1. THE Version_Consistency_Test SHALL 移除對 `extract_pyproject_version`、`extract_app_version`、`is_derived` 與 3-tuple `SOURCE_SPECS` 的所有引用。
2. THE Version_Consistency_Test SHALL 將一致性斷言改以 3 個 Manual_Version_Source 進行，並斷言 SOURCE_SPECS 為 3 項 2-tuple 且首筆為權威來源。
3. WHEN Version_Consistency_Test 對 `check_consistency` 進行 property-based test 時, THE Version_Consistency_Test SHALL 傳入新增的 `structure_errors` 參數並維持至少 100 次 hypothesis 迭代，且不弱化既有斷言。
4. WHEN Version_Consistency_Test 對 true fail-fast 進行 property-based test 時, THE Version_Consistency_Test SHALL 依 2-tuple SOURCE_SPECS 調整 spy/mock 並維持至少 100 次 hypothesis 迭代。
5. THE Version_Consistency_Test SHALL 以呼叫 `check_structure(repo_root)` 斷言其對真實 `pyproject.toml` / `app.py` 回傳空清單，並以反例內容斷言其回傳非空且含對應說明。

---

### Requirement 10: 清理與既有 spec 的紀錄

**User Story:** As a KeepLink 維護者, I want 既有 spec 的誤加任務被清除且本次修正有設計紀錄, so that 既有 spec 回到乾淨完結狀態、且本次剃除與改法可追溯。

#### Acceptance Criteria

1. THE 本修正 SHALL 移除 `version-consistency-gate/tasks.md` 中先前對話誤加的「任務 15」整段，使該舊 spec 回到乾淨的 30/30 完結狀態。
2. THE 本修正 SHALL 於 `version-consistency-gate/design.md` 新增一段「設計修正紀錄」，說明本次剃除 Derived 執行值擷取與改以結構檢查的改法。
3. THE 本修正 SHALL 不更動 `version-consistency-gate` 既有 requirements 的編號與需求意圖。

---

### Requirement 11: 驗收以 CI 四個 job 全綠為依據

**User Story:** As a KeepLink 維護者, I want 以 GitHub Actions CI 四個 job 全綠作為完成依據, so that 跨平台 × 跨 Python 版本的最小環境可跑性被真正驗證（本機測試不算數）。

#### Acceptance Criteria

1. WHEN 於本機執行 `ruff check scripts/ test/` 時, THE 本修正 SHALL 使其不回報任何錯誤。
2. WHEN 於本機執行 `pytest test/` 時, THE 本修正 SHALL 使所有測試通過（含 property tests ≥100 迭代）。
3. WHEN 於 repo 根目錄執行 `python scripts/check_version.py` 時, THE Version_Check_Script SHALL 以 exit code `0` 結束。
4. WHEN 對 `scripts/check_version.py` 執行 grep 時, THE 檔案 SHALL 不含 `tomllib`、`import keeplink_mcp`、`hatchling` 與 `sqlalchemy` 任一字樣。
5. WHEN 將本修正 push 後, THE CI_Pipeline SHALL 於 CI_Matrix 四個 job（`ubuntu-latest` + `windows-latest` × Python `3.10` + `3.13`）全部標記為通過，且此為本修正完成與否的最終依據。
