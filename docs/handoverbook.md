# KeepLink-MCP 項目交接手冊

> 最後更新：2026-08-21 | 版本：v1.2.0（含 archive_and_cite）

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

**取捨**：v1.1.1 僅檢查基本可達性與 DB 連線，不做深度依賴檢查。

### ADR-008：否決的擴張方向（Rejected Directions）

**決策**：明確否決以下三個擴張方向，KeepLink 維持「AI 研究時的網頁存檔中介軟體」的核心定位，不擴張為通用存檔平台或整站爬取工具。

**背景**：在 v1.1.1 發佈後，曾探索三個擴張方向並各自建立過 spec。經評估後全部否決並刪除對應 spec 目錄。此 ADR 記錄否決理由，避免未來重蹈覆轍。

**否決方向一：通用存檔服務（`keeplink-v2-universal-archiver`）**
- 構想：擴張為「本地優先的通用網頁存檔服務」，MCP 僅是眾多 ingestion 介面之一（另含 CLI、Browser Extension、RSS/Sitemap monitor、批量匯入），並支援多後端（Internet Archive、Archive.today、ArchiveBox）。
- 否決理由：與 **ArchiveBox** 正面衝突。ArchiveBox 已完整實作多格式、多後端、Web UI、CLI、Browser Extension、API，且社群成熟。KeepLink 橫向擴張只會進入紅海，喪失差異化。

**否決方向二：整站存檔編排（`site-archive-orchestration`）**
- 構想：新增 `discover_urls`（爬 sitemap/RSS 找 URL）、`archive_batch`、`get_batch_progress` 三個 MCP 工具，讓外部 AI 能編排「存檔整個網站」。
- 否決理由：
  1. **與 ArchiveTeam / grab-site / Browsertrix / AutoWebArchiver 重疊** — 整站爬取與 sitemap/RSS 發現已有成熟方案。
  2. **偽需求** — 想不出高頻的真實用戶會對 AI 說「幫我存整個網站」。相較之下「AI 研究時保全引用來源」有明確場景。
  3. `discover_urls` 的發現能力，外部 AI client（Claude/Kiro）本身用 web fetch 就能做，不需 KeepLink 承擔。

**否決方向三：內建 LLM 的存檔 agent**
- 構想：讓 KeepLink 自己用 LLM 判斷哪些頁面值得存、自動決策存檔策略。
- 否決理由：存檔是確定性任務（驗證 → 排隊 → SPN2 → 重試），無模糊決策空間。塞入 LLM 帶來成本、延遲、不確定性，且違背「本地優先、零外部依賴、LLM-free」的核心哲學。這是反模式。

**確認的正確方向**：回到核心定位 X — 深耕「AI-native 的存檔體驗」。KeepLink 保持零 LLM 依賴，僅提供確定性工具；智慧（理解意圖、過濾）留在外部 AI client。下一步聚焦 `archive_and_cite`（存檔 + 回傳結構化 citation），解決「AI 產出的引用連結會失效」這個無人佔據的痛點。

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
    ├── server.py            # MCP tools 定義 (archive_url, get_archive_status, archive_and_cite)
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
| KeepLink_BASE_BACKOFF | 300.0 | 基礎退避秒數 |
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
| 任務卡在 processing | 進程異常退出後殘留 | v1.1.1 起已自動恢復：啟動時 `recovery.py` 會將 stuck 任務重設為 pending，無需手動介入 |

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

### v1.1.1 已完成

- ✅ `/status` 批量查詢
- ✅ Docker compose
- ✅ 任務卡住自動恢復（Stuck Task Recovery）

### v2.0+ 多來源存檔與驗證規劃（分兩階段）

> 定位延續 ADR-008：收斂在服務 `archive_and_cite` 的「引用防失效」核心場景，維持 LLM-free、本地優先，不擴張為通用存檔器（避免與 ArchiveBox 正面競爭）。目標是「多來源存檔（能力 A）+ 多來源可用性驗證（能力 B）」。

**已完成的技術可行性查證結論（決定分兩階段的依據）：**

