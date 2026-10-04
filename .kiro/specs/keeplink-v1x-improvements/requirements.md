# Requirements Document

## Introduction

本規格書彙整 KeepLink-MCP 的一批 v1.x 改進（A、C、H、I），目標版本統一為 **1.2.0**。KeepLink-MCP 是一個本地非同步中介軟體，讓 AI agent 把網頁存檔到 Internet Archive（SPN2），採雙進程架構：MCP Server（stdio，薄代理）＋ FastAPI Service（HTTP ＋ 背景 Worker）＋ SQLite 佇列。核心哲學為本地優先、零外部依賴、LLM-free、deterministic、fail-fast，且不擴張為通用存檔器。

本批四項改進各自獨立且皆為既有功能的補強，須維持既有行為與測試不被破壞：

- **A — 版本源統一**：修正 README、handoverbook、程式碼三處版本不一致的真實 bug，建立單一版本真相來源。
- **C — 失敗任務手動重試**：新增 `retry_task` MCP 工具與對應 HTTP 端點，讓永久 failed 任務可重新進入 Worker 管線。
- **H — 多種 citation 格式**：`archive_and_cite` 支援 markdown（預設）、bibtex、apa、plain 四種格式。
- **I — CI 補強**：CI matrix 加入 windows-latest，確保跨平台正確性。

## Glossary

- **KeepLink_System**：KeepLink-MCP 整體系統。
- **MCP_Server**：以 stdio 傳輸、透過 httpx 代理請求到 FastAPI Service 的薄代理進程（`src/keeplink_mcp/mcp_server/server.py`）。
- **FastAPI_Service**：提供 HTTP API 與背景 Worker 的服務進程（`src/keeplink_mcp/api/`）。
- **Worker**：從 SQLite 佇列取出 pending 任務並執行存檔的背景輪詢迴圈。
- **Task_Repository**：封裝 ArchiveTask 資料存取的資料層（`src/keeplink_mcp/db/repository.py`）。
- **ArchiveTask**：佇列中的單一存檔任務，含 `task_id`、`status`、`retry_count`、`next_retry_at`、`error_message`、`result_url`、`updated_at` 等欄位。
- **Task_Status**：任務生命週期狀態列舉，值為 `pending`、`processing`、`success`、`failed`（`TaskStatus`）。
- **Version_Source**：任一標示 KeepLink_System 版本字串的位置，包含 `src/keeplink_mcp/__init__.py` 的 `__version__`、`pyproject.toml` 的 `version`、`FastAPI_Service` app metadata 的 `version`、`README.md` 顯示的版本字串、`docs/handoverbook.md` 的版本標記。
- **Authoritative_Version**：版本真相的權威來源，定義為 `src/keeplink_mcp/__init__.py` 的 `__version__`。
- **Target_Version**：本批統一的目標版本字串，值為 `1.2.0`。
- **Version_Consistency_Test**：既有的版本一致性測試（`test/test_version_consistency.py`，原 keeplink-v1-improvements 的 Property 7）。
- **Retry_Tool**：新增的 MCP 工具 `retry_task`。
- **Retry_Endpoint**：新增的 FastAPI HTTP 端點，處理任務重試請求。
- **Citation_Builder**：確定性、純函式、零 I/O 的引用建構器（`src/keeplink_mcp/citation/builder.py` 的 `build_citation`）。
- **Citation_Format**：`archive_and_cite` / `POST /api/cite` 支援的輸出格式，值為 `markdown`、`bibtex`、`apa`、`plain`。
- **Complete_Citation**：`archived_url` 與 `archived_at` 皆非 null 的引用（存檔已完成）。
- **Pending_Citation**：`archived_url` 與 `archived_at` 皆為 null 的引用（存檔進行中）。
- **CI_Pipeline**：GitHub Actions 工作流程（`.github/workflows/ci.yml`），執行 lint、test、security audit。
- **CI_Matrix**：CI_Pipeline 的作業系統與 Python 版本組合矩陣。

## Requirements

### Requirement 1: 統一單一版本真相來源（改進 A）

**User Story:** As a KeepLink 維護者, I want 所有標示版本的位置都與單一權威來源一致並統一為 1.2.0, so that 使用者、文件與套件不再看到互相矛盾的版本號。

