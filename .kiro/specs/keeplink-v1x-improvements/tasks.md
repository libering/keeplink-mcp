# Implementation Plan: KeepLink v1.x Improvements（A / C / H / I）

## Overview

依改進分組（A 版本源統一 → C 失敗任務手動重試 `retry_task` → H `archive_and_cite` 多格式 citation → I CI 加 Windows matrix）逐項實作，各組彼此獨立、可分開驗證，每組結尾設 checkpoint。全批 **無資料庫 schema 變更**：改進 C 僅更新既有 `ArchiveTask` 欄位值並沿用既有 `fetch_pending_tasks` 管線；改進 H 為純函式擴充且向後相容（未指定 `format` 時逐字元不變）。

各組採 **interface-first** 順序：先定義列舉 / sentinel / schema / 函式簽章與 MCP inputSchema，再實作，最後補測試。測試分層：**property-based tests（hypothesis，每條 ≥100 iterations）** 驗證跨輸入不變式；**golden / example unit tests** 驗證具體字面輸出、工具/schema 註冊結構與具體邊界；兩者分開為不同 sub-task。property test 以註解 `# Feature: keeplink-v1x-improvements, Property {n}: {text}` 標註並對映 design 的 13 條 Correctness Properties。目標版本統一為 **1.2.0**，權威來源為 `src/keeplink_mcp/__init__.py` 的 `__version__`。

## Tasks

- [x] 1. 改進 A — 版本源統一（權威來源對齊 + 版本一致性測試擴充）
  - [x] 1.1 將 `src/keeplink_mcp/__init__.py` 的 `__version__` 設為權威值 `"1.2.0"`
    - 此為 Authoritative_Version，其餘來源皆對齊此字串
    - _Requirements: 1.1_

  - [x] 1.2 對齊其餘版本來源字串為 `1.2.0`
    - `pyproject.toml` 的 `version = "1.2.0"`（_Requirements: 1.2_）
    - `src/keeplink_mcp/api/app.py` 的 `FastAPI(title="KeepLink MCP", version="1.2.0")`，保留字面字串（不改為變數引用，以維持既有測試正則擷取）（_Requirements: 1.3_）
    - `README.md` 頂部顯示的版本字串 → `1.2.0`（_Requirements: 1.4_）
    - `docs/handoverbook.md` 版本標記 → `1.2.0`（_Requirements: 1.5_）
    - _Requirements: 1.2, 1.3, 1.4, 1.5_

  - [x] 1.3 擴充 `test/test_version_consistency.py` 的來源擷取（interface first）
    - 新增 `get_readme_version() -> str`：擷取 README.md 頂部版本 token；找不到則 `raise ValueError`（fail-fast，避免 README 未實際檢查卻綠燈）
    - 新增 `collect_version_sources() -> dict[str, str]`：回傳 `{'pyproject.toml', '__init__.py', 'app.py', 'README.md'}` 四源的版本字串，作為相等斷言與失敗訊息的單一來源清單
    - _Requirements: 1.6, 1.7_

  - [x] 1.4 擴充 `test/test_version_consistency.py` 的 Property 1 property-based test
    - **Property 1: 版本一致性與 semver 格式** — 四源版本字串全部相等（唯一值集合大小為 1）；每源符合 `^\d+\.\d+\.\d+$`；不相等時失敗訊息列出 `{source: version}`
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.6, 1.7, 1.8**
    - 以 `# Feature: keeplink-v1x-improvements, Property 1: {text}` 註解標註；hypothesis ≥100 iterations
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.6, 1.7, 1.8_

  - [x] 1.5 撰寫改進 A 的 example unit tests
    - `__version__ == "1.2.0"` 的具體斷言
    - `docs/handoverbook.md` 版本標記存在且為 `1.2.0`（doc-presence / 文件審查）
    - _Requirements: 1.1, 1.5_

