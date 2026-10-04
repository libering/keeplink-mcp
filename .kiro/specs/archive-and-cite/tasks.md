# Implementation Plan: archive_and_cite

## Overview

依 interface-first 順序實作 `archive_and_cite` MCP 工具：先建立純函式 `citation` package（`Citation` dataclass + 格式化函式，先定義簽章再實作）→ 新增 `CiteRequest` / `CitationResponse` schemas → 新增 `POST /api/cite` 端點（複用既有 `validate_url` / `find_recent_task` / `create_task`）→ 註冊 `archive_and_cite` MCP 工具與 handler → property-based tests（hypothesis，每條 ≥100 iterations）與 unit tests → 更新文件 → 最終執行 lint 與測試。**無資料庫 schema 變更**，完全複用既有存檔管線。

## Tasks

- [x] 1. 建立 citation package 純函式核心（interface first）
  - [x] 1.1 建立 `src/keeplink_mcp/citation/__init__.py` 與 `builder.py` 的介面骨架
    - 新增 `citation` package（`__init__.py`）
    - 於 `builder.py` 先定義 `Citation` frozen dataclass（欄位 `title`、`original_url`、`archived_url`、`archived_at`、`task_id`、`formatted`）
    - 定義函式簽章（尚未實作）：`normalize_title`、`build_citation`（keyword-only）、`_format_complete`、`_format_pending`
    - 檔案 <200 行、零 I/O、single responsibility
    - _Requirements: 8.3_

  - [x] 1.2 實作 `normalize_title`
    - `None` 或 strip 後為空（含 `\t`、全形空白）→ 回傳 `None`；否則回傳 strip 後的值
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 1.3 實作 `_format_complete` 與 `_format_pending`
    - `_format_complete`（含 title）→ `[title](archived_url) (original: original_url, archived YYYY-MM-DD)`
    - `_format_complete`（無 title）→ 以 `archived_url` 作為連結文字取代 title，仍含 original URL 與 `YYYY-MM-DD` 日期
    - 日期以 `archived_at.strftime("%Y-%m-%d")` 渲染
    - `_format_pending` → 說明存檔進行中，並包含 `original_url` 與 `task_id`
    - _Requirements: 3.3, 3.4, 3.5, 4.3_

  - [x] 1.4 實作 `build_citation`
    - 先以 `normalize_title` 正規化 title
    - pending（`archived_url is None`）→ `archived_at` 亦須為 `None`，產生 Pending_Citation
    - complete（`archived_url` 非空）→ `archived_at` 須提供，產生完整引用
    - fail-fast：`archived_url` 與 `archived_at` 只設其一 → `raise ValueError`（不以 fallback 掩蓋）
    - _Requirements: 1.3, 3.1, 3.2, 4.1, 4.2, 7.2, 7.3_

  - [x] 1.5 撰寫 `Citation_Builder` 純函式的 property-based tests（`test/test_citation_builder_properties.py`）
    - **Property 1: 格式化確定性** — **Validates: Requirements 8.1, 8.2**
    - **Property 2: 兩態完整性不變式** — **Validates: Requirements 1.3, 4.1, 4.2, 7.1, 7.2, 7.3**
    - **Property 3: Cache-hit 欄位對映** — **Validates: Requirements 3.1, 3.2**
    - **Property 4: 完整引用（含 title）格式** — **Validates: Requirements 3.3, 3.5**
    - **Property 5: 完整引用（無 title）格式** — **Validates: Requirements 3.4, 3.5**
    - **Property 6: 進行中引用格式** — **Validates: Requirements 4.3**
    - **Property 7: 標題正規化** — **Validates: Requirements 5.1, 5.2, 5.3**
    - **Property 11: 無 I/O 純粹性** — **Validates: Requirements 2.4, 2.5, 8.3**
    - 每個測試以 `# Feature: archive-and-cite, Property {n}: {text}` 註解標註；hypothesis ≥100 iterations
    - _Requirements: 1.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 7.1, 7.2, 7.3, 8.1, 8.2, 8.3_

  - [x] 1.6 撰寫 `Citation_Builder` 黃金範例 unit tests（`test/test_citation_builder_examples.py`）
    - complete-with-title、complete-without-title、pending 三個 golden string 範例
    - `build_citation` 對只設 `archived_url` 或只設 `archived_at` 拋 `ValueError`
    - _Requirements: 3.3, 3.4, 4.3, 7.2, 7.3_

- [x] 2. 新增 CiteRequest / CitationResponse schemas
  - [x] 2.1 於 `src/keeplink_mcp/api/schemas.py` 新增 `CiteRequest` 與 `CitationResponse`
    - `CiteRequest`：`url: str`、`title: str | None = None`
    - `CitationResponse`：`title`（nullable）、`original_url`、`archived_url`（nullable）、`archived_at`（nullable）、`task_id`、`formatted`
    - _Requirements: 7.1, 7.2, 7.3_

