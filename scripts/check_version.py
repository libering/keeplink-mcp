"""Version consistency check script (Version_Check_Script).

能力 B 的核心：封裝所有版本擷取、純文字結構檢查與一致性比對邏輯，作為
pre-commit hook、CI 與未來 git skill 共用的 single source of truth（DRY）。

本檔以 interface-first 建立骨架：資料結構、常數與各層函式簽章 + docstring。
各層實作於後續任務（2.2 擷取 / 2.3 結構檢查 / 2.4 蒐集 / 2.5 判定 /
2.6 報告 + CLI）完成；骨架階段實作一律 raise NotImplementedError。

設計定位（version-gate-minimal-env-fix 修正後）：
    閘門執行環境 = 最小/乾淨環境，腳本只能用 Python 標準庫（re + pathlib），
    不得 import 專案 runtime 程式碼、不得依賴非標準庫的 TOML 解析器、不得
    import 專案的 app 或 ORM 相依與建置後端套件。已剃除兩個
    Derived_Version_Source 的執行值擷取，改以純文字結構檢查層 check_structure
    於 commit 當下守住能力 A 接線。

分層職責（SoC，互不越界）：
    擷取層 extraction  每個 Manual 來源一個純函式，輸出版本字串或 raise（fail-fast）；
                       共用 _read_source 讀檔 helper（DRY）。不含任何 Derived 執行值擷取。
    結構檢查層 structure  check_structure 以 STRUCTURE_SPECS（required/forbidden regex）
                          純文字驗證能力 A 接線未被改回寫死；不執行 app、不解析 toml。
    蒐集層 collect     依 2-tuple SOURCE_SPECS 順序逐一擷取，first-failure 即停止
                       （true fail-fast）。
    判定層 checking    輸入來源清單與 structure_errors，輸出比對結果（純函式、無 I/O）。
    報告層 report      將判定結果渲染為 Version_Report 文字（新增結構違規分節）。
    CLI 層 cli         協調各層、設定輸出編碼、決定 exit code；唯一與 stdout/stderr 互動處。

Requirements: 1.3（納管來源皆為 Manual 比對 + 結構檢查驗 Derived 接線）、
    4.1（腳本位置與僅標準庫）、5.3（來源清單集中維護）、6.1（判定併入結構檢查）。
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# 常數（Constants）
# ---------------------------------------------------------------------------

# repo 根目錄：本檔位於 <repo>/scripts/check_version.py，故上溯兩層即 repo 根。
# 用 pathlib 解析以確保跨平台（Windows 11 + Linux）路徑正確。
REPO_ROOT: Path = Path(__file__).resolve().parent.parent

# Semver_Format：語意化版本字串格式，供判定層驗證來源版本合法性。
SEMVER_PATTERN: re.Pattern[str] = re.compile(r"^\d+\.\d+\.\d+$")

# handoverbook 當前版本標記只允許出現在檔首標頭區塊；僅掃描前 N 行以避開
# 檔案後段的 Historical_Version_Reference（歷史版本記錄），防止誤判。
HEADER_SCAN_LINES: int = 10


# ---------------------------------------------------------------------------
# 資料結構（Data structures）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VersionSource:
    """單一納管版本來源的擷取結果（修正後移除 is_derived）。

    修正後所有納管來源皆為 Manual_Version_Source，不再需要 is_derived 區分
    （Derived 接線改由結構檢查層驗證，不再擷取其執行值）。

    Attributes:
        name: 顯示名，如 "src/keeplink_mcp/__init__.py"、"docs/handoverbook.md"。
        version: 從該來源擷取出的版本字串。
    """

    name: str
    version: str


@dataclass(frozen=True)
class StructureCheck:
    """能力 A 接線的單項純文字結構規則。

    以 required/forbidden regex 驗證指定檔案內容，不執行程式、不解析 toml。

    Attributes:
        name: 顯示名，如 "pyproject.toml 動態版本接線"。
        file_path: 相對 repo_root 的 posix 路徑。
        required: 必須全部命中（缺一即違規）的 regex 清單。
        forbidden: 必須全部不命中（命中任一即違規）的 regex 清單。
    """

    name: str
    file_path: str
    required: list[re.Pattern[str]]
    forbidden: list[re.Pattern[str]]


@dataclass(frozen=True)
class CheckResult:
    """一致性判定結果（新增 structure_errors），同時是 render_report 與測試斷言的資料契約。

    true fail-fast 場景下 ``sources`` 只含到失敗點為止（不累積成完整清單），
    且 ``extraction_error`` 指出首個擷取失敗的來源。

    Attributes:
        authoritative: 權威版本（來自 __init__.py 的 __version__）。
        sources: 已成功擷取的來源清單（修正後為 3 個 Manual）。
        ok: 全部來源 == 權威、皆合法 semver，且 structure_errors 為空時為 True。
        mismatches: {來源名: 版本}，僅列不等於 authoritative 者。
        invalid_format: {來源名: 版本}，僅列違反 Semver_Format 者。
        structure_errors: 能力 A 結構檢查的違規說明清單（空代表接線正確）。
        extraction_error: 首個擷取失敗來源的說明（true fail-fast）；無失敗則為 None。
    """

    authoritative: str
    sources: list[VersionSource]
    ok: bool
    mismatches: dict[str, str]
    invalid_format: dict[str, str]
    structure_errors: list[str]
    extraction_error: str | None


# ---------------------------------------------------------------------------
# 擷取層（extraction）—— 純函式，fail-fast，僅標準庫；實作於任務 2.2
# ---------------------------------------------------------------------------


def _read_source(path: Path) -> str:
    """共用讀檔 helper：明確 encoding="utf-8" 讀檔，失敗 raise ValueError。

    DRY：三個 extractor 共用此讀檔邏輯，各自負責自己的 regex 擷取。
    跨平台：以 pathlib 傳入的 path 讀取，encoding 明確指定確保行為一致。

    Args:
        path: 待讀取的檔案路徑（由呼叫端以 pathlib 組出）。

    Returns:
        檔案的完整文字內容。

    Raises:
        ValueError: 檔案無法讀取時（fail-fast，指明來源與底層原因）。
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"無法讀取版本來源 {path}：{exc}") from exc


