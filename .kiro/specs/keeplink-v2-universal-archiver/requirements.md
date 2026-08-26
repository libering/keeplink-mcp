# Requirements Document

## Introduction

KeepLink v2.0 — 從「AI Agent 存檔工具」擴展為「本地優先的通用網頁存檔服務」。核心定位轉變：MCP 僅是眾多 Ingestion 介面之一，系統提供統一的 URL 佇列管理、多後端存檔引擎、自動化 URL 發現、以及優先級排程能力。

設計哲學維持不變：本地優先、零外部基礎設施依賴、一行指令啟動。

本文件涵蓋 v2.0 里程碑的核心擴展領域：
1. 多 Ingestion 介面（CLI、Browser Extension Webhook、RSS/Sitemap Monitor）
2. 多存檔後端引擎（Internet Archive、Archive.today、ArchiveBox）
3. URL 發現與監控（RSS Feed / Sitemap 掃描器）
4. 批量匯入
5. 可用性檢查（存檔前查詢既有快照）
6. 優先級佇列
7. Webhook 通知

## Glossary

- **Ingestion_Interface**: 接受 URL 存檔請求的入口層，包含 MCP Server、REST API、CLI Tool、Browser Extension Webhook、RSS/Sitemap Monitor
- **Archive_Backend**: 實際執行網頁存檔的目標引擎，如 Internet Archive SPN2、Archive.today、ArchiveBox
- **Backend_Registry**: 管理所有已註冊 Archive_Backend 的模組，負責路由任務至對應後端
- **CLI_Tool**: 命令列介面工具，允許使用者透過終端機提交存檔請求、查詢狀態、批量匯入
- **Browser_Extension_Webhook**: 接收瀏覽器擴充功能 POST 請求的 HTTP 端點，用於從瀏覽器一鍵存檔
- **RSS_Monitor**: 定期輪詢 RSS/Atom Feed 的背景模組，自動將新發現的 URL 加入佇列
- **Sitemap_Monitor**: 定期掃描 Sitemap XML 的背景模組，自動將新發現的 URL 加入佇列
- **Feed_Source**: RSS_Monitor 或 Sitemap_Monitor 所監控的單一資料來源（一個 feed URL 或 sitemap URL）
- **Availability_Checker**: 在提交存檔前查詢目標後端是否已存在近期快照的模組
- **Freshness_Window**: 可用性檢查的時間閾值，若既有快照在此時間窗口內則跳過存檔（預設 24 小時）
- **Priority_Level**: 任務優先級，分為 critical、high、normal、low 四個等級
- **Batch_Import**: 從文件（純文字 URL 列表、CSV、JSON）中讀取多個 URL 並批量加入佇列的功能
- **Webhook_Notification**: 任務完成（成功或最終失敗）時向已註冊的外部 HTTP 端點發送通知
- **Dispatcher**: 從佇列中取出任務並根據 target_backend 路由至對應 Archive_Backend 的排程器
- **ArchiveTask**: 系統核心資料模型，代表一個 URL 存檔作業（擴展自 v1.x 模型）
- **Deduplication_Window**: 同一 URL + 同一後端在此時間窗口內不重複存檔（預設 24 小時）

## Requirements

### Requirement 1: 多後端存檔引擎架構

**User Story:** As a user, I want to archive URLs to multiple backends (Internet Archive, Archive.today, ArchiveBox), so that I have redundant copies and can choose the best destination for each use case.

#### Acceptance Criteria

