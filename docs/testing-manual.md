# KeepLink-MCP v1.1 測試手冊

> 版本：v1.1.0 | 最後更新：2026-08-18

---

## 1. 環境準備

### 前置條件

```powershell
# 確認 Python 版本 (≥3.10)
python --version

# 進入專案目錄
cd "D:\vibe x\coding\開源\another system"

# 啟動虛擬環境
.venv\Scripts\Activate.ps1

# 安裝開發依賴（含 pytest, hypothesis, respx, ruff）
pip install -e ".[dev]"
```

### 確認安裝

```powershell
python -c "import keeplink_mcp; print(keeplink_mcp.__version__)"
# 預期輸出: 1.0.0
```

---

## 2. 自動化測試

### 2.1 跑全部測試

```powershell
pytest test/ -v
```

**預期結果**：84 個測試全部通過（PASSED）。

### 2.2 跑特定模組

```powershell
# Worker 相關
pytest test/test_worker.py -v

# 錯誤分類器
pytest test/test_error_classifier.py -v

# API 路由
pytest test/test_api_routes.py -v

# MCP Server
pytest test/test_mcp_server.py -v

# URL 驗證
pytest test/test_url_validator.py -v

# Repository
pytest test/test_repository.py -v

# 退避策略
pytest test/test_backoff.py -v
```

### 2.3 Property-Based Tests

```powershell
pytest test/ -v -k "property"
```

### 2.4 Lint 檢查

```powershell
ruff check src/ test/
```

**預期結果**：All checks passed!

---

## 3. 手動 Smoke Test（端到端）

### 3.1 啟動 FastAPI 後端

```powershell
python -m keeplink_mcp.main
```

**預期輸出**：
```
KeepLink MCP Service starting on 127.0.0.1:19210, db=...
```

### 3.2 測試 API 端點

開另一個終端：

#### POST /api/archive（提交存檔）

```powershell
$response = Invoke-RestMethod -Method POST `
  -Uri "http://127.0.0.1:19210/api/archive" `
  -ContentType "application/json" `
  -Body '{"url": "https://example.com"}'
$response | ConvertTo-Json
```

**預期回應**（201 Created）：
```json
{
  "task_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "url": "https://example.com",
  "status": "PENDING",
  "result_url": null,
  "created_at": "2026-08-18T...",
  "is_deduplicated": false
}
```

#### GET /api/status/{task_id}（查詢狀態）

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:19210/api/status/<上面的task_id>"
```

**預期回應**（200 OK）：
```json
{
  "task_id": "...",
  "url": "https://example.com",
  "status": "PENDING | PROCESSING | SUCCESS | FAILED",
  "result_url": "https://web.archive.org/web/...",
  "error_message": null,
  "retry_count": 0,
  "created_at": "...",
  "updated_at": "..."
}
```

#### GET /api/status?url=...（用 URL 查詢）

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:19210/api/status?url=https://example.com"
```

#### 24h 去重驗證

再次 POST 相同 URL：

```powershell
$response2 = Invoke-RestMethod -Method POST `
  -Uri "http://127.0.0.1:19210/api/archive" `
  -ContentType "application/json" `
  -Body '{"url": "https://example.com"}'
$response2 | ConvertTo-Json
```

**預期**：`is_deduplicated: true`，`task_id` 與第一次相同。

#### 無效 URL 驗證

```powershell
try {
  Invoke-RestMethod -Method POST `
    -Uri "http://127.0.0.1:19210/api/archive" `
    -ContentType "application/json" `
    -Body '{"url": "not-a-valid-url"}'
} catch { $_.Exception.Response.StatusCode }
```

**預期**：422 Unprocessable Entity。

### 3.3 測試 MCP Server（stdio 協議）

#### 方法 A：直接 pipe JSON-RPC

```powershell
$init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
$list = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# 用 echo pipe 進 MCP server（會同時啟動 FastAPI 後端）
echo "$init`n$list" | python -m keeplink_mcp.mcp_server.main
```

**預期**：收到 JSON-RPC response 包含 `archive_url` 和 `get_archive_status` 兩個工具。

#### 方法 B：Kiro IDE 驗證

1. 確認 `~/.kiro/settings/mcp.json` 中 keeplink 設定：
   ```json
   {
     "mcpServers": {
       "keeplink": {
         "command": "python",
         "args": ["-m", "keeplink_mcp.mcp_server.main"],
         "cwd": "D:\\vibe x\\coding\\開源\\another system"
       }
     }
   }
   ```
2. 重啟 Kiro
3. 在 MCP Server 面板確認 `keeplink` 顯示為綠色（已連線）
4. 在 chat 中測試：「請幫我存檔 https://example.com」

---

## 4. Worker 行為驗證

### 4.1 正常存檔流程

1. 提交一個存檔請求
2. 觀察日誌：`data/archiver.log`
3. 預期看到：
   - `"action": "poll_batch"` — Worker 撈到任務
   - `"action": "archive_success"` — 存檔成功（需要網路）
   
```powershell
Get-Content data/archiver.log -Wait
```

### 4.2 429 重試行為（v1.1 修正）

模擬 IA API 回傳 429 時，Worker 應該：
- 分類為 `RETRYABLE`
- 排程指數退避重試（60s → 120s → 240s → 480s → 960s）
- 最多重試 5 次

驗證方式：查看日誌中 `"action": "archive_retry"` 和 `backoff_sec` 欄位遞增。

### 4.3 自動 spawn 後端（v1.1 新功能）

```powershell
# 只啟動 MCP Server — 它應該自動 spawn FastAPI 後端
python -m keeplink_mcp.mcp_server.main
# （用 Ctrl+C 中斷）