| 來源 | 用途 | 可行性 | 認證 | 主要風險 |
|------|------|--------|------|----------|
| IA Wayback Availability API (`/wayback/available?url=`) | 能力 B 查詢 | ✅ 簡單，純 GET | 無 | 幾乎無 |
| Common Crawl CDX Index (`index.commoncrawl.org`) | 能力 B 查詢 | ✅ 可行 | 無 | 覆蓋率不保證（爬到才有）、每月獨立索引需逐月查、延遲較高 |
| Archive.today (`/submit/` → 輪詢 `wip/<id>`) | 能力 A 存檔 fallback | ⚠️ 可行但脆弱 | 無（非官方） | Cloudflare/反爬狀態反覆（2022 修→2023 壞→2024/08 恢復）、無官方 API 隨時可能改版、Python 無活躍套件需自寫 |
| ~~Amber (amberlink.org)~~ | — | ❌ 剔除 | — | 去中心化 self-hosted 工具，快照散落各網站主機，無中心化端點可送存/可查詢，套不進 KeepLink 的「送 URL→拿 permanent URL」模型 |

**第一階段（高 ROI、低風險，優先）— 能力 B：多來源可用性驗證**

- [ ] 新增 availability 查詢抽象層（唯讀，不落 `ArchiveTask` 主表，保持 model 乾淨）
- [ ] IA Availability API 查詢來源
- [ ] Common Crawl CDX 查詢來源（覆蓋率不保證，僅作加分）
- [ ] `CitationResponse` 擴充 `alternate_snapshots`（nullable list），cache-hit 時填入其他來源存底
- 特性：無認證、不碰 Archive.today 的反爬坑、直接強化 `archive_and_cite` 引用防失效價值；與第二階段解耦，即使第二階段放棄也不受影響

**第二階段（需接受外部脆弱性）— 能力 A：多後端存檔**

- [ ] 抽出 `ArchiveBackend` Protocol（interface first），worker 只管排程/重試/rate limit，不管打哪個 API
- [ ] 將現有 IA/waybackpy 邏輯搬進 `worker/backends/internet_archive.py`（僅搬家，不改寫）
- [ ] 新增 `worker/backends/archive_today.py`（自寫 submit + 輪詢 wip；**best-effort，失敗不強 retry**）
- [ ] `ArchiveTask` 新增 `backend` 欄位（`String(32)`，`default="internet_archive"`，有 default → 免寫 migration、舊資料相容）
- 心理準備：Archive.today parser 會週期性壞掉、需維護；此後端可設為可選

**其他長期候選（未排期）**

- [ ] Webhook 通知（任務完成時回調）
- [ ] Web UI 儀表板
- [ ] SPN2 outlinks capture 參數
- [ ] 與 Internet Archive 正式 API key 對接（提高現有 IA 後端 quota/穩定性；註：這是強化現有後端，非新增後端）

> 已否決：WARC 整站爬取模式、通用存檔平台、內建 LLM 決策（詳見 ADR-008）。

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

## 10. v1.1.1 Release Notes

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

---

## 12. `archive_and_cite` 工具（存檔 + 結構化引用）

### 定位

`archive_and_cite` 是 KeepLink 的新增 MCP 工具，實踐核心定位 X —「AI 研究時的網頁存檔中介軟體」。AI agent 在研究過程中引用網頁來源時，這些連結會發生 link rot（失效或內容變動）。此工具讓 AI 在**一次呼叫**中同時完成兩件事：把來源 URL 排入既有存檔佇列，並立即取回一個指向 permanent Wayback Machine URL 的結構化 Citation（含可直接貼上的 `formatted` 字串）。

工具維持與 `archive_url` 一致的 non-blocking 行為（<50ms 回傳，不等待存檔完成），並保持 KeepLink 100% deterministic、LLM-free 的哲學：**產生 Citation 時不抓取頁面內容、不呼叫任何 LLM**。因此 `title` 一律由呼叫端提供（AI 已在閱讀頁面時讀到標題），KeepLink 不代為擷取。

本工具完全複用既有存檔管線（URL validation/normalization、24h dedup、background Worker、Token Bucket rate limiter、exponential backoff retry），**無資料庫 schema 變更**。

### 參數（inputSchema）