#### Acceptance Criteria

1. THE KeepLink_System SHALL 將 `src/keeplink_mcp/__init__.py` 的 `__version__` 設為 Target_Version（`1.2.0`）作為 Authoritative_Version。
2. THE KeepLink_System SHALL 將 `pyproject.toml` 的 `version` 設為與 Authoritative_Version 相同的字串。
3. THE FastAPI_Service SHALL 於 app metadata 的 `version` 使用與 Authoritative_Version 相同的字串。
4. THE `README.md` SHALL 於頂部顯示的版本字串使用與 Authoritative_Version 相同的字串。
5. THE `docs/handoverbook.md` SHALL 於版本標記使用與 Authoritative_Version 相同的字串。
6. WHEN Version_Consistency_Test 執行時, THE Version_Consistency_Test SHALL 讀取 `pyproject.toml`、`src/keeplink_mcp/__init__.py`、`FastAPI_Service` app metadata 與 `README.md` 顯示的版本字串, 並斷言四者字串完全相等。
7. IF 任一 Version_Source 的版本字串與 Authoritative_Version 不相等, THEN THE Version_Consistency_Test SHALL 判定為失敗並在訊息中列出不一致的來源與其各自的版本值。
8. THE KeepLink_System SHALL 使每個受檢查的 Version_Source 的版本字串符合語意化版本格式 `\d+\.\d+\.\d+`。

---

### Requirement 2: 失敗任務手動重試工具（改進 C）

**User Story:** As an AI agent 使用者, I want 透過一個工具把永久 failed 的任務重新排入佇列, so that 我不必手動修改資料庫就能重試被放棄的存檔（例如遇到 403 後）。

#### Acceptance Criteria

1. THE MCP_Server SHALL 於 list_tools 回應中提供名為 `retry_task` 的 Retry_Tool，其 inputSchema 具有必填字串參數 `task_id`。
2. THE FastAPI_Service SHALL 提供一個 Retry_Endpoint 接受帶有 `task_id` 的重試請求，其風格鏡射既有 `POST /api/archive` 端點。
3. WHEN Retry_Endpoint 收到一個 `status` 為 `failed` 的 ArchiveTask 的重試請求, THE Task_Repository SHALL 將該任務的 `status` 設為 `pending`、將 `retry_count` 重設為 `0`、將 `next_retry_at` 清為 null、並將 `error_message` 清為 null。
4. WHEN Retry_Endpoint 成功將一個任務由 `failed` 重設為 `pending`, THE FastAPI_Service SHALL 回傳該任務更新後的 `task_id` 與 `status`（`pending`）。
5. WHILE 一個任務已被 Retry_Endpoint 重設為 `pending`, THE Worker SHALL 依既有 fetch_pending_tasks 規則將該任務重新納入處理。
6. IF 指定 `task_id` 對應的 ArchiveTask 的 `status` 不是 `failed`（即為 `pending`、`processing` 或 `success`）, THEN THE Retry_Endpoint SHALL 拒絕該請求並回傳明確的錯誤，指出目前狀態且僅允許 `failed` → `pending` 轉換，且不得修改該任務任何欄位。
7. IF 指定的 `task_id` 在資料庫中不存在, THEN THE Retry_Endpoint SHALL 回傳明確的 not-found 錯誤。
8. IF FastAPI_Service 無法連線, THEN THE Retry_Tool SHALL 回傳明確的後端不可達錯誤，沿用既有 MCP 工具的錯誤處理模式。
9. WHEN 對任一先前曾被 Retry_Endpoint 重設為 `pending` 的任務再次呼叫 Retry_Endpoint, THE Retry_Endpoint SHALL 因該任務當前狀態非 `failed` 而依條件 6 拒絕該請求並回傳非 `failed` 狀態的拒絕錯誤（作為冪等性邊界的可驗證行為）。

---

### Requirement 3: archive_and_cite 多種 citation 格式（改進 H）

**User Story:** As an AI agent 使用者, I want 在產生引用時選擇 markdown、bibtex、apa 或 plain 格式, so that 我能把引用直接貼進不同的文件或工具而無需手動改寫。

#### Acceptance Criteria