def extract_init_version(repo_root: Path) -> str:
    """擷取權威版本 __version__（權威來源）。

    讀 ``src/keeplink_mcp/__init__.py``，以 regex 取 ``__version__ = "X.Y.Z"``。

    Args:
        repo_root: repo 根目錄。

    Returns:
        擷取出的版本字串。

    Raises:
        ValueError: 檔案無法讀取或找不到 __version__ 時（fail-fast，指明來源與原因）。
    """
    path = repo_root / "src" / "keeplink_mcp" / "__init__.py"
    content = _read_source(path)
    match = re.search(r'__version__\s*=\s*"(\d+\.\d+\.\d+)"', content)
    if match is None:
        raise ValueError(f"在 {path} 找不到 __version__ 版本宣告")
    return match.group(1)


def extract_readme_version(repo_root: Path) -> str:
    """擷取 README 頂部版本字串（Manual_Version_Source）。

    讀 ``README.md``，以 regex ``Version:\\s*(\\d+\\.\\d+\\.\\d+)`` 擷取。

    Args:
        repo_root: repo 根目錄。

    Returns:
        擷取出的版本字串。

    Raises:
        ValueError: 檔案無法讀取或找不到版本字串時（fail-fast）。
    """
    path = repo_root / "README.md"
    content = _read_source(path)
    match = re.search(r"Version:\s*(\d+\.\d+\.\d+)", content)
    if match is None:
        raise ValueError(f"在 {path} 找不到 README 版本字串")
    return match.group(1)


def extract_handoverbook_version(repo_root: Path) -> str:
    """擷取 handoverbook 當前版本標記（Handoverbook_Current_Marker，Manual 來源）。

    只掃描檔首前 ``HEADER_SCAN_LINES`` 行，以 regex ``版本：v(\\d+\\.\\d+\\.\\d+)``
    取第一個命中，避開檔案後段的 Historical_Version_Reference。

    Args:
        repo_root: repo 根目錄。

    Returns:
        當前版本標記的版本字串。

    Raises:
        ValueError: 前 N 行內找不到當前版本標記時（fail-fast，不 fallback 掃全檔、不回空值）。
    """
    path = repo_root / "docs" / "handoverbook.md"
    content = _read_source(path)
    header = "\n".join(content.splitlines()[:HEADER_SCAN_LINES])
    match = re.search(r"版本：v(\d+\.\d+\.\d+)", header)
    if match is None:
        raise ValueError(f"在 {path} 前 {HEADER_SCAN_LINES} 行找不到當前版本標記")
    return match.group(1)


# ---------------------------------------------------------------------------
# 結構檢查層（structure）—— 純文字 regex，不執行 app、不解析 toml；實作於任務 2.3
# ---------------------------------------------------------------------------