| 參數 | 型別 | 必填 | 說明 |
|------|------|------|------|
| `url` | string | ✅ 必填 | 要存檔並引用的來源 URL（http 或 https），會先經 URL_Validator 驗證與正規化 |
| `title` | string | 選填 | 呼叫端已讀到的頁面標題，作為引用的連結文字。**純空白字串（含 `\t`、全形空白）視同未提供**，`title` 欄位會設為 null |

### Citation 回應欄位

回應為結構化 Citation JSON，欄位如下：

| 欄位 | 型別 | Nullable | 說明 |
|------|------|----------|------|
| `title` | string | ✅ 可為 null | 正規化後的標題；未提供或純空白時為 null |
| `original_url` | string | ❌ 恆非空 | 正規化後的原始來源 URL |
| `archived_url` | string | ✅ 可為 null | permanent Wayback Machine URL（即 ArchiveTask 的 `result_url`）；存檔尚未完成時為 null |
| `archived_at` | timestamp | ✅ 可為 null | 存檔達成 success 的時間戳（success 任務的 `updated_at`）；尚未完成時為 null |
| `task_id` | string | ❌ 恆非空 | 追蹤 ID；Pending 時供 `get_archive_status` 補完使用 |
| `formatted` | string | ❌ 恆非空 | 可直接貼上的 markdown/純文字引用字串 |

> **不變式**：`original_url`、`task_id`、`formatted` 恆為非空。`archived_url` 與 `archived_at` **同時**為 null（Pending）或**同時**非 null（Complete），不會只設其一。

### 兩種回應形態

工具依 24h dedup window 內的既有任務狀態，回傳兩種形態之一：

- **Cache_Hit（COMPLETE）**：24h 內該 URL 已成功存檔（`status == success` 且具 `result_url`）。立即回傳完整 Citation：
  - `archived_url` = 既有任務的 `result_url`
  - `archived_at` = 既有任務的 `updated_at`
  - `formatted`（含 title）：`[title](archived_url) (original: original_url, archived YYYY-MM-DD)`
  - `formatted`（無 title）：以 `archived_url` 作為連結文字取代 title，仍含 original URL 與 `YYYY-MM-DD` 日期

- **Pending_Citation**：24h 內無成功任務（新建任務，或既有任務仍在 pending/processing）。回傳進行中的 Citation：
  - `archived_url` = null、`archived_at` = null
  - `formatted` 說明存檔進行中，並包含 `original_url` 與 `task_id`
  - 工具結果會附上引導文字，指示呼叫端稍後以 `task_id` 呼叫 `get_archive_status` 補完

> 若既有任務為 `failed`，`find_recent_task` 回傳 None，工具會建立新任務重新進入管線（與 `archive_url` 行為對齊）。

### Async citation-completion 流程

存檔本身仍由背景 Worker 非同步完成，`archive_and_cite` 不阻塞等待。因此 Pending_Citation 需以「延後補完」方式取得 permanent URL：

```
1. AI 呼叫 archive_and_cite(url, title?)
       └─ 24h 內無成功存檔 → 回傳 Pending_Citation
          （archived_url = null、archived_at = null、附 task_id）

2. 背景 Worker 非同步處理存檔
       └─ SPN2 成功 → mark_success 寫入 result_url 與 updated_at

3. AI 稍後以 task_id 呼叫 get_archive_status(task_id)
       └─ status == success → 取得 permanent archived_url，補完 Citation
```

換言之：**Pending_Citation 是暫態**，`get_archive_status(task_id)` 是把它升級為完整引用的補完管道。AI 只需保留 `task_id`，待存檔完成後再查一次即可拿到永久連結。

### 相關檔案

| 檔案 | 用途 |
|------|------|
| `src/keeplink_mcp/mcp_server/server.py` | `archive_and_cite` Tool 註冊與 `_handle_archive_and_cite` handler |
| `src/keeplink_mcp/api/routes.py` | `POST /api/cite`（`create_citation`）端點 |
| `src/keeplink_mcp/api/schemas.py` | `CiteRequest` / `CitationResponse` Pydantic 模型 |
| `src/keeplink_mcp/citation/builder.py` | `Citation` dataclass + `build_citation` 純函式（零 I/O、deterministic） |
