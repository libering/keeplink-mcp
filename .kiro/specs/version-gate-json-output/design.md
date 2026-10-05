# Design Document: version-gate-json-output

## Overview

本設計為 `scripts/check_version.py`（Version_Check_Script）新增一個 `--json` 輸出模式。核心策略是「gate 不動核心邏輯、只往外加一個輸出模式」：既有的擷取 / 結構檢查 / 一致性判定層完全不動，僅在最外層 CLI 做兩件事——

1. **小重構**：將 `_evaluate(repo_root)` 的回傳型別由 `tuple[int, str]`（已 render 的人讀字串）改為 `tuple[int, CheckResult]`（判定結果物件本體）。讓 `main()` 能依輸出模式自行選擇 `render_report`（人讀）或 `render_json`（機器讀）。`run_check` 對應調整為只取 exit_code。
2. **新增一個序列化純函式** `render_json(result: CheckResult) -> str` 與 `main()` 的 `--json` 旗標解析。

此設計把「判定」與「渲染」徹底解耦（SoC）：`_evaluate` 只負責產生 `CheckResult` 與 exit_code，渲染交給 `render_report` / `render_json` 兩個同層的純函式。exit_code 由單一 `_evaluate` 產生，兩種輸出模式共用，天然保證 exit code 語義一致（Req 1.4）。

### 設計原則對齊

- **純標準庫**：`json` 為標準庫，新增 `import json` 不違反「僅標準庫」約束（Req 4.2）。不新增任何非標準庫依賴。
- **對外穩定契約**：JSON 欄位名為英文，直接對應 `CheckResult` 的欄位，形成下游 skill 可依賴的穩定契約（Req 2）。
- **不破壞既有行為**：`render_report`、`collect_sources`、`check_structure`、`check_consistency` 一律不改；`main()` 無 `--json` 時的分支逐字維持原行為（Req 4.1）。
- **fail-fast 不變**：`collect_sources` 的 first-failure 行為不動（Req 4.4）。

## Architecture

### 層次與資料流（修改後）

```
collect_sources ─┐
check_structure ─┼─> check_consistency ─> CheckResult ─┐
                 │                                      │
             (fail-fast 擷取失敗時直接組 CheckResult)   │
                                                        v
                                              _evaluate(repo_root)
                                              回傳 (exit_code, CheckResult)
                                                        │
                                   ┌────────────────────┴────────────────────┐
                                   │                                         │
                        main() 無 --json                          main() 有 --json
                                   │                                         │
                        render_report(result)                     render_json(result)
                        exit 0 → stdout                            一律 → stdout
                        非零 → stderr + flush                       （不論 ok 與否）
                                   │                                         │
                                   └──────────────> sys.exit(exit_code) <────┘
```

**關鍵變更點只有三處，皆在渲染/CLI 邊界，不觸及核心邏輯：**

| 元件 | 現狀 | 修改後 |
| --- | --- | --- |
| `_evaluate` | 回 `tuple[int, str]`（render 好的字串） | 回 `tuple[int, CheckResult]`（結果物件） |
| `run_check` | `exit_code, _ = _evaluate(...)` 取字串丟棄 | 不變語義（仍只取 exit_code），對應新回傳型別 |
| `main` | 固定 `render_report` 走 stdout/stderr | 解析 `--json`；有則 `render_json`→stdout；無則維持原邏輯（`render_report`） |
| `render_json` | （不存在） | 新增純函式，`json.dumps(..., ensure_ascii=False, indent=2)` |

## Components and Interfaces

### 介面定義（Interface First）

既有資料結構不變，僅列出本功能涉及的簽章變更與新增：

```python
# 修改：回傳型別由 tuple[int, str] 改為 tuple[int, CheckResult]
def _evaluate(repo_root: Path) -> tuple[int, "CheckResult"]:
    """協調 collect -> check_structure -> check_consistency，回傳 (exit_code, CheckResult)。

    擷取失敗（true fail-fast）時組出帶 extraction_error 的 CheckResult 並回 (1, result)。
    不再於此層呼叫 render_report —— 渲染決策上移至 main()。
    """

# 不變語義：仍只取 exit_code（對應新回傳型別，丟棄 CheckResult）
def run_check(repo_root: Path | None = None) -> int: ...

# 新增：純函式，CheckResult -> JSON_Contract 文字
def render_json(result: "CheckResult") -> str:
    """將 CheckResult 序列化為對外穩定的 JSON_Contract 文字。

    使用標準庫 json.dumps(payload, ensure_ascii=False, indent=2)：
    - ensure_ascii=False：保留 CJK / emoji 等非 ASCII 字元（Req 3.1）。
    - indent=2：可讀縮排。
    欄位名為英文，直接對應 CheckResult 欄位，形成下游 skill 的穩定契約。
    """

# 修改：main() 新增 --json 分支
def main() -> None:
    """CLI 進入點。

    開頭 reconfigure stdout/stderr 為 UTF-8（既有行為，兩模式共用）。
    解析 "--json" in sys.argv[1:]：
      - 有：render_json(result) 一律寫 stdout（不論 ok），sys.exit(exit_code)。
      - 無：維持既有行為逐字不變（exit 0 → stdout；否則 → stderr + flush）。
    """
```

### JSON_Contract Schema（對外穩定契約）

```json
{
  "ok": true,
  "authoritative": "1.2.0",
  "sources": [
    {"name": "src/keeplink_mcp/__init__.py", "version": "1.2.0"},
    {"name": "README.md", "version": "1.2.0"},
    {"name": "docs/handoverbook.md", "version": "1.2.0"}
  ],
  "mismatches": {},
  "invalid_format": {},
  "structure_errors": [],
  "extraction_error": null
}
```

