# KeepLink-MCP 項目交接手冊

> 最後更新：2026-07-18 | 版本：v1.0.0

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
├── logging_setup.py         # 結構化日誌：stdout + file 雙輸出
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
│   └── schemas.py           # Pydantic request/response 模型
│
├── worker/                  # ⚙️ 背景處理層
│   ├── archiver.py          # BackgroundWorker — 輪詢 + 處理 + 重試
│   └── error_classifier.py  # 錯誤分類邏輯 (retryable vs non-retryable)
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
    → worker/archiver.py → waybackpy → Internet Archive
    → db/repository.py → mark_success / schedule_retry / mark_failed
```

---

## 5. 運維手冊

### 啟動服務

```bash
# 主服務（FastAPI + Worker）
python -m KeepLink_mcp.main

# MCP Server（由 AI Client 自動管理，通常不需手動啟動）
python -m KeepLink_mcp.mcp_server.main
```

### 停止服務

- `Ctrl+C` 或 `kill -SIGTERM <pid>`
- Worker 會等待當前任務完成後優雅退出

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
| 任務卡在 processing | 進程異常退出 | 重啟後 Worker 會重新處理（需手動將 stuck 任務改回 pending） |

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
# 全部測試（84 個）
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
- 任務卡在 processing 需手動修復
- 無批量匯入功能
- Windows 上端口綁定可能需要手動配置

### v2.0 規劃

- [ ] **多後端引擎**：Archive.today、ArchiveBox、Hoarder Web 等
- [ ] **Web UI 儀表板**：任務列表、成功率統計、手動重試按鈕
- [ ] **`/status` 批量查詢**：一次查多個 task 狀態
- [ ] **WARC 爬取模式**：整站存檔
- [ ] **Docker compose**：一鍵部署
- [ ] **任務優先級**：高優先任務插隊處理
- [ ] **Webhook 通知**：任務完成時回調

---

## 8. CI/CD 流程

```
Push/PR to main
    → GitHub Actions (ci.yml)
    → Ruff lint → Pytest (84 tests) → pip-audit
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
