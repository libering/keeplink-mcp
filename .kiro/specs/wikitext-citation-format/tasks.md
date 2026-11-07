# Implementation Plan: wikitext-citation-format

## Overview

為 `archive_and_cite` 新增 `wikitext` 引用格式。順序為:先凍結既有四種格式的輸出作為回歸基準(在動 `builder.py` 之前),再建立獨立模組 `citation/wikitext.py`(先跳脫 helper,再 formatter),然後在 `builder.py` 登記、同步 MCP enum、補端對端測試與文件,最後執行完整驗證。

原則:一次只動一個模組;每個任務含對應測試;不新增依賴;不 bump 版本號;測試斷言驗證實際輸出字串,不使用 Mock 取代被測邏輯。Python >=3.10。

## Tasks

- [x] 1. 凍結既有格式的回歸基準(必須在修改 `builder.py` 之前完成)
  - [x] 1.1 建立 `test/test_wikitext_regression.py` 並寫入八個凍結字面字串
    - 先以現行未修改的程式碼,用 `_H_` 系列固定輸入(title `Example`、original_url `https://example.com/article`、archived_url `https://web.archive.org/web/20240101000000/https://example.com/article`、task_id `task-abc-123`、archived_at `2024-01-01 12:30:45`)印出 markdown、bibtex、apa、plain 在 complete 與 pending 兩態的 `formatted`
    - 將輸出以字面字串寫入測試,斷言 `build_citation(...).formatted == 字面字串`;共八個案例
    - 另加一個案例:不傳 `format` 時等於 markdown 字面字串
    - 寫完後讀回檔案,確認中文註解沒有亂碼
    - _Requirements: 7.1, 7.2, 7.3_

- [x] 2. 建立 `citation/wikitext.py` 並以測試驗證
  - [x] 2.1 建立 `src/keeplink_mcp/citation/wikitext.py`:常數與跳脫 helper
    - 先寫模組 docstring、`URL_STATUS_LIVE = "live"`、`escape_title`、`escape_url` 的完整簽名與 docstring(interface first),再實作本體
    - `escape_title`:`" ".join(title.splitlines())` 後以單趟 `str.translate` 替換 `| [ ] { } <` 為 `&#124; &#91; &#93; &#123; &#125; &#60;`;`= & >` 不動
    - `escape_url`:以 `re.sub` 比對空白與 `" ' < > [ ] { | }`,每個符合字元交給 `urllib.parse.quote(ch, safe="")`
    - 只使用標準函式庫;不匯入 `builder.py`(避免循環匯入)
    - 每個函式 ≤50 行,檔案 ≤500 行
    - _Requirements: 3.3, 3.4, 3.5, 4.1, 4.2, 6.2_

  - [x] 2.2 在 `wikitext.py` 實作 `complete_wikitext` 與 `pending_wikitext`
    - 簽名使用關鍵字參數,與 `builder.py` 的 `CompleteFormatter` / `PendingFormatter` 慣例一致
    - `complete_wikitext`:`{{cite web |title=… |url=… |url-status=live |archive-url=… |archive-date=YYYY-MM-DD}}`;title 為 None 時輸出 `|title=`(空值,不填補)
    - `pending_wikitext`:`{{cite web |url=… <!-- Archiving in progress; task_id: … -->}}`;task_id 中的 `-->` 改寫為 `-- >`
    - 日期以 `archived_at.strftime("%Y-%m-%d")` 產生,與既有 formatter 一致
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 5.1, 5.2, 5.3, 6.1, 6.4_

  - [x] 2.3 建立 `test/test_wikitext_properties.py`:complete 態的屬性測試
    - 在檔案內實作獨立的測試解析器 `parse_cite_web`:去前綴 `{{cite web`、去後綴 `}}`、移除 `<!-- ... -->`、以 `|` 切分、以第一個 `=` 分名稱與值並去除前後空白;不得重用 `wikitext.py` 的程式碼
    - title 策略:`st.text()` 混入 `st.sampled_from(["|", "{{", "}}", "=", "[", "]", "<!--", "\n"])`;URL 策略含空白與保留字元的路徑
    - 此階段 `builder.py` 尚未登記,直接呼叫 `complete_wikitext` 驗證;builder 層的屬性由 3.2 補上
    - **Property 1: 範本封裝與單行** — **Validates: Requirements 2.1**
    - **Property 2: 參數集合與順序不被破壞**(含 `formatted` 除首尾外不含 `{` `}`)— **Validates: Requirements 2.2, 2.6, 3.3, 3.5, 4.1**
    - **Property 3: 欄位值正確** — **Validates: Requirements 2.3, 2.4, 2.5**
    - **Property 4: title 跳脫往返**(策略排除 `&`)— **Validates: Requirements 3.1, 3.3, 3.4**
    - **Property 5: title 缺失不捏造** — **Validates: Requirements 3.2**
    - 每條 `@settings(max_examples=100)`,註解 `# Feature: wikitext-citation-format, Property N: ...`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 2.4 在 `test/test_wikitext_properties.py` 加入 pending 態屬性測試
    - **Property 6: pending 不含封存欄位** — 參數名稱序列恆為 `["url"]`;含 `task_id` 與 `Archiving in progress`;不含 `archive-url`、`archive-date`、`url-status`、`title=`
    - **Validates: Requirements 5.1, 5.2**
    - 另以 example 斷言 `task_id` 含 `-->` 時被改寫為 `-- >`,且輸出仍以 `}}` 結尾(**Validates: Requirements 5.3**)
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 2.5 建立 `test/test_wikitext_examples.py`:golden 字面字串測試
    - 直接呼叫 `complete_wikitext` / `pending_wikitext`,以完整字面字串斷言輸出內容
    - 案例:(a) complete 一般 title;(b) title 為 None;(c) title `A | B {{x}} [y]=z` 預期 `A &#124; B &#123;&#123;x&#125;&#125; &#91;y&#93;=z`;(d) URL 含空白與 `|`;(e) pending
    - 加一個案例斷言 `archive-date` 對 `datetime(2024, 1, 1, 12, 30, 45)` 為 `2024-01-01`,不含 `access-date`
    - 不使用 Mock
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.2, 3.3, 3.4, 4.1, 5.1, 5.2_

