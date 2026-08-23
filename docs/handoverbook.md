# KeepLink-MCP 項目交接手冊

> 最後更新：2026-08-21 | 版本：v1.1.0

---

## 1. 項目概覽

### 一句話定位

KeepLink-MCP 是一個本地非同步中介軟體，讓 AI Agent 能在不阻塞推理迴圈的情況下將網頁存檔至 Internet Archive。

### 核心問題

AI 代理在深度研究過程中需要保全網頁證據，但 Internet Archive 的 Save Page Now (SPN2) API 有嚴格的速率限制。直接同步呼叫會阻塞推理、觸發 429 錯誤、甚至導致 Agent 崩潰。

### 解決方案

透過 MCP 協議暴露 `archive_url` 工具，AI 呼叫後 <50ms 返回（僅寫入本地 SQLite 佇列），背景 Worker 以指數退避策略非同步處理存檔請求。

---

## 2. 架構決策紀錄 (ADR)

### ADR-001：為什麼用 SQLite 而非 Redis

**決策**：使用 SQLite + WAL 模式作為任務佇列。

**原因**：
- 零外部依賴，使用者無需安裝任何基礎設施
- 任務持久化 — 進程重啟後佇列不丟失
- WAL 模式下讀寫並行效能足夠（單機場景，QPS < 100）
- 符合「一行指令啟動」的易用性目標

**取捨**：不適合高並發分散式場景，但這不是 v1.0 的目標。

### ADR-002：為什麼採用雙進程架構

**決策**：MCP Server 和 FastAPI Service 運行在獨立進程中。

**原因**：
- MCP Server 需要透過 stdio 與 AI Client 通訊（阻塞式 pipe）
- FastAPI Service 需要獨立的 async event loop 管理 HTTP + Worker
- 解耦後 MCP Server 是無狀態的薄代理，FastAPI 負責所有業務邏輯
- 兩者可以獨立重啟

**通訊方式**：httpx async client → localhost:19210

### ADR-003：為什麼選擇指數退避而非固定間隔

**決策**：使用 `base_backoff * 2^retry_count` 公式。

**原因**：
- Internet Archive API 的 429 回應明確要求「慢下來」
- 指數退避符合 HTTP 標準做法（RFC 7231）
- 隨著重試次數增加，間隔拉長，避免持續撞到 rate limit
- 最大 5 次重試（16 分鐘上限），不會無限等待

### ADR-004：為什麼 24 小時去重窗口

**決策**：同一 URL 在 24 小時內不重複存檔。

**原因**：
- AI Agent 可能在短時間內對同一頁面呼叫多次 archive_url
- 重複存檔浪費 IA API 配額
- 24h 足夠涵蓋一次深度研究 session
- failed 狀態的任務不阻擋重試（允許手動重新提交）

### ADR-005：錯誤分類策略

**決策**：429/5xx/network error 可重試；401/403 不可重試。

**原因**：
- 429：明確的速率限制，退避後通常能成功
- 5xx：伺服器暫時故障，重試合理
- 網路錯誤：瞬態問題，重試合理
- 401/403：認證/權限問題，重試不會改善結果，直接失敗避免浪費
- 未知錯誤：fail-fast，不遮蓋問題

### ADR-006：Token Bucket Rate Limiter

**決策**：採用 Token Bucket 演算法作為 Worker 層的速率限制器。

**原因**：
- Internet Archive SPN2 API 預設限制約 15 req/min
- Token Bucket 允許短時 burst（桶滿時可一次送出多個請求），同時維持長期平均速率
- 比 Fixed Window 更平滑，比 Leaky Bucket 更靈活
- 可透過環境變數動態調整 tokens / interval，方便未來對接不同 quota

**取捨**：單機場景下無需分散式 rate limiter，記憶體內實作即可。

### ADR-007：Health Check 端點設計

**決策**：提供 `GET /api/health` 端點，回傳服務健康狀態。

**原因**：
- Docker 容器化後需要 healthcheck 機制判斷服務是否就緒
- 回傳 Worker 狀態、DB 連線狀態、佇列深度等關鍵指標
- 符合 12-Factor App 的可觀察性原則
- 為未來 Kubernetes 部署的 liveness/readiness probe 做準備