# 集中維護的結構規則清單，驗證能力 A 接線未被改回寫死。
# 兩條規則各守住一個 Derived_Version_Source 的接線：
#   1. pyproject.toml：版本須走 Hatch 動態（dynamic = ["version"]），
#      forbidden 以 re.MULTILINE 限定「行首」靜態 version 賦值，避免誤傷
#      requires-python / target-version 等其他含 "version" 字樣的鍵。
#   2. app.py：FastAPI metadata 版本須引用權威 __version__，
#      forbidden 攔截改回寫死字面量（version="x"）的回歸。
STRUCTURE_SPECS: list[StructureCheck] = [
    StructureCheck(
        name="pyproject.toml 動態版本接線",
        file_path="pyproject.toml",
        required=[re.compile(r'dynamic\s*=\s*\[\s*"version"\s*\]')],
        forbidden=[re.compile(r'^version\s*=\s*"[^"]+"', re.MULTILINE)],
    ),
    StructureCheck(
        name="app.py 引用權威版本接線",
        file_path="src/keeplink_mcp/api/app.py",
        required=[re.compile(r"version\s*=\s*__version__")],
        forbidden=[re.compile(r'version\s*=\s*"[^"]+"')],
    ),
]


def _check_one_structure(repo_root: Path, spec: StructureCheck) -> list[str]:
    """檢查單條 StructureCheck，回傳其違規說明清單（空代表通過）。

    以 pathlib 由 posix 風格 file_path 組出跨平台路徑；讀檔失敗（缺檔）即視為
    一條違規並直接回傳（不再檢查 required/forbidden）。

    Args:
        repo_root: repo 根目錄。
        spec: 待檢查的單條結構規則。

    Returns:
        此規則的違規說明清單。
    """
    path = repo_root.joinpath(*spec.file_path.split("/"))
    try:
        content = _read_source(path)
    except ValueError as exc:
        return [f"{spec.name}：{exc}"]
    errors: list[str] = []
    for pattern in spec.required:
        if pattern.search(content) is None:
            errors.append(f"{spec.name}：缺少必要接線 /{pattern.pattern}/（{spec.file_path}）")
    for pattern in spec.forbidden:
        if pattern.search(content) is not None:
            errors.append(f"{spec.name}：出現禁止樣式 /{pattern.pattern}/（{spec.file_path}）")
    return errors


def check_structure(repo_root: Path) -> list[str]:
    """純文字驗證能力 A 接線（不執行 app、不解析 toml、僅標準庫 re）。

    對每個 StructureCheck：讀取檔案內容，確認所有 required 命中、
    所有 forbidden 不命中；任一違規收集一條說明。

    Args:
        repo_root: repo 根目錄。

    Returns:
        違規說明清單；空清單代表全部接線正確。

    Note:
        檔案讀取失敗（檔案不存在）亦視為違規並收集說明後繼續處理其餘規則
        （fail-fast：能力 A 的目標檔應存在，缺檔代表接線被破壞；不靜默略過、
        不提早中止）。
    """
    errors: list[str] = []
    for spec in STRUCTURE_SPECS:
        errors.extend(_check_one_structure(repo_root, spec))
    return errors


# ---------------------------------------------------------------------------
# 蒐集層（collect）—— 2-tuple SOURCE_SPECS + true fail-fast；實作於任務 2.4
# ---------------------------------------------------------------------------

# 集中維護的來源清單，供 no-args 預設檢查使用（Req 5.3 / 8.5）。
# 每項為 2-tuple (顯示名, 擷取函式)，恰 3 項全為 Manual；順序即 fail-fast 的檢查順序。
# 首筆為權威來源 src/keeplink_mcp/__init__.py，不納入 pyproject/app metadata。
SOURCE_SPECS: list[tuple[str, Callable[[Path], str]]] = [
    ("src/keeplink_mcp/__init__.py", extract_init_version),
    ("README.md", extract_readme_version),
    ("docs/handoverbook.md", extract_handoverbook_version),
]


def collect_sources(repo_root: Path) -> tuple[list[VersionSource], str | None]:
    """依 SOURCE_SPECS 順序逐一擷取版本來源（true fail-fast）。

    第一個擷取例外即停止、不續掃、不將失敗累積成清單。

    Args:
        repo_root: repo 根目錄。

    Returns:
        (已成功擷取的來源清單, 首個錯誤說明 or None)。
    """
    collected: list[VersionSource] = []
    for name, extractor in SOURCE_SPECS:
        try:
            version = extractor(repo_root)
        except Exception as exc:  # noqa: BLE001 - fail-fast：首個失敗即停止並回報
            return collected, f"{name}：{exc}"
        collected.append(VersionSource(name=name, version=version))
    return collected, None


