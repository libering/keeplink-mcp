# KeepLink-MCP

A local daemon that lets AI agents archive web pages to the Wayback Machine in the background, so they don't block on Internet Archive's slow/rate-limited API.

Works with any MCP-compatible client (Kiro, Cursor, Claude Desktop, etc).

**Version: 1.2.0**

## Why?

When AI agents do deep research, they read a lot of web pages. Those pages disappear all the time (link rot). Archiving them to the Internet Archive is the obvious fix — but IA's API is slow and rate-limited. If your agent calls it synchronously, it blocks for seconds or gets 429'd.

This tool solves that. The agent calls `archive_url`, gets a task ID back in milliseconds, and moves on. A background worker handles the actual archiving with proper retry logic.

## What it does

- Exposes `archive_url` and `get_archive_status` as MCP tools
- Queues requests in local SQLite (survives restarts, no Redis needed)
- Background worker retries with exponential backoff on 429/5xx
- Deduplicates — same URL within 24h won't be re-archived
- Binds to localhost only, no telemetry, nothing phones home

## Architecture

```
AI Client (Kiro/Cursor)
    │ stdio (MCP JSON-RPC)
    ▼
┌─────────────────────┐
│   MCP Server        │  ← Separate process
│   (archive_url,     │
│    get_archive_status)│
└────────┬────────────┘
         │ httpx (localhost:19210)
┌────────▼────────────────────────┐
│   FastAPI Service + Worker      │  ← Main process
│   ┌─────────┐ ┌──────────────┐ │
│   │  Routes  │ │ Background   │ │
│   │  /api/*  │ │ Worker       │ │
│   └────┬─────┘ └──────┬───────┘ │
│        │               │         │
│   ┌────▼───────────────▼───────┐ │
│   │    SQLite (WAL mode)       │ │
│   └────────────────────────────┘ │
└──────────────────────────────────┘
         │
         │ waybackpy
         ▼
   Internet Archive SPN2
```

Two processes: the MCP server talks stdio with your AI client, and forwards requests over HTTP to the FastAPI service. The FastAPI service manages the queue and runs the background worker.

## Getting started

You need Python 3.10+.

```bash
git clone https://github.com/keeplink/keeplink-mcp.git
cd keeplink-mcp
pip install -e ".[dev]"
```

Start the backend service:

```bash
python -m keeplink_mcp.main
```

Runs on `127.0.0.1:19210` by default.

Run the tests:

```bash
pytest test/ -v   # 84 tests, takes ~12s
```

## Docker

Run with Docker (v1.1.0+):

```bash
# Build the image
docker build -t keeplink-mcp .

# Run the container
docker run -d \
  --name keeplink \
  -p 19210:19210 \
  -v keeplink-data:/app/data \
  -e KEEPLINK_DB_PATH=/app/data/task.db \
  -e KEEPLINK_LOG_FILE=/app/data/archiver.log \
  keeplink-mcp
```

Or use docker-compose:

```bash
# Start the service
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the service
docker-compose down
```

The docker-compose.yml maps port 19210 and creates a persistent volume for the SQLite database.

## MCP tools

### `archive_url`

Queue a URL for archiving.

| Param | Type | Required | |
|-------|------|----------|-|
| url | string | yes | Must be http or https |

Returns: `{ "task_id": "...", "status": "pending", "url": "..." }`

### `get_archive_status`

Check on a task. Pass either `task_id` or `url` (at least one).

| Param | Type | Required | |
|-------|------|----------|-|
| task_id | string | no | The ID from archive_url |
| url | string | no | Looks up the most recent task for this URL |

Returns the task status, archive URL (if done), error info (if failed).

### `get_archive_status_batch`

Query status of multiple tasks in a single request (v1.1.0+).

| Param | Type | Required | |
|-------|------|----------|-|
| task_ids | string | no | Comma-separated task IDs (max 50 combined with urls) |
| urls | string | no | Comma-separated URLs (max 50 combined with task_ids) |

Returns: `{ "results": [...], "total_requested": N, "total_found": M }`

Each result contains the same fields as `get_archive_status`. Non-matching identifiers are silently omitted.

### `archive_and_cite`

Archive a cited web source **and** get back a paste-ready structured citation in one call. Non-blocking, same as `archive_url` — it returns immediately without waiting for archiving to finish.

| Param | Type | Required | |
|-------|------|----------|-|
| url | string | yes | Must be http or https |
| title | string | no | The page title you already read; used as the citation link text |

Returns a Citation object: `{ "title": ..., "original_url": ..., "archived_url": ..., "archived_at": ..., "task_id": ..., "formatted": ... }`.

The response comes in one of two shapes depending on archive state:

- **Cache_Hit** — if the URL was already archived within the 24h dedup window, you get a **complete** citation immediately: `archived_url` points at the permanent Wayback Machine snapshot, `archived_at` is set, and `formatted` is a ready-to-paste markdown reference like `[title](archived_url) (original: original_url, archived YYYY-MM-DD)`. When you don't supply a `title`, the `archived_url` is used as the link text instead.
- **Pending_Citation** — otherwise archiving is queued/in progress: `archived_url` and `archived_at` are `null`, and `formatted` states that archiving is in progress and includes the `original_url` and `task_id`. Call `get_archive_status(task_id)` later to obtain the permanent `archived_url` and complete the citation.

