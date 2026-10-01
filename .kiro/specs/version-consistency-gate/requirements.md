# Requirements Document

## Introduction

本規格書處理 KeepLink-MCP 長期存在的「版本號不一致」硬傷。專案的版本字串目前散落在多個需手動同步的來源（`pyproject.toml`、`src/keeplink_mcp/__init__.py`、`src/keeplink_mcp/api/app.py` 的 FastAPI metadata、`README.md`、`docs/handoverbook.md`），改一處常忘了改其他；而既有的一致性測試只在 CI（push 之後）才跑，攔截太晚。本 spec 分兩大能力處理此問題：

- **能力 A — 消滅同步點（單一真實來源）**：以 `src/keeplink_mcp/__init__.py` 的 `__version__` 作為唯一權威來源（Authoritative_Version）。`pyproject.toml` 改用 hatchling 動態版本，`FastAPI_Service` app metadata 改為 import `__version__`，使手動同步點由 4 個降為 1 個。
- **能力 B — 提早攔截（commit 當下 + CI 雙層）**：新增單一檢查腳本 `scripts/check_version.py`，供 git pre-commit hook 與 CI 共同呼叫；在 commit 當下即攔截不一致，並於 CI 再次把關。`docs/handoverbook.md` 這類同時含「當前版本標記」與「歷史版本記錄」的混合文件，以只抓頂部當前版本標記的專屬規則納管，不誤判歷史記錄。整體採 fail-fast：不一致時明確列出 `{來源: 版本}` 並以非零 exit code 終止，不靜默通過。

本 spec 承接 keeplink-v1x-improvements（版本已對齊為 `1.2.0`），沿用其既有術語（Authoritative_Version、Version_Source 等）。系統為 Windows 11，CI 同時有 `windows-latest` 與 `ubuntu-latest`，故檢查腳本與 hook 必須跨平台。

### 明確不在範圍（Out of Scope）

- 不改寫已推送的 git 歷史（過去用錯作者身分的 commit）。
- git skill 的統一與改良（對齊三個 CLI 的 git skill、補 secret 掃描、修 description 反模式）屬「之後的另一件事」，不在本 spec；但本 spec 產出的 Version_Check_Script 須可被外部（hook/CI/skill）以簡單 CLI 方式呼叫並回傳明確 exit code。

## Glossary

- **KeepLink_System**：KeepLink-MCP 整體系統。
- **FastAPI_Service**：提供 HTTP API 與背景 Worker 的服務進程（`src/keeplink_mcp/api/`），其 app metadata 帶有 `version` 欄位。
- **Version_Source**：任一標示 KeepLink_System 版本字串的位置。本 spec 納管的 Version_Source 為：`src/keeplink_mcp/__init__.py` 的 `__version__`、`pyproject.toml` 的專案版本、`FastAPI_Service` app metadata 的 `version`、`README.md` 頂部顯示的版本字串、`docs/handoverbook.md` 頂部的當前版本標記。
- **Authoritative_Version**：版本真相的唯一權威來源，定義為 `src/keeplink_mcp/__init__.py` 的 `__version__`。
- **Derived_Version_Source**：其版本值於建置或執行期由 Authoritative_Version 推導而非手動維護的 Version_Source（能力 A 完成後包含 `pyproject.toml` 與 `FastAPI_Service` app metadata）。
- **Manual_Version_Source**：其版本值仍以人工維護、需由檢查機制把關與 Authoritative_Version 一致的 Version_Source（能力 A 完成後包含 `README.md` 頂部版本字串與 `docs/handoverbook.md` 頂部當前版本標記）。
- **Handoverbook_Current_Marker**：`docs/handoverbook.md` 頂部標示「當前版本」的單一標記（形如 `版本：v<X.Y.Z>`），與檔案中的歷史版本記錄（Release Notes、ADR 內提及的舊版本等）區隔。
- **Historical_Version_Reference**：`docs/handoverbook.md` 中屬於歷史記錄而非當前版本的版本字串（例如 `v1.1.1`、`1.1.0`），不納入一致性檢查。
- **Semver_Format**：語意化版本字串格式，定義為符合正規表示式 `^\d+\.\d+\.\d+$`。
- **Version_Check_Script**：新增於 repo 內的單一檢查腳本 `scripts/check_version.py`，封裝版本一致性檢查邏輯，作為 CI 與 Pre_Commit_Hook 共同呼叫的 single source of truth。
- **Version_Check_CLI**：Version_Check_Script 對外的命令列呼叫介面，供 hook、CI 與未來的 git skill 呼叫。
- **Version_Report**：Version_Check_Script 執行後輸出的檢查結果，於不一致時包含每個受檢 Version_Source 及其版本值的 `{來源: 版本}` 列表。
- **Pre_Commit_Hook**：安裝於本機 git 的 `pre-commit` 掛鉤，於 commit 建立前呼叫 Version_Check_Script。
- **CI_Pipeline**：GitHub Actions 工作流程（`.github/workflows/ci.yml`）。
- **CI_Matrix**：CI_Pipeline 的作業系統與 Python 版本組合矩陣，包含 `ubuntu-latest` 與 `windows-latest`。
- **Version_Consistency_Test**：驗證版本一致性與檢查邏輯的自動化測試（含 property-based test），置於 `test/`。