**取捨**：v1.1.0 僅檢查基本可達性與 DB 連線，不做深度依賴檢查。

---

## 3. 術語表 (Ubiquitous Language)

| 術語 | 定義 |
|------|------|
| **MCP** | Model Context Protocol — AI 模型與外部工具的標準通訊協議 |
| **SPN2** | Save Page Now v2 — Internet Archive 的網頁存檔 API |
| **WAL** | Write-Ahead Logging — SQLite 的並行讀寫模式 |
| **ArchiveTask** | 系統中的核心資料模型，代表一個 URL 存檔作業 |
| **TaskStatus** | 任務生命週期狀態：pending → processing → success/failed |
| **Deduplication Window** | 24 小時去重窗口，防止重複存檔 |
| **next_retry_at** | 任務的下次可重試時間點，Worker 在此之前會跳過該任務 |
| **Exponential Backoff** | 指數退避 — 每次重試間隔翻倍 (60s, 120s, 240s, 480s, 960s) |
| **Error Classifier** | 將 API 錯誤分類為 retryable 或 non-retryable 的模組 |
| **Background Worker** | 輪詢 pending 任務並實際呼叫 IA API 的背景處理器 |

---

## 4. 模組地圖

```
src/KeepLink_mcp/
├── __init__.py              # 版本號定義
├── config.py                # 組態管理：環境變數讀取 + 預設值
├── logging_setup.py         # 結構化日誌：stdout + file 雙輸出 + Log Rotation
├── main.py                  # 主入口：啟動 FastAPI + Worker (uvicorn)
│
├── db/                      # 🗄️ 持久層
│   ├── models.py            # ArchiveTask ORM 定義 (SQLAlchemy 2.0)
│   ├── session.py           # Engine 建立 + WAL mode + session factory
│   └── repository.py        # TaskRepository — 所有 DB 操作封裝
│
├── api/                     # 🌐 HTTP 服務層 (FastAPI)
│   ├── app.py               # App factory — 組裝 FastAPI 實例
│   ├── routes.py            # 路由：POST /archive, GET /status
│   ├── schemas.py           # Pydantic request/response 模型
│   ├── health.py            # Health check 端點 (GET /api/health)
│   └── batch_routes.py      # 批量狀態查詢端點 (GET /api/status/batch)
│
├── worker/                  # ⚙️ 背景處理層
│   ├── archiver.py          # BackgroundWorker — 輪詢 + 處理 + 重試
│   ├── error_classifier.py  # 錯誤分類邏輯 (retryable vs non-retryable)
│   ├── rate_limiter.py      # Token Bucket 速率限制器
│   └── recovery.py          # 啟動時卡住任務自動恢復 (Stuck Task Recovery)
│
└── mcp_server/              # 🔌 MCP 協議層
    ├── main.py              # MCP 進程入口 (stdio transport)
    ├── server.py            # MCP tools 定義 (archive_url, get_archive_status)
    └── url_validator.py     # URL 驗證 + 正規化
```

### 資料流

```
AI Agent → [MCP stdio] → mcp_server/server.py
    → [httpx POST] → api/routes.py
    → [SQLAlchemy] → db/repository.py → SQLite

Background Worker (polling loop):
    db/repository.py → fetch_pending_tasks
    → worker/rate_limiter.py → acquire token
    → worker/archiver.py → waybackpy → Internet Archive
    → db/repository.py → mark_success / schedule_retry / mark_failed

Startup Recovery:
    worker/recovery.py → 將 stuck (processing) 任務重設為 pending
```

---

## 5. 運維手冊

### 啟動服務

```bash
# 主服務（FastAPI + Worker）
python -m KeepLink_mcp.main

# MCP Server（由 AI Client 自動管理，通常不需手動啟動）
python -m KeepLink_mcp.mcp_server.main

# Docker Compose 一鍵啟動
docker compose up -d
```

### 停止服務

- `Ctrl+C` 或 `kill -SIGTERM <pid>`
- Worker 會等待當前任務完成後優雅退出（30 秒超時）

### 環境變數配置