1. THE Backend_Registry SHALL maintain a collection of registered Archive_Backend instances, each identified by a unique string name (e.g., "internet_archive", "archive_today", "archivebox").
2. THE Backend_Registry SHALL expose a unified interface with methods: `archive(url, backend_name)` and `check_availability(url, backend_name)`, where each Archive_Backend implements both methods.
3. WHEN a new ArchiveTask is created, THE ArchiveTask model SHALL include a `target_backend` field specifying which Archive_Backend processes the task (default: "internet_archive").
4. WHEN the Dispatcher fetches a pending task, THE Dispatcher SHALL route it to the corresponding Archive_Backend based on the task's `target_backend` field.
5. IF a specified `target_backend` is not registered in the Backend_Registry, THEN THE system SHALL reject the task creation request with an error indicating the backend is unavailable.
6. THE Backend_Registry SHALL support runtime registration of Archive_Backend instances during application startup based on configuration, without requiring code changes for each new backend.
7. WHEN an Archive_Backend fails to process a task, THE Dispatcher SHALL apply the same exponential backoff retry logic as v1.x, respecting the backend-specific rate limiter.
8. THE system SHALL maintain a separate Token_Bucket rate limiter instance for each registered Archive_Backend, configurable independently via environment variables with pattern `KEEPLINK_{BACKEND_NAME}_RATE_LIMIT_TOKENS` and `KEEPLINK_{BACKEND_NAME}_RATE_LIMIT_INTERVAL_SEC`.

---

### Requirement 2: Internet Archive SPN2 後端（既有功能遷移）

**User Story:** As an existing user, I want the Internet Archive SPN2 backend to continue working after the v2 refactor, so that my current archiving workflow is unaffected.

#### Acceptance Criteria

1. THE Internet_Archive_Backend SHALL implement the Archive_Backend interface, encapsulating the existing waybackpy SPN2 API call logic.
2. THE Internet_Archive_Backend SHALL use the existing `KEEPLINK_IA_ACCESS_KEY` and `KEEPLINK_IA_SECRET_KEY` environment variables for authentication.
3. THE Internet_Archive_Backend SHALL reuse the existing error classification logic (retryable: 429/5xx/network; non-retryable: 401/403) without behavioral changes.
4. THE Internet_Archive_Backend SHALL remain the default `target_backend` when no backend is explicitly specified in a task creation request.
5. WHEN the `check_availability` method is called, THE Internet_Archive_Backend SHALL query the Wayback Machine Availability API to determine if a snapshot exists within the configured Freshness_Window.

---

### Requirement 3: Archive.today 後端

**User Story:** As a user, I want to archive URLs to Archive.today as an alternative backend, so that I have a second independent archive copy.

#### Acceptance Criteria

1. THE Archive_Today_Backend SHALL implement the Archive_Backend interface.
2. WHEN `archive(url)` is called, THE Archive_Today_Backend SHALL submit the URL to Archive.today's submission endpoint and return the resulting snapshot URL upon success.
3. IF Archive.today returns an HTTP error or network failure, THEN THE Archive_Today_Backend SHALL raise an exception compatible with the existing error classification system.
4. WHEN the `check_availability` method is called, THE Archive_Today_Backend SHALL query Archive.today to determine if a cached snapshot exists within the configured Freshness_Window.
5. WHERE the Archive_Today_Backend is enabled (via configuration), THE Backend_Registry SHALL register it during startup.

---

### Requirement 4: ArchiveBox 自託管後端

**User Story:** As a self-hosted user, I want to archive URLs to my local ArchiveBox instance, so that I maintain full control over my archived content.

#### Acceptance Criteria

1. THE ArchiveBox_Backend SHALL implement the Archive_Backend interface.
2. THE ArchiveBox_Backend SHALL communicate with an ArchiveBox instance via its REST API at a user-configured base URL (environment variable `KEEPLINK_ARCHIVEBOX_URL`).
3. WHEN `archive(url)` is called, THE ArchiveBox_Backend SHALL submit the URL to the ArchiveBox API's add endpoint and return the snapshot identifier upon success.
4. IF the ArchiveBox API is unreachable or returns an error, THEN THE ArchiveBox_Backend SHALL raise an exception compatible with the existing error classification system.
5. WHERE an API key is required by the ArchiveBox instance, THE ArchiveBox_Backend SHALL use the `KEEPLINK_ARCHIVEBOX_API_KEY` environment variable for authentication.
6. WHEN the `check_availability` method is called, THE ArchiveBox_Backend SHALL query the ArchiveBox API to determine if the URL has been previously archived.