## Requirements

### Requirement 1: 建立單一權威版本來源（能力 A）

**User Story:** As a KeepLink 維護者, I want 版本字串只有一個需人工維護的權威來源, so that 我升版時只改一處，其餘來源自動跟隨，不再互相矛盾。

#### Acceptance Criteria

1. THE KeepLink_System SHALL 以 `src/keeplink_mcp/__init__.py` 的 `__version__` 作為 Authoritative_Version。
2. THE Authoritative_Version SHALL 符合 Semver_Format。
3. THE KeepLink_System SHALL 使需人工維護版本值的 Manual_Version_Source 數量不超過兩個（`README.md` 頂部版本字串與 `docs/handoverbook.md` 頂部當前版本標記），其餘納管的 Version_Source 皆為 Derived_Version_Source。

---

### Requirement 2: pyproject.toml 採用 hatchling 動態版本（能力 A）

**User Story:** As a KeepLink 維護者, I want pyproject.toml 的版本由 __version__ 動態推導, so that 我不需要在 pyproject.toml 手動同步版本號。

#### Acceptance Criteria

1. THE `pyproject.toml` SHALL 於 `[project]` 宣告 `dynamic = ["version"]` 且不再寫死 `version` 靜態字串。
2. THE `pyproject.toml` SHALL 於 `[tool.hatch.version]` 設定 `path = "src/keeplink_mcp/__init__.py"`，使 hatchling 由 Authoritative_Version 推導專案版本。
3. WHEN 以 hatchling 建置或查詢專案版本時, THE `pyproject.toml` 所推導出的版本 SHALL 等於 Authoritative_Version。
4. THE `pyproject.toml` SHALL 保留既有 build backend 為 hatchling 的設定與既有 wheel 打包設定，不破壞既有建置行為。

---

### Requirement 3: FastAPI app metadata 引用權威版本（能力 A）

**User Story:** As a KeepLink 維護者, I want FastAPI 服務的版本 metadata 直接引用 __version__, so that API 顯示的版本永遠與權威來源一致而無需手動同步。

#### Acceptance Criteria

1. THE FastAPI_Service SHALL 於 `src/keeplink_mcp/api/app.py` 匯入 `from keeplink_mcp import __version__`，並以 `__version__` 作為 app metadata 的 `version` 值。
2. THE FastAPI_Service SHALL 不於 `src/keeplink_mcp/api/app.py` 寫死任何版本字串常量作為 app metadata 的 `version`。
3. WHEN FastAPI_Service 建立 app 實例時, THE FastAPI_Service app metadata 的 `version` SHALL 等於 Authoritative_Version。

---

### Requirement 4: 單一版本檢查腳本（能力 B 核心）

**User Story:** As a KeepLink 維護者, I want 一支可被多方呼叫的版本檢查腳本, so that pre-commit hook、CI 與未來的 git skill 都跑同一套一致性邏輯而不重複實作。

#### Acceptance Criteria

