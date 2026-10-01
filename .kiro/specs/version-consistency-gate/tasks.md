# Implementation Plan: version-consistency-gate

## Overview

本實作計畫依 design.md 的兩大能力與分層架構切分任務，遵守專案硬規則（interface first、單檔 ≤500 行、單函式 ≤50 行、SoC/DRY、fail-fast、跨平台）。

實作語言為 **Python**（design 已使用具體 Python：dataclass、type hints、hatchling、hypothesis）。

任務分組原則：

- **能力 A**（Derived_Version_Source 化：`pyproject.toml` + `app.py`）與**能力 B**（`scripts/check_version.py` 檢查腳本）大致獨立，可並行推進。
- 能力 B 內部依「介面骨架（dataclass + 簽章）→ 各層實作（擷取 → 蒐集 → 判定 → 報告 → CLI）→ 測試」的順序，每步建立在前一步之上，最終由 CLI 收斂為單一進入點。
- 測試（property / example / smoke）在其受測程式碼實作完成後才進行。
- hook 與 CI 為薄殼，依賴 `check_version.py` CLI 契約穩定後再接線。
- 每個 property test 以註解標記 `Feature: version-consistency-gate, Property {n}: {property_text}`，hypothesis ≥100 迭代。
- 測試放 `test/`、文件放 `docs/`、腳本放 `scripts/`、hook 放 `.githooks/`。

## Tasks

- [x] 1. 能力 B — 建立 check_version.py 介面骨架（interface first）
  - 建立新模組 `scripts/check_version.py`
  - 定義 `VersionSource`（frozen dataclass：`name: str`、`version: str`、`is_derived: bool`）與 `CheckResult`（frozen dataclass：`authoritative: str`、`sources: list[VersionSource]`、`ok: bool`、`mismatches: dict[str, str]`、`invalid_format: dict[str, str]`、`extraction_error: str | None`）
  - 定義常數 `REPO_ROOT`、`SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")`、`HEADER_SCAN_LINES = 10`、`SOURCE_SPECS`
  - 為擷取層 / 蒐集層 / 判定層 / 報告層 / CLI 的所有函式先寫出型別註記簽章與 docstring（實作留待後續任務），確保 SoC 分層邊界清楚
  - _Requirements: 4.1, 8.5_

- [x] 2. 能力 B — 擷取層（extraction，純函式 + fail-fast）
  - [x] 2.1 實作 Manual 來源擷取函式 `extract_init_version` 與 `extract_readme_version`
    - `extract_init_version(repo_root: Path) -> str`：讀 `src/keeplink_mcp/__init__.py`，regex `__version__ = "X.Y.Z"`；失敗 `raise ValueError`（指明來源與原因）
    - `extract_readme_version(repo_root: Path) -> str`：讀 `README.md`，regex `Version:\s*(\d+\.\d+\.\d+)`；失敗 `raise ValueError`
    - 以與作業系統無關的方式解析路徑（`pathlib.Path`）與讀取檔案（明確 `encoding="utf-8"`）
    - _Requirements: 4.2, 4.7_

  - [x] 2.2 實作 `extract_handoverbook_version`（handoverbook 當前標記專屬規則）
    - 只掃描檔首前 `HEADER_SCAN_LINES` 行，regex `版本：v(\d+\.\d+\.\d+)` 取**第一個**命中（`re.search` 首個 match），回傳 group(1)
    - 前 N 行內找不到 `版本：v<semver>` 即 `raise ValueError`（不 fallback 掃全檔、不回空值）
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 2.3 實作 Derived 來源擷取函式 `extract_pyproject_version` 與 `extract_app_version`
    - `extract_pyproject_version(repo_root: Path) -> str`：取 hatchling 推導版本（`importlib.metadata.version("keeplink-mcp")` 於已安裝環境，或 hatchling metadata API）；推導失敗 `raise`
    - `extract_app_version(repo_root: Path) -> str`：import app factory 建立實例後讀 `app.version`；失敗 `raise`
    - 兩者皆為 Derived_Version_Source（其值定義上應 == 權威），此層僅負責取得推導值
    - _Requirements: 2.3, 3.3_

  - [x] 2.4 為擷取層寫 unit / edge case 測試
    - example：對真實 `docs/handoverbook.md` 斷言擷取到當前版本；對真實 `__init__.py`/`README.md` 斷言擷取成功
    - edge case：生成「缺標記」「標記在掃描窗外（第 100+ 行）」的 handoverbook 內容，斷言 `extract_handoverbook_version` `raise ValueError`
    - _Requirements: 5.2, 5.4_

  - [x] 2.5 為 Property 4 寫 property test（handoverbook 防污染）
    - **Property 4: Handoverbook 只擷取當前標記、不受歷史引用污染**
    - **Validates: Requirements 5.1, 5.3, 9.5**
    - 生成策略：頂部當前標記 semver ＋ 檔尾附加隨機數量、隨機值的歷史 `v<semver>` / `升級至 <semver>` 片段；斷言 `extract_handoverbook_version` 回傳恆等於頂部當前標記值
    - 註解標記 `Feature: version-consistency-gate, Property 4: ...`；hypothesis ≥100 迭代