---

### Requirement 5: CLI 工具

**User Story:** As a power user, I want a command-line tool to submit URLs, check status, and manage the archive queue, so that I can interact with KeepLink without needing an AI Agent or browser.

#### Acceptance Criteria

1. THE CLI_Tool SHALL provide a `keeplink archive <url>` command that submits a single URL for archiving and prints the assigned task_id.
2. THE CLI_Tool SHALL provide a `keeplink status <task_id>` command that queries and displays the current status of a task (task_id, url, status, result_url, error_message, retry_count).
3. THE CLI_Tool SHALL provide a `keeplink import <file_path>` command that reads a file containing URLs (one per line) and submits each for archiving, printing a summary of submitted count and any rejected URLs.
4. THE CLI_Tool SHALL accept optional flags: `--backend <name>` (target backend, default "internet_archive"), `--priority <level>` (priority level, default "normal"), `--tag <tag>` (metadata tag for categorization).
5. THE CLI_Tool SHALL communicate with the FastAPI Service via HTTP (localhost:19210) and SHALL NOT directly access the SQLite database.
6. IF the FastAPI Service is not running, THEN THE CLI_Tool SHALL display a clear error message indicating the service is unreachable and suggest starting it.
7. THE CLI_Tool SHALL provide a `keeplink list` command that displays the most recent 20 tasks with their status, sorted by creation time descending.
8. THE CLI_Tool SHALL support a `--json` flag on all commands to output results in JSON format for scripting integration.

---

### Requirement 6: Browser Extension Webhook 端點

**User Story:** As a browser user, I want to archive the current page with one click via a browser extension, so that I can preserve web pages I'm reading without switching to a terminal.

#### Acceptance Criteria

1. THE Browser_Extension_Webhook SHALL expose a `POST /api/extension/archive` endpoint that accepts a JSON body with fields: `url` (required), `title` (optional), `tags` (optional array of strings), `backend` (optional, default "internet_archive"), `priority` (optional, default "normal").
2. WHEN a valid request is received, THE Browser_Extension_Webhook SHALL create an ArchiveTask and return HTTP 201 with the task_id and a deduplication indicator (`is_deduplicated`: true/false).
3. THE Browser_Extension_Webhook SHALL apply the same URL validation and normalization logic as the existing `/api/archive` endpoint.
4. THE Browser_Extension_Webhook SHALL apply the same 24-hour Deduplication_Window logic per URL + backend combination.
5. IF the request body is missing the required `url` field or the URL is invalid, THEN THE Browser_Extension_Webhook SHALL return HTTP 422 with a descriptive error message.
6. THE Browser_Extension_Webhook SHALL support CORS headers to allow requests from browser extension origins (configurable allowed origins via `KEEPLINK_CORS_ORIGINS` environment variable, default: all origins).

---

### Requirement 7: RSS/Atom Feed 監控

**User Story:** As a content curator, I want KeepLink to automatically monitor RSS/Atom feeds and archive new articles, so that I never miss archiving content from sources I follow.

#### Acceptance Criteria

1. THE RSS_Monitor SHALL maintain a list of Feed_Source entries, each containing: feed_url, poll_interval_minutes (default 60), target_backend (default "internet_archive"), priority (default "low"), enabled flag, and optional tags.
2. THE RSS_Monitor SHALL run as a background task within the FastAPI Service process, polling each enabled Feed_Source at its configured interval.
3. WHEN new entries are discovered in a feed (entries not previously seen based on their unique link URL), THE RSS_Monitor SHALL create an ArchiveTask for each new entry's URL.
4. THE RSS_Monitor SHALL persist the set of previously seen entry URLs per Feed_Source to avoid re-queuing entries across service restarts.
5. IF a feed fetch fails (network error, invalid XML, HTTP error), THEN THE RSS_Monitor SHALL log a WARNING and retry on the next poll cycle without affecting other Feed_Source entries.
6. THE RSS_Monitor SHALL support both RSS 2.0 and Atom 1.0 feed formats.
7. THE RSS_Monitor SHALL expose CRUD endpoints for Feed_Source management: `POST /api/feeds` (add), `GET /api/feeds` (list), `PUT /api/feeds/{id}` (update), `DELETE /api/feeds/{id}` (remove).
8. WHEN a Feed_Source is disabled (enabled=false), THE RSS_Monitor SHALL skip it during polling cycles without removing its configuration or seen-entries history.