1. THE Version_Check_Script SHALL 位於 `scripts/check_version.py`。
2. THE Version_Check_Script SHALL 蒐集所有納管的 Version_Source（`__init__.py` 的 `__version__`、`pyproject.toml` 推導版本、`FastAPI_Service` app metadata 的 `version`、`README.md` 頂部版本字串、`docs/handoverbook.md` 的 Handoverbook_Current_Marker）並將每個來源的版本值與 Authoritative_Version 比對。
3. WHEN 所有納管 Version_Source 的版本字串皆等於 Authoritative_Version 且皆符合 Semver_Format, THE Version_Check_Script SHALL 以 exit code `0` 結束。
4. IF 任一納管 Version_Source 的版本字串與 Authoritative_Version 不相等, THEN THE Version_Check_Script SHALL 以非零 exit code 結束，並於 Version_Report 輸出每個受檢 Version_Source 及其版本值的 `{來源: 版本}` 列表。
5. IF 任一納管 Version_Source 的版本字串不符合 Semver_Format, THEN THE Version_Check_Script SHALL 以非零 exit code 結束，並於 Version_Report 指出違反格式的來源與其版本值。
6. IF 掃描過程中遇到第一個無法被讀取或無法從中擷取版本字串的 Version_Source, THEN THE Version_Check_Script SHALL 立即停止對其餘 Version_Source 的檢查、以非零 exit code 結束，並於 Version_Report 僅指出該第一個無法擷取版本的來源（true fail-fast，不繼續掃描其餘來源、不將失敗來源累積成一次性清單）。
7. THE Version_Check_Script SHALL 於 `ubuntu-latest` 與 `windows-latest` 兩種作業系統皆能執行，並以與作業系統無關的方式解析檔案路徑與讀取檔案內容。

---

### Requirement 5: handoverbook.md 當前版本標記專屬規則（能力 B）

**User Story:** As a KeepLink 維護者, I want 檢查腳本只驗證 handoverbook 的當前版本標記, so that 大量歷史版本記錄不會被誤判為不一致。

#### Acceptance Criteria

1. THE Version_Check_Script SHALL 僅以 `docs/handoverbook.md` 的 Handoverbook_Current_Marker 作為該檔案的版本值來源。
2. THE Version_Check_Script SHALL 依據頂部當前版本標記的形式（`版本：v<X.Y.Z>`）擷取 Handoverbook_Current_Marker 的版本字串。
3. WHILE `docs/handoverbook.md` 同時含有 Handoverbook_Current_Marker 與一個以上的 Historical_Version_Reference, THE Version_Check_Script SHALL 只比對 Handoverbook_Current_Marker 而不將任何 Historical_Version_Reference 納入一致性判定。
4. IF `docs/handoverbook.md` 缺少可辨識的 Handoverbook_Current_Marker, THEN THE Version_Check_Script SHALL 以非零 exit code 結束並於 Version_Report 指出當前版本標記缺失（fail-fast）。

---

### Requirement 6: Git pre-commit hook 於 commit 當下攔截（能力 B）

**User Story:** As a KeepLink 貢獻者, I want commit 前自動檢查版本一致性, so that 不論用哪個工具或手動 git 提交，版本不一致都在 push 之前就被擋下。

#### Acceptance Criteria

1. THE Pre_Commit_Hook SHALL 於 git `pre-commit` 階段呼叫 Version_Check_Script。
2. IF Pre_Commit_Hook 呼叫 Version_Check_Script 得到非零 exit code, THEN THE Pre_Commit_Hook SHALL 阻擋該次 commit 並將 Version_Report 呈現給提交者。
3. WHEN Pre_Commit_Hook 呼叫 Version_Check_Script 得到 exit code `0`, THE Pre_Commit_Hook SHALL 允許該次 commit 繼續。
4. THE Pre_Commit_Hook SHALL 於 `ubuntu-latest`（Linux）與 Windows 11 開發環境皆可執行。
5. WHERE commit 經由標準 git commit 流程（會觸發 git 的 `pre-commit` 階段）被建立, THE Pre_Commit_Hook SHALL 對任何觸發該 commit 的方式（AI 工具或手動 `git commit`）皆生效，不依賴任何特定編輯器或 AI 工具的整合。
6. THE 規格 SHALL 提供讓貢獻者於本機啟用 Pre_Commit_Hook 的方式，且不依賴任何需連外網下載的第三方 pre-commit 框架作為運作前提。
7. IF 某工具以繞過標準 git commit 流程的方式（不觸發 git 的 `pre-commit` 階段，例如直接以 libgit2 或直接寫入 `.git/objects` 建立 commit）建立 commit, THEN 該情境 SHALL 不由 Pre_Commit_Hook 涵蓋，而改由 CI_Pipeline（見 Requirement 7）於 push 或 PR 階段作為第二道防線攔截版本不一致。