- [x] 3. 能力 B — 蒐集層（collect，true fail-fast）
  - [x] 3.1 實作 `SOURCE_SPECS` 與 `collect_sources`
    - 填入集中維護的 `SOURCE_SPECS`（`__init__.py` 權威、`pyproject.toml` Derived、FastAPI app metadata Derived、`README.md` Manual、`docs/handoverbook.md` Manual），供 no-args 預設檢查使用
    - `collect_sources(repo_root: Path) -> tuple[list[VersionSource], str | None]`：依 `SOURCE_SPECS` 順序逐一呼叫擷取函式，**第一個**擷取例外即停止、不續掃、不累積清單，回傳（已成功來源, 首個錯誤說明 or None）
    - _Requirements: 4.2, 4.6, 8.5_

  - [x] 3.2 為 Property 3 寫 property test（true fail-fast 停止點）
    - **Property 3: True fail-fast 於首個擷取失敗即停止**
    - **Validates: Requirements 4.6**
    - 生成策略：生成失敗索引 `k`，令第 k 個擷取函式拋錯並以 spy/mock 計數後續呼叫；斷言已成功來源恰為索引 0..k-1（數量 == k）、錯誤說明只提及索引 k、索引 k 之後的擷取函式從未被呼叫
    - 註解標記 `Feature: version-consistency-gate, Property 3: ...`；hypothesis ≥100 迭代

- [x] 4. 能力 B — 判定層（checking，純函式無 I/O）
  - [x] 4.1 實作 `is_semver` 與 `check_consistency`
    - `is_semver(version: str) -> bool`：以 `SEMVER_PATTERN` 判定
    - `check_consistency(authoritative: str, sources: list[VersionSource]) -> CheckResult`：比對每個來源 == authoritative 且皆合法 semver，產生 `mismatches`（不等於權威者）與 `invalid_format`（格式違規者）、設定 `ok`；純資料轉換、無 I/O
    - _Requirements: 4.3, 4.4, 4.5, 1.2_

  - [x] 4.2 為 Property 1 寫 property test（一致性判定與不一致報告）
    - **Property 1: 一致性判定與不一致報告**
    - **Validates: Requirements 4.2, 4.3, 4.4, 9.3, 9.4**
    - 生成策略：生成 semver 值套用到所有來源（一致集）→ `ok=True`；隨機挑一來源改為不同 semver（不一致集）→ `ok=False` 且報告列出每個來源與其版本值
    - 受測對象 `check_consistency` + `render_report`（依賴任務 5.1）
    - 註解標記 `Feature: version-consistency-gate, Property 1: ...`；hypothesis ≥100 迭代

  - [x] 4.3 為 Property 2 寫 property test（Semver 格式違規偵測）
    - **Property 2: Semver 格式違規偵測**
    - **Validates: Requirements 1.2, 4.5**
    - 生成策略：以 `st.text()` 過濾掉合法 semver 產生非 semver 字串，注入某來源；斷言 `ok=False` 且 `invalid_format` 含該來源與其版本值
    - 註解標記 `Feature: version-consistency-gate, Property 2: ...`；hypothesis ≥100 迭代