# ---------------------------------------------------------------------------
# 判定層（checking）—— 純函式，無 I/O，併入結構檢查；實作於任務 2.5
# ---------------------------------------------------------------------------


def is_semver(version: str) -> bool:
    """判定版本字串是否符合 Semver_Format。

    Args:
        version: 待驗證的版本字串。

    Returns:
        符合 ``^\\d+\\.\\d+\\.\\d+$`` 時為 True。
    """
    return SEMVER_PATTERN.match(version) is not None


def check_consistency(
    authoritative: str,
    sources: list[VersionSource],
    structure_errors: list[str],
) -> CheckResult:
    """比對每個來源 == authoritative 且皆為合法 semver，併入 structure_errors（純資料轉換）。

    產生 mismatches（不等於權威者）與 invalid_format（格式違規者），將傳入的
    structure_errors 放入結果；ok 僅在 無 mismatch、無 invalid_format、且
    structure_errors 為空 時為 True。純資料轉換、無 I/O，可被 property test 直接餵資料。

    Args:
        authoritative: 權威版本字串。
        sources: 已成功擷取的來源清單。
        structure_errors: 結構檢查層回傳的違規說明清單（空代表接線正確）。

    Returns:
        完整的 CheckResult。
    """
    mismatches: dict[str, str] = {}
    invalid_format: dict[str, str] = {}
    for source in sources:
        if source.version != authoritative:
            mismatches[source.name] = source.version
        if not is_semver(source.version):
            invalid_format[source.name] = source.version
    ok = (
        len(mismatches) == 0
        and len(invalid_format) == 0
        and len(structure_errors) == 0
    )
    return CheckResult(
        authoritative=authoritative,
        sources=sources,
        ok=ok,
        mismatches=mismatches,
        invalid_format=invalid_format,
        structure_errors=structure_errors,
        extraction_error=None,
    )


# ---------------------------------------------------------------------------
# 報告層（report）—— Version_Report，格式相容 + 新增結構違規分節；實作於任務 2.6
# ---------------------------------------------------------------------------


# 權威版本行統一標籤：所有情境皆以此標示權威來源，供呼叫端與測試穩定比對。
_AUTHORITATIVE_LABEL = "src/keeplink_mcp/__init__.py 的 __version__"


def _render_extraction_failure(result: CheckResult) -> str:
    """渲染擷取失敗（true fail-fast）情境的報告。

    只呈現到首個失敗點為止的資訊：失敗標題、權威版本行、首個失敗來源說明。
    """
    return "\n".join(
        [
            "❌ 版本一致性檢查失敗：來源擷取錯誤（fail-fast，已於首個失敗來源停止）",
            f"{_AUTHORITATIVE_LABEL}：{result.authoritative}",
            f"首個擷取失敗來源：{result.extraction_error}",
        ]
    )


def _render_source_lines(result: CheckResult) -> list[str]:
    """渲染受檢來源清單分節（每行一筆 {name: version}）。"""
    lines = ["受檢來源清單 {來源: 版本}："]
    lines.extend(f"  - {source.name}: {source.version}" for source in result.sources)
    return lines


def _render_mapping_section(title: str, mapping: dict[str, str]) -> list[str]:
    """渲染一個 {name: version} 對映分節；mapping 為空時回空清單（不附分節）。"""
    if not mapping:
        return []
    lines = [title]
    lines.extend(f"  - {name}: {version}" for name, version in mapping.items())
    return lines


def _render_structure_section(structure_errors: list[str]) -> list[str]:
    """渲染能力 A 接線結構違規分節；為空時回空清單（不附分節）。"""
    if not structure_errors:
        return []
    lines = ["能力 A 接線結構違規："]
    lines.extend(f"  - {error}" for error in structure_errors)
    return lines


def _render_inconsistent(result: CheckResult) -> str:
    """渲染不一致/格式違規/結構違規情境的報告（逐分節組裝）。"""
    lines = [
        "❌ 版本一致性檢查失敗：來源版本不一致",
        f"{_AUTHORITATIVE_LABEL}：{result.authoritative}",
    ]
    lines.extend(_render_source_lines(result))
    lines.extend(_render_mapping_section("與權威版本不相符的來源：", result.mismatches))
    lines.extend(
        _render_mapping_section(
            "格式違規（不符 Semver_Format）的來源：", result.invalid_format
        )
    )
    lines.extend(_render_structure_section(result.structure_errors))
    return "\n".join(lines)