---

### Requirement 8: Sitemap 監控

**User Story:** As a website owner, I want KeepLink to monitor my sitemap and automatically archive new pages, so that all my published content is preserved without manual intervention.

#### Acceptance Criteria

1. THE Sitemap_Monitor SHALL maintain a list of Feed_Source entries (shared model with RSS_Monitor) for sitemap URLs, each containing: sitemap_url, poll_interval_minutes (default 360), target_backend, priority (default "low"), enabled flag, and optional tags.
2. THE Sitemap_Monitor SHALL run as a background task within the FastAPI Service process, polling each enabled sitemap source at its configured interval.
3. WHEN new URLs are discovered in a sitemap (URLs not previously seen for this Feed_Source), THE Sitemap_Monitor SHALL create an ArchiveTask for each new URL.
4. THE Sitemap_Monitor SHALL support standard sitemap.xml format including sitemap index files (sitemapindex containing multiple sitemap references).
5. THE Sitemap_Monitor SHALL persist the set of previously seen URLs per Feed_Source to avoid re-queuing across service restarts.
6. IF a sitemap fetch fails, THEN THE Sitemap_Monitor SHALL log a WARNING and retry on the next poll cycle.
7. THE Sitemap_Monitor SHALL reuse the same CRUD endpoints as RSS_Monitor (`/api/feeds`) with a `type` field distinguishing "rss" from "sitemap".
8. WHERE a sitemap entry includes a `<lastmod>` timestamp newer than the last poll time, THE Sitemap_Monitor SHALL treat it as a new entry regardless of whether the URL was previously seen (to capture page updates).

---

### Requirement 9: 批量匯入

**User Story:** As a user with a large URL collection, I want to import a file of URLs for batch archiving, so that I can quickly queue hundreds of URLs without individual commands.

#### Acceptance Criteria

1. THE Batch_Import module SHALL accept URL list files in three formats: plain text (one URL per line), CSV (with a header row, URL in the first column), and JSON (array of objects with a `url` field).
2. WHEN processing a batch file, THE Batch_Import module SHALL validate each URL using the existing URL validation logic and skip invalid entries with a warning log.
3. THE Batch_Import module SHALL expose a `POST /api/import` endpoint that accepts a multipart file upload containing the URL list.
4. WHEN a batch import is submitted, THE Batch_Import module SHALL return HTTP 202 with a summary: total_count, accepted_count, rejected_count, and rejected_urls (array of URLs with rejection reasons).
5. THE Batch_Import module SHALL apply the Deduplication_Window logic per URL + backend combination, counting deduplicated URLs in the accepted count with `is_deduplicated` flag.
6. THE Batch_Import module SHALL support optional parameters: `backend` (target backend for all URLs), `priority` (priority level for all URLs), `tags` (tags applied to all imported URLs).
7. IF the uploaded file exceeds 10,000 URLs, THEN THE Batch_Import module SHALL return HTTP 422 with an error indicating the maximum batch size limit.
8. THE Batch_Import module SHALL process URLs asynchronously (not block the HTTP response), creating tasks in the background after returning the 202 response.

---

### Requirement 10: 可用性檢查（Availability Check）

**User Story:** As a user, I want KeepLink to check if a URL already has a recent archive before submitting a new request, so that API quota is not wasted on redundant archiving.

#### Acceptance Criteria

