# Design Document: KeepLink v1.1 Improvements

## Overview

本设计覆盖 KeepLink-MCP v1.1→v1.2 的改进集，分三个批次交付：

1. **Reliability（可靠性）**：Rate Limiting、Stuck Task Recovery、Graceful Shutdown
2. **UX（用户体验）**：Health Check Endpoint、Batch Status Query、Log Rotation
3. **DevOps**：Docker Containerization、Version Alignment、README Update

设计原则：最小化对现有接口的侵入性变更，新功能以新模块形式引入，通过现有入口点整合。

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────┐
│                     main.py (lifespan)                          │
│  ┌──────────┐   ┌─────────────┐   ┌────────────────────────┐  │
│  │ FastAPI  │   │  Background │   │  Stuck Task Recovery   │  │
│  │  App     │   │  Worker     │   │  (startup hook)        │  │
│  └────┬─────┘   └──────┬──────┘   └────────────────────────┘  │
│       │                 │                                       │
│       │    ┌────────────┴──────────────┐                       │
│       │    │    TokenBucketLimiter     │ ← NEW                 │
│       │    └────────────┬──────────────┘                       │
│       │                 │                                       │
│       │                 ▼                                       │
│       │         SPN2 API (waybackpy)                           │
│       │                                                        │
│       ▼                                                        │
│  ┌──────────────────────────────────────┐                      │
│  │  API Routes                          │                      │
│  │  /api/archive                        │                      │
│  │  /api/status/{task_id}               │                      │
│  │  /api/status?url=                    │                      │
│  │  /api/status/batch        ← NEW     │                      │
│  │  /api/health              ← NEW     │                      │
│  └──────────────────────────────────────┘                      │
└────────────────────────────────────────────────────────────────┘
```

---

## Batch 1: Reliability

### Component 1: Token Bucket Rate Limiter

**New File:** `src/keeplink_mcp/worker/rate_limiter.py`

#### Interface

```python
"""Token bucket rate limiter for SPN2 API call throttling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitConfig:
    """Configuration for the token bucket.

    Attributes:
        max_tokens: Maximum tokens (burst capacity). 0 = disabled.
        interval_sec: Token replenishment interval in seconds.
        wait_timeout_sec: Max wait time before declaring rate-limited.
    """
    max_tokens: int
    interval_sec: float
    wait_timeout_sec: float = 30.0

    @property
    def is_disabled(self) -> bool:
        return self.max_tokens <= 0


class TokenBucketLimiter:
    """Async token bucket that controls outbound SPN2 call rate.

    Usage:
        limiter = TokenBucketLimiter(config)
        acquired = await limiter.acquire(timeout=config.wait_timeout_sec)
        if not acquired:
            # handle rate-limited case
    """

    def __init__(self, config: RateLimitConfig) -> None: ...

    async def acquire(self, timeout: float | None = None) -> bool:
        """Attempt to acquire a token. Returns True if acquired, False on timeout."""
        ...

    @property
    def available_tokens(self) -> int:
        """Current number of available tokens (for health stats)."""
        ...
```

#### Design Details

- **算法**：标准令牌桶。tokens 初始为 max_tokens，每 interval_sec 补充 1 个 token 直到 max_tokens。
- **并发安全**：使用 `asyncio.Lock` 保护 token 计数器，`asyncio.Event` 通知等待者。
- **禁用路径**：当 `max_tokens == 0` 时，`acquire()` 立即返回 True，不执行任何等待逻辑。
- **超时处理**：使用 `asyncio.wait_for` 包装等待，超时抛出后返回 False。

#### Integration Point

**Modified File:** `src/keeplink_mcp/worker/archiver.py`

在 `_process_task()` 中，调用 `_call_spn2()` 前插入 `limiter.acquire()` 调用：

```python
async def _process_task(self, task: ArchiveTask) -> None:
    # ... mark_processing ...
    if not self._rate_limit_config.is_disabled:
        acquired = await self._limiter.acquire(
            timeout=self._rate_limit_config.wait_timeout_sec
        )
        if not acquired:
            await self._handle_rate_limited(task)
            return
    # ... proceed with _call_spn2 ...
```

**Modified File:** `src/keeplink_mcp/config.py`

新增配置字段（所有值通过环境变量读取，无硬编码密钥）：

```python
# Rate limiting — controls outbound SPN2 API call frequency
rate_limit_tokens: int = int(os.getenv("KEEPLINK_RATE_LIMIT_TOKENS", "15"))
rate_limit_interval_sec: float = float(os.getenv("KEEPLINK_RATE_LIMIT_INTERVAL_SEC", "60.0"))
rate_limit_wait_timeout_sec: float = float(os.getenv("KEEPLINK_RATE_LIMIT_TIMEOUT", "30.0"))
```

---

### Component 2: Stuck Task Recovery

**New File:** `src/keeplink_mcp/worker/recovery.py`

#### Interface

```python
"""Stuck task recovery: transitions orphaned processing tasks back to pending on startup."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker


