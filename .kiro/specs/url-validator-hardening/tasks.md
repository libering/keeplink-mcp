# Implementation Plan: url-validator-hardening

## Overview

在 `src/keeplink_mcp/mcp_server/url_validator.py` 先定義常數與函式介面,再實作長度、userinfo、受限主機三項檢查並接入 `validate_url`。測試放在新檔 `test/test_url_validator_hardening.py` 與 `test/test_url_validator_hardening_api.py`,不修改既有測試(影響評估見 design.md)。最後更新文件並執行品質閘門。本計畫不 bump 版本、不新增依賴。

## Tasks

- [x] 1. 強化 url_validator 模組
  - [x] 1.1 定義介面與固定訊息常數
    - 在 `url_validator.py` 加入 `MAX_URL_LENGTH = 2048` 與 `MSG_TOO_LONG`、`MSG_SCHEME`、`MSG_HOSTNAME`、`MSG_USERINFO`、`MSG_PRIVATE_HOST`
    - 宣告 `_reject_too_long`、`_reject_userinfo`、`_reject_private_host`、`_is_restricted_ip` 的簽名與 docstring
    - 既有兩則訊息文字必須與現況逐字相同
    - _Requirements: 4.2, 4.3, 5.1, 6.1_

  - [x] 1.2 實作三項檢查並接入 validate_url
    - 實作 `_reject_too_long`(strip 後長度)、`_reject_userinfo`(`username`/`password` 非 None)、`_reject_private_host`(`rstrip(".")`、localhost 與 `.localhost`、`ipaddress.ip_address`、IPv4-mapped 轉換)、`_is_restricted_ip`
    - `validate_url` 依序呼叫:strip → 長度 → scheme → 主機名 → userinfo → 受限主機;每個函式 ≤ 50 行,檔案 ≤ 500 行
    - 不做 DNS 解析、不新增依賴
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.6, 2.7, 3.1, 3.2, 3.3, 3.4, 4.1, 4.4, 4.5, 4.6, 4.7, 5.1, 6.1_

- [x] 2. 撰寫測試
  - [x] 2.1 撰寫 example 測試
    - 新建 `test/test_url_validator_hardening.py`,檔頭標記 `# Feature: url-validator-hardening`
    - 反例:IPv4(127.0.0.1、10.0.0.1、172.16.0.1、192.168.1.1、169.254.169.254、0.0.0.0、224.0.0.1、240.0.0.1)、IPv6 經 `_reject_private_host` 直接測(`::1`、`fe80::1`、`fc00::1`、`::`、`ff02::1`、`::ffff:127.0.0.1`)並確認 `validate_url("http://[::1]/")` 仍拋錯、localhost 變體(`LOCALHOST.`、`a.localhost`)、userinfo(`user:pass@`、只有 `user@`)、長度 2049
    - 正例:長度 2048、前後空白不計入、公網 IP `93.184.216.34`、`@` 出現在 path/query、query 含 `token=`、既有合法 URL 原樣回傳
    - 驗證順序(超長 + ftp 得到長度錯誤)、固定訊息不含帳密與主機名、monkeypatch `socket.getaddrinfo` 為拋錯以證明未做 DNS
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.2, 3.3, 3.4, 4.4, 4.5, 4.6, 6.3_

  - [x] 2.2 撰寫 Property 1 與 Property 2 的 property test
    - 在 `test/test_url_validator_hardening.py` 加入;`@settings(max_examples=100)`
    - 註解標記 `# Feature: url-validator-hardening, Property 1: 受限 IPv4 字面值一律被拒絕` 與 `Property 2: 公網 IPv4 字面值通過`
    - 位址由測試內獨立列出的 CIDR 清單產生,不呼叫實作的 `_is_restricted_ip`
    - **Property 1: 受限 IPv4 字面值一律被拒絕**
    - **Property 2: 公網 IPv4 字面值通過**
    - **Validates: Requirements 2.2, 2.5, 2.6, 4.4**
    - _Requirements: 2.2, 2.5, 2.6, 4.4, 6.4_

  - [x] 2.3 撰寫 Property 3、4、5 的 property test
    - 在 `test/test_url_validator_hardening.py` 加入;`@settings(max_examples=100)`
    - 註解標記 `# Feature: url-validator-hardening, Property 3: ...`、`Property 4: 長度邊界`、`Property 5: 所有拒絕訊息皆為固定字串`
    - Property 3 的 username/password 產生器排除 `/ ? # @ :` 與空白,並確認不與固定訊息文字相同
    - **Property 3: 帶 userinfo 的 URL 被拒絕且訊息不洩漏帳密**
    - **Property 4: 長度邊界**
    - **Property 5: 所有拒絕訊息皆為固定字串**
    - **Validates: Requirements 1.1, 1.2, 1.3, 3.1, 3.2, 3.3, 3.4, 4.7, 5.1**
    - _Requirements: 1.1, 1.2, 1.3, 3.1, 3.2, 3.3, 3.4, 4.7, 5.1, 6.4_

  - [x] 2.4 撰寫入口整合測試
    - 新建 `test/test_url_validator_hardening_api.py`,沿用 `test_cite_endpoint_properties.py` 的 app/DB fixture 風格
    - 對 `/api/archive` 與 `/api/cite` 各送帶帳密 URL 與內網 URL,斷言 HTTP 422、回應文字不含帳密與輸入 URL、任務總數不變
    - 對 `_handle_archive_and_cite` 送帶帳密 URL,斷言回傳訊息為 `Invalid URL format: ...` 且不含帳密、未發出 HTTP 請求
    - _Requirements: 5.2, 5.3_

