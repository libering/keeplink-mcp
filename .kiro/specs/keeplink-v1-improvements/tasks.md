# Implementation Plan: KeepLink v1.1 Improvements

## Overview

分三批次实现 KeepLink-MCP v1.2.0 改进集：Reliability（速率限制、卡住任务恢复、优雅停机）→ UX（健康检查、批量状态查询、日志轮转）→ DevOps（Docker、版本对齐、README）。每个任务遵循 interface → implementation → integration → test 模式。

## Tasks

- [x] 1. Batch 1 — Reliability: Rate Limiter
  - [x] 1.1 Create `src/keeplink_mcp/worker/rate_limiter.py` with TokenBucketLimiter
    - Define `RateLimitConfig` dataclass with `max_tokens`, `interval_sec`, `wait_timeout_sec`, `is_disabled` property
    - Implement `TokenBucketLimiter` class with `acquire(timeout)` → bool and `available_tokens` property
    - Use `asyncio.Lock` for concurrency safety; `asyncio.wait_for` for timeout handling
    - When `max_tokens == 0`, `acquire()` returns True immediately (disabled path)
    - _Requirements: 1.1, 1.2, 1.4, 1.5_

  - [x] 1.2 Add rate limit config fields to `src/keeplink_mcp/config.py`
    - Add `rate_limit_tokens`, `rate_limit_interval_sec`, `rate_limit_wait_timeout_sec` fields
    - Read from env vars `KEEPLINK_RATE_LIMIT_TOKENS` (default 15), `KEEPLINK_RATE_LIMIT_INTERVAL_SEC` (default 60.0), `KEEPLINK_RATE_LIMIT_TIMEOUT` (default 30.0)
    - _Requirements: 1.4_

  - [x] 1.3 Integrate rate limiter into `src/keeplink_mcp/worker/archiver.py`
    - Instantiate `TokenBucketLimiter` in `BackgroundWorker.__init__` using config
    - In `_process_task()`, call `limiter.acquire()` before `_call_spn2()`
    - If acquire returns False (timeout), skip task this round (leave as pending)
    - _Requirements: 1.1, 1.2, 1.3, 1.5_

  - [x] 1.4 Write property test for TokenBucketLimiter
    - **Property 1: Token bucket enforces rate ceiling**
    - For any sequence of N acquire() calls within a single interval, successful acquisitions ≤ max_tokens
    - **Validates: Requirements 1.1**

  - [x] 1.5 Write unit tests for rate limiter
    - Test disabled path (max_tokens=0 → immediate success)
    - Test timeout behavior (no tokens available → returns False after timeout)
    - Test token replenishment (after interval, new token available)
    - _Requirements: 1.1, 1.2, 1.4, 1.5_

- [x] 2. Batch 1 — Reliability: Stuck Task Recovery
  - [x] 2.1 Add `recover_processing_tasks()` method to `src/keeplink_mcp/db/repository.py`
    - Bulk UPDATE: `SET status='pending', updated_at=now() WHERE status='processing'`
    - Return affected rowcount
    - _Requirements: 4.1, 4.2_

  - [x] 2.2 Create `src/keeplink_mcp/worker/recovery.py` with `recover_stuck_tasks()`
    - Async function accepting session_factory and logger
    - Call repository's `recover_processing_tasks()`, return count
    - Must execute BEFORE worker polling loop starts
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [x] 2.3 Integrate stuck task recovery into `src/keeplink_mcp/main.py` lifespan startup
    - Call `recover_stuck_tasks()` before `worker.start()`
    - Log recovered count at INFO level if count > 0
    - _Requirements: 4.3, 4.4_

  - [x] 2.4 Write property test for stuck task recovery
    - **Property 3: Stuck task recovery preserves non-processing tasks**
    - For any set of tasks, after recovery: all previously-processing → pending; all others unchanged
    - **Validates: Requirements 4.2**