---

### Requirement 7: CI 呼叫同一支檢查腳本（能力 B）

**User Story:** As a KeepLink 維護者, I want CI 也跑同一支版本檢查腳本, so that 即使有人繞過本機 hook，push/PR 階段仍會攔截版本不一致。

#### Acceptance Criteria

1. THE CI_Pipeline SHALL 於既有作業中呼叫 Version_Check_Script（`scripts/check_version.py`），而非另行實作一套版本檢查邏輯。
2. WHEN CI_Pipeline 於某作業實際執行 Version_Check_Script 且其回傳非零 exit code, THEN THE CI_Pipeline SHALL 使該實際執行中的作業標記為失敗，並使 Version_Report 出現於該作業的執行輸出。
3. WHEN CI_Pipeline 於某作業實際執行 Version_Check_Script 且其回傳 exit code `0`, THE CI_Pipeline SHALL 允許該版本檢查步驟通過。
4. THE CI_Pipeline SHALL 於 CI_Matrix 同時保留 `ubuntu-latest` 與 `windows-latest`，使版本檢查步驟於兩種作業系統皆被執行。
5. THE CI_Pipeline SHALL 保留既有 lint、test 與 security audit 步驟的行為，不因加入版本檢查步驟而移除或改變既有步驟。

---

### Requirement 8: 檢查腳本的可重用 CLI 介面（能力 B 對外契約）

**User Story:** As a 未來 git skill 的整合者, I want 以簡單且穩定的 CLI 方式呼叫版本檢查, so that hook、CI 與 git skill 都能依 exit code 判斷結果而無需理解內部實作。

#### Acceptance Criteria

1. THE Version_Check_CLI SHALL 可透過單一命令（例如 `python scripts/check_version.py`）於 repo 根目錄被呼叫。
2. THE Version_Check_CLI SHALL 以 exit code `0` 表示一致、以非零 exit code 表示不一致或檢查失敗。
3. IF 檢查結果為不一致或檢查失敗, THEN THE Version_Check_CLI SHALL 將 Version_Report 輸出至標準錯誤或標準輸出（emission 義務：確保有輸出）。
4. THE Version_Check_CLI SHALL 確保其輸出的 Version_Report 可被呼叫端（hook/CI/skill）可靠擷取，包含以正確編碼輸出、且不因緩衝或輸出串流問題而遺失內容（capturability 義務：確保呼叫端確實擷取得到）。
5. WHERE 呼叫端未提供任何參數（no-args 情境）, THE Version_Check_CLI SHALL 以 Version_Check_Script 內部集中維護的來源清單完成預設檢查，不要求呼叫端提供版本值或來源清單作為參數；當呼叫端有提供版本值或來源清單參數時，其行為（是否忽略或補充該參數）不在本需求的保證範圍內。

---

### Requirement 9: 版本一致性與檢查邏輯的自動化測試

**User Story:** As a KeepLink 維護者, I want 檢查邏輯本身有自動化測試涵蓋, so that 一致與不一致兩種情境都被驗證，且升版時的 drift 會失敗得很大聲。

#### Acceptance Criteria

1. THE Version_Consistency_Test SHALL 置於 `test/` 目錄。
2. WHEN Version_Consistency_Test 執行時, THE Version_Consistency_Test SHALL 蒐集所有納管的 Version_Source 並斷言其版本值皆等於 Authoritative_Version。
3. WHEN Version_Consistency_Test 對檢查邏輯進行 property-based test 時, THE Version_Consistency_Test SHALL 以至少 100 次 hypothesis 迭代驗證「所有來源相等且皆符合 Semver_Format 時檢查通過、任一來源不相等時檢查失敗」的性質。
4. WHEN 檢查邏輯偵測到不一致而失敗時, THE Version_Consistency_Test SHALL 斷言失敗訊息包含每個不一致來源與其版本值（對應 Version_Report 的 `{來源: 版本}` 列表）。
5. WHEN Version_Consistency_Test 驗證 `docs/handoverbook.md` 的處理時, THE Version_Consistency_Test SHALL 斷言 Handoverbook_Current_Marker 被納入比對而 Historical_Version_Reference 不被納入。
6. THE Version_Consistency_Test SHALL 以呼叫 Version_Check_Script 所提供的檢查邏輯為受測對象，不重複實作一套獨立的一致性判定。
