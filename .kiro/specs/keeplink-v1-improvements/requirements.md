# Requirements Document

## Introduction

KeepLink-MCP v1.1.0 改进集，涵盖可靠性增强（速率限制、卡住任务恢复、优雅停机）、用户体验提升（健康检查、批量状态查询、日志轮转）以及 DevOps 支撑（Docker 容器化、版本对齐、README 更新）。实现分三批次交付：Reliability → UX → DevOps。

## Glossary

- **Worker**: 后台轮询进程，负责从数据库获取待处理任务并调用 Internet Archive SPN2 API 完成归档
- **Token_Bucket**: 一种基于令牌补充的速率限制算法，允许突发请求但维持长期平均速率
- **SPN2_API**: Internet Archive 的 Save Page Now 2 API，用于提交网页快照请求
- **Health_Endpoint**: 提供服务存活性和就绪性信息的 HTTP 端点
- **Batch_Status_Endpoint**: 支持一次查询多个任务状态的 API 端点
- **Stuck_Task**: 因进程意外终止而残留在 processing 状态的任务
- **Graceful_Shutdown**: 接收终止信号后等待当前工作完成再退出的停机策略
- **RotatingFileHandler**: Python logging 模块提供的日志文件轮转处理器

## Requirements

### Requirement 1: Rate Limiting for SPN2 API Calls

**User Story:** As a service operator, I want outbound SPN2 API calls to be rate-limited, so that the service respects Internet Archive's usage policies and avoids being blocked.

#### Acceptance Criteria

1. THE Worker SHALL enforce a configurable Token_Bucket rate limiter before each SPN2_API call.
2. WHEN a task is ready to call SPN2_API but no token is available AND rate limiting is enabled, THE Worker SHALL wait for a token to become available until the configured wait timeout (default 30 seconds, configurable via KEEPLINK_RATE_LIMIT_WAIT_TIMEOUT_SEC) is reached.
3. IF the wait timeout is exceeded before a token becomes available, THEN THE Worker SHALL transition the task status to pending with an incremented retry_count and schedule it for retry using the standard exponential backoff mechanism.
4. IF the waiting mechanism itself fails to activate (e.g., asyncio error), THEN THE Worker SHALL fail the task immediately, transition the task status to pending with an incremented retry_count, and schedule it for retry using the standard exponential backoff mechanism.
5. THE Token_Bucket SHALL support configuration via environment variables KEEPLINK_RATE_LIMIT_TOKENS (default 15 tokens) and KEEPLINK_RATE_LIMIT_INTERVAL_SEC (default 60 seconds), defining the refill count and refill period respectively.
6. WHERE rate limiting is disabled (KEEPLINK_RATE_LIMIT_TOKENS set to 0), THE Worker SHALL bypass all rate limit logic including the timeout mechanism, and the Token_Bucket acquire() call SHALL return True immediately without waiting.
7. WHEN the Worker starts, THE Token_Bucket SHALL initialize with a full bucket of tokens equal to the configured KEEPLINK_RATE_LIMIT_TOKENS value.

---

### Requirement 2: Health Check Endpoint

**User Story:** As a service operator, I want a health check endpoint exposing liveness, readiness, and operational stats, so that monitoring systems can detect service degradation.

#### Acceptance Criteria

1. THE Health_Endpoint SHALL expose a GET /api/health route that returns a JSON response with HTTP status 200 when both liveness and readiness are true, and HTTP status 503 when either is false.
2. THE Health_Endpoint SHALL include a liveness field set to true whenever the HTTP server is accepting connections and able to execute the handler.
3. THE Health_Endpoint SHALL include a readiness field set to true only when both conditions hold: (a) a test query to the database succeeds within 5000ms and takes at least 1ms (ensuring real database interaction rather than cached results), and (b) the Worker polling loop has executed at least one cycle since startup.
4. THE Health_Endpoint SHALL include operational statistics: current queue depth (number of tasks with status pending), success count and failure count within a rolling window of the last 60 minutes.
5. WHEN any database-related readiness failure occurs (including the database being completely unreachable, the connection being refused, or the test query exceeding 5000ms), THE Health_Endpoint SHALL report readiness as false and include an error_description field stating the specific cause of the failure.
6. THE Health_Endpoint SHALL respond within 2000ms when the database is reachable and the pending-task count query scans fewer than 100,000 rows.
7. IF both task_ids and url query parameters are absent from the request, THEN THE Health_Endpoint SHALL return the health JSON without requiring any query parameters.

---

### Requirement 3: Docker Containerization

**User Story:** As a developer, I want a Docker setup with multi-stage build and docker-compose, so that I can deploy KeepLink-MCP consistently across environments.

#### Acceptance Criteria

1. THE Dockerfile SHALL use a multi-stage build with python:3.12-slim as the runtime base image.
2. WHEN the multi-stage build pattern is used (as specified in AC1), THE Dockerfile SHALL install only production dependencies in the final stage.
3. THE docker-compose.yml SHALL define a single service that maps the configured API port and mounts a data volume for the SQLite database.
4. THE docker-compose.yml SHALL support environment variable overrides for all KEEPLINK_ prefixed configuration.
5. WHEN the container starts, THE entrypoint SHALL run the keeplink command as defined in pyproject.toml scripts.

---

### Requirement 4: Stuck Task Recovery on Restart

**User Story:** As a service operator, I want tasks stuck in processing state to be recovered on restart, so that no archive requests are permanently lost due to unexpected shutdowns.

#### Acceptance Criteria

