# OmniArchive-MCP

A local daemon that lets AI agents archive web pages to the Wayback Machine in the background, so they don't block on Internet Archive's slow/rate-limited API.

Works with any MCP-compatible client (Kiro, Cursor, Claude Desktop, etc).

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
git clone https://github.com/your-org/omniarchive-mcp.git
cd omniarchive-mcp
pip install -e ".[dev]"
```

Start the backend service:

```bash
python -m omniarchive_mcp.main
```

Runs on `127.0.0.1:19210` by default.

Run the tests:

```bash
pytest test/ -v   # 84 tests, takes ~12s
```

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

## Hooking it up to your editor

You need both: the backend service running, AND the MCP server configured in your client.

**Kiro** — `.kiro/settings/mcp.json`:
```json
{
  "mcpServers": {
    "omniarchive": {
      "command": "python",
      "args": ["-m", "omniarchive_mcp.mcp_server.main"]
    }
  }
}
```

**Cursor** — `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "omniarchive": {
      "command": "python",
      "args": ["-m", "omniarchive_mcp.mcp_server.main"]
    }
  }
}
```

**Claude Desktop** — `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "omniarchive": {
      "command": "python",
      "args": ["-m", "omniarchive_mcp.mcp_server.main"]
    }
  }
}
```

Don't forget to start the backend first: `python -m omniarchive_mcp.main`

## Configuration

Everything's controlled via env vars (prefix `OMNIARCHIVE_`):

| Variable | Default | What it does |
|----------|---------|--------------|
| `OMNIARCHIVE_API_HOST` | `127.0.0.1` | Bind address |
| `OMNIARCHIVE_API_PORT` | `19210` | Port |
| `OMNIARCHIVE_DB_PATH` | `./data/task.db` | Where the SQLite file lives |
| `OMNIARCHIVE_MAX_RETRIES` | `5` | How many times to retry a failed archive |
| `OMNIARCHIVE_BASE_BACKOFF` | `60.0` | Base retry delay in seconds (doubles each time) |
| `OMNIARCHIVE_WORKER_CONCURRENCY` | `1` | How many tasks to process per poll cycle |
| `OMNIARCHIVE_POLL_INTERVAL` | `5.0` | Seconds between queue polls |
| `OMNIARCHIVE_IA_ACCESS_KEY` | — | Your IA S3 key (optional, for higher rate limits) |
| `OMNIARCHIVE_IA_SECRET_KEY` | — | Your IA S3 secret |
| `OMNIARCHIVE_LOG_LEVEL` | `INFO` | Log verbosity |
| `OMNIARCHIVE_LOG_FILE` | `./data/archiver.log` | Log file location |

## How it works under the hood

1. Agent calls `archive_url` → MCP server validates the URL → POSTs to FastAPI
2. FastAPI checks if this URL was already submitted in the last 24h (dedup) → writes to SQLite → returns task ID
3. Worker picks up pending tasks every 5s → calls Internet Archive via waybackpy
4. Success? Stores the archive URL. Got 429/5xx? Backs off and retries. Got 403? Gives up.

## License

MIT. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