1. THE Citation_Builder SHALL 接受一個 `format` 參數，其有效值為 `markdown`、`bibtex`、`apa`、`plain`。
2. WHERE 呼叫者未指定 `format`, THE Citation_Builder SHALL 使用 `markdown` 作為預設值，且其產生的 `formatted` 字串與本改進前既有實作的輸出完全一致（逐字元相同）。
3. THE Citation_Builder SHALL 對 Complete_Citation 與 Pending_Citation 兩種狀態、以及四種 Citation_Format 的每一組合，皆產生確定性的 `formatted` 字串（相同輸入必定得到相同輸出，且不執行任何網路、頁面抓取、LLM 或 DB 存取）。
4. WHERE `format` 為 `plain`, THE Citation_Builder SHALL 產生純文字的 `formatted` 字串，不含任何 markdown 連結語法（例如不含 `[text](url)` 結構）。
5. WHERE 引用為 Pending_Citation 且 `format` 為 `bibtex` 或 `apa`, THE Citation_Builder SHALL 在 `archived_url` 尚不存在的情況下產生合理的輸出，並以 `original_url` 與 `task_id` 表達存檔進行中的狀態。
6. THE `CiteRequest` schema SHALL 新增可選的 `format` 欄位，未提供時預設為 `markdown`。
7. THE MCP_Server SHALL 於 `archive_and_cite` 工具的 inputSchema 新增可選的 `format` 參數，列舉值為 `markdown`、`bibtex`、`apa`、`plain`。
8. IF 呼叫者提供的 `format` 值不在有效列舉值之內, THEN THE FastAPI_Service SHALL 先於內部記錄（log）該驗證失敗以利除錯, 再回傳明確的驗證錯誤（fail-fast），不得靜默退回預設格式。
9. WHEN `POST /api/cite` 收到含有效 `format` 的請求, THE FastAPI_Service SHALL 以該 `format` 呼叫 Citation_Builder 並於 `CitationResponse.formatted` 回傳對應格式的字串。
10. THE Citation_Builder SHALL 對所有格式維持既有的結構化欄位不變（`title`、`original_url`、`archived_url`、`archived_at`、`task_id` 保持既有語意），`format` 只影響 `formatted` 字串。
11. THE FastAPI_Service SHALL 僅在請求經過正確處理且確實呼叫 Citation_Builder 之後才回傳 `CitationResponse`，不得在未經請求處理或未呼叫 Citation_Builder 的情況下產生 `CitationResponse`。

---

### Requirement 4: CI 加入 Windows matrix（改進 I）

**User Story:** As a KeepLink 維護者, I want CI 也在 Windows 上執行 lint、test 與 security audit, so that 跨平台問題（路徑、port 綁定、檔案處理）能在合併前被 CI 攔截。

#### Acceptance Criteria

1. THE CI_Pipeline SHALL 於 CI_Matrix 同時包含 `ubuntu-latest` 與 `windows-latest` 作業系統。
2. THE CI_Pipeline SHALL 於 CI_Matrix 對每個作業系統維持既有的 Python 版本組合（`3.10` 與 `3.13`）。
3. WHEN CI_Pipeline 於 `windows-latest` 執行時, THE CI_Pipeline SHALL 執行 lint（`ruff check`）、test（`pytest`）與 security audit（`pip-audit`）三個步驟。
4. WHEN CI_Pipeline 於既有 `ubuntu-latest` 執行時, THE CI_Pipeline SHALL 維持與本改進前相同的 lint、test 與 security audit 行為。
5. IF 某作業系統的作業於本次執行中實際運行、且其實際執行的 lint、test 或 security audit 步驟失敗, THEN THE CI_Pipeline SHALL 使該實際執行中的作業標記為失敗（fail-fast，不因單一平台通過而掩蓋另一平台的失敗），且不因未於本次實際執行的平台而使作業失敗。
6. WHERE CI_Pipeline 於某作業系統執行時, WHEN 該作業實際執行的 lint、test 與 security audit 步驟皆通過, THE CI_Pipeline SHALL 允許該作業通過（部分執行語意：僅就實際執行到的步驟評估，未執行到的步驟不影響通過判定；與條件 5「實際執行且失敗才失敗」一致）。
