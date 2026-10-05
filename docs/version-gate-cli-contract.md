# Version Gate CLI 契約規格 + 跨 session 交接

> 本文件規格化 `scripts/check_version.py`（版本一致性閘門）的對外 CLI 契約，
> 供跨 CLI 的 git skill（Gemini / OpenCode / Kiro）作為「版本檢查」步驟的穩定依賴。
> 同時作為「版本監測標準化」的跨 session 交接文件。

## 1. 這個閘門是什麼

`scripts/check_version.py` 檢查 KeepLink 的版本在多個來源一致，並確認「能力 A 接線」未被改回寫死。它是 pre-commit hook 與 CI 共用的單一檢查腳本，純標準庫、最小環境可跑（Python 3.10+ 乾淨環境，不需安裝專案 runtime）。

**納管的 3 個 Manual 來源**（版本須彼此一致）：
- `src/keeplink_mcp/__init__.py` 的 `__version__`（權威來源）
- `README.md` 頂部版本字串
- `docs/handoverbook.md` 頂部當前版本標記

**能力 A 接線的結構檢查**（純文字 regex，不執行 app、不解析 toml）：
- `pyproject.toml` 須用 `dynamic = ["version"]`（禁行首靜態 `version=`）
- `src/keeplink_mcp/api/app.py` 須用 `version=__version__`（禁字面量）

## 2. CLI 契約（skill 依賴這個）

### 呼叫方式
- 人讀模式：`python scripts/check_version.py`
- 機器讀模式：`python scripts/check_version.py --json`

兩模式共用相同的 exit code 語義。

### Exit code
| code | 意義 |
|------|------|
| `0`  | 一致（所有來源 == 權威、皆合法 semver、接線完好） |
| 非零 | 不一致 / 格式違規 / 結構違規 / 擷取失敗 |

### 人讀模式輸出路由
- exit 0：Version_Report 寫 stdout
- 非零：Version_Report 寫 stderr 並 flush

### `--json` 模式輸出路由
- 不論 exit code，JSON 一律寫 stdout（呼叫端用 exit code 判成敗、用 JSON 讀細節）

### `--json` 的穩定 JSON schema（對外契約，欄位名固定）
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
欄位	型別	意義
ok	boolean	全部通過時 true
authoritative	string	權威版本（擷取失敗時可能為 ""）
sources	array of {name, version}	已成功擷取的來源，同序
mismatches	object(string→string)	不等於權威的來源（空則 {}）
invalid_format	object(string→string)	違反 semver 格式的來源（空則 {}）
structure_errors	array of string	能力 A 接線違規說明（空則 []）
extraction_error	string | null	首個擷取失敗來源的說明（無則 null）
編碼保證
--json 用 ensure_ascii=False（保留中文），main() 開頭 reconfigure stdout/stderr 為 UTF-8（Windows console 安全）。

穩定性保證
JSON 的 7 個英文欄位名是對外契約，不隨內部 dataclass 重構變動。skill 可安全依賴。

3. skill 如何消費（版本檢查步驟）
git skill 在 push / release 前做版本把關的建議接法：


exit_code = run("python scripts/check_version.py --json")  # 於 repo 根
payload = json.parse(stdout)
if exit_code != 0:
    # 版本漂移/接線壞/擷取失敗 → 擋下 push/release
    # payload.mismatches / structure_errors / extraction_error 給出細節
    STOP, 呈現 payload 給使用者
exit code 判成敗、JSON 讀細節。不需要 parse 人讀的中文報告。

4. 跨 session 交接：版本監測標準化
已完成（KeepLink repo，已進 main）
版本一致性閘門（spec: version-consistency-gate）：能力 A（pyproject 動態版本 + app.py import __version__）+ 能力 B（check_version.py + pre-commit hook + CI）
最小環境修正（spec: version-gate-minimal-env-fix）：剃除 Derived 執行值擷取、改純文字 check_structure
--json 機器契約（spec: version-gate-json-output）：本文件規格化的對象
CI 四 job 綠（ubuntu/windows × Python 3.10/3.13）
下游工作（在 skill manager session 做，不在 KeepLink）
目標：Y 方案——三個 CLI 的 git skill 各留各自格式，但對齊同一套共同規範；共同規範放 skill manager 資料夾共用。

相關 session：sess_5ef1fd59-6dda-4fa1-af56-04701e9ee9e0
要對齊的 skill：Gemini github-push / github-release / github-project-skill、OpenCode git-commit-push、Kiro（需從零建一支 commit/push skill）
已完成的 skill 治理：歸檔 git-workflow-skill、github-release-skill（移到 ~/.gemini/config/skills/_archived/）；修掉 OpenCode git-commit-push 的寫死作者個資（改用本機 git config）
共同規範建議 8 條：commit 前自審 / 選擇性 add / 作者用 git config / secret 掃描 / Conventional Commits / 禁裸 force / 版本一致性檢查（呼叫本文件的 --json 契約）/ description 只寫「Use when」不摘要 workflow
未決項：SCGP-AI 兩支 description 摘要 workflow 的反模式待修；git-commit-push 待補 secret 掃描