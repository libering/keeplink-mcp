# Implementation Plan: OmniArchive MCP

## Overview

按模組依賴順序實作，從底層（DB model 更新、config 更新）開始，向上逐步實作 validator、error classifier、worker、FastAPI routes、MCP server，最後完成入口點與整合測試。語言為 Python 3.10+。

## Tasks

- [x] 1. 更新 DB Model 與 Repository
  - [x] 1.1 在 `ArchiveTask` model 中新增 `next_retry_at` 欄位
    - 在 `src/omniarchive_mcp/db/models.py` 中新增 `next_retry_at: Mapped[datetime | None]` 欄位，類型為 `DateTime(timezone=True), nullable=True, default=None`
    - _Requirements: 5.1, 7.4_
  - [x] 1.2 在 `TaskRepository` 中新增 `find_recent_task` 去重查詢方法
    - 在 `src/omniarchive_mcp/db/repository.py` 中新增方法，查詢 24 小時內同 URL 的最近任務（status 為 pending/processing/success 時返回該任務，否則返回 None）
    - _Requirements: 4.1, 4.2, 4.3, 4.5_
  - [x] 1.3 更新 `fetch_pending_tasks` 加入 `next_retry_at` 過濾條件
    - 修改 WHERE 條件：`status == PENDING AND (next_retry_at IS NULL OR next_retry_at <= utcnow())`
    - _Requirements: 6.6, 7.4_
  - [x] 1.4 更新 `schedule_retry` 方法加入 `next_retry_at` 參數
    - 修改 `schedule_retry` 接受 `next_retry_at: datetime` 參數並寫入欄位
    - _Requirements: 7.4_

- [x] 2. 更新 Config
  - [x] 2.1 在 `Config` dataclass 中新增 `worker_concurrency` 欄位
    - 在 `src/omniarchive_mcp/config.py` 中新增 `worker_concurrency: int`，讀取 `OMNIARCHIVE_WORKER_CONCURRENCY` 環境變數，預設值為 1
    - _Requirements: 10.8, 6.4_

- [x] 3. 實作 URL Validator
  - [x] 3.1 建立 `src/omniarchive_mcp/mcp_server/url_validator.py`
    - 實作 `ValidationError` exception 類別
    - 實作 `validate_url(raw: str) -> str` 函式：strip whitespace → 驗證 scheme (http/https) → 驗證 hostname（至少一個 dot） → 返回正規化 URL
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_
  - [x] 3.2 撰寫 URL Validator 的 property test
    - **Property 1: URL 驗證 round-trip 一致性**
    - **Property 2: 無效 URL 一律被拒絕**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
    - 在 `test/test_url_validator.py` 中使用 hypothesis 實作，最少 100 次迭代

- [x] 4. 實作 Error Classifier
  - [x] 4.1 建立 `src/omniarchive_mcp/worker/error_classifier.py`
    - 定義 `ErrorCategory` enum (RETRYABLE, NON_RETRYABLE)
    - 實作 `classify_error(exception: Exception) -> ErrorCategory` 函式
    - 429/500/502/503/504/timeout/connection error → RETRYABLE
    - 401/403 → NON_RETRYABLE
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_
  - [x] 4.2 撰寫 Error Classifier 的 property test
    - **Property 3: 錯誤分類的完整性**
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5**
    - 在 `test/test_error_classifier.py` 中使用 hypothesis 實作

- [x] 5. Checkpoint
  - 確保所有測試通過，若有疑問請詢問用戶。

- [x] 6. 實作 Background Worker
  - [x] 6.1 建立 `src/omniarchive_mcp/worker/archiver.py`
    - 實作 `BackgroundWorker` 類別：`__init__`, `start`, `stop`, `_poll_and_process`, `_process_task`, `_calculate_backoff`
    - 輪詢 pending tasks（遵循 next_retry_at 過濾）
    - 使用 waybackpy 呼叫 SPN2 API
    - 根據 error_classifier 結果決定重試或標記失敗
    - 支援 concurrency 配置（每輪最多處理 N 個任務）
    - 支援 graceful shutdown（完成當前任務後停止）
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 7.2, 7.3, 7.4, 8.6_
  - [x] 6.2 撰寫 Worker backoff 計算的 property test
    - **Property 4: 指數退避計算正確性**
    - **Validates: Requirements 7.2**
    - 在 `test/test_backoff.py` 中使用 hypothesis 實作
  - [x] 6.3 撰寫 Worker 整合測試
    - Mock waybackpy，測試 success/retry/fail 流程
    - 驗證 next_retry_at 過濾邏輯
    - **Property 7: Worker 跳過未到期的重試任務**
    - **Validates: Requirements 6.6, 7.4**
    - 在 `test/test_worker.py` 中實作