1. WHEN the service starts, THE Worker SHALL query the database for all tasks with status processing.
2. WHEN processing-status tasks are found at startup, THE Worker SHALL transition each task's status to pending while preserving the existing retry_count and clearing next_retry_at to null.
3. THE Worker SHALL log the count of recovered stuck tasks at INFO level during startup, including when the count is zero.
4. THE stuck task recovery SHALL execute before the Worker begins its normal polling loop.
5. IF the stuck task recovery query or update fails due to a database error, THEN THE Worker SHALL log the error at ERROR level and abort startup rather than proceeding with a potentially inconsistent task queue.
6. IF the service aborts startup for other reasons after the recovery process has begun, THE Worker SHALL leave any partial task status changes as-is without attempting a rollback.
7. THE stuck task recovery SHALL enforce a configurable timeout (default 30 seconds, configurable via KEEPLINK_RECOVERY_TIMEOUT_SEC). IF the recovery exceeds this timeout, THEN THE Worker SHALL log a WARNING and proceed to start the normal polling loop with the partially recovered tasks.

---

### Requirement 5: Graceful Shutdown

**User Story:** As a service operator, I want the service to complete in-progress archive tasks before shutting down, so that partial state corruption is avoided.

#### Acceptance Criteria

1. WHEN a termination signal (SIGTERM or SIGINT) is received, THE Worker SHALL stop accepting new tasks from the queue and SHALL NOT start any new polling cycles.
2. WHEN a termination signal is received, THE Worker SHALL wait for all currently executing archive operations to complete, applying their normal success or error handling logic to persist results before exiting.
3. IF a currently executing task does not complete within 30 seconds after the stop signal, THEN THE Worker SHALL cancel the task, transition its database status to pending so that Stuck_Task recovery can re-process it on next startup, and log a warning including the task_id. Each task receives its own independent 30-second window; one task timing out does not affect other in-progress tasks.
4. THE Worker SHALL apply the 30-second timeout independently to each in-flight task, so that one slow task does not force cancellation of other tasks that may complete within their own 30-second window.
5. THE Worker SHALL log the shutdown initiation and completion events at INFO level.
6. IF logging fails during task cancellation, THEN THE Worker SHALL proceed with the cancellation and database status transition regardless of the logging failure.

---

### Requirement 6: Log Rotation

**User Story:** As a service operator, I want log files to rotate automatically, so that disk space is not exhausted by indefinitely growing log files.

#### Acceptance Criteria

1. THE logging_setup module SHALL use RotatingFileHandler instead of FileHandler for file-based logging.
2. THE RotatingFileHandler SHALL rotate the log file when it exceeds a configurable maximum size (default 10 MB).
3. THE RotatingFileHandler SHALL retain a configurable number of backup files (default 5).
4. THE rotation configuration SHALL be overridable via environment variables KEEPLINK_LOG_MAX_BYTES and KEEPLINK_LOG_BACKUP_COUNT.
5. WHERE the environment is development (indicated by a configurable flag or when no log file path is configured), THE logging_setup module SHALL use a standard FileHandler as a fallback instead of RotatingFileHandler.

---

### Requirement 7: Batch Status Query

**User Story:** As an MCP client, I want to query the status of multiple tasks in a single request, so that I reduce round-trip overhead when managing many archive jobs.

#### Acceptance Criteria

1. THE Batch_Status_Endpoint SHALL expose a GET /api/status/batch route that returns a JSON response containing an array of task status objects.
2. WHEN task_ids query parameter is provided as comma-separated task ID strings, THE Batch_Status_Endpoint SHALL return status information (task_id, url, status, result_url, error_message, retry_count, created_at, updated_at) for each specified task ID that exists in the database.
3. WHEN urls query parameter is provided as comma-separated URL strings, THE Batch_Status_Endpoint SHALL return the most recent task status for each specified URL that exists in the database.
4. WHEN both task_ids and urls parameters are provided in the same request, THE Batch_Status_Endpoint SHALL process both parameters and merge the results into a single response array, with the combined total of identifiers subject to the 50-identifier limit.
5. IF neither task_ids nor urls parameter is provided, THEN THE Batch_Status_Endpoint SHALL return HTTP 422 with an error message indicating that at least one query parameter is required.
6. IF the combined total of identifiers across all parameters exceeds 50, THEN THE Batch_Status_Endpoint SHALL return HTTP 422 with an error message indicating the maximum limit of 50 identifiers per request.
7. THE Batch_Status_Endpoint SHALL return partial results containing only tasks that match existing records, silently omitting identifiers that do not correspond to any stored task.
8. THE Batch_Status_Endpoint SHALL respond within 3000ms when queried with the maximum of 50 identifiers under normal operating conditions.
9. WHEN no matching tasks are found for any of the requested identifiers, THE Batch_Status_Endpoint SHALL return HTTP 200 with an empty results array.

---

### Requirement 8: Version Alignment

**User Story:** As a maintainer, I want the version string to be consistent across pyproject.toml and the package __init__.py, so that tooling and users see a unified version.

#### Acceptance Criteria

1. THE pyproject.toml SHALL declare version as "1.2.0".
2. THE keeplink_mcp/__init__.py SHALL declare __version__ = "1.2.0".
3. THE two version declarations SHALL always contain the same value.

---

### Requirement 9: README Update

**User Story:** As a new user, I want the README to reflect v1.1 changes and Docker usage instructions, so that I can onboard quickly.

#### Acceptance Criteria

1. THE README.md SHALL document all new endpoints introduced in this release (health check, batch status).
2. THE README.md SHALL include Docker usage instructions covering both docker build and docker-compose up workflows.
3. THE README.md SHALL document new environment variables introduced by rate limiting and log rotation features.
4. THE README.md SHALL reflect the updated version number (1.2.0).
