# Requirements Document

## Introduction

`archive_and_cite` 是 KeepLink-MCP 的新增 MCP 工具，實踐核心定位 X —「AI 研究時的網頁存檔中介軟體」。當 AI agent 在研究過程中引用網頁來源時，這些來源連結會發生 link rot（連結失效或內容變動）。本功能讓 AI 在一次呼叫中同時完成兩件事：將來源 URL 排入存檔佇列，並立即取回一個指向 permanent Wayback Machine URL 的結構化 citation，使 AI 產出的引用具備持久、可驗證的參照。

本功能完全複用既有存檔管線（URL validation/normalization、24h dedup、background Worker、Token_Bucket rate limiter、exponential backoff retry），不重新發明任何既有邏輯。工具維持與現有 `archive_url` 一致的 non-blocking 行為（<50ms 回傳），並保持 KeepLink 100% deterministic、LLM-free 的哲學：不呼叫任何 LLM、不抓取頁面內容。

Citation 的產生與格式化邏輯獨立為新的 citation-building 模組，與存檔邏輯分離（separation of concerns）。本迭代僅提供一種通用 markdown/純文字 citation 格式；學術格式（APA/MLA/BibTeX）明確排除於本迭代範圍之外，延後處理。

## Glossary

- **archive_and_cite_Tool**: 新增的 MCP 工具，接受 url 與 optional title，排入存檔並回傳結構化 citation
- **Citation**: 結構化引用物件，包含 title、original_url、archived_url、archived_at 與一個可直接貼上的 formatted 字串欄位
- **Citation_Builder**: 負責由存檔狀態與輸入參數確定性地產生 Citation 的獨立模組，不含存檔邏輯
- **Cite_Endpoint**: FastAPI 後端新增的 HTTP 端點，供 archive_and_cite_Tool 轉發請求
- **archived_url**: Citation 指向的 permanent Wayback Machine URL，即既有 ArchiveTask 的 result_url
- **original_url**: 使用者提供並經 normalization 的原始來源 URL
- **Pending_Citation**: archived_url 與 archived_at 為 null 的 Citation 形態，代表存檔仍在進行中，需後續以 get_archive_status(task_id) 補完
- **Cache_Hit**: 在 24h dedup window 內已成功存檔（status 為 success 且具備 result_url）的既有任務，可直接回傳已完成的 Citation
- **Dedup_Window**: 既有的 24 小時去重時間窗，判定是否複用既有任務
- **formatted**: Citation 中一個可直接貼上的 markdown 引用字串欄位
- **URL_Validator**: 既有的 URL 驗證與正規化模組（validate_url）
- **Worker**: 既有的後台存檔進程，複用其存檔管線

## Requirements

### Requirement 1: archive_and_cite MCP 工具註冊與轉發

**User Story:** As an AI agent, I want a single `archive_and_cite` MCP tool, so that I can archive a cited source and receive a structured citation in one call.

#### Acceptance Criteria

1. THE archive_and_cite_Tool SHALL register with the MCP Server exposing an input schema with a required string parameter url and an optional string parameter title.
2. WHEN the archive_and_cite_Tool is invoked with a url, THE archive_and_cite_Tool SHALL forward the archiving request to the Cite_Endpoint on the FastAPI Service.
3. WHEN the archive_and_cite_Tool returns a result, THE archive_and_cite_Tool SHALL return a structured Citation object containing the fields title, original_url, archived_url, archived_at, task_id, and formatted.
4. THE archive_and_cite_Tool SHALL return a result within 50 milliseconds of invocation under normal operating conditions, without waiting for archiving to complete.
5. IF the FastAPI Service is unreachable when the archive_and_cite_Tool forwards a request, THEN THE archive_and_cite_Tool SHALL return an error result indicating the backend service is unavailable.

---

### Requirement 2: 存檔管線複用與去重

**User Story:** As a service maintainer, I want `archive_and_cite` to reuse the existing archive pipeline, so that validation, normalization, deduplication, worker processing, rate limiting, and retry behave identically to `archive_url`.

#### Acceptance Criteria

1. WHEN the Cite_Endpoint receives a url, THE Cite_Endpoint SHALL validate and normalize the url using the existing URL_Validator before any further processing.
2. WHEN the Cite_Endpoint processes a validated url that has no dedup-eligible task within the Dedup_Window, THE Cite_Endpoint SHALL create a new ArchiveTask using the existing archive pipeline so that the existing Worker, Token_Bucket rate limiter, and exponential backoff retry process the task.
3. WHEN the Cite_Endpoint processes a validated url that has a dedup-eligible task within the Dedup_Window, THE Cite_Endpoint SHALL reuse the existing task rather than creating a new one.
4. THE Cite_Endpoint SHALL NOT fetch the page content of the url for any purpose, including title extraction.
5. THE Cite_Endpoint SHALL NOT invoke any large language model during request processing.

---

### Requirement 3: 已完成存檔的 Cache Hit Citation

**User Story:** As an AI agent, I want an immediate complete citation when the source was already archived, so that I can paste a permanent reference without a follow-up call.