def render_report(result: CheckResult) -> str:
    """將判定結果渲染為 Version_Report 文字。

    分派優先序與修正前相同：擷取失敗（fail-fast）> 一致 > 不一致/格式違規/結構違規。
    不一致情境在 ``structure_errors`` 非空時才附上「能力 A 接線結構違規」分節並逐條列出，
    為空時不附。

    Args:
        result: check_consistency 或 collect 階段產出的判定結果。

    Returns:
        供人閱讀的 Version_Report 字串。
    """
    if result.extraction_error is not None:
        return _render_extraction_failure(result)
    if result.ok:
        return f"✅ 版本一致：所有來源皆為 {result.authoritative}"
    return _render_inconsistent(result)


def render_json(result: CheckResult) -> str:
    """將 CheckResult 序列化為對外穩定的 JSON_Contract 文字（純函式，無 I/O）。

    手工組裝 payload dict 而非用 dataclasses.asdict：讓對外欄位名與順序即為穩定
    契約，與 CheckResult / VersionSource 內部實作解耦，下游 skill 可安全依賴。
    sources 以 list[VersionSource] 攤平為 {name, version} 物件、同序保留。

    json.dumps 參數：
        ensure_ascii=False —— 保留 CJK / emoji 等非 ASCII 字元（不轉義為 \\uXXXX）。
        indent=2 —— 可讀縮排。
    None 自然序列化為 null、空容器自然序列化為 {} / []。

    Args:
        result: 判定層或 collect 階段產出的 CheckResult。

    Returns:
        對外穩定的 JSON_Contract 文字。
    """
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


# ---------------------------------------------------------------------------
# CLI 層（cli）—— 唯一與 stdout/stderr、sys.exit 互動的層；實作於任務 2.6
# ---------------------------------------------------------------------------


def _evaluate(repo_root: Path) -> tuple[int, CheckResult]:
    """協調 collect -> check_structure -> check_consistency，回傳 (exit code, CheckResult)。

    擷取失敗時走 true fail-fast 單一來源結果；擷取成功後呼叫 check_structure(repo_root)，
    將結果傳入 check_consistency。渲染決策上移至 main()——本層只產生 CheckResult 與
    exit code，由呼叫端依輸出模式自行選擇 render_report（人讀）或 render_json（機器讀），
    確保兩種輸出模式共用同一 exit code 語義。

    Args:
        repo_root: repo 根目錄。

    Returns:
        (exit code, CheckResult)。
    """
    sources, extraction_error = collect_sources(repo_root)
    if extraction_error is not None:
        # true fail-fast：僅呈現首個失敗來源；sources 只到失敗點前（可能為空）。
        authoritative = sources[0].version if sources else ""
        result = CheckResult(
            authoritative=authoritative,
            sources=sources,
            ok=False,
            mismatches={},
            invalid_format={},
            structure_errors=[],
            extraction_error=extraction_error,
        )
        return 1, result
    authoritative = sources[0].version
    structure_errors = check_structure(repo_root)
    result = check_consistency(authoritative, sources, structure_errors)
    return (0 if result.ok else 1), result


def run_check(repo_root: Path | None = None) -> int:
    """協調 collect -> check_structure -> check_consistency，回傳 exit code。

    僅取 _evaluate 的 exit code（丟棄 CheckResult），語義與重構前一致。

    Args:
        repo_root: repo 根目錄；None 時預設使用 REPO_ROOT。

    Returns:
        exit code：0 表示一致；非零表示不一致 / 格式違規 / 結構違規 / 擷取失敗。
    """
    exit_code, _ = _evaluate(repo_root if repo_root is not None else REPO_ROOT)
    return exit_code


def main() -> None:
    """CLI 進入點。

    開頭將 stdout/stderr 重設為 UTF-8（Windows console 對策，兩模式共用）。
    依 ``"--json" in sys.argv[1:]`` 選擇輸出模式：

    - 有 ``--json``（Json_Flag）：以 render_json(result) 一律寫 stdout（不論 ok），
      呼叫端用 exit code 判成敗、用 JSON 讀細節。
    - 無 ``--json``（Default_Mode）：維持既有行為逐字不變——一致（exit 0）寫
      stdout；其餘寫 stderr 並 flush。

    兩模式最後皆 ``sys.exit(exit_code)``，共用同一 exit code 語義。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    exit_code, result = _evaluate(REPO_ROOT)
    if "--json" in sys.argv[1:]:
        # 機器讀模式：單一輸出管道（stdout）較易擷取，不論 ok 皆走 stdout。
        print(render_json(result))
        sys.exit(exit_code)
    report = render_report(result)
    if exit_code == 0:
        print(report)
    else:
        print(report, file=sys.stderr)
        sys.stderr.flush()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
