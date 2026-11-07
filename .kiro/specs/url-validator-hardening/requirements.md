# Requirements Document
## Introduction
安全審查發現 `src/keeplink_mcp/mcp_server/url_validator.py` 的 `validate_url` 只檢查 scheme 為 http/https 且主機名含點,導致 `http://127.0.0.1/admin`、`http://192.168.1.1/`、`http://169.254.169.254/latest/meta-data/`、`http://user:pass@example.com/x` 與 10 萬字元 URL 全被接受。這些 URL 會存入 SQLite,再由 Worker 交給 Internet Archive 抓取,因此帶帳密或指向內網位址的 URL 會外洩到公開服務。

本功能強化 `validate_url`:拒絕帶 userinfo 的 URL、拒絕 localhost 與內網/迴路 IP 字面值、限制 URL 長度上限為 2048 字元,並維持既有行為與錯誤型別相容。`validate_url` 由 `api/routes.py`(`/api/archive`、`/api/cite`)與 `mcp_server/server.py` 共用,因此單點修改即可涵蓋所有入口。

## Glossary
- **URL_Validator**:`keeplink_mcp.mcp_server.url_validator` 模組中的 `validate_url` 及其輔助函式。
- **ValidationError**:URL_Validator 拋出的錯誤型別,為 `ValueError` 子類。
- **Normalized_URL**:對輸入執行 `strip()` 後的字串,長度與所有檢查皆以此字串為準。
- **Userinfo**:URL 中 `@` 之前的認證部分(`user:pass@host`);以 `urllib.parse.urlparse` 的 `username` 或 `password` 非 `None` 判定。
- **Restricted_Host**:主機名為 `localhost`、以 `.localhost` 結尾,或可解析為 IP 字面值且屬於 loopback、private、link-local、reserved、unspecified、multicast 任一類別(IPv4 與 IPv6)的主機。
- **IP_Literal**:可由標準庫 `ipaddress.ip_address` 直接解析的主機字串(含去除方括號的 IPv6 與 IPv4-mapped IPv6)。
- **Max_URL_Length**:Normalized_URL 的長度上限,值為 2048 字元。
- **Archive_Entry_Points**:`POST /api/archive`、`POST /api/cite` 與 MCP 工具(`archive_url`、`archive_and_cite`)。

## Requirements
### 需求 1

**使用者故事:** 作為服務維運者,我希望帶帳密的 URL 在入口就被拒絕,以免憑證被存入資料庫並送往公開的 Internet Archive。

#### 驗收條件

1. IF Normalized_URL 含有 Userinfo(`urlparse` 的 `username` 或 `password` 非 `None`), THEN THE URL_Validator SHALL 拋出 ValidationError。
2. IF Normalized_URL 的 Userinfo 只有 username 而無 password(例如 `http://user@example.com/`), THEN THE URL_Validator SHALL 拋出 ValidationError。
3. IF URL_Validator 因 Userinfo 拒絕輸入, THEN THE URL_Validator SHALL 使錯誤訊息不包含 username、password 與完整 URL 的任何片段。
4. WHEN Normalized_URL 的 `@` 僅出現在 path、query 或 fragment 中, THE URL_Validator SHALL 接受該 URL(前提是其餘檢查皆通過)。

### 需求 2

**使用者故事:** 作為服務維運者,我希望指向本機與內網的 URL 被拒絕,以免 Worker 或外部服務被誘導存取內部資源(例如雲端 metadata 端點)。

#### 驗收條件

1. IF 主機名為 `localhost`(不分大小寫,含結尾點如 `localhost.`)或以 `.localhost` 結尾, THEN THE URL_Validator SHALL 拋出 ValidationError。
2. IF 主機名為屬於 loopback、private、link-local、reserved、unspecified 或 multicast 任一類別的 IPv4 IP_Literal, THEN THE URL_Validator SHALL 拋出 ValidationError。
3. IF 主機名為屬於 loopback、private、link-local、reserved、unspecified 或 multicast 任一類別的 IPv6 IP_Literal(含 `[::1]`), THEN THE URL_Validator SHALL 拋出 ValidationError。
4. IF 主機名為 IPv4-mapped IPv6 位址(例如 `[::ffff:127.0.0.1]`)且其對應的 IPv4 位址屬於 Restricted_Host 類別, THEN THE URL_Validator SHALL 拋出 ValidationError。
5. WHEN 主機名為不屬於上述任何類別的公網 IPv4 IP_Literal(例如 `93.184.216.34`), THE URL_Validator SHALL 接受該 URL(前提是其餘檢查皆通過)。
6. THE URL_Validator SHALL 僅以字串解析判定 Restricted_Host,且不執行任何 DNS 解析或網路 I/O。
7. IF URL_Validator 因 Restricted_Host 拒絕輸入, THEN THE URL_Validator SHALL 使錯誤訊息不包含完整 URL 與主機名。