#### Acceptance Criteria

1. WHEN a validated url has a Cache_Hit within the Dedup_Window, THE Citation_Builder SHALL populate the Citation archived_url field with the existing task result_url.
2. WHEN a validated url has a Cache_Hit within the Dedup_Window, THE Citation_Builder SHALL populate the Citation archived_at field with the timestamp at which the existing task reached success status.
3. WHEN a Citation has a non-null archived_url and a non-null title, THE Citation_Builder SHALL produce a formatted string of the form `[title](archived_url) (original: original_url, archived YYYY-MM-DD)`.
4. WHEN a Citation has a non-null archived_url and a null title, THE Citation_Builder SHALL produce a formatted string that uses the archived_url as the link text in place of a title.
5. THE Citation_Builder SHALL format the archived_at date within the formatted string using the YYYY-MM-DD calendar-date representation.

---

### Requirement 4: 進行中存檔的 Pending Citation

**User Story:** As an AI agent, I want a pending citation with a task_id when archiving is still running, so that I know to follow up and complete the citation later.

#### Acceptance Criteria

1. WHEN a validated url has no Cache_Hit and archiving is queued or in progress, THE Citation_Builder SHALL return a Pending_Citation with archived_url set to null and archived_at set to null.
2. WHEN a Pending_Citation is returned, THE Citation_Builder SHALL populate the original_url field and the task_id field with the values of the created or reused task.
3. WHEN a Citation has a null archived_url, THE Citation_Builder SHALL produce a formatted string that states archiving is in progress and includes both the original_url and the task_id.
4. WHEN a Pending_Citation is returned, THE archive_and_cite_Tool SHALL include guidance in its result instructing the caller to invoke get_archive_status with the task_id to obtain the final archived_url and complete the Citation.

---

### Requirement 5: 標題來源與 optional title

**User Story:** As an AI agent, I want to supply the title I already read from the page, so that the citation is human-readable without KeepLink fetching the page.

#### Acceptance Criteria

1. WHEN the archive_and_cite_Tool is invoked with a non-empty title, THE Citation SHALL set the title field to the supplied title value.
2. WHEN the archive_and_cite_Tool is invoked without a title, THE Citation SHALL set the title field to null.
3. IF the archive_and_cite_Tool is invoked with a title consisting only of whitespace characters, THEN THE Citation SHALL set the title field to null.

---

### Requirement 6: 無效 URL 的錯誤處理

**User Story:** As an AI agent, I want a clear error when the URL is invalid, so that I can correct the input instead of receiving a malformed citation.

#### Acceptance Criteria

1. IF the archive_and_cite_Tool is invoked with a url that fails URL_Validator validation, THEN THE archive_and_cite_Tool SHALL return an error result describing the validation failure and SHALL NOT return a Citation object.
2. IF the Cite_Endpoint receives a url that fails URL_Validator validation, THEN THE Cite_Endpoint SHALL return an HTTP 422 response with an error detail describing the validation failure.
3. WHEN the archive_and_cite_Tool returns a validation error, THE archive_and_cite_Tool SHALL NOT create any ArchiveTask.

---

### Requirement 7: Citation 回應 Schema

**User Story:** As an MCP client developer, I want a well-defined citation response schema, so that I can reliably parse the citation fields.

#### Acceptance Criteria

1. THE Cite_Endpoint SHALL return a JSON response conforming to a Pydantic Citation schema with fields title (nullable string), original_url (string), archived_url (nullable string), archived_at (nullable timestamp), task_id (string), and formatted (string).
2. THE Citation schema SHALL always include a non-null original_url, a non-null task_id, and a non-null formatted string, regardless of whether archiving is complete or pending.
3. WHERE archiving is pending, THE Citation schema archived_url field and archived_at field SHALL both be null.

---

### Requirement 8: 確定性格式化

**User Story:** As a maintainer, I want citation formatting to be deterministic, so that identical inputs and archive state always produce identical citations.

#### Acceptance Criteria

1. THE Citation_Builder SHALL produce identical Citation output for identical combinations of title, original_url, and archive state (archived_url plus archived_at, or pending).
2. THE Citation_Builder SHALL derive the formatted string solely from the Citation fields title, original_url, archived_url, archived_at, and task_id.
3. THE Citation_Builder SHALL NOT perform any network request, page fetch, or large language model invocation while producing a Citation.

---

### Requirement 9: 文件更新

**User Story:** As a new user, I want the handoverbook and README to describe the new tool and the async citation-completion flow, so that I understand how to use `archive_and_cite`.

#### Acceptance Criteria

1. THE handoverbook.md SHALL document the archive_and_cite_Tool, including its parameters (url, optional title) and the Citation response fields.
2. THE handoverbook.md SHALL describe the async citation-completion flow in which a Pending_Citation is completed later via get_archive_status(task_id).
3. THE README.md SHALL document the archive_and_cite_Tool and its Cache_Hit versus Pending_Citation behaviour.
4. THE README.md SHALL state that KeepLink does not fetch page content or invoke a large language model when producing a Citation.
