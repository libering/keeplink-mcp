# Requirements Document

## Introduction

OmniArchive MCP 是一個非同步 MCP 中間件系統，讓 AI Agent 在深度研究過程中能將網頁存檔至 Internet Archive，而不阻塞其推理迴圈。系統採用雙進程架構：MCP Server 透過內部 HTTP 呼叫與 FastAPI Service 通訊，FastAPI Service 負責任務管理與持久化，搭配背景佇列處理與指數退避重試機制，解決 Internet Archive Save Page Now API 的速率限制問題。

## Glossary

- **MCP_Server**: 透過 MCP 協議暴露工具的服務端模組，透過內部 HTTP 呼叫與 FastAPI_Service 通訊（獨立進程）
- **FastAPI_Service**: 接收內部 HTTP 請求並寫入任務佇列的 API 服務層，負責所有 DB 讀寫操作
- **Background_Worker**: 輪詢待處理任務並呼叫 Internet Archive SPN2 API 的背景處理模組
- **ArchiveTask**: 代表一個 URL 存檔作業的資料模型，包含生命週期狀態
- **TaskStatus**: 任務狀態列舉，包含 pending、processing、success、failed
- **SPN2_API**: Internet Archive 的 Save Page Now v2 API
- **Exponential_Backoff**: 指數退避重試策略，每次重試間隔按指數增長
- **WAL_Mode**: SQLite 的 Write-Ahead Logging 模式，允許並行讀寫
- **URL_Validator**: 負責檢驗輸入 URL 格式與協議合法性的驗證邏輯
- **Deduplication_Window**: URL 去重時間窗口（預設 24 小時），在此窗口內相同 URL 的重複提交將依據既有任務狀態決定是否建立新任務
- **next_retry_at**: ArchiveTask 的欄位，記錄下次可重試的 UTC 時間點，Worker 在此時間前會跳過該任務

## Requirements

### Requirement 1: MCP 工具介面 — archive_url

**User Story:** As an AI Agent, I want to call an `archive_url` tool via MCP protocol, so that I can submit web pages for archiving without blocking my reasoning loop.

#### Acceptance Criteria

1. WHEN an AI Agent invokes the `archive_url` tool with a valid URL, THE MCP_Server SHALL forward the request to FastAPI_Service via internal HTTP call and return a task_id within 50ms
2. WHEN the `archive_url` tool is invoked, THE MCP_Server SHALL return a structured response containing the task_id and initial status of "pending"
3. IF the MCP_Server receives a request with missing or null URL parameter, THEN THE MCP_Server SHALL return an MCP protocol error with a descriptive message

### Requirement 2: MCP 工具介面 — get_archive_status

**User Story:** As an AI Agent, I want to query the status of an archive task, so that I can check whether a previously submitted URL has been successfully archived.

#### Acceptance Criteria

1. WHEN an AI Agent invokes the `get_archive_status` tool with a valid task_id, THE MCP_Server SHALL query FastAPI_Service and return the task's current status, result_url (if success), and error_message (if failed)
2. WHEN an AI Agent invokes the `get_archive_status` tool with a URL instead of task_id, THE MCP_Server SHALL query FastAPI_Service and return the most recent task associated with that URL
3. IF the provided task_id does not exist in the system, THEN THE MCP_Server SHALL return a structured error response indicating "task not found"
4. IF the provided URL has no associated tasks, THEN THE MCP_Server SHALL return a structured error response indicating "no tasks found for this URL"
5. WHEN the `get_archive_status` tool is invoked, THE MCP_Server SHALL accept either a task_id parameter or a url parameter (at least one required)

### Requirement 3: URL 驗證

**User Story:** As the system, I want to validate incoming URLs before enqueuing, so that invalid requests are rejected immediately and do not consume queue resources.

#### Acceptance Criteria