- [x] 3. Batch 1 — Reliability: Graceful Shutdown
  - [x] 3.1 Enhance `BackgroundWorker.stop()` in `src/keeplink_mcp/worker/archiver.py`
    - Track active tasks via `_active_tasks: set[asyncio.Task]`
    - `stop(timeout=30.0)`: set `_running = False`, await active tasks with timeout
    - Cancel tasks exceeding timeout, log WARNING for each cancelled task
    - Log shutdown initiation and completion at INFO level
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 3.2 Update `src/keeplink_mcp/main.py` lifespan shutdown to use graceful stop
    - Call `await worker.stop(timeout=30.0)` in shutdown phase
    - _Requirements: 5.1, 5.2_

  - [x] 3.3 Write unit tests for graceful shutdown
    - Test normal shutdown (all tasks complete within timeout)
    - Test timeout cancellation (task exceeds 30s → cancelled + WARNING logged)
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [x] 4. Checkpoint — Batch 1 complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Batch 2 — UX: Health Check Endpoint
  - [x] 5.1 Add `get_queue_stats()` method to `src/keeplink_mcp/db/repository.py`
    - Return dict with `queue_depth` (pending count), `success_count` (24h), `failure_count` (24h)
    - Use single query with GROUP BY status + time window filter
    - _Requirements: 2.4_

  - [x] 5.2 Create `src/keeplink_mcp/api/health.py` with health router
    - Define `HealthStats` and `HealthResponse` Pydantic models
    - Implement `GET /api/health` route
    - Liveness: always True (process alive)
    - Readiness: check DB connectivity (`SELECT 1`) + worker._running flag
    - Include queue_depth, success_count, failure_count stats
    - Return readiness=false with error description if DB unavailable
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 5.3 Register health router in `src/keeplink_mcp/api/app.py`
    - Import and include `health_router`
    - _Requirements: 2.1_

  - [x] 5.4 Write property test for health stats
    - **Property 2: Health stats reflect actual task distribution**
    - For any set of tasks: queue_depth == count(pending), success_count == count(success in 24h), failure_count == count(failed in 24h)
    - **Validates: Requirements 2.4**

  - [x] 5.5 Write unit tests for health endpoint
    - Test successful health response with all fields
    - Test DB unavailable → readiness=false
    - _Requirements: 2.1, 2.2, 2.3, 2.5_