| 變數 | 預設值 | 說明 |
|------|--------|------|
| KeepLink_API_HOST | 127.0.0.1 | 綁定地址 |
| KeepLink_API_PORT | 19210 | 綁定端口 |
| KeepLink_DB_PATH | ./data/task.db | SQLite 路徑 |
| KeepLink_MAX_RETRIES | 5 | 最大重試次數 |
| KeepLink_BASE_BACKOFF | 60.0 | 基礎退避秒數 |
| KeepLink_WORKER_CONCURRENCY | 1 | 每輪處理任務數 |
| KeepLink_POLL_INTERVAL | 5.0 | 輪詢間隔秒數 |
| KeepLink_IA_ACCESS_KEY | — | IA S3 Access Key |
| KeepLink_IA_SECRET_KEY | — | IA S3 Secret Key |
| KeepLink_LOG_LEVEL | INFO | 日誌等級 |
| KeepLink_LOG_FILE | ./data/archiver.log | 日誌文件路徑 |
| KEEPLINK_RATE_LIMIT_TOKENS | 15 | Token Bucket 容量（每週期可發送請求數） |
| KEEPLINK_RATE_LIMIT_INTERVAL_SEC | 60.0 | Token Bucket 補充週期（秒） |
| KEEPLINK_RATE_LIMIT_TIMEOUT | 30.0 | 等待 token 的最大超時秒數 |
| KEEPLINK_LOG_MAX_BYTES | 10485760 | 單一日誌檔案最大大小（10MB） |
| KEEPLINK_LOG_BACKUP_COUNT | 5 | 日誌輪替保留檔案數 |

### 日誌查看

```bash
# 即時查看
tail -f data/archiver.log

# Windows PowerShell
Get-Content data/archiver.log -Wait
```

### 資料庫操作

```bash
# 查看所有任務
python -c "
import sqlite3
conn = sqlite3.connect('data/task.db')
for row in conn.execute('SELECT task_id, url, status, retry_count FROM archive_tasks'):
    print(row)
"

# 查看失敗任務
python -c "
import sqlite3
conn = sqlite3.connect('data/task.db')
for row in conn.execute(\"SELECT task_id, url, error_message FROM archive_tasks WHERE status='failed'\"):
    print(row)
"
```

### 常見問題

| 問題 | 原因 | 解法 |
|------|------|------|
| `winerror 10013` | Windows 端口權限不足 | 換用更高端口 (19210+) |
| `winerror 10048` | 端口被佔用 | `netstat -ano \| findstr :19210` 找到並殺進程 |
| Worker 持續 "Archive failed" | IA API 拒絕 / 網路問題 | 檢查 IA_ACCESS_KEY 是否設定；確認能訪問 web.archive.org |
| MCP Server EOF error | 直接在終端跑 MCP Server | MCP Server 需由 AI Client (Kiro/Cursor) 管理，不能裸跑 |
| 任務卡在 processing | 進程異常退出後殘留 | v1.1.0 起已自動恢復：啟動時 `recovery.py` 會將 stuck 任務重設為 pending，無需手動介入 |

---

## 6. 開發指南

### 環境搭建

```bash
git clone <repo>
cd KeepLink-MCP
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e ".[dev]"
```

### 跑測試

```bash
# 全部測試（80+ 個）
pytest test/ -v

# 只跑特定模組
pytest test/test_worker.py -v

# 只跑 property tests
pytest test/ -v -k "property"
```

### Lint

```bash
ruff check src/ test/
ruff check src/ test/ --fix  # 自動修復
```

### 新增功能的步驟

1. 先更新 `requirements.md` 加入新需求（EARS 格式）
2. 更新 `design.md` 加入介面定義
3. 實作程式碼（遵循模組邊界）
4. 寫測試（unit + property test）
5. 確認 `pytest` + `ruff check` 通過
6. 提交 PR

### 測試架構

- **conftest.py** — in-memory SQLite fixtures
- **hypothesis** — property-based testing (每個 property ≥100 次迭代)
- **respx** — mock httpx 呼叫 (MCP → FastAPI)
- **unittest.mock** — mock waybackpy (Worker 測試)

---