| 鍵 | JSON 型別 | 對應 CheckResult 欄位 | 空值表現 |
| --- | --- | --- | --- |
| `ok` | boolean | `ok` | — |
| `authoritative` | string | `authoritative` | 擷取失敗時可能為 `""` |
| `sources` | array of {name, version} | `sources`（list[VersionSource]） | `[]` |
| `mismatches` | object(string→string) | `mismatches` | `{}` |
| `invalid_format` | object(string→string) | `invalid_format` | `{}` |
| `structure_errors` | array of string | `structure_errors` | `[]` |
| `extraction_error` | string \| null | `extraction_error` | `null` |

### render_json 實作要點

`CheckResult.sources` 為 `list[VersionSource]`（frozen dataclass），不能直接丟給 `json.dumps`。於 `render_json` 內以明確的 payload dict 組裝（而非 `dataclasses.asdict`，以保證欄位名與順序即為對外契約、不隨 dataclass 內部變動）：

```python
payload = {
    "ok": result.ok,
    "authoritative": result.authoritative,
    "sources": [{"name": s.name, "version": s.version} for s in result.sources],
    "mismatches": dict(result.mismatches),
    "invalid_format": dict(result.invalid_format),
    "structure_errors": list(result.structure_errors),
    "extraction_error": result.extraction_error,
}
return json.dumps(payload, ensure_ascii=False, indent=2)
```

此法保證：欄位名為對外契約（與 dataclass 欄位名解耦）、`None` 自然序列化為 `null`、空容器自然序列化為 `{}`/`[]`。

## Data Models

本功能不新增資料結構，沿用既有：

- `CheckResult`：七欄位 frozen dataclass（見 requirements Glossary）。
- `VersionSource`：`name` / `version` 兩欄位 frozen dataclass。

`render_json` 為純轉換函式，輸入 `CheckResult`、輸出 `str`，無 I/O、無副作用，可被 property test 直接餵資料。

## Error Handling

- **擷取失敗（fail-fast）**：`_evaluate` 於 `collect_sources` 回傳 `extraction_error` 時，組出 `extraction_error` 非 None 的 `CheckResult` 並回 `(1, result)`。`--json` 模式下此 result 經 `render_json` 輸出 `extraction_error` 為字串、`ok` 為 false，呼叫端以非零 exit code + JSON 細節判讀（Req 1.3）。
- **`--json` 模式輸出導向**：不論 `ok` 與否，JSON 一律寫 stdout（呼叫端用 exit code 判成敗、用 JSON 讀細節）。這與 default 模式「非零走 stderr」刻意不同——機器讀取情境下，單一輸出管道（stdout）較易擷取。
- **編碼**：`main()` 開頭對 stdout/stderr `reconfigure(encoding="utf-8")`（既有行為，兩模式共用），搭配 `ensure_ascii=False`，確保 Windows console 下 CJK/emoji 不致 `UnicodeEncodeError`（Req 3.2）。
- **引數解析**：以 `"--json" in sys.argv[1:]` 判定，簡潔且不引入 argparse 的額外行為（如 `--help` 攔截）風險，最小化對 default 行為的影響。

## Testing Strategy

### Dual Testing Approach

- **Property test（1 條核心）**：`render_json` 的 round-trip 欄位保真，涵蓋 schema 完整性、各欄位型別/值/順序、非 ASCII 保留。
- **Example / Integration test（CLI 契約與回歸）**：以 `subprocess`（`sys.executable`、`encoding="utf-8"`）實際呼叫腳本，驗證 `--json` 的 stdout/exit code 契約與 default 模式回歸。

### 測試檔案

新增 `test/test_cli_json_output.py`：
- `test_json_mode_consistent_repo`：子行程 `--json` 於當前一致 repo → exit 0、stdout 合法 JSON、`ok==true`、`authoritative=="1.2.0"`、`sources` 含三個來源（`__init__.py` / `README.md` / `docs/handoverbook.md`）、`mismatches`/`invalid_format`/`structure_errors` 皆空、`extraction_error` 為 null。
- `test_render_json_inconsistent_unit`：單元測試 `render_json`，建含 mismatches 與 structure_errors 的不一致 CheckResult，斷言 `json.loads(render_json(r))` 各欄位正確、`ok==false`。
- `test_default_mode_regression`：不帶 `--json` 行為回歸（exit 0、人讀報告穩定標記）。
- `test_render_json_roundtrip_property`：Property 1，≥100 迭代，生成器涵蓋 CJK/emoji 與空/非空邊界。

### Property Test Configuration

- 最少 100 迭代（`@settings(max_examples=200)`，與既有 property test 一致）。
- 標註格式：`# Feature: version-gate-json-output, Property 1: render_json round-trip 欄位保真`。

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: render_json round-trip 欄位保真

*For any* 合法的 `CheckResult`（其字串欄位可含 ASCII、CJK 與 emoji，映射與陣列欄位可為空或非空，`extraction_error` 可為字串或 None），將其以 `render_json` 序列化再以標準 JSON 解析器解析所得的物件，SHALL 滿足：

- 鍵集合恰為 `{ok, authoritative, sources, mismatches, invalid_format, structure_errors, extraction_error}`；
- `ok` 為布林且等於 `result.ok`；`authoritative` 等於 `result.authoritative`；
- `sources` 為等長、同序的物件陣列，第 i 個元素的 `name`/`version` 等於 `result.sources[i]` 的對應欄位；
- `mismatches`、`invalid_format` 等於對應映射（空則為 `{}`）；
- `structure_errors` 等於對應字串陣列（空則為 `[]`）；
- `extraction_error` 於 `result.extraction_error` 為 None 時為 JSON `null`，否則為相等字串；
- 非 ASCII 字元不被破壞（round-trip 相等即蘊含）。

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.3**