- [x] 3. 新增 Cite_Endpoint（POST /api/cite）
  - [x] 3.1 於 `src/keeplink_mcp/api/routes.py` 新增 `create_citation`（POST /api/cite）
    - 鏡射 `create_archive` 的 validation / dedup 結構，輸出 `CitationResponse`
    - 步驟：`normalize_title(request.title)` → `validate_url`（失敗回 HTTP 422 `ErrorResponse`，不建任務）→ `repo.find_recent_task(validated_url)`
    - 既有 `status == success` 且 `result_url` 非空 → Cache-hit COMPLETE：`archived_url=result_url`、`archived_at=task.updated_at`
    - 既有 pending/processing → Pending：複用 `task_id`、`archived_url=None`、`archived_at=None`
    - 無既有任務 → `repo.create_task(validated_url)` 進入既有 Worker / rate-limit / retry 管線
    - 以 `build_citation` 產生 Citation 並回傳；僅讀 DB 與呼叫純函式（無抓頁、無 LLM）
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 4.1, 4.2, 6.2, 6.3_

  - [x] 3.2 撰寫 Cite_Endpoint property-based tests（`test/test_cite_endpoint_properties.py`）
    - 以 in-memory SQLite + ASGI test client 生成隨機 url / 任務狀態
    - **Property 8: 無效 URL 拒絕且無副作用** — **Validates: Requirements 2.1, 6.1, 6.2, 6.3**
    - **Property 9: 去重複用** — **Validates: Requirements 2.3**
    - 每個測試以 `# Feature: archive-and-cite, Property {n}: {text}` 註解標註；hypothesis ≥100 iterations
    - _Requirements: 2.1, 2.3, 6.1, 6.2, 6.3_

  - [x] 3.3 撰寫 Cite_Endpoint unit tests（`test/test_cite_endpoint.py`）
    - pipeline 複用：空 DB 下 POST 後 `create_task` 被呼叫、任務入 pending（Req 2.2）
    - 無抓頁 / 無 LLM：斷言 cite 路徑僅存取 DB 與純函式，無對外頁面抓取、無 LLM client（Req 2.4, 2.5）
    - _Requirements: 2.2, 2.4, 2.5_

- [x] 4. 註冊 archive_and_cite MCP 工具與 handler
  - [x] 4.1 於 `src/keeplink_mcp/mcp_server/server.py` 的 `list_tools()` 新增 `archive_and_cite` Tool
    - inputSchema：required string `url`、optional string `title`
    - description 說明 non-blocking、Cache_Hit 即回完整 citation、Pending 附 task_id、不抓頁不呼叫 LLM
    - _Requirements: 1.1_

  - [x] 4.2 於 `call_tool()` 分派並實作 `_handle_archive_and_cite`
    - 轉發前先 `validate_url`；失敗回 error result、不 POST、不建任務（Req 6.1, 6.3）
    - 沿用既有 `httpx.AsyncClient(base_url, timeout=10.0)` 對 `/api/cite` 發 POST（Req 1.2）
    - 捕捉 `httpx.ConnectError`/`ConnectTimeout`/`HTTPError`/`ValueError` → `{"error": "Backend service unavailable"}`（與既有 handler 逐字一致，Req 1.5）
    - 回傳含 `title`、`original_url`、`archived_url`、`archived_at`、`task_id`、`formatted` 的 Citation JSON（Req 1.3）
    - Pending（`archived_url` 為 null）時於 TextContent 附引導字串，指示以 `task_id` 呼叫 `get_archive_status` 補完（Req 4.4）
    - _Requirements: 1.2, 1.3, 1.5, 4.4, 6.1, 6.3_

  - [x] 4.3 撰寫工具層 property-based test（`test/test_archive_and_cite_tool_properties.py`）
    - 以 mock httpx 回傳隨機 pending `CitationResponse`
    - **Property 10: 進行中引導文字** — **Validates: Requirements 4.4**
    - 以 `# Feature: archive-and-cite, Property 10: {text}` 註解標註；hypothesis ≥100 iterations
    - _Requirements: 4.4_

  - [x] 4.4 撰寫工具層 unit tests（`test/test_archive_and_cite_tool.py`）
    - 工具註冊（Req 1.1）：`list_tools()` 含 `archive_and_cite`，`inputSchema.required == ["url"]` 且具 optional `title`
    - 轉發行為（Req 1.2）：mock httpx，斷言對 `/api/cite` 發 POST 且帶正規化後 url
    - Backend 不可達（Req 1.5）：注入 `httpx.ConnectError`，斷言回傳 backend-unavailable
    - _Requirements: 1.1, 1.2, 1.5_

- [x] 5. Checkpoint — 核心實作與測試完成
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. 更新文件
  - [x] 6.1 更新 `docs/handoverbook.md`
    - 記錄 `archive_and_cite` 工具，含參數（url、optional title）與 Citation 回應欄位
    - 描述 async citation-completion 流程：Pending_Citation 稍後以 `get_archive_status(task_id)` 補完
    - _Requirements: 9.1, 9.2_

  - [x] 6.2 更新 `README.md`
    - 記錄 `archive_and_cite` 工具及其 Cache_Hit vs Pending_Citation 行為
    - 聲明 KeepLink 產生 Citation 時不抓取頁面內容、不呼叫 LLM
    - _Requirements: 9.3, 9.4_

- [x] 7. Final Checkpoint — 執行 lint 與測試
  - 執行 `ruff check src/ test/`
  - 執行 `pytest test/`（hypothesis property tests 於此執行，≥100 iterations）
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- 標註 `*` 的 sub-task 為選用（測試相關），可在快速 MVP 時略過；核心實作 sub-task 不標 `*`
- 每個任務引用其實作的 requirement / correctness property 編號（依 design traceability table）
- Property tests 對映 design 的 11 條 correctness properties，每條一個獨立 property-based test，置於 `test/`
- 文件置於 `docs/`；測試置於 `test/`（依專案結構規範）
- 無資料庫 schema 變更 —— 既有 `archive_tasks` 表已足夠；無 repository 新方法
- 各新模組遵循 single-responsibility：`citation/builder.py` 為零 I/O 純函式模組（<200 行）

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["1.4"] },
    { "id": 3, "tasks": ["1.5", "1.6", "3.1"] },
    { "id": 4, "tasks": ["3.2", "3.3", "4.1", "4.2"] },
    { "id": 5, "tasks": ["4.3", "4.4", "6.1", "6.2"] }
  ]
}
```