async def recover_stuck_tasks(
    session_factory: sessionmaker,
    logger: logging.Logger,
) -> int:
    """Find all tasks stuck in 'processing' and revert them to 'pending'.

    Must be called BEFORE the worker polling loop starts.

    Args:
        session_factory: Async session factory for DB access.
        logger: Logger for recording recovery count.

    Returns:
        Number of tasks recovered.
    """
    ...
```

#### Design Details

- 单一 SQL UPDATE: `UPDATE archive_tasks SET status='pending', updated_at=now() WHERE status='processing'`
- 返回 affected rowcount，由调用方记录日志。
- 在 `main.py` 的 lifespan startup 中，`worker.start()` 之前调用。

#### Integration Point

**Modified File:** `src/keeplink_mcp/main.py`

在 lifespan startup 阶段：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Step 1: Recover stuck tasks BEFORE worker starts
    recovered = await recover_stuck_tasks(session_factory, logger)
    if recovered > 0:
        logger.info("Recovered %d stuck tasks", recovered)

    # Step 2: Start worker
    worker_task = asyncio.create_task(worker.start())
    yield
    # ... shutdown ...
```

**New Method in:** `src/keeplink_mcp/db/repository.py`

```python
async def recover_processing_tasks(self) -> int:
    """Bulk transition all processing tasks to pending. Returns count."""
    ...
```

---

### Component 3: Graceful Shutdown

**Modified File:** `src/keeplink_mcp/worker/archiver.py`

#### Interface Changes

```python
class BackgroundWorker:
    # Existing: start(), stop()
    # New: enhanced stop with drain + timeout

    async def stop(self, timeout: float = 30.0) -> None:
        """Gracefully stop the worker.

        1. Stop accepting new tasks (_running = False)
        2. Wait for in-progress tasks to complete (up to timeout)
        3. Cancel any tasks exceeding timeout with a warning log.
        """
        ...
```

#### Design Details

- 使用 `asyncio.TaskGroup` 或跟踪 `_active_tasks: set[asyncio.Task]` 来监控正在执行的任务。
- `stop()` 先设置 `_running = False`（停止接受新任务），然后 `asyncio.wait(_active_tasks, timeout=timeout)`。
- 超时后对未完成的 task 调用 `.cancel()`，并记录 WARNING 日志。
- Uvicorn 的 SIGTERM/SIGINT 触发 lifespan shutdown，进而调用 `worker.stop(timeout=30.0)`。

#### Integration Point

**Modified File:** `src/keeplink_mcp/main.py`

```python
# Shutdown: stop worker gracefully with 30s timeout
await worker.stop(timeout=30.0)
worker_task.cancel()
```

---

## Batch 2: UX

### Component 4: Health Check Endpoint

**New File:** `src/keeplink_mcp/api/health.py`

#### Interface

```python
"""Health check endpoint providing liveness, readiness, and operational stats."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response schema for GET /api/health."""
    liveness: bool
    readiness: bool
    readiness_error: str | None = None
    stats: HealthStats


class HealthStats(BaseModel):
    """Operational statistics included in health response."""
    queue_depth: int       # Count of tasks in 'pending' status
    success_count: int     # Tasks with 'success' status (last 24h)
    failure_count: int     # Tasks with 'failed' status (last 24h)


async def health_check(session: AsyncSession, worker_running: bool) -> HealthResponse:
    """Compute health status by checking DB connectivity and counting tasks."""
    ...
```

#### Design Details

- **Liveness**：始终为 True（进程活着才能响应）。
- **Readiness**：执行一个轻量 `SELECT 1` 验证 DB 连通性，且检查 worker._running 标志。
- **Stats**：`SELECT COUNT(*) ... GROUP BY status` 配合 24h 时间窗口。
- 使用独立 router 文件避免 routes.py 膨胀。

#### Integration Point

**Modified File:** `src/keeplink_mcp/api/app.py`

```python
from keeplink_mcp.api.health import health_router
app.include_router(health_router)
```