- [x] 3. 在 `builder.py` 登記 wikitext
  - [x] 3.1 修改 `src/keeplink_mcp/citation/builder.py`:新增列舉值並登記
    - `CitationFormat` 末端新增 `WIKITEXT = "wikitext"`
    - 匯入 `complete_wikitext`、`pending_wikitext`,各在 `_COMPLETE_FORMATTERS`、`_PENDING_FORMATTERS` 字典末端加一行
    - 只做加法:既有四種格式的程式碼、`build_citation` 邏輯一行都不改;完成後以 `git diff` 確認
    - 確認檔案行數 <500
    - _Requirements: 1.1, 6.5, 7.1_

  - [x] 3.2 建立 `test/test_wikitext_builder_properties.py`:builder 層屬性測試
    - **Property 7: 確定性** — 相同輸入、兩種 archive state,連續兩次 `build_citation(format=WIKITEXT)` 結果完全相等 — **Validates: Requirements 6.1**
    - **Property 9: 結構化欄位不受 format 影響** — wikitext 與 markdown 的 `title`、`original_url`、`archived_url`、`archived_at`、`task_id` 相同 — **Validates: Requirements 6.3**
    - 另加 example:`archived_url` 設而 `archived_at` 為 None 時 `build_citation(format=WIKITEXT)` 拋 `ValueError` — **Validates: Requirements 6.5**
    - 每條 `@settings(max_examples=100)`
    - _Requirements: 6.1, 6.3, 6.5_

  - [x] 3.3 在 `test/test_wikitext_regression.py` 加入 Property 8 屬性測試
    - **Property 8: 既有格式逐位元組不變** — 對 markdown、bibtex、apa、plain × complete/pending,`build_citation(...).formatted` 等於直接呼叫既有私有 formatter(`_complete_markdown`、`_pending_markdown`、`_complete_bibtex`、`_pending_bibtex`、`_complete_apa`、`_pending_apa`、`_complete_plain`、`_pending_plain`)的輸出
    - **Validates: Requirements 7.1, 7.2**
    - _Requirements: 7.1, 7.2_

- [x] 4. 同步 MCP 工具定義
  - [x] 4.1 修改 `src/keeplink_mcp/mcp_server/server.py`:`archive_and_cite` 的 `format` enum 與描述
    - `enum` 改為 `["markdown", "bibtex", "apa", "plain", "wikitext"]`
    - 描述文字加入 `wikitext`(Wikipedia `{{cite web}}`,含 archive-url / archive-date / url-status),維持「只影響 `formatted`」的說明
    - 不動 handler 邏輯
    - _Requirements: 1.3_

  - [x] 4.2 更新 `test/test_citation_builder_examples.py` 的 enum 測試
    - 將 `test_format_enum_is_the_four_values` 更名為 `test_format_enum_lists_all_five_values`,期望值改為 `["markdown", "bibtex", "apa", "plain", "wikitext"]`
    - 新增斷言:`format` 的 description 含 `wikitext`
    - 新增斷言:`[f.value for f in CitationFormat]` 等於同一列表(含順序)
    - 讀回檔案確認沒有亂碼
    - _Requirements: 1.1, 1.3_

