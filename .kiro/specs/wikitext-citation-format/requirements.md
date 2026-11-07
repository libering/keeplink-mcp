# Requirements Document

## Introduction

本規格為 KeepLink-MCP 的 `archive_and_cite` 引用輸出新增第五種格式 `wikitext`,輸出 Wikipedia `{{cite web}}` 範本字串,含封存欄位 `archive-url`、`archive-date`、`url-status`。需求由已核准的 `design.md` 推導,欄位順序、跳脫規則與 `url-status` 取值皆以 Wikipedia 官方文件為依據(來源見 design)。

### Out of Scope

- 不改動 markdown、bibtex、apa、plain 四種格式的任何輸出,並提供回歸保證。
- 不自動抓取頁面標題、作者或日期;`builder.py` 維持零 I/O 純函式。
- 不新增任何執行期依賴;使用者不需另外申請帳號或 API key。
- 不處理 `cite web` 以外的範本(例如 `cite news`)。
- 不輸出 `access-date`(語意是編輯者讀到來源的日期,與封存日期不同)、作者、出版者、網站名稱。
- 不在本 spec 內變更版本號;發佈由使用者另外決定。

## Glossary

- **Citation_Builder**:確定性、純函式、零 I/O 的引用建構器(`src/keeplink_mcp/citation/builder.py` 的 `build_citation`)。
- **Wikitext_Formatter**:新增的純函式模組 `src/keeplink_mcp/citation/wikitext.py`,提供 `complete_wikitext`、`pending_wikitext`、`escape_title`、`escape_url`。
- **Citation_Format**:`CitationFormat` 列舉,既有值為 `markdown`、`bibtex`、`apa`、`plain`,本規格新增 `wikitext`。
- **Complete_Citation**:`archived_url` 與 `archived_at` 皆非 null 的引用。
- **Pending_Citation**:`archived_url` 與 `archived_at` 皆為 null 的引用。
- **Wikitext_Citation**:`format=wikitext` 時 `Citation.formatted` 的內容,為單一字串。
- **Cite_Endpoint**:FastAPI 的 `POST /api/cite`。
- **MCP_Server**:提供 `archive_and_cite` 工具的 MCP 進程(`src/keeplink_mcp/mcp_server/server.py`)。
- **Template_Parameter**:Wikitext_Citation 中以 `|` 分隔的 `名稱=值` 單元(不含 HTML 註解)。
- **Reserved_Characters**:會破壞範本語法的字元,在 title 中為 `|`、`[`、`]`、`{`、`}`、`<` 與換行類字元,在 URL 中為空白、`"`、`'`、`<`、`>`、`[`、`]`、`{`、`|`、`}`。
- **Regression_Suite**:`test/` 下凍結既有四種格式輸出的測試集合。
- **Project_Docs**:`docs/` 目錄下列出支援引用格式的專案文件與 `README.md`。

## Requirements

### Requirement 1: 註冊 wikitext 格式

**User Story:** As a 使用 AI agent 研究並撰寫 Wikipedia 內容的編輯者, I want 在 `archive_and_cite` 選擇 `wikitext` 格式, so that 取得可直接貼上的 `{{cite web}}` 範本。

#### Acceptance Criteria

1. THE Citation_Format SHALL 包含值 `wikitext`,且既有四個值的內容與順序不變,`wikitext` 位於最後。
2. WHEN Cite_Endpoint 收到 `format` 為 `wikitext` 的請求, THE Cite_Endpoint SHALL 回傳 `formatted` 為 Wikitext_Citation 的 CitationResponse。
3. THE MCP_Server SHALL 於 `archive_and_cite` 的 inputSchema 中,將 `format` 的 enum 設為 `["markdown", "bibtex", "apa", "plain", "wikitext"]`,並於描述文字說明 `wikitext`。
4. IF `format` 的值不屬於 Citation_Format, THEN THE Cite_Endpoint SHALL 維持既有行為回傳 422 並記錄 warning,不產生 Citation。

### Requirement 2: Complete 態輸出結構

**User Story:** As a 編輯者, I want 封存完成的引用輸出完整的封存欄位, so that 貼上後讀者能連到封存版本。

#### Acceptance Criteria

1. WHEN 以 `format=wikitext` 建構 Complete_Citation, THE Wikitext_Formatter SHALL 產生以 `{{cite web ` 開頭、以 `}}` 結尾且不含換行字元的單一字串。
2. WHEN 以 `format=wikitext` 建構 Complete_Citation, THE Wikitext_Formatter SHALL 依序輸出恰好五個 Template_Parameter:`title`、`url`、`url-status`、`archive-url`、`archive-date`。
3. WHEN 以 `format=wikitext` 建構 Complete_Citation, THE Wikitext_Formatter SHALL 將 `url` 設為 `original_url`(經 URL 跳脫),並將 `archive-url` 設為 `archived_url`(經 URL 跳脫)。
4. WHEN 以 `format=wikitext` 建構 Complete_Citation, THE Wikitext_Formatter SHALL 將 `archive-date` 設為 `archived_at` 的 `YYYY-MM-DD` 表示。
5. WHEN 以 `format=wikitext` 建構 Complete_Citation, THE Wikitext_Formatter SHALL 將 `url-status` 設為 `live`。
6. THE Wikitext_Formatter SHALL 僅輸出 Requirement 2.2 列出的五個 Template_Parameter,不輸出 `access-date`、作者、出版者、網站名稱等其他參數。