**Modified File:** `src/keeplink_mcp/db/repository.py`

新增：

```python
async def get_queue_stats(self, window_hours: int = 24) -> dict[str, int]:
    """Return {queue_depth, success_count, failure_count} for health check."""
    ...
```

---

### Component 5: Batch Status Query

**New File:** `src/keeplink_mcp/api/batch_routes.py`

#### Interface

```python
"""Batch status query endpoint for multiple tasks/URLs in a single request."""

from __future__ import annotations

from pydantic import BaseModel

from keeplink_mcp.api.schemas import TaskStatusResponse


class BatchStatusResponse(BaseModel):
    """Response for GET /api/status/batch."""
    results: list[TaskStatusResponse]
    total_requested: int
    total_found: int


# Route: GET /api/status/batch?task_ids=id1,id2&url=url1,url2
# - task_ids: comma-separated task IDs (max 50)
# - url: comma-separated URLs (max 50, combined total with task_ids)
# Returns partial results for matching identifiers, omits non-matching.
```

#### Design Details

- 验证 `len(task_ids) + len(urls) <= 50`，超过返回 422。
- task_ids 查询：`SELECT ... WHERE task_id IN (:ids)`
- urls 查询：每个 URL 取最新一条（使用 window function 或逐个查询）。
- 合并结果，去重（同一 task 可能同时匹配 ID 和 URL）。
- 使用独立 router 避免 routes.py 超过 500 行。

#### Integration Point

**Modified File:** `src/keeplink_mcp/api/app.py`

```python
from keeplink_mcp.api.batch_routes import batch_router
app.include_router(batch_router)
```

**Modified File:** `src/keeplink_mcp/db/repository.py`

新增：

```python
async def get_tasks_by_ids(self, task_ids: list[str]) -> list[ArchiveTask]:
    """Fetch tasks matching the given IDs. Returns only existing ones."""
    ...

async def get_latest_tasks_by_urls(self, urls: list[str]) -> list[ArchiveTask]:
    """For each URL, return the most recent task. Omits URLs with no tasks."""
    ...
```

---

### Component 6: Log Rotation

**Modified File:** `src/keeplink_mcp/logging_setup.py`

#### Interface Changes

```python
def setup_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    max_bytes: int = 10 * 1024 * 1024,   # 10 MB default
    backup_count: int = 5,
) -> logging.Logger:
    """Configure logging with RotatingFileHandler instead of FileHandler."""
    ...
```

#### Design Details

- 将 `logging.FileHandler` 替换为 `logging.handlers.RotatingFileHandler`。
- 传入 `maxBytes` 和 `backupCount` 参数。
- 签名扩展保持向后兼容（新参数有默认值）。

**Modified File:** `src/keeplink_mcp/config.py`

新增配置字段：

```python
# Log rotation — prevents unbounded disk usage
log_max_bytes: int = int(os.getenv("KEEPLINK_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
log_backup_count: int = int(os.getenv("KEEPLINK_LOG_BACKUP_COUNT", "5"))
```

---

## Batch 3: DevOps

### Component 7: Docker Containerization

**New File:** `Dockerfile`

```dockerfile
# Stage 1: Build dependencies
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime
FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY src/ src/
VOLUME ["/app/data"]
EXPOSE 19210
CMD ["keeplink"]
```

**New File:** `docker-compose.yml`

```yaml
services:
  keeplink:
    build: .
    ports:
      - "${KEEPLINK_API_PORT:-19210}:19210"
    volumes:
      - keeplink-data:/app/data
    environment:
      - KEEPLINK_DB_PATH=/app/data/task.db
      - KEEPLINK_LOG_FILE=/app/data/archiver.log
      - KEEPLINK_API_HOST=0.0.0.0
      # All other KEEPLINK_ vars can be added here
    restart: unless-stopped

volumes:
  keeplink-data:
```

---

### Component 8: Version Alignment

**Modified Files:**
- `pyproject.toml`: `version = "1.2.0"`
- `src/keeplink_mcp/__init__.py`: `__version__ = "1.2.0"`
- `src/keeplink_mcp/api/app.py`: `FastAPI(title="KeepLink MCP", version="1.2.0")`

---

### Component 9: README Update

**Modified File:** `README.md`

新增章节：
- Health Check Endpoint 文档
- Batch Status Query 文档
- Docker 使用说明（build + compose）
- 新环境变量文档（Rate Limiting + Log Rotation）
- 版本号更新至 1.2.0

---

## Data Models

### New Configuration Fields (Config dataclass)