- [x] 2. Checkpoint — 改進 A 完成
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. 改進 C — 失敗任務手動重試 `retry_task`（interface first）
  - [x] 3.1 定義改進 C 的介面骨架（sentinel / schema / MCP inputSchema）
    - `src/keeplink_mcp/db/repository.py`：定義 `RetryRejected` frozen dataclass（承載 `current_status: TaskStatus`）與 `retry_task` 簽章 `async def retry_task(self, task_id: str) -> ArchiveTask | RetryRejected | None`（尚未實作）
    - `src/keeplink_mcp/api/schemas.py`：新增 `RetryRequest`（`task_id: str`）與 `RetryResponse`（`task_id: str`、`status: str`）
    - `src/keeplink_mcp/mcp_server/server.py`：於 `list_tools()` 註冊 `retry_task` Tool，inputSchema 具必填字串 `task_id`（`required == ["task_id"]`）
    - _Requirements: 2.1_

  - [x] 3.2 實作 `TaskRepository.retry_task`（狀態轉換單一真實來源）
    - 以 status-guarded UPDATE 執行：`UPDATE ... WHERE task_id=:id AND status='failed'`，重設 `status=pending`、`retry_count=0`、`next_retry_at=NULL`、`error_message=NULL`
    - `rowcount == 1` → 回傳重新讀取的 `ArchiveTask`；`rowcount == 0` → 以一次 `get_task` 判別回傳 `None`（不存在）或 `RetryRejected(current_status)`（存在但非 failed）
    - 非 failed 態不得修改任何欄位（原子性、fail-fast、無副作用）
    - _Requirements: 2.3, 2.4, 2.6_

  - [x] 3.3 實作 Retry_Endpoint（`POST /api/retry`，routes.py）
    - 鏡射 `create_archive` 風格（`APIRouter`、`SessionDep`、`JSONResponse` 錯誤）
    - 將 `repo.retry_task` 三態結果對映 HTTP：`ArchiveTask` → 200 `RetryResponse(task_id, status="pending")`；`RetryRejected` → 409（detail 指出目前狀態且「僅允許 failed → pending」）；`None` → 404 not-found
    - _Requirements: 2.2, 2.4, 2.6, 2.7, 2.9_

  - [x] 3.4 實作 `retry_task` MCP 工具 handler（`_handle_retry_task`，server.py）
    - 於 `call_tool()` 分派；缺 `task_id` → `{"error": "task_id is required"}`（不 POST）
    - 沿用既有 httpx 模式對 `/api/retry` 發 POST；捕捉 `httpx.ConnectError`/`ConnectTimeout` → `{"error": "Backend service unavailable"}`（與既有 handler 逐字一致）
    - 200 → 回 `{task_id, status}` JSON；404 / 409 → 將後端 detail 逐字上呈（區分 not-found vs 非 failed 拒絕）
    - _Requirements: 2.8_

  - [x] 3.5 撰寫 `retry_task` 狀態轉換 property-based tests（`test/test_retry_repository_properties.py`）
    - 以 in-memory SQLite，隨機初始 `retry_count`（含 >0）、`next_retry_at`（含非 null）、`error_message`（含非 null）與任務狀態
    - **Property 2: retry 重設不變式** — failed 任務經 retry 後 `status==pending`、`retry_count==0`、`next_retry_at is None`、`error_message is None`，回傳 `task_id` 等於原值 — **Validates: Requirements 2.3, 2.4**
    - **Property 3: 非 failed 一律拒絕且不改任何欄位** — 非 failed 任務 retry 回 `RetryRejected(current_status)` 且所有欄位與呼叫前逐一相等 — **Validates: Requirements 2.6**
    - **Property 4: 重試冪等邊界** — failed 任務 retry 成功轉 pending 後再次 retry 回 `RetryRejected(current_status==pending)` 且不改任何欄位 — **Validates: Requirements 2.9**
    - 每個測試以 `# Feature: keeplink-v1x-improvements, Property {n}: {text}` 註解標註；hypothesis ≥100 iterations
    - _Requirements: 2.3, 2.4, 2.6, 2.9_

  - [x] 3.6 撰寫改進 C 的 example unit tests（`test/test_retry_task.py`）
    - 工具註冊：`list_tools()` 含 `retry_task`、`inputSchema.required == ["task_id"]`（Req 2.1）
    - 端點存在：`POST /api/retry` 可路由（Req 2.2）
    - not-found → 404（Req 2.7）；缺 `task_id` → 對應 error（Req 2.1）；backend down（注入 `httpx.ConnectError`）→ backend-unavailable（Req 2.8）
    - 非 failed 拒絕的一個具體範例：斷言 409 detail 含目前狀態字串
    - _Requirements: 2.1, 2.2, 2.7, 2.8_

  - [x] 3.7 撰寫改進 C 的 integration test（`test/test_retry_integration.py`）
    - 重設一個 `failed` 任務後，斷言它出現在 `fetch_pending_tasks()` 結果中（驗證與既有 Worker 撿取規則的接線，1–2 例）
    - _Requirements: 2.5_