- [x] 7. 實作 FastAPI API 層
  - [x] 7.1 建立 `src/omniarchive_mcp/api/schemas.py`
    - 定義 Pydantic models：`ArchiveRequest`, `ArchiveResponse`, `TaskStatusResponse`, `ErrorResponse`
    - _Requirements: 1.2, 2.1_
  - [x] 7.2 建立 `src/omniarchive_mcp/api/routes.py`
    - 實作 `POST /api/archive`：接收 URL → validate → dedup check → create task → return response
    - 實作 `GET /api/status/{task_id}`：查詢並返回任務狀態
    - 實作 `GET /api/status?url={url}`：按 URL 查詢最近任務
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 2.1, 2.2_
  - [x] 7.3 建立 `src/omniarchive_mcp/api/app.py`
    - 實作 FastAPI app factory，注入 session_factory 依賴
    - 掛載 routes，設定 CORS（僅 localhost）
    - _Requirements: 10.6_
  - [x] 7.4 撰寫 FastAPI 路由整合測試
    - 在 `test/test_api_routes.py` 中使用 httpx.AsyncClient
    - 測試 archive 建立、去重命中、status 查詢、404 錯誤
    - **Property 5: 去重窗口內的冪等性**
    - **Property 6: 去重窗口內失敗任務允許重試**
    - **Validates: Requirements 4.1, 4.2, 4.3**

- [x] 8. Checkpoint
  - 確保所有測試通過，若有疑問請詢問用戶。

- [x] 9. 實作 MCP Server
  - [x] 9.1 建立 `src/omniarchive_mcp/mcp_server/server.py`
    - 使用官方 `mcp` SDK 建立 MCP server
    - 實作 `archive_url` tool：validate URL → httpx POST to FastAPI → return task_id + status
    - 實作 `get_archive_status` tool：接受 task_id 或 url → httpx GET from FastAPI → return status
    - 處理 FastAPI 連線失敗的錯誤情境
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.4, 2.5_
  - [x] 9.2 建立 `src/omniarchive_mcp/mcp_server/main.py`
    - MCP Server 獨立進程入口點
    - 載入 config，初始化 server，啟動 stdio transport
    - _Requirements: 11.2_
  - [x] 9.3 撰寫 MCP Server 整合測試
    - 在 `test/test_mcp_server.py` 中使用 respx mock FastAPI 回應
    - 測試 archive_url（正常、驗證失敗、服務不可用）
    - 測試 get_archive_status（by task_id、by url、not found）
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.4_

- [x] 10. 實作主進程入口點
  - [x] 10.1 建立 `src/omniarchive_mcp/main.py`
    - 實作 `main()` 函式：load config → setup logging → init DB → start FastAPI (uvicorn) → start BackgroundWorker → handle SIGTERM/SIGINT graceful shutdown
    - _Requirements: 11.1, 11.3, 11.4, 11.5, 11.6_

- [x] 11. 撰寫 Repository 測試與 conftest
  - [x] 11.1 建立 `test/conftest.py`
    - 建立共享 fixtures：in-memory SQLite engine、session factory、sample tasks
    - _Requirements: 5.2, 5.4_
  - [x] 11.2 建立 `test/test_repository.py`
    - 測試 create_task 預設值正確
    - 測試 find_recent_task 去重邏輯（pending/processing/success 命中、failed 不命中、超過 24h 不命中）
    - 測試 fetch_pending_tasks 遵循 next_retry_at 過濾
    - **Property 8: 任務狀態轉換合法性**
    - **Validates: Requirements 5.1, 5.5, 6.6**

- [x] 12. Final Checkpoint
  - 確保所有測試通過，執行 `ruff check src/ test/` 確認無 lint 錯誤。若有疑問請詢問用戶。

## Notes

- 標記 `*` 的子任務為可選，可跳過以加速 MVP 開發
- 每個任務引用具體需求條款以確保可追溯性
- Property tests 使用 hypothesis 庫，每個至少 100 次迭代
- Unit tests 與 property tests 互補：前者捕獲具體 bug，後者驗證通用正確性
- Checkpoint 確保增量驗證，及早發現問題