### Requirement 3: title 處理

**User Story:** As a 編輯者, I want title 中的特殊字元被安全處理且缺失時不被捏造, so that 範本語法不被破壞且引文不含不實標題。

#### Acceptance Criteria

1. WHEN title 非 null, THE Wikitext_Formatter SHALL 將 title 經 `escape_title` 後作為 `title` 的值。
2. IF title 為 null(含空字串與純空白經 `normalize_title` 轉為 null), THEN THE Wikitext_Formatter SHALL 輸出值為空字串的 `title` 參數,維持五個參數的位置不變,且不以 URL 或任何其他資料填補。
3. WHEN title 含有 `|`、`[`、`]`、`{`、`}` 或 `<`, THE Wikitext_Formatter SHALL 分別替換為 `&#124;`、`&#91;`、`&#93;`、`&#123;`、`&#125;`、`&#60;`。
4. WHEN title 含有 `=`, THE Wikitext_Formatter SHALL 保留該字元原樣。
5. WHEN title 含有換行類字元, THE Wikitext_Formatter SHALL 將各行以單一空白連接,使輸出維持單行。
6. IF title 含有任意 Reserved_Characters 組合, THEN THE Wikitext_Formatter SHALL 維持 Requirement 2.2 的五個 Template_Parameter 的個數與順序不變。

### Requirement 4: URL 處理

**User Story:** As a 編輯者, I want URL 中的特殊字元被安全編碼, so that 含空白或保留字元的網址不會破壞範本。

#### Acceptance Criteria

1. WHEN `original_url` 或 `archived_url` 含有空白或 `"`、`'`、`<`、`>`、`[`、`]`、`{`、`|`、`}`, THE Wikitext_Formatter SHALL 將各該字元以 UTF-8 百分比編碼取代。
2. WHEN `original_url` 或 `archived_url` 不含 Reserved_Characters, THE Wikitext_Formatter SHALL 原樣輸出該 URL,包含已存在的 `%` 序列。

### Requirement 5: Pending 態輸出

**User Story:** As a 編輯者, I want 存檔進行中的引用清楚標示尚未完成, so that 我不會把沒有封存連結的引用誤當成完成品。

#### Acceptance Criteria

1. WHEN 以 `format=wikitext` 建構 Pending_Citation, THE Wikitext_Formatter SHALL 產生以 `{{cite web ` 開頭、以 `}}` 結尾的單一字串,其中唯一的 Template_Parameter 為 `url`(值為經 URL 跳脫的 `original_url`),並於範本內以 HTML 註解載明 `Archiving in progress` 與 `task_id`。
2. WHEN 以 `format=wikitext` 建構 Pending_Citation, THE Wikitext_Formatter SHALL 不輸出 `archive-url`、`archive-date`、`url-status` 與 `title`,亦不虛構 `archived_url`。
3. IF `task_id` 含有 `-->`, THEN THE Wikitext_Formatter SHALL 將其改寫為 `-- >`,使 HTML 註解不被提前結束。

### Requirement 6: 純函式性質與相容性

**User Story:** As a KeepLink 維護者, I want 新格式維持零 I/O、確定性且不改變結構化欄位, so that 專案的核心設計原則不被破壞。

#### Acceptance Criteria

1. THE Wikitext_Formatter SHALL 對相同輸入產生完全相同的輸出。
2. THE Wikitext_Formatter SHALL 僅依賴 Python 標準函式庫,且 `pyproject.toml` 的執行期依賴清單不變。
3. WHEN `format=wikitext`, THE Citation_Builder SHALL 回傳與 `format=markdown` 相同的 `title`、`original_url`、`archived_url`、`archived_at`、`task_id`。
4. THE Wikitext_Formatter SHALL 不執行網路請求、頁面抓取、檔案存取或資料庫存取。
5. IF `archived_url` 與 `archived_at` 只設其一, THEN THE Citation_Builder SHALL 維持既有的 `ValueError` fail-fast 行為,不呼叫 Wikitext_Formatter。

### Requirement 7: 既有格式回歸保證

**User Story:** As a 既有使用者, I want 原有四種格式的輸出完全不變, so that 升級不會破壞我已經依賴的輸出。

#### Acceptance Criteria

1. WHEN `format` 為 `markdown`、`bibtex`、`apa` 或 `plain`, THE Citation_Builder SHALL 在 Complete_Citation 與 Pending_Citation 兩種狀態下產生與本變更前逐位元組相同的 `formatted`。
2. WHEN 呼叫端未指定 `format`, THE Citation_Builder SHALL 產生與 `format=markdown` 相同的 `formatted`。
3. THE Regression_Suite SHALL 以固定輸入的凍結字面字串(四種格式 × 兩種狀態,共八個組合)作為基準,且該基準於修改 `builder.py` 之前先以既有程式碼產生。

### Requirement 8: 文件同步

**User Story:** As a 使用者, I want 專案文件列出 `wikitext`, so that 我能發現並正確使用這個格式。

#### Acceptance Criteria

1. THE Project_Docs SHALL 於每一處列出支援引用格式的位置加入 `wikitext`,包含 `format` 參數表、功能清單與模組結構說明。
2. THE Project_Docs SHALL 說明 `wikitext` 的欄位、`url-status=live` 的理由、title 缺失時輸出空 `title` 參數,以及 `archive-date` 為近似值這項已知限制。
3. THE Project_Docs SHALL 維持版本號不變。