- [x] 4. Checkpoint — 改進 C 完成
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. 改進 H — `archive_and_cite` 多格式 citation（interface first）
  - [x] 5.1 定義改進 H 的介面骨架（列舉 / 分派結構 / schema / MCP inputSchema）
    - `src/keeplink_mcp/citation/builder.py`：定義 `CitationFormat(str, Enum)`（`markdown`/`bibtex`/`apa`/`plain`）；`build_citation` 新增 keyword-only `format: CitationFormat = CitationFormat.MARKDOWN`；定義 8 個 formatter 簽章（`_complete_{markdown,bibtex,apa,plain}`、`_pending_{markdown,bibtex,apa,plain}`）與兩張分派表 `_COMPLETE_FORMATTERS` / `_PENDING_FORMATTERS`（尚未實作內容）
    - 若 `builder.py` 逼近 500 行，將 formatter 拆入新模組 `src/keeplink_mcp/citation/formatters.py`（single responsibility）
    - `src/keeplink_mcp/api/schemas.py`：`CiteRequest` 新增 `format: CitationFormat = CitationFormat.MARKDOWN`（可選、預設 markdown）
    - `src/keeplink_mcp/mcp_server/server.py`：`archive_and_cite` inputSchema.properties 新增可選 `format`（enum `["markdown","bibtex","apa","plain"]`）
    - _Requirements: 3.1, 3.6, 3.7_

  - [x] 5.2 實作四個 formatter 與分派（含向後相容核心）
    - `_complete_markdown` / `_pending_markdown`：字串逐字元等於既有 `_format_complete` / `_format_pending`（既有函式收攏為 markdown formatter，輸出不動）
    - `_complete_plain` / `_pending_plain`：純文字，不含任何 markdown 連結語法（無 `[text](url)`）
    - `_complete_bibtex` / `_complete_apa`：complete 態以 title/original_url/archived_url/archived_at 產生
    - `_pending_bibtex` / `_pending_apa`：pending 態以 `original_url` 作 URL 欄位、以 note/附註表達「archiving in progress, task_id=...」，不虛構 `archived_url`
    - `build_citation` 依狀態（`archived_url` 是否為 None）選表、依 `format` 選 formatter；日期一律 `strftime("%Y-%m-%d")`；維持結構化欄位語意不變、零 I/O、確定性
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.10_

  - [x] 5.3 端點傳遞 format 並實作非法值 fail-fast（routes.py + app.py）
    - `create_citation` 於兩個分支（cache-hit complete 與 pending/create）呼叫 `build_citation` 時皆傳 `format=request.format`
    - 掛 `RequestValidationError` handler（或端點層顯式二次檢查）：非法 `format` 值先 `logger.warning` 記錄再回 422，不建 Citation、不靜默退回預設
    - _Requirements: 3.8, 3.9, 3.11_

  - [x] 5.4 `archive_and_cite` MCP 工具傳遞 format（`_handle_archive_and_cite`，server.py）
    - payload 僅在呼叫者提供 `format` 時帶上，讓後端預設 markdown 生效
    - 非法 format 由後端 422 回傳，沿用既有「非 2xx → `{"error": detail}`」路徑上呈，不在工具層改預設
    - _Requirements: 3.7_

  - [x] 5.5 撰寫 `Citation_Builder` 多格式 property-based tests（`test/test_citation_builder_properties.py`）
    - **Property 5: markdown 預設向後相容** — 不傳 `format` 與 `format=MARKDOWN` 逐字元相同，且等於既有 markdown 邏輯輸出 — **Validates: Requirements 3.2**
    - **Property 6: 多格式確定性** — 相同輸入多次呼叫產生完全相同 `Citation` — **Validates: Requirements 3.1, 3.3**
    - **Property 7: 兩態 × 四格式完整性** — 任一狀態 × 格式組合皆產生非空 `formatted` — **Validates: Requirements 3.1**
    - **Property 8: plain 不含 markdown 連結語法** — `format==plain` 時 `formatted` 不含 `](` 結構 — **Validates: Requirements 3.4**
    - **Property 9: pending 態 bibtex/apa fallback** — pending + bibtex/apa 時 `formatted` 同時含 `original_url` 與 `task_id` 且標示進行中 — **Validates: Requirements 3.5**
    - **Property 10: 結構化欄位不受 format 影響** — 任一 format 的結構化欄位與 markdown 相同 — **Validates: Requirements 3.10**
    - **Property 13: Citation_Builder 無 I/O 純粹性** — 建構過程無網路/抓頁/LLM/DB — **Validates: Requirements 3.3**
    - 每個測試以 `# Feature: keeplink-v1x-improvements, Property {n}: {text}` 註解標註；hypothesis ≥100 iterations
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.10_

  - [x] 5.6 撰寫 Cite_Endpoint property-based tests（`test/test_cite_endpoint_properties.py`）
    - 以 in-memory SQLite + ASGI test client，隨機 format + 任務狀態
    - **Property 11: 端點 format 端到端一致** — `POST /api/cite` 回應的 `formatted` 等於以相同狀態與 format 呼叫 `build_citation` 所得 — **Validates: Requirements 3.9**
    - **Property 12: 非法 format fail-fast 且不產生 CitationResponse** — 非列舉 format → 422、不產生 `CitationResponse`、不靜默退回預設 — **Validates: Requirements 3.8, 3.11**
    - 每個測試以 `# Feature: keeplink-v1x-improvements, Property {n}: {text}` 註解標註；hypothesis ≥100 iterations
    - _Requirements: 3.8, 3.9, 3.11_

  - [x] 5.7 撰寫改進 H 的 golden / example unit tests
    - 8 條 golden `formatted` 字串範例（複製 `test/test_citation_builder_examples.py` 慣例）：complete × {markdown,bibtex,apa,plain} 與 pending × {markdown,bibtex,apa,plain} 各一（Req 3.1, 3.4, 3.5）
    - schema 預設：`CiteRequest(url=...).format == CitationFormat.MARKDOWN`（Req 3.6）
    - 工具 inputSchema：`archive_and_cite` 的 `format` enum == 四值且非必填（Req 3.7）
    - 非法 format 先 log 再 422：以 `caplog` 驗證 `logger.warning` 記錄非法值（Req 3.8 的 log 面）
    - _Requirements: 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 6. Checkpoint — 改進 H 完成
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. 改進 I — CI 加入 Windows matrix（`.github/workflows/ci.yml`）
  - [x] 7.1 擴充 CI matrix 為 os × python-version 兩維度
    - `strategy.matrix` 加入 `os: [ubuntu-latest, windows-latest]`；`runs-on: ${{ matrix.os }}`
    - 維持 `python-version: ["3.10", "3.13"]`（每平台皆兩版本，共 4 個 job）
    - 加入 `fail-fast: false`（各平台獨立評估，一平台失敗不取消另一平台）
    - `Install dependencies` / `Lint` / `Test` / `Security audit` 四步的 `run` 指令維持跨平台（PowerShell / bash 皆相容）相同指令，無需 `if: runner.os == ...` 分支
    - 無專屬 property test：改進 I 由 CI 於各平台實跑 `ruff check` / `pytest` / `pip-audit` 三步驟作為驗收（SMOKE 設定檢視 + INTEGRATION 實跑）
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