### 需求 3

**使用者故事:** 作為服務維運者,我希望 URL 長度有上限,以免超長字串佔用資料庫與記憶體。

#### 驗收條件

1. IF Normalized_URL 長度大於 2048 字元, THEN THE URL_Validator SHALL 拋出 ValidationError。
2. WHEN Normalized_URL 長度恰為 2048 字元且其餘檢查皆通過, THE URL_Validator SHALL 接受該 URL。
3. THE URL_Validator SHALL 以 `strip()` 後的字元數計算長度,使前後空白不計入。
4. IF URL_Validator 因長度拒絕輸入, THEN THE URL_Validator SHALL 使錯誤訊息只包含長度上限數值,而不包含輸入內容。

### 需求 4

**使用者故事:** 作為既有使用者,我希望合法 URL 與既有行為完全不變,以免強化後出現回歸。

#### 驗收條件

1. THE URL_Validator SHALL 在所有檢查之前先對輸入執行 `strip()`,並回傳 stripped 後的字串。
2. IF scheme 不是 `http` 或 `https`, THEN THE URL_Validator SHALL 拋出訊息為 `scheme must be http or https` 的 ValidationError。
3. IF 主機名為空或不含點, THEN THE URL_Validator SHALL 拋出訊息為 `malformed hostname` 的 ValidationError。
4. WHEN 輸入為 scheme 為 http/https、主機名含點、非 Restricted_Host、無 Userinfo 且長度不超過 Max_URL_Length 的 URL, THE URL_Validator SHALL 原樣回傳 Normalized_URL。
5. WHEN 輸入的查詢參數含 token(例如 `https://example.com/p?token=abc`), THE URL_Validator SHALL 接受該 URL。
6. THE URL_Validator SHALL 依序執行長度、scheme、主機名格式、Userinfo、Restricted_Host 檢查,並在第一個失敗處拋出 ValidationError。
7. THE URL_Validator SHALL 以 ValidationError(`ValueError` 子類)作為所有拒絕情境的唯一錯誤型別。

### 需求 5

**使用者故事:** 作為服務維運者,我希望錯誤訊息不洩漏憑證,以免帳密被寫入 API 回應與日誌。

#### 驗收條件

1. THE URL_Validator SHALL 使每一則 ValidationError 訊息為不含輸入內容的固定字串。
2. WHEN Archive_Entry_Points 收到被 URL_Validator 拒絕的 URL, THE Archive_Entry_Points SHALL 回傳不含輸入 URL 的錯誤回應(HTTP API 為 422,MCP 工具為 `Invalid URL format: <固定訊息>`)。
3. WHEN Archive_Entry_Points 收到被 URL_Validator 拒絕的 URL, THE Archive_Entry_Points SHALL 不建立任何 ArchiveTask。

### 需求 6

**使用者故事:** 作為開發者,我希望變更範圍受控且有自動化測試,以便安全合併。

#### 驗收條件

1. THE URL_Validator SHALL 僅使用 Python 標準庫(`urllib.parse`、`ipaddress`),且不新增任何第三方依賴。
2. THE 變更 SHALL 不修改 `pyproject.toml` 版本號與任何版本同步檔案。
3. THE 測試套件 SHALL 包含涵蓋 IPv4、IPv6、localhost、Userinfo 與長度邊界 2048/2049 的 example 測試。
4. THE 測試套件 SHALL 包含至少一個 hypothesis property test,每個 property 至少執行 100 次迭代。
5. WHEN 完成實作, THE 專案 SHALL 使 `ruff check src/ test/`、`pytest test/ -q` 與 `python scripts/check_version.py` 全部通過。