- [x] 5. 端對端測試(不改 `api/` 程式碼)
  - [x] 5.1 在 `test/test_cite_endpoint.py` 加入 `format=wikitext` 的端對端案例
    - 沿用該檔既有 fixture 與風格,實際走 `POST /api/cite`
    - 案例:(a) 快取命中(complete)時回應 `formatted` 以 `{{cite web ` 開頭、含 `|url-status=live`、含 `|archive-url=`、含 `|archive-date=` 且為 `YYYY-MM-DD`;(b) 新排入(pending)時回應 `formatted` 含 `task_id` 與 `Archiving in progress`,不含 `archive-url`;(c) 非法 `format`(例如 `Wikitext`)回 422
    - 斷言回應 body 中的實際字串內容,不 Mock 掉 `build_citation`
    - _Requirements: 1.2, 1.4, 2.1, 2.5, 5.1, 5.2_

- [x] 6. 確認既有掃描式測試涵蓋 wikitext
  - [x] 6.1 執行並檢查會以 `list(CitationFormat)` 掃描的既有測試
    - 執行 `pytest test/test_citation_builder_properties.py test/test_cite_endpoint_properties.py test/test_citation_builder_examples.py test/test_cite_endpoint.py test/test_archive_and_cite_tool.py test/test_archive_and_cite_tool_properties.py -q`
    - 以 `grep` 搜尋 `test/` 中其他寫死 `bibtex`、`apa`、`plain` 清單或「四種」的斷言,有則同步更新
    - 若某條既有屬性因 wikitext 失敗,先判斷是測試假設過窄還是實作缺陷,依事實修正,並在任務紀錄寫明原因
    - _Requirements: 6.3, 7.1_

- [x] 7. 文件更新
  - [x] 7.1 更新 `docs/` 下列出支援引用格式的專案文件
    - 以搜尋 `markdown / bibtex / apa / plain` 與 `markdown/bibtex/apa/plain` 定位所有位置(模組結構樹、功能清單、`archive_and_cite` 的 `format` 參數表)
    - 模組結構樹新增 `citation/wikitext.py` 一行;功能清單與參數表加入 `wikitext`
    - 新增簡短段落:wikitext 的五個欄位及順序、`url-status=live` 的理由(預先封存,KeepLink 不探測原網址存活)、title 缺失時輸出空 `|title=`(不捏造)、title 與 URL 的跳脫規則、`archive-date` 在快取命中時為近似值的已知限制
    - 不變更版本號
    - 寫入後讀回確認沒有亂碼
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 7.2 更新 `README.md`
    - `README.md` 目前沒有列出 `format` 可選值;在 `archive_and_cite` 說明處加入一句列出五種格式(markdown 預設、bibtex、apa、plain、wikitext)及 `wikitext` 的一行用途說明
    - 不變更版本號
    - 寫入後讀回確認沒有亂碼
    - _Requirements: 8.1, 8.2, 8.3_

- [x] 8. Checkpoint - 確認 wikitext 功能與回歸基準
  - 確認 `test/test_wikitext_*.py` 全數通過,Ensure all tests pass, ask the user if questions arise.

- [x] 9. 最終驗證
  - [x] 9.1 執行 lint、測試與版本檢查,並取得實體產物證據
    - 執行 `ruff check src/ test/`,必須零錯誤
    - 執行 `pytest test/ -q`,必須全數通過
    - 執行 `python scripts/check_version.py`,必須 exit 0 且版本號與變更前相同
    - 實體證據:以 `python -c` 呼叫 `build_citation(..., format=CitationFormat.WIKITEXT)` 印出 complete 與 pending 的實際 `formatted`,確認 complete 為非空單行、含五個參數、`archive-date` 為 `YYYY-MM-DD`;pending 不含 `archive-url`
    - 以 `git diff --stat` 與 `git diff src/keeplink_mcp/citation/builder.py` 確認 `builder.py` 僅有新增行,既有 formatter 無變動;確認 `pyproject.toml` 未變
    - 確認 `wikitext.py` ≤500 行、`builder.py` <500 行、最長函式 ≤50 行
    - _Requirements: 6.2, 6.4, 7.1, 8.3_

## Notes

- 任務 1.1 必須先於 3.1:回歸基準要在 `builder.py` 被修改前以舊程式碼產出。
- 版本號不在本 spec 內變更;發佈由使用者另外決定。
- D6、D7(`{` `}` `<` 的實體跳脫)為依文件推論的設計,建議使用者在 Special:ExpandTemplates 人工確認一次渲染結果;此為一次性人工驗證,不列為自動化任務。
- 與 Git 相關的提交由使用者另外決定,本計畫不包含 commit 任務。

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["2.2", "4.1"] },
    { "id": 2, "tasks": ["2.3", "2.5", "3.1"] },
    { "id": 3, "tasks": ["2.4", "3.2", "3.3", "4.2", "5.1", "7.1", "7.2"] },
    { "id": 4, "tasks": ["6.1"] },
    { "id": 5, "tasks": ["9.1"] }
  ]
}
```