1. WHEN an ArchiveTask is about to be processed by the Dispatcher, THE Availability_Checker SHALL query the target Archive_Backend's `check_availability` method to determine if a snapshot exists within the configured Freshness_Window.
2. IF a recent snapshot is found within the Freshness_Window (default 24 hours, configurable via `KEEPLINK_FRESHNESS_WINDOW_HOURS`), THEN THE Dispatcher SHALL mark the task as "success" with the existing snapshot URL as `result_url` and set a flag indicating it was a cache hit (`is_cached`: true).
3. IF no recent snapshot is found or the availability check fails (network error, timeout), THEN THE Dispatcher SHALL proceed with the normal archiving flow.
4. THE Availability_Checker SHALL respect the target backend's rate limiter when making availability queries.
5. WHERE availability checking is disabled for a specific task (via `skip_availability_check` flag on the ArchiveTask), THE Dispatcher SHALL bypass the availability check and proceed directly to archiving.
6. THE Availability_Checker SHALL complete its query within 10 seconds; IF the check exceeds this timeout, THEN THE Dispatcher SHALL proceed with archiving as if no snapshot was found.

---

### Requirement 11: 優先級佇列

**User Story:** As a user, I want to assign priority levels to archive tasks, so that urgent URLs are processed before less important ones.

#### Acceptance Criteria

1. THE ArchiveTask model SHALL include a `priority` field with four levels: critical (0), high (1), normal (2), low (3), where lower numeric value indicates higher priority.
2. WHEN the Dispatcher fetches pending tasks from the queue, THE Dispatcher SHALL order tasks by priority (ascending numeric value) first, then by created_at (ascending) within the same priority level.
3. THE default priority for tasks created via the REST API or MCP Server SHALL be "normal" (2) unless explicitly specified.
4. THE default priority for tasks created via RSS_Monitor or Sitemap_Monitor SHALL be "low" (3) unless overridden in the Feed_Source configuration.
5. WHEN a task with "critical" priority is created, THE Dispatcher SHALL process it in the next poll cycle regardless of other pending tasks (preemptive scheduling within the poll batch).
6. THE priority field SHALL be settable at task creation time and SHALL NOT be modifiable after creation.

---

### Requirement 12: Webhook 通知

**User Story:** As a system integrator, I want to receive webhook notifications when archive tasks complete, so that I can trigger downstream workflows automatically.

#### Acceptance Criteria