```python
# Rate limiting (Requirement 1) — all read from env vars, no secrets
rate_limit_tokens: int          # KEEPLINK_RATE_LIMIT_TOKENS, default 15
rate_limit_interval_sec: float  # KEEPLINK_RATE_LIMIT_INTERVAL_SEC, default 60.0
rate_limit_wait_timeout_sec: float  # KEEPLINK_RATE_LIMIT_TIMEOUT, default 30.0

# Log rotation (Requirement 6)
log_max_bytes: int              # KEEPLINK_LOG_MAX_BYTES, default 10485760
log_backup_count: int           # KEEPLINK_LOG_BACKUP_COUNT, default 5
```

### No DB Schema Changes

现有 `archive_tasks` 表结构满足所有需求，无需 migration。

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Rate limit timeout | Task 保持 pending，Worker 跳过本轮处理，下次 poll 重试 |
| Health check DB failure | 返回 `readiness: false`，附带 error description |
| Batch query > 50 items | 返回 HTTP 422，含明确错误信息 |
| Graceful shutdown timeout (30s) | Cancel 超时 task，记录 WARNING |
| Stuck task recovery finds 0 tasks | 不记录日志（静默成功） |

---

## File Change Summary

### New Files
| File | Purpose |
|------|---------|
| `src/keeplink_mcp/worker/rate_limiter.py` | Token bucket implementation |
| `src/keeplink_mcp/worker/recovery.py` | Stuck task recovery logic |
| `src/keeplink_mcp/api/health.py` | Health check endpoint + router |
| `src/keeplink_mcp/api/batch_routes.py` | Batch status query endpoint + router |
| `Dockerfile` | Multi-stage container build |
| `docker-compose.yml` | Container orchestration |

### Modified Files
| File | Change |
|------|--------|
| `src/keeplink_mcp/config.py` | Add rate limit + log rotation config fields |
| `src/keeplink_mcp/worker/archiver.py` | Integrate rate limiter, enhance stop() with drain+timeout |
| `src/keeplink_mcp/main.py` | Add stuck task recovery at startup, pass timeout to stop() |
| `src/keeplink_mcp/logging_setup.py` | Replace FileHandler with RotatingFileHandler |
| `src/keeplink_mcp/api/app.py` | Include health + batch routers, update version |
| `src/keeplink_mcp/db/repository.py` | Add recover_processing_tasks, get_queue_stats, batch query methods |
| `src/keeplink_mcp/__init__.py` | Update __version__ to 1.2.0 |
| `pyproject.toml` | Update version to 1.2.0 |
| `README.md` | Document new features, Docker, env vars |

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Token bucket enforces rate ceiling

*For any* sequence of N acquire() calls within a single interval, the number of successful acquisitions SHALL never exceed max_tokens, regardless of call timing within that interval.

**Validates: Requirements 1.1**

### Property 2: Health stats reflect actual task distribution

*For any* set of tasks in the database with arbitrary status distributions, the health endpoint's queue_depth SHALL equal the count of pending tasks, success_count SHALL equal the count of success tasks within the 24h window, and failure_count SHALL equal the count of failed tasks within the 24h window.

**Validates: Requirements 2.4**

### Property 3: Stuck task recovery preserves non-processing tasks

*For any* set of tasks in the database, after recovery executes, all previously-processing tasks SHALL have status pending, and all tasks that were NOT in processing status SHALL remain unchanged.

**Validates: Requirements 4.2**

### Property 4: Batch query by task_ids returns correct subset

*For any* set of existing tasks and any subset of task IDs (including non-existent IDs), the batch endpoint SHALL return exactly those tasks whose IDs exist in the database, omitting non-existent IDs.

**Validates: Requirements 7.2, 7.6**

### Property 5: Batch query by URLs returns most recent task per URL

*For any* set of URLs where each URL has one or more tasks, the batch endpoint SHALL return exactly one task per URL, and that task SHALL have the most recent created_at timestamp among all tasks for that URL.

**Validates: Requirements 7.3**

### Property 6: Batch query rejects requests exceeding 50 identifiers

*For any* request containing more than 50 combined identifiers (task_ids + urls), the batch endpoint SHALL return HTTP 422 and no task data.

**Validates: Requirements 7.4, 7.5**

### Property 7: Version consistency across sources

*For all* releases, the version string in pyproject.toml, keeplink_mcp/__init__.py, and FastAPI app metadata SHALL be identical.

**Validates: Requirements 8.1, 8.2, 8.3**