KeepLink stays fully deterministic and LLM-free when producing a citation: it does **not** fetch the page content (supply the `title` yourself) and does **not** invoke any large language model.

## HTTP API Endpoints

### Health Check

`GET /api/health` — Check service liveness, readiness, and operational stats (v1.1.0+).

**Response (200 when healthy, 503 when degraded):**

```json
{
  "liveness": true,
  "readiness": true,
  "readiness_error": null,
  "stats": {
    "queue_depth": 5,
    "success_count": 42,
    "failure_count": 3
  }
}
```

- `liveness`: Always `true` if the server responds
- `readiness`: `true` only when database is reachable and worker has completed at least one poll cycle
- `stats.queue_depth`: Number of pending tasks
- `stats.success_count`: Successfully archived tasks (last 24h)
- `stats.failure_count`: Failed tasks (last 24h)

### Batch Status Query

`GET /api/status/batch` — Query multiple tasks at once (v1.1.0+).

**Query Parameters:**

| Param | Description |
|-------|-------------|
| `task_ids` | Comma-separated task IDs |
| `urls` | Comma-separated URLs |

**Constraints:**
- At least one parameter required
- Combined total of identifiers ≤ 50
- Returns HTTP 422 if constraints violated

**Response:**

```json
{
  "results": [
    {
      "task_id": "abc123",
      "url": "https://example.com",
      "status": "success",
      "result_url": "https://web.archive.org/...",
      "error_message": null,
      "retry_count": 0,
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T10:35:00Z"
    }
  ],
  "total_requested": 3,
  "total_found": 1
}
```

## Hooking it up to your editor

You need both: the backend service running, AND the MCP server configured in your client.

**Kiro** — `.kiro/settings/mcp.json`:
```json
{
  "mcpServers": {
    "keeplink": {
      "command": "python",
      "args": ["-m", "keeplink_mcp.mcp_server.main"]
    }
  }
}
```

**Cursor** — `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "keeplink": {
      "command": "python",
      "args": ["-m", "keeplink_mcp.mcp_server.main"]
    }
  }
}
```

**Claude Desktop** — `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "keeplink": {
      "command": "python",
      "args": ["-m", "keeplink_mcp.mcp_server.main"]
    }
  }
}
```

Don't forget to start the backend first: `python -m keeplink_mcp.main`

## Configuration

Everything's controlled via env vars (prefix `KEEPLINK_`):

### Core Settings

| Variable | Default | What it does |
|----------|---------|--------------|
| `KEEPLINK_API_HOST` | `127.0.0.1` | Bind address |
| `KEEPLINK_API_PORT` | `19210` | Port |
| `KEEPLINK_DB_PATH` | `./data/task.db` | Where the SQLite file lives |
| `KEEPLINK_MAX_RETRIES` | `5` | How many times to retry a failed archive |
| `KEEPLINK_BASE_BACKOFF` | `300.0` | Base retry delay in seconds (doubles each time, 5min aligns with IA cooldown) |
| `KEEPLINK_WORKER_CONCURRENCY` | `1` | How many tasks to process per poll cycle |
| `KEEPLINK_POLL_INTERVAL` | `5.0` | Seconds between queue polls |
| `KEEPLINK_IA_ACCESS_KEY` | — | Your IA S3 key (optional, for higher rate limits) |
| `KEEPLINK_IA_SECRET_KEY` | — | Your IA S3 secret |
| `KEEPLINK_LOG_LEVEL` | `INFO` | Log verbosity |
| `KEEPLINK_LOG_FILE` | `./data/archiver.log` | Log file location |

### Rate Limiting (v1.1.0+)

| Variable | Default | What it does |
|----------|---------|--------------|
| `KEEPLINK_RATE_LIMIT_TOKENS` | `7` | Max tokens in bucket (burst capacity). Set to `0` to disable rate limiting. |
| `KEEPLINK_RATE_LIMIT_INTERVAL_SEC` | `60.0` | Seconds between token refills |
| `KEEPLINK_RATE_LIMIT_TIMEOUT` | `30.0` | Max seconds to wait for a token before retrying later |

### Log Rotation (v1.1.0+)

| Variable | Default | What it does |
|----------|---------|--------------|
| `KEEPLINK_LOG_MAX_BYTES` | `10485760` | Max log file size before rotation (10 MB default) |
| `KEEPLINK_LOG_BACKUP_COUNT` | `5` | Number of rotated log files to keep |

## How it works under the hood

1. Agent calls `archive_url` → MCP server validates the URL → POSTs to FastAPI
2. FastAPI checks if this URL was already submitted in the last 24h (dedup) → writes to SQLite → returns task ID
3. Worker picks up pending tasks every 5s → calls Internet Archive via waybackpy
4. Success? Stores the archive URL. Got 429/5xx? Backs off and retries. Got 403? Gives up.

## License

MIT. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