1. THE Webhook_Notification module SHALL maintain a list of registered webhook endpoints, each containing: url, events (array of event types to subscribe to), enabled flag, and optional secret (for HMAC signature verification).
2. WHEN an ArchiveTask transitions to "success" status, THE Webhook_Notification module SHALL send an HTTP POST to all enabled webhooks subscribed to the "task.success" event, with a JSON body containing: task_id, url, status, result_url, backend, created_at, completed_at.
3. WHEN an ArchiveTask transitions to "failed" status (permanent failure after all retries exhausted), THE Webhook_Notification module SHALL send an HTTP POST to all enabled webhooks subscribed to the "task.failed" event, with a JSON body containing: task_id, url, status, error_message, backend, retry_count.
4. WHERE a webhook has a configured secret, THE Webhook_Notification module SHALL include an `X-KeepLink-Signature` header containing the HMAC-SHA256 signature of the request body using the secret as key.
5. IF a webhook delivery fails (network error or non-2xx response), THEN THE Webhook_Notification module SHALL retry delivery up to 3 times with a fixed 30-second interval between retries.
6. IF all delivery retries are exhausted, THEN THE Webhook_Notification module SHALL log a WARNING and discard the notification without affecting the archive task's status.
7. THE Webhook_Notification module SHALL expose CRUD endpoints for webhook management: `POST /api/webhooks` (register), `GET /api/webhooks` (list), `PUT /api/webhooks/{id}` (update), `DELETE /api/webhooks/{id}` (remove).
8. THE Webhook_Notification module SHALL deliver notifications asynchronously (fire-and-forget from the Worker's perspective), not blocking the Dispatcher's processing loop.

---

### Requirement 13: ArchiveTask 模型擴展

**User Story:** As a developer, I want the ArchiveTask data model to support multi-backend, priority, tags, and source tracking, so that the system can handle the v2.0 feature set.

#### Acceptance Criteria

1. THE ArchiveTask model SHALL include the following new fields: `target_backend` (string, default "internet_archive"), `priority` (integer, default 2), `source` (string indicating origin: "mcp", "api", "cli", "extension", "rss_monitor", "sitemap_monitor", "batch_import"), `tags` (JSON array of strings, default empty), `is_cached` (boolean, default false), `title` (optional string for page title metadata).
2. THE database migration SHALL preserve all existing v1.x task data without modification, adding new columns with their default values.
3. THE Deduplication_Window logic SHALL be scoped to the combination of URL + target_backend (the same URL can be archived to different backends within the same window).
4. THE ArchiveTask model SHALL maintain backward compatibility: tasks created without specifying new fields SHALL use their default values and behave identically to v1.x tasks.
5. THE ArchiveTask model SHALL include an index on `(status, priority, next_retry_at)` to optimize the Dispatcher's fetch query performance.

---

### Requirement 14: 統一 Ingestion 層介面

**User Story:** As a developer, I want all ingestion interfaces to use a common internal API for task creation, so that validation, deduplication, and normalization logic is not duplicated.

#### Acceptance Criteria

1. THE system SHALL provide an internal `TaskCreationService` that encapsulates: URL validation, URL normalization, deduplication check, backend validation, and ArchiveTask creation.
2. ALL Ingestion_Interface implementations (MCP Server, REST API, CLI Tool, Browser Extension Webhook, RSS Monitor, Sitemap Monitor, Batch Import) SHALL use the TaskCreationService for creating tasks, and SHALL NOT directly insert records into the database.
3. THE TaskCreationService SHALL accept a standardized input: url (required), target_backend (optional), priority (optional), source (required), tags (optional), title (optional), skip_availability_check (optional).
4. WHEN the TaskCreationService receives a request, THE TaskCreationService SHALL return a result containing: task_id, is_deduplicated flag, and the normalized URL.
5. IF the URL fails validation, THEN THE TaskCreationService SHALL raise a ValidationError with a descriptive message, allowing each Ingestion_Interface to map it to its appropriate error format (HTTP 422, MCP error, CLI stderr).

---

### Requirement 15: 組態擴展

**User Story:** As a service operator, I want all new v2 features to be configurable via environment variables, so that I can customize behavior without code changes.

#### Acceptance Criteria

1. THE Config module SHALL support the following new environment variables with their defaults:
   - `KEEPLINK_ARCHIVEBOX_URL` (no default, enables ArchiveBox backend when set)
   - `KEEPLINK_ARCHIVEBOX_API_KEY` (no default, optional authentication)
   - `KEEPLINK_ARCHIVE_TODAY_ENABLED` (default "false", enables Archive.today backend)
   - `KEEPLINK_FRESHNESS_WINDOW_HOURS` (default 24, availability check window)
   - `KEEPLINK_AVAILABILITY_CHECK_ENABLED` (default "true", master switch for availability checking)
   - `KEEPLINK_CORS_ORIGINS` (default "*", allowed CORS origins for browser extension)
   - `KEEPLINK_BATCH_IMPORT_MAX_URLS` (default 10000, maximum URLs per batch import)
   - `KEEPLINK_WEBHOOK_RETRY_COUNT` (default 3, webhook delivery retries)
   - `KEEPLINK_WEBHOOK_RETRY_INTERVAL_SEC` (default 30, interval between webhook retries)
2. THE Config module SHALL validate all configuration values at startup and log an ERROR with a clear message if any value is invalid (e.g., negative numbers, malformed URLs).
3. IF a required backend configuration is missing (e.g., ArchiveBox URL when ArchiveBox tasks exist in the queue), THEN THE system SHALL log a WARNING at startup but continue operating with available backends.
4. THE Config module SHALL maintain backward compatibility: all existing KEEPLINK_ environment variables from v1.x SHALL continue to work with their documented behavior.