1. WHEN a URL is submitted, THE URL_Validator SHALL verify that the URL contains a valid scheme (http or https)
2. WHEN a URL is submitted, THE URL_Validator SHALL verify that the URL contains a valid hostname with at least one dot separator
3. IF a URL with an invalid scheme (not http/https) is submitted, THEN THE MCP_Server SHALL reject it immediately with a descriptive error
4. IF a URL with an empty or malformed hostname is submitted, THEN THE MCP_Server SHALL reject it immediately with a descriptive error
5. WHEN a valid URL is submitted, THE URL_Validator SHALL normalize the URL by stripping trailing whitespace before processing

### Requirement 4: URL 去重機制

**User Story:** As the system, I want to deduplicate archive requests within a 24-hour window, so that redundant requests do not waste API quota and processing resources.

#### Acceptance Criteria

1. WHEN a URL is submitted and an existing task for the same URL with status "pending" or "processing" exists within the Deduplication_Window, THE FastAPI_Service SHALL return the existing task_id without creating a new task
2. WHEN a URL is submitted and an existing task for the same URL with status "success" exists within the Deduplication_Window, THE FastAPI_Service SHALL return the existing task_id and result_url without creating a new task
3. WHEN a URL is submitted and an existing task for the same URL with status "failed" exists within the Deduplication_Window, THE FastAPI_Service SHALL create a new task to allow re-attempt
4. WHEN a URL is submitted and no existing task for the same URL exists within the Deduplication_Window, THE FastAPI_Service SHALL create a new task normally
5. THE FastAPI_Service SHALL define the Deduplication_Window as 24 hours from the most recent task's created_at timestamp for the same URL

### Requirement 5: 任務佇列持久化

**User Story:** As a system operator, I want archive tasks to be persisted in SQLite, so that no tasks are lost on process restart and concurrent access is handled safely.

#### Acceptance Criteria

1. WHEN a new archive request is accepted, THE FastAPI_Service SHALL create an ArchiveTask record with status "pending", retry_count 0, next_retry_at as null, and a generated UUID task_id
2. THE FastAPI_Service SHALL store all ArchiveTask records in a SQLite database configured with WAL mode
3. WHEN the database is accessed concurrently by the API writer and the Background_Worker reader, THE SQLite engine SHALL handle concurrent access via WAL mode without corruption
4. WHEN the system starts, THE FastAPI_Service SHALL create the database file and schema if they do not already exist
5. WHEN an ArchiveTask transitions state, THE FastAPI_Service SHALL update the updated_at timestamp to the current UTC time

### Requirement 6: 背景 Worker 處理

**User Story:** As the system, I want a background worker to poll and process pending tasks, so that URLs are archived asynchronously without blocking the MCP response.

#### Acceptance Criteria

1. WHILE the system is running, THE Background_Worker SHALL poll for pending tasks at a configurable interval (default 5 seconds)
2. WHEN a pending task is found, THE Background_Worker SHALL transition its status to "processing" before calling the SPN2_API
3. WHEN the SPN2_API returns a successful archive URL, THE Background_Worker SHALL update the task status to "success" and store the result_url
4. WHEN processing tasks, THE Background_Worker SHALL process up to N tasks per poll cycle, where N is configurable via OMNIARCHIVE_WORKER_CONCURRENCY environment variable (default 1)
5. WHILE the Background_Worker is processing a task, THE Background_Worker SHALL not pick up the same task again in a subsequent poll
6. WHEN a task has a non-null next_retry_at value that is in the future, THE Background_Worker SHALL skip that task and continue processing other pending tasks

### Requirement 7: 指數退避重試邏輯

**User Story:** As the system, I want to retry failed requests with exponential backoff, so that transient errors from the Internet Archive are handled gracefully without violating rate limits.

#### Acceptance Criteria

1. WHEN the SPN2_API returns a 429 (Too Many Requests) or 5xx error, THE Background_Worker SHALL schedule the task for retry with exponential backoff delay
2. THE Background_Worker SHALL calculate backoff delay as base_backoff_sec * 2^(retry_count), producing delays of 1m, 2m, 4m, 8m, 16m for retries 0-4
3. WHEN a task reaches the maximum retry count of 5, THE Background_Worker SHALL mark the task as "failed" and record the final error message
4. WHEN a retry is scheduled, THE Background_Worker SHALL revert the task status to "pending", increment retry_count by 1, and set next_retry_at to current UTC time plus the calculated backoff delay