- [x] 8. Final Checkpoint — 執行 lint 與測試
  - 執行 `ruff check src/ test/`
  - 執行 `pytest test/`（hypothesis property tests 於此執行，每條 ≥100 iterations）
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- 標註 `*` 的 sub-task 為選用（測試相關：property / golden / example / integration），可在快速 MVP 時略過；核心實作 sub-task 與頂層任務不標 `*`
- 每組（A/C/H/I）結尾設 checkpoint；改進彼此獨立，可分開實作與驗證
- Property tests 對映 design 的 13 條 correctness properties，每條一個獨立 property-based test，置於 `test/`；property test 與 golden/example test 分屬不同 sub-task
- property test 一律以 `# Feature: keeplink-v1x-improvements, Property {n}: {text}` 註解標註，hypothesis ≥100 iterations
- interface-first：每組先定義列舉 / sentinel / schema / 函式簽章 / MCP inputSchema，再實作
- 全批 **無資料庫 schema 變更**：改進 C 僅更新既有 `ArchiveTask` 欄位值並沿用既有 `fetch_pending_tasks`
- 改進 A 保留 `app.py` 版本字面字串（不改變數引用），以維持既有版本一致性測試的正則擷取
- 改進 H 未指定 `format` 時輸出逐字元等於既有實作（markdown 預設向後相容）
- 改進 I 無新增測試檔——其價值為讓改進 A/C/H 的既有與新增測試在 Windows 上真的跑一遍
- 測試置於 `test/`、文件置於 `docs/`（依專案結構規範）

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "3.1", "5.1", "7.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "3.2", "5.2"] },
    { "id": 2, "tasks": ["1.4", "1.5", "3.3", "5.3"] },
    { "id": 3, "tasks": ["3.4", "3.5", "5.4", "5.5"] },
    { "id": 4, "tasks": ["3.6", "3.7", "5.6", "5.7"] }
  ]
}
```