# 確認後端進程有被建立：
Get-Process -Name python | Where-Object { $_.CommandLine -like "*keeplink_mcp.main*" }
```

---

## 5. 資料庫驗證

### 查看任務列表

```powershell
python -c "
import sqlite3
conn = sqlite3.connect('data/task.db')
print('=== All Tasks ===')
for row in conn.execute('SELECT task_id, url, status, retry_count, result_url FROM archive_tasks'):
    print(row)
"
```

### 查看失敗任務

```powershell
python -c "
import sqlite3
conn = sqlite3.connect('data/task.db')
for row in conn.execute(""SELECT task_id, url, error_message, retry_count FROM archive_tasks WHERE status='FAILED'""):
    print(row)
"
```

### 重設資料庫（開發用）

```powershell
Remove-Item data/task.db, data/task.db-shm, data/task.db-wal -ErrorAction SilentlyContinue
python -m keeplink_mcp.main  # 會自動重建
```

---

## 6. CI/CD 驗證

GitHub Actions 會在每次 push/PR 時自動跑：

```yaml
# .github/workflows/ci.yml
- Ruff lint
- Pytest (84 tests)
- pip-audit (依賴安全掃描)
```

本地模擬 CI：

```powershell
ruff check src/ test/ ; pytest test/ -v ; pip-audit
```

---

## 7. 錯誤場景測試清單

| # | 場景 | 預期行為 | 驗證方式 |
|---|------|---------|---------|
| 1 | 無效 URL（`ftp://...`） | 422 拒絕 | POST /api/archive |
| 2 | 空 URL | 422 拒絕 | POST /api/archive |
| 3 | 不存在的 task_id | 404 Not found | GET /api/status/fake-id |
| 4 | 後端未啟動時打 API | Connection refused | curl 127.0.0.1:19210 |
| 5 | IA API 返回 429 | Worker 重試（RETRYABLE） | 日誌 archive_retry |
| 6 | IA API 返回 401 | 立即失敗（NON_RETRYABLE） | 日誌 archive_failed |
| 7 | 網路斷線 | Worker 重試 | 拔網路 → 觀察日誌 |
| 8 | 同 URL 24h 內重複提交 | 回傳 is_deduplicated=true | POST 兩次 |
| 9 | 5 次重試後仍失敗 | 標記 FAILED，不再重試 | 查 DB status=FAILED |
| 10 | Ctrl+C 優雅關閉 | Worker 完成當前任務後停止 | 觀察 "worker_stop" 日誌 |

---

## 8. 效能基準

| 指標 | 預期值 |
|------|--------|
| POST /api/archive 響應時間 | < 50ms（僅寫 SQLite） |
| MCP archive_url 工具響應 | < 100ms（含 httpx 代理） |
| Worker 輪詢間隔 | 5 秒（可配） |
| 記憶體佔用（idle） | < 50 MB |
| SQLite DB 在 10K 任務時查詢 | < 100ms |

---

## 9. 環境變數配置參考

| 變數 | 預設值 | 說明 |
|------|--------|------|
| KEEPLINK_API_HOST | 127.0.0.1 | 綁定地址 |
| KEEPLINK_API_PORT | 19210 | 綁定端口 |
| KEEPLINK_DB_PATH | ./data/task.db | SQLite 路徑 |
| KEEPLINK_MAX_RETRIES | 5 | 最大重試次數 |
| KEEPLINK_BASE_BACKOFF | 60.0 | 基礎退避秒數 |
| KEEPLINK_WORKER_CONCURRENCY | 1 | 每輪處理任務數 |
| KEEPLINK_POLL_INTERVAL | 5.0 | 輪詢間隔秒數 |
| KEEPLINK_IA_ACCESS_KEY | — | IA S3 Access Key（選填） |
| KEEPLINK_IA_SECRET_KEY | — | IA S3 Secret Key（選填） |
| KEEPLINK_LOG_LEVEL | INFO | 日誌等級 |
| KEEPLINK_LOG_FILE | ./data/archiver.log | 日誌文件路徑 |