### Requirement 8: 錯誤分類

**User Story:** As the system, I want to classify errors as retryable or non-retryable, so that permanent failures are not endlessly retried and transient failures get another chance.

#### Acceptance Criteria

1. WHEN the SPN2_API returns HTTP 429, THE Background_Worker SHALL classify the error as retryable
2. WHEN the SPN2_API returns HTTP 500, 502, 503, or 504, THE Background_Worker SHALL classify the error as retryable
3. WHEN the SPN2_API returns HTTP 403 (Forbidden/Auth error), THE Background_Worker SHALL classify the error as non-retryable and mark the task as "failed" immediately
4. WHEN the SPN2_API returns HTTP 401 (Unauthorized), THE Background_Worker SHALL classify the error as non-retryable and mark the task as "failed" immediately
5. IF a network timeout or connection error occurs, THEN THE Background_Worker SHALL classify the error as retryable
6. WHEN a task is marked as permanently failed, THE Background_Worker SHALL record the HTTP status code and error detail in the error_message field

### Requirement 9: 日誌與可觀測性

**User Story:** As a system operator, I want structured local logging, so that I can monitor system health and troubleshoot issues without external telemetry.

#### Acceptance Criteria

1. THE system SHALL log all events to both stdout and a local file (configurable path)
2. WHEN a task is created, THE system SHALL log the task_id and target URL at INFO level
3. WHEN a task transitions to success, THE system SHALL log the task_id and result_url at INFO level
4. WHEN a task transitions to failed, THE system SHALL log the task_id, error_message, and retry_count at WARNING level
5. WHEN a retryable error occurs, THE system SHALL log the task_id, HTTP status, and next retry delay at WARNING level
6. THE system SHALL use a consistent log format with timestamp, level, module name, and message

### Requirement 10: 組態管理

**User Story:** As a system operator, I want all operational parameters to be configurable via environment variables, so that I can adjust behavior without modifying code.

#### Acceptance Criteria

1. THE system SHALL support configuration via environment variables prefixed with OMNIARCHIVE_
2. THE system SHALL provide sensible defaults for all configuration values when environment variables are not set
3. WHEN the OMNIARCHIVE_DB_PATH variable is set, THE system SHALL use the specified path for the SQLite database file
4. WHEN the OMNIARCHIVE_MAX_RETRIES variable is set, THE Background_Worker SHALL use the specified value as the maximum retry count
5. WHEN the OMNIARCHIVE_BASE_BACKOFF variable is set, THE Background_Worker SHALL use the specified value (in seconds) as the base backoff interval
6. THE system SHALL bind the API service to 127.0.0.1 only, preventing external network access
7. WHEN the OMNIARCHIVE_IA_ACCESS_KEY and OMNIARCHIVE_IA_SECRET_KEY variables are set, THE Background_Worker SHALL use them for SPN2_API authentication
8. WHEN the OMNIARCHIVE_WORKER_CONCURRENCY variable is set, THE Background_Worker SHALL use the specified value as the maximum number of tasks processed per poll cycle

### Requirement 11: 啟動與生命週期

**User Story:** As a system operator, I want the system to start all components in the correct order and shut down gracefully, so that no tasks are lost or corrupted during lifecycle transitions.

#### Acceptance Criteria

1. WHEN the system starts, THE system SHALL initialize the database, start the FastAPI_Service, and start the Background_Worker in the correct dependency order
2. WHEN the system starts, THE MCP_Server SHALL run as a separate process and communicate with FastAPI_Service via internal HTTP calls
3. WHEN the system receives a shutdown signal (SIGTERM/SIGINT), THE system SHALL stop accepting new tasks, allow the current processing task to complete, and then shut down cleanly
4. IF the database initialization fails at startup, THEN THE system SHALL log the error and exit with a non-zero exit code
5. WHEN the system starts successfully, THE system SHALL log a startup message indicating the configured API host, port, and database path
6. WHILE the system is shutting down, THE Background_Worker SHALL not start processing any new tasks from the queue