- [x] 5. 能力 B — 報告層 + CLI（對外契約 + 編碼）
  - [x] 5.1 實作 `render_report`
    - `render_report(result: CheckResult) -> str`：不一致時含每個受檢來源的 `{來源: 版本}` 列表、格式違規來源、或首個擷取失敗來源；一致時回簡短 OK 訊息
    - _Requirements: 4.4, 4.5, 4.6_

  - [x] 5.2 實作 `run_check` 與 `main`（CLI 進入點 + Windows UTF-8 對策）
    - `run_check(repo_root: Path | None = None) -> int`：協調 `collect_sources` → `check_consistency` → `render_report`；回傳 exit code（0 一致 / 非零 其餘：不一致 / 格式違規 / 擷取失敗）
    - `main() -> None`：開頭 `sys.stdout.reconfigure(encoding="utf-8")` 與 `sys.stderr.reconfigure(encoding="utf-8")`；no-args 走 `SOURCE_SPECS` 預設檢查；不一致/失敗時把 Version_Report 寫 stderr 並 `flush()`、一致時簡短寫 stdout；`sys.exit(run_check())`
    - 加上 `if __name__ == "__main__": main()`
    - _Requirements: 4.3, 4.4, 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 5.3 為 Property 5 寫 property test（Version_Report 編碼 round-trip）
    - **Property 5: Version_Report 編碼 round-trip**
    - **Validates: Requirements 8.4**
    - 生成策略：生成 ASCII + 中文（CJK 範圍）+ emoji 混合字串，經 UTF-8 輸出串流寫出後以 UTF-8 讀回，斷言解碼字串 == 原始內容
    - 註解標記 `Feature: version-consistency-gate, Property 5: ...`；hypothesis ≥100 迭代

  - [x] 5.4 為 CLI 契約寫 example 測試（子行程呼叫）
    - 以 `subprocess` 呼叫 `python scripts/check_version.py`：斷言 no-args 可跑、對一致情境 exit code 0、對不一致情境非零、不一致時輸出非空且含來源清單
    - _Requirements: 8.1, 8.2, 8.3, 8.5_

- [x] 6. Checkpoint — 確認能力 B 腳本可獨立運作
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. 能力 A — pyproject.toml 改 hatchling 動態版本
  - 於 `[project]` 移除靜態 `version = "..."`，改宣告 `dynamic = ["version"]`
  - 新增 `[tool.hatch.version]` 設定 `path = "src/keeplink_mcp/__init__.py"`
  - 保留既有 build backend（hatchling）與既有 wheel 打包設定（`[tool.hatch.build.targets.wheel]`）不變
  - _Requirements: 2.1, 2.2, 2.4_

- [x] 8. 能力 A — app.py 引用權威版本
  - 於 `src/keeplink_mcp/api/app.py` 改為 `from keeplink_mcp import __version__`
  - app metadata `version` 改用 `__version__`，移除任何寫死的版本字面量
  - _Requirements: 3.1, 3.2_

- [x] 9. 能力 A — 結構檢查與推導驗證 example 測試
  - 斷言 `pyproject.toml` 含 `dynamic = ["version"]`、無靜態 `version` 行、`[tool.hatch.version].path` 正確、wheel 設定保留
  - 斷言 `app.py` `import __version__` 且無版本字面量
  - 斷言 `extract_pyproject_version() == extract_app_version() == __version__`（推導 == 權威）
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3_