- [x] 6. Batch 2 — UX: Batch Status Query
  - [x] 6.1 Add batch query methods to `src/keeplink_mcp/db/repository.py`
    - `get_tasks_by_ids(task_ids: list[str]) → list[ArchiveTask]`: SELECT WHERE task_id IN (...)
    - `get_latest_tasks_by_urls(urls: list[str]) → list[ArchiveTask]`: For each URL, return most recent task (window function or subquery)
    - _Requirements: 7.2, 7.3, 7.6_

  - [x] 6.2 Create `src/keeplink_mcp/api/batch_routes.py` with batch status router
    - Define `BatchStatusResponse` Pydantic model
    - Implement `GET /api/status/batch` route accepting `task_ids` and `url` query params (comma-separated)
    - Validate combined count ≤ 50, return HTTP 422 if exceeded
    - Merge results from both queries, deduplicate, return partial results
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 6.3 Register batch router in `src/keeplink_mcp/api/app.py`
    - Import and include `batch_router`
    - _Requirements: 7.1_

  - [x] 6.4 Write property tests for batch status
    - **Property 4: Batch query by task_ids returns correct subset**
    - **Validates: Requirements 7.2, 7.6**
    - **Property 5: Batch query by URLs returns most recent task per URL**
    - **Validates: Requirements 7.3**
    - **Property 6: Batch query rejects requests exceeding 50 identifiers**
    - **Validates: Requirements 7.4, 7.5**

  - [x] 6.5 Write unit tests for batch endpoint
    - Test valid batch query with mixed task_ids and urls
    - Test 422 response when > 50 identifiers
    - Test partial results (some IDs exist, some don't)
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

- [x] 7. Batch 2 — UX: Log Rotation
  - [x] 7.1 Add log rotation config fields to `src/keeplink_mcp/config.py`
    - Add `log_max_bytes` (env `KEEPLINK_LOG_MAX_BYTES`, default 10MB) and `log_backup_count` (env `KEEPLINK_LOG_BACKUP_COUNT`, default 5)
    - _Requirements: 6.4_

  - [x] 7.2 Modify `src/keeplink_mcp/logging_setup.py` to use RotatingFileHandler
    - Replace `logging.FileHandler` with `logging.handlers.RotatingFileHandler`
    - Pass `maxBytes` and `backupCount` from config
    - Keep function signature backward-compatible (new params have defaults)
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 7.3 Write unit test for log rotation setup
    - Verify RotatingFileHandler is used when log_file is configured
    - Verify maxBytes and backupCount are passed correctly
    - _Requirements: 6.1, 6.2, 6.3_

- [x] 8. Checkpoint — Batch 2 complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Batch 3 — DevOps: Docker Containerization
  - [x] 9.1 Create `Dockerfile` with multi-stage build
    - Stage 1 (builder): python:3.12-slim, install deps via pip
    - Stage 2 (runtime): python:3.12-slim, copy installed packages, expose 19210, CMD ["keeplink"]
    - Mount volume for /app/data (SQLite + logs)
    - _Requirements: 3.1, 3.2, 3.5_

  - [x] 9.2 Create `docker-compose.yml`
    - Define single keeplink service with port mapping, data volume, env var overrides
    - Support all KEEPLINK_ prefixed env var configuration
    - Use `restart: unless-stopped`
    - _Requirements: 3.3, 3.4_

- [x] 10. Batch 3 — DevOps: Version Alignment
  - [x] 10.1 Update version strings to "1.2.0"
    - Update `pyproject.toml` version field
    - Update `src/keeplink_mcp/__init__.py` `__version__`
    - Update `src/keeplink_mcp/api/app.py` FastAPI version parameter
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 10.2 Write assertion test for version consistency
    - **Property 7: Version consistency across sources**
    - Read version from pyproject.toml, __init__.py, and app metadata; assert all equal
    - **Validates: Requirements 8.1, 8.2, 8.3**

- [x] 11. Batch 3 — DevOps: README Update
  - [x] 11.1 Update `README.md` with v1.2 changes
    - Document health check endpoint (GET /api/health)
    - Document batch status endpoint (GET /api/status/batch)
    - Add Docker usage instructions (docker build + docker-compose up)
    - Document new env vars: KEEPLINK_RATE_LIMIT_TOKENS, KEEPLINK_RATE_LIMIT_INTERVAL_SEC, KEEPLINK_RATE_LIMIT_TIMEOUT, KEEPLINK_LOG_MAX_BYTES, KEEPLINK_LOG_BACKUP_COUNT
    - Update version number to 1.2.0
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [x] 12. Final Checkpoint — All batches complete
  - Ensure all tests pass (`pytest test/ -v`)
  - Run lint (`ruff check src/ test/`)
  - Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation per batch
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- No DB schema migration needed — existing `archive_tasks` table structure is sufficient
- All new modules follow single-responsibility: one file = one concern

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "2.1", "7.1"] },
    { "id": 1, "tasks": ["1.3", "2.2", "5.1", "6.1"] },
    { "id": 2, "tasks": ["1.4", "1.5", "2.3", "2.4", "3.1", "5.2", "6.2", "7.2"] },
    { "id": 3, "tasks": ["3.2", "3.3", "5.3", "5.4", "5.5", "6.3", "6.4", "6.5", "7.3"] },
    { "id": 4, "tasks": ["9.1", "9.2", "10.1"] },
    { "id": 5, "tasks": ["10.2", "11.1"] }
  ]
}
```
