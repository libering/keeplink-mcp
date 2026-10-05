# Requirements Document

## Introduction

版本一致性閘門腳本 `scripts/check_version.py`（以下稱 Version_Check_Script）目前只提供人讀的中文 Version_Report 輸出。跨 CLI 的 git skill（Gemini / OpenCode / Kiro）若要依據檢查結果做自動化決策，必須 parse 中文自由文字，脆弱且不穩定。

本功能為 Version_Check_Script 新增一個 `--json` 輸出模式：以標準庫 `json` 序列化既有的 `CheckResult`，輸出一份欄位名為英文、對外穩定的結構化 JSON 契約（以下稱 JSON_Contract），讓下游 skill 以機器解析取得檢查細節，而非解析中文文字。

本功能的範圍嚴格限縮於「新增一個輸出模式」：不改動版本擷取、結構檢查、一致性判定等核心邏輯，且既有的無參數人讀報告行為、exit code 語義、UTF-8 輸出、fail-fast 行為必須逐字維持不變。腳本須維持僅使用 Python 標準庫（不新增任何非標準庫 import）。

## Glossary

- **Version_Check_Script**：`scripts/check_version.py`，版本一致性閘門腳本。
- **CheckResult**：既有的 frozen dataclass，承載一致性判定結果，欄位為 `authoritative`、`sources`、`ok`、`mismatches`、`invalid_format`、`structure_errors`、`extraction_error`。
- **VersionSource**：既有的 frozen dataclass，欄位為 `name`、`version`。
- **Version_Report**：既有的人讀中文報告文字，由 `render_report` 產生。
- **JSON_Contract**：`--json` 模式輸出的結構化 JSON 文字，欄位名為英文、對外穩定。
- **Render_Json_Function**：新增的純函式 `render_json(result: CheckResult) -> str`，將 CheckResult 序列化為 JSON_Contract。
- **Json_Flag**：命令列旗標 `--json`，啟用 JSON 輸出模式。
- **Default_Mode**：未提供 `--json` 時的既有無參數人讀報告模式。
- **Exit_Code**：行程結束碼；0 代表所有來源一致且無結構違規，非零代表不一致 / 格式違規 / 結構違規 / 擷取失敗。
- **Standard_Library**：Python 隨附標準庫（含 `json`、`re`、`sys`、`pathlib`、`dataclasses`、`collections.abc`）。

## Requirements

### Requirement 1

**User Story:** 作為下游 git skill 的開發者，我想以 `--json` 旗標取得結構化的版本檢查結果，以便用機器解析而非解析中文文字取得檢查細節。

#### Acceptance Criteria

1. WHEN 使用者以 `--json` 旗標執行 Version_Check_Script, THE Version_Check_Script SHALL 將 Render_Json_Function 的輸出寫至標準輸出（stdout）。
2. WHEN 使用者以 `--json` 旗標執行 Version_Check_Script 且所有來源一致, THE Version_Check_Script SHALL 以 Exit_Code 0 結束。
3. IF 使用者以 `--json` 旗標執行 Version_Check_Script 且存在不一致、格式違規、結構違規或擷取失敗, THEN THE Version_Check_Script SHALL 以非零 Exit_Code 結束，並仍將 JSON_Contract 寫至標準輸出（stdout）。
4. THE Version_Check_Script SHALL 在 `--json` 模式與 Default_Mode 使用相同的 Exit_Code 語義（0 代表一致、非零代表其餘情況）。

### Requirement 2

**User Story:** 作為下游 git skill 的開發者，我想要一份欄位名穩定的 JSON 契約，以便我的解析程式碼不因內部實作變動而破裂。

#### Acceptance Criteria

1. THE Render_Json_Function SHALL 輸出包含 `ok`、`authoritative`、`sources`、`mismatches`、`invalid_format`、`structure_errors`、`extraction_error` 七個鍵的 JSON 物件。
2. THE Render_Json_Function SHALL 將 `ok` 鍵序列化為 JSON 布林值，其值等於輸入 CheckResult 的 `ok` 欄位。
3. THE Render_Json_Function SHALL 將 `authoritative` 鍵序列化為字串，其值等於輸入 CheckResult 的 `authoritative` 欄位。
4. THE Render_Json_Function SHALL 將 `sources` 鍵序列化為物件陣列，每個元素包含 `name` 與 `version` 兩個字串鍵，順序與 CheckResult 的 `sources` 清單順序一致。
5. THE Render_Json_Function SHALL 將 `mismatches` 與 `invalid_format` 鍵各序列化為字串對字串的 JSON 物件；WHERE 對應的 CheckResult 欄位為空, THE Render_Json_Function SHALL 輸出空物件 `{}`。
6. THE Render_Json_Function SHALL 將 `structure_errors` 鍵序列化為字串陣列；WHERE 對應的 CheckResult 欄位為空, THE Render_Json_Function SHALL 輸出空陣列 `[]`。
7. THE Render_Json_Function SHALL 將 `extraction_error` 鍵序列化為字串或 JSON `null`，其值等於輸入 CheckResult 的 `extraction_error` 欄位。

### Requirement 3

**User Story:** 作為跨平台（Windows 11 與 Linux）的使用者，我想要 JSON 輸出正確處理非 ASCII 內容，以便中文來源名稱與訊息不被轉義破壞。

#### Acceptance Criteria

1. THE Render_Json_Function SHALL 以保留非 ASCII 字元（不轉義為 `\uXXXX`）的方式序列化 JSON_Contract。
2. WHEN 以 `--json` 模式輸出 JSON_Contract, THE Version_Check_Script SHALL 以 UTF-8 編碼寫出標準輸出串流。
3. THE Render_Json_Function SHALL 產生可由標準 JSON 解析器成功解析的文字（符合 round-trip：解析後各欄位值等於原 CheckResult 對應欄位）。

### Requirement 4

**User Story:** 作為維護者，我想要新增 `--json` 模式時不破壞任何既有行為，以便既有的 hook、CI 與呼叫端不受影響。

#### Acceptance Criteria

1. WHEN 使用者不帶 `--json` 旗標執行 Version_Check_Script, THE Version_Check_Script SHALL 產生與新增本功能前逐字相同的 Version_Report 輸出與相同的輸出串流配置（一致走 stdout、其餘走 stderr 並 flush）。
2. THE Version_Check_Script SHALL 僅使用 Standard_Library，不新增任何非標準庫 import。
3. THE Version_Check_Script SHALL 維持單檔不超過 500 行、單一函式不超過 50 行。
4. THE Version_Check_Script SHALL 維持既有的 fail-fast 行為（首個擷取失敗即停止，不累積成完整清單）。