## 7. 已知限制與未來規劃

### v1.0 已知限制

- 僅支援 Internet Archive 單一後端
- 無 Web UI（僅 CLI + 日誌）
- 無批量匯入功能
- Windows 上端口綁定可能需要手動配置

### v1.1.0 已完成

- ✅ `/status` 批量查詢
- ✅ Docker compose
- ✅ 任務卡住自動恢復（Stuck Task Recovery）

### v2.0+ 對接 Archive Team 規劃

- [ ] 與 Archive Team 正式 API key 對接
- [ ] 支援 SPN2 API 的 outlinks capture 參數
- [ ] 支援 availability check 端點（檢查 URL 是否已存在快照）
- [ ] 多後端引擎（Archive.today, ArchiveBox, Hoarder Web）
- [ ] Webhook 通知（任務完成時回調）
- [ ] Web UI 儀表板
- [ ] WARC 爬取模式

---

## 8. CI/CD 流程

```
Push/PR to main
    → GitHub Actions (ci.yml)
    → Ruff lint → Pytest (80+ tests) → pip-audit
    → ✅ 合併

GitHub Release (tag v1.x.x)
    → GitHub Actions (publish.yml)
    → python -m build → PyPI publish (Trusted Publisher)
```

### Dependabot

- 每週掃描 pip 依賴 + GitHub Actions 版本
- 自動開 PR 更新有漏洞的套件

---

## 9. 安全性摘要

| 面向 | 措施 |
|------|------|
| Secrets | 環境變數，從不硬編碼 |
| 網路暴露 | 預設綁定 127.0.0.1 |
| SQL Injection | SQLAlchemy ORM，無原生 SQL 拼接 |
| Input Validation | URL scheme + hostname 驗證 |
| 錯誤暴露 | 不返回 stack trace |
| 遙測 | 無外部 telemetry |
| 依賴安全 | pip-audit CI + Dependabot |

---

## 10. v1.1.0 Release Notes

### 新增功能

| 功能 | 說明 |
|------|------|
| **Rate Limiter** | Token Bucket 演算法，預設 15 req/min，防止超出 IA API 限制 |
| **Stuck Task Recovery** | 啟動時自動將卡在 processing 的任務重設為 pending |
| **Graceful Shutdown** | 收到 SIGTERM 後等待當前任務完成（30 秒超時） |
| **Health Check Endpoint** | `GET /api/health` — 回傳服務狀態、Worker 狀態、DB 連線、佇列深度 |
| **Batch Status Query** | `GET /api/status/batch` — 一次查詢最多 50 個任務狀態 |
| **Log Rotation** | RotatingFileHandler，單檔 10MB × 保留 5 份 |
| **Docker Containerization** | Multi-stage build + docker compose 一鍵部署 |
| **Version** | 版本號升級至 1.1.0 |

---

## 11. Archive Team 對接準備

### 目前狀態

本地測試通過，MCP 工具（`archive_url`、`get_archive_status`）可正常使用。Worker 層的 Token Bucket Rate Limiter 已就位，可控制對 IA API 的請求速率。

### 下一步

需要取得正式 Internet Archive API credentials（S3-like access key + secret key），設定至環境變數後即可對接正式環境。

### 對接要點

- Rate Limiter 已預設 15 req/min（符合 IA 公開 API 限制），可依實際取得的 quota 透過 `KEEPLINK_RATE_LIMIT_TOKENS` 及 `KEEPLINK_RATE_LIMIT_INTERVAL_SEC` 調整
- 指數退避策略已處理 429 回應，對接後無需額外邏輯
- 錯誤分類器已區分 retryable / non-retryable，401/403 直接失敗不重試

### 待確認事項

| 項目 | 說明 |
|------|------|
| IA Rate Limit Quota | 正式 API key 的實際 rate limit 額度（可能高於公開限制） |
| Outlinks Capture | 是否需要啟用 SPN2 的 `capture_outlinks` 參數（影響存檔範圍） |
| Priority Queue | 是否需要任務優先級機制（部分 URL 需優先存檔） |
| Availability Check | 是否在存檔前先查詢 IA 是否已有近期快照（避免重複存檔） |