- [x] 10. 既有測試遷移（待決點 3：改為 import check_version.py）
  - [x] 10.1 重構 test/test_version_consistency.py 為 import 腳本函式
    - 於 `test/`（或 `conftest.py`）註冊 `scripts` 為可 import 路徑
    - 改為 `from scripts.check_version import ...`，刪除測試內重複的擷取/判定實作
    - 依遷移對應表處理：`get_pyproject_version` → 移除（改由 `extract_pyproject_version` 走推導驗證）；`get_app_version` → 移除（改驗 `import __version__` 推導結果）；`get_init_version`/`get_readme_version` → 併入 `extract_init_version`/`extract_readme_version`；新增 `extract_handoverbook_version` 涵蓋；`check_version_consistency` → `check_consistency`
    - 保留既有 `_TARGET_VERSION = "1.2.0"` golden/example 測試精神（drift 大聲失敗），其 handoverbook 檢查改用腳本的 `extract_handoverbook_version`
    - _Requirements: 9.1, 9.6_

  - [x] 10.2 補齊真實來源一致性與來源清單組成 example 測試
    - 斷言 `collect_sources` 所得全部 == `__version__`（Req 9.2）
    - 斷言權威來源為 `__init__.py`、Manual 來源數為 2、其餘為 Derived（Req 1.1–1.3）
    - 斷言檢查失敗時失敗訊息含每個不一致來源與其版本值（Req 9.4）、handoverbook 只納 current marker 不納歷史（Req 9.5）
    - _Requirements: 1.1, 1.3, 9.2, 9.4, 9.5_

- [x] 11. 能力 B — pre-commit hook（薄殼、跨平台、受版控）
  - 建立 `.githooks/pre-commit`（POSIX sh）：以 `command -v python` 偵測選 `python`/`python3`（執行環境相容，非業務 fallback），呼叫 `python scripts/check_version.py`，非零 exit 時輸出攔截訊息至 stderr 並 `exit 1`
  - 於 `CONTRIBUTING.md` 新增啟用說明：`git config core.hooksPath .githooks`（不依賴需連外網的第三方 pre-commit 框架）
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [x] 12. 能力 B — CI 呼叫同一支檢查腳本
  - 於 `.github/workflows/ci.yml` 既有 `test` job 內、`Install dependencies` 之後新增一步 `Version consistency check`（`run: python scripts/check_version.py`）
  - 保留既有 lint、test、security audit 步驟不變；matrix 保留 `ubuntu-latest` + `windows-latest`，使版本檢查步驟於兩平台皆執行
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [x] 13. Smoke 測試（基礎設施存在性）
  - 斷言 `scripts/check_version.py` 存在且可 import（4.1）
  - 斷言 `.githooks/pre-commit` 存在且呼叫腳本（6.1, 6.5, 6.6）
  - 斷言 `.github/workflows/ci.yml` 含版本檢查步驟（7.1）、matrix 含 ubuntu+windows（7.4）、保留 lint/test/audit（7.5）
  - 斷言 `CONTRIBUTING.md` 含 `core.hooksPath` 啟用說明（6.6）
  - _Requirements: 4.1, 6.1, 6.5, 6.6, 7.1, 7.4, 7.5_

- [x] 14. Final checkpoint — lint 與測試收尾
  - 執行 `ruff check src/ test/ scripts/` 並修正所有告警
  - 執行 `pytest test/ -v` 確認全部通過（含 property tests ≥100 迭代）
  - 確認 `python scripts/check_version.py` 於本機回 exit code 0
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- 標記 `*` 的子任務為測試相關（unit / property / example / smoke），可為快速 MVP 略過；核心實作任務不得標記為可選。
- 每個任務標註對應的 requirements 子條款以利追溯。
- 能力 A（任務 7、8、9）與能力 B 腳本（任務 1–5）大致獨立，可並行；任務 10 的既有測試遷移依賴能力 B 腳本函式（1–5）與能力 A 變更（7、8）皆就緒。
- 5 條 Correctness Properties 各自獨立為一個 property test 子任務，緊鄰其受測實作以提早抓錯。
- Checkpoint（任務 6、14）確保增量驗證。

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "7", "8"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3", "9"] },
    { "id": 2, "tasks": ["2.4", "2.5", "3.1"] },
    { "id": 3, "tasks": ["3.2", "4.1"] },
    { "id": 4, "tasks": ["4.3", "5.1"] },
    { "id": 5, "tasks": ["4.2", "5.2"] },
    { "id": 6, "tasks": ["5.3", "5.4", "11", "12"] },
    { "id": 7, "tasks": ["10.1"] },
    { "id": 8, "tasks": ["10.2", "13"] }
  ]
}
```