- [x] 3. 檢查點 - 確認測試通過
  - 執行 `pytest test/ -q`,確認既有測試與新測試皆通過;若有既有測試因新規則失敗,依實際失敗修正並記錄於 design.md 影響評估。有疑問時詢問使用者。

- [x] 4. 更新文件
  - [x] 4.1 更新 handoverbook 第 9 節安全性摘要
    - 修改 `docs/handoverbook.md` 第 9 節(約第 431 行)的 `Input Validation` 列:改為「URL scheme + hostname 驗證;拒絕 userinfo、localhost / 內網 / 迴路 IP 字面值、長度 > 2048」
    - 新增或補充一列說明已知限制:不做 DNS 解析(不防 DNS rebinding)、非標準 IPv4 寫法(如 `127.1`)不在防護範圍
    - 以 `read_file` 讀回該段,確認繁體中文與符號無亂碼、表格欄位對齊
    - _Requirements: 6.2_

  - [x] 4.2 更新 README 的 URL 驗證敘述
    - README 第 309 至 312 行「How it works under the hood」第 1 點僅寫 "validates the URL";在其後加一句英文說明會拒絕含帳密、localhost / 內網位址與超過 2048 字元的 URL
    - 以 `read_file` 讀回確認無亂碼
    - _Requirements: 6.2_

- [x] 5. 最終驗證
  - [x] 5.1 執行品質閘門
    - 執行 `ruff check src/ test/`,必須無錯誤
    - 執行 `pytest test/ -q`,必須全數通過
    - 執行 `python scripts/check_version.py`,必須 exit 0;確認 `git diff` 不含版本號變更與依賴變更
    - _Requirements: 6.1, 6.2, 6.5_

## Notes

- 所有測試任務皆為必做,不標記為可選,因為使用者要求 example 與 property 測試皆需交付。
- 每個 property 測試對應 design.md 的單一 Property,並以 `Feature: url-validator-hardening, Property N` 註解標記。
- 本 spec 只產出文件;實作請從 tasks.md 逐項執行。

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["2.1", "2.4", "4.1", "4.2"] },
    { "id": 3, "tasks": ["2.2"] },
    { "id": 4, "tasks": ["2.3"] },
    { "id": 5, "tasks": ["5.1"] }
  ]
}
```
