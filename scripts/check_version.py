"""Version consistency check script (Version_Check_Script).

能力 B 的核心：封裝所有版本擷取與一致性比對邏輯，作為 pre-commit hook、
CI 與未來 git skill 共用的 single source of truth（single source of truth / DRY）。

本檔以 interface-first 建立骨架：資料結構、常數與各層函式簽章 + docstring。
各層實作於後續任務（2 擷取 / 3 蒐集 / 4 判定 / 5 報告 + CLI）完成。

分層職責（SoC，互不越界）：
    擷取層 extraction  每個 Version_Source 一個純函式，輸出版本字串或 raise（fail-fast）。
    蒐集層 collect     依 SOURCE_SPECS 順序逐一擷取，first-failure 即停止（true fail-fast）。
    判定層 checking    輸入來源清單，輸出比對結果（純函式、無 I/O）。
    報告層 report      將判定結果渲染為 Version_Report 文字。
    CLI 層 cli         協調各層、設定輸出編碼、決定 exit code；唯一與 stdout/stderr 互動處。

Requirements: 4.1（腳本位置）、8.5（no-args 以內部集中來源清單完成預設檢查）。
"""

from __future__ import annotations

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
# 檔案後段的 Historical_Version_Reference（歷史版本記錄），防止誤判（見待決點 2）。
HEADER_SCAN_LINES: int = 10


# ---------------------------------------------------------------------------
# 資料結構（Data structures）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VersionSource:
    """單一納管版本來源的擷取結果。

    Attributes:
        name: 顯示名，如 "src/keeplink_mcp/__init__.py"、"docs/handoverbook.md"。
        version: 從該來源擷取出的版本字串。
        is_derived: True 表示 Derived_Version_Source（建置/執行期由權威值推導，
            其值定義上應等於權威值）；False 表示 Manual_Version_Source（人工維護）。
    """

    name: str
    version: str
    is_derived: bool


@dataclass(frozen=True)
class CheckResult:
    """一致性判定結果，同時是 render_report 與測試斷言的資料契約。

    true fail-fast 場景下 ``sources`` 只含到失敗點為止（不累積成完整清單），
    且 ``extraction_error`` 指出首個擷取失敗的來源。

    Attributes:
        authoritative: 權威版本（來自 __init__.py 的 __version__）。
        sources: 已成功擷取的來源清單。
        ok: 全部來源皆等於 authoritative 且皆為合法 semver 時為 True。
        mismatches: {來源名: 版本}，僅列不等於 authoritative 者。
        invalid_format: {來源名: 版本}，僅列違反 Semver_Format 者。
        extraction_error: 首個擷取失敗來源的說明（true fail-fast）；無失敗則為 None。
    """

    authoritative: str
    sources: list[VersionSource]
    ok: bool
    mismatches: dict[str, str]
    invalid_format: dict[str, str]
    extraction_error: str | None


# ---------------------------------------------------------------------------
# 擷取層（extraction）—— 純函式，fail-fast；實作於任務 2
# ---------------------------------------------------------------------------


def extract_init_version(repo_root: Path) -> str:
    """擷取權威版本 __version__（Manual/權威來源）。

    讀 ``src/keeplink_mcp/__init__.py``，以 regex 取 ``__version__ = "X.Y.Z"``。

    Args:
        repo_root: repo 根目錄。

    Returns:
        擷取出的版本字串。

    Raises:
        ValueError: 檔案無法讀取或找不到 __version__ 時（fail-fast，指明來源與原因）。
    """
    # 以 pathlib 組路徑、明確 encoding="utf-8" 讀檔，確保 Windows/Linux 行為一致。
    source_path = repo_root / "src" / "keeplink_mcp" / "__init__.py"
    try:
        content = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        # fail-fast：檔案不存在或無法讀取時明確指出來源與底層原因，不靜默回退。
        raise ValueError(f"無法讀取版本來源 {source_path}：{exc}") from exc

    # 綁定 __version__ 賦值語句，避免誤中檔內其他版本字串。
    match = re.search(r'__version__\s*=\s*"(\d+\.\d+\.\d+)"', content)
    if match is None:
        raise ValueError(f"於 {source_path} 找不到 __version__ = \"X.Y.Z\" 版本字串")
    return match.group(1)


def extract_pyproject_version(repo_root: Path) -> str:
    """擷取 pyproject 的 hatchling 推導版本（Derived_Version_Source）。

    能力 A 後 pyproject 為動態版本，其值定義上等於權威值；此層僅負責取得推導值
    （如 ``importlib.metadata.version("keeplink-mcp")`` 或 hatchling metadata API）。

    Args:
        repo_root: repo 根目錄。

    Returns:
        推導出的專案版本字串。

    Raises:
        Exception: 推導失敗時 raise（fail-fast）。
    """
    # WHY 不用 importlib.metadata.version("keeplink-mcp")：本專案以 editable 方式
    # 安裝，其 dist metadata 可能早於版本升版而過期（實測回報舊版 1.0.0），會誤報。
    # 故直接由 pyproject 的 [tool.hatch.version] 設定推導——這正是 hatchling 建置期
    # 的取值來源，能反映真實權威值而不受已安裝 dist 狀態影響。
    #
    # 優先走 hatchling metadata API（design 意圖）；若環境未安裝 hatchling，
    # 則退回「讀 [tool.hatch.version].path 指向的檔案並擷取其 __version__」，
    # 忠實重現 hatchling 會推導出的值（此為推導機制相容，非遮蓋業務錯誤的 fallback）。
    pyproject_path = repo_root / "pyproject.toml"
    try:
        pyproject_content = pyproject_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"無法讀取版本來源 {pyproject_path}：{exc}") from exc

    try:
        from hatchling.metadata.core import ProjectMetadata

        metadata = ProjectMetadata(str(repo_root), None)
        return metadata.version
    except ImportError:
        return _derive_pyproject_version_from_config(repo_root, pyproject_content)


def _derive_pyproject_version_from_config(repo_root: Path, pyproject_content: str) -> str:
    """由 pyproject 的 [tool.hatch.version].path 推導版本（hatchling 未安裝時的等價路徑）。

    讀取 ``[tool.hatch.version].path`` 指向的檔案，擷取其 ``__version__``；
    這正是 hatchling 的 ``regex`` version source 預設行為（重現而非繞過）。

    Raises:
        ValueError: 缺少 hatch version 設定、目標檔無法讀取或找不到版本字串時（fail-fast）。
    """
    import tomllib

    config = tomllib.loads(pyproject_content)
    version_path = (
        config.get("tool", {}).get("hatch", {}).get("version", {}).get("path")
    )
    if not version_path:
        raise ValueError("pyproject.toml 缺少 [tool.hatch.version].path，無法推導版本")

    # version_path 於 pyproject 以 posix 形式書寫；以 pathlib 拆解為跨平台路徑。
    target = repo_root.joinpath(*version_path.split("/"))
    try:
        target_content = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"無法讀取 hatch version 來源 {target}：{exc}") from exc

    match = re.search(r'__version__\s*=\s*"(\d+\.\d+\.\d+)"', target_content)
    if match is None:
        raise ValueError(f"於 {target} 找不到 __version__ = \"X.Y.Z\" 版本字串")
    return match.group(1)


def extract_app_version(repo_root: Path) -> str:
    """擷取 FastAPI app metadata 的版本（Derived_Version_Source）。

    import app factory 建立實例後讀 ``app.version``；能力 A 後該值來自 import __version__。

    Args:
        repo_root: repo 根目錄。

    Returns:
        app metadata 的版本字串。

    Raises:
        Exception: import 或讀取失敗時 raise（fail-fast）。
    """
    # WHY 實際呼叫 create_app 而非只讀原始碼：Req 3.3 要求驗證「建立 app 實例時」
    # metadata.version == 權威值，故須真正建構實例讀 app.version。
    # create_app 只把 session_factory 注入路由層並掛載 router，建構期不會開 DB 連線，
    # 因此傳入一個「不綁定任何 engine」的 async_sessionmaker stub 即可安全建構、
    # 不觸及真實 DB（fail-fast：任何 import/建構錯誤都會往外 raise）。
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from keeplink_mcp.api.app import create_app

    # 無 bind 的 sessionmaker：僅供建構 app 用，永不實際開啟 session，故不連 DB。
    stub_session_factory: async_sessionmaker = async_sessionmaker()
    app = create_app(stub_session_factory)
    return app.version


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
    # 以 pathlib 組路徑、明確 encoding="utf-8" 讀檔，確保跨平台一致。
    readme_path = repo_root / "README.md"
    try:
        content = readme_path.read_text(encoding="utf-8")
    except OSError as exc:
        # fail-fast：讀取失敗即明確指出來源與原因，不靜默回退。
        raise ValueError(f"無法讀取版本來源 {readme_path}：{exc}") from exc

    match = re.search(r"Version:\s*(\d+\.\d+\.\d+)", content)
    if match is None:
        raise ValueError(f"於 {readme_path} 找不到 'Version: X.Y.Z' 版本字串")
    return match.group(1)


def extract_handoverbook_version(repo_root: Path) -> str:
    """擷取 handoverbook 當前版本標記（Handoverbook_Current_Marker，Manual 來源）。

    只掃描檔首前 ``HEADER_SCAN_LINES`` 行，以 regex ``版本：v(\\d+\\.\\d+\\.\\d+)``
    取第一個命中，避開檔案後段的 Historical_Version_Reference（見待決點 2）。

    Args:
        repo_root: repo 根目錄。

    Returns:
        當前版本標記的版本字串。

    Raises:
        ValueError: 前 N 行內找不到當前版本標記時（fail-fast，不 fallback 掃全檔、不回空值）。
    """
    # 以 pathlib 組路徑、明確 encoding="utf-8" 讀檔，確保跨平台一致。
    handoverbook_path = repo_root / "docs" / "handoverbook.md"
    try:
        content = handoverbook_path.read_text(encoding="utf-8")
    except OSError as exc:
        # fail-fast：讀取失敗即明確指出來源與原因，不靜默回退。
        raise ValueError(f"無法讀取版本來源 {handoverbook_path}：{exc}") from exc

    # 只取檔首前 N 行組成掃描窗：當前標記固定在頂部標頭區塊，歷史引用最早於
    # 第 100 行後，故窗外的 Historical_Version_Reference 天然被排除（見待決點 2）。
    header = "\n".join(content.splitlines()[:HEADER_SCAN_LINES])

    # regex 綁定「版本：v」前綴，歷史引用缺此前綴，為第二層防誤判；取首個命中。
    match = re.search(r"版本：v(\d+\.\d+\.\d+)", header)
    if match is None:
        raise ValueError(
            f"於 {handoverbook_path} 前 {HEADER_SCAN_LINES} 行找不到當前版本標記 "
            f"'版本：vX.Y.Z'（handoverbook 當前版本標記缺失）"
        )
    return match.group(1)


# ---------------------------------------------------------------------------
# 蒐集層（collect）—— true fail-fast；SOURCE_SPECS 與實作於任務 3
# ---------------------------------------------------------------------------

# 集中維護的來源清單，供 no-args 預設檢查使用（Req 8.5）。
# 每項為 (顯示名, is_derived, 擷取函式)；順序即 fail-fast 的檢查順序（Req 4.6）。
# 順序：權威來源（__init__.py）置首，其後 Derived，再 Manual，與 design 版本流向一致。
SOURCE_SPECS: list[tuple[str, bool, Callable[[Path], str]]] = [
    ("src/keeplink_mcp/__init__.py", False, extract_init_version),  # 權威
    ("pyproject.toml", True, extract_pyproject_version),  # Derived（hatchling 推導）
    ("FastAPI app metadata", True, extract_app_version),  # Derived（import __version__）
    ("README.md", False, extract_readme_version),  # Manual
    ("docs/handoverbook.md", False, extract_handoverbook_version),  # Manual
]


def collect_sources(repo_root: Path) -> tuple[list[VersionSource], str | None]:
    """依 SOURCE_SPECS 順序逐一擷取版本來源（true fail-fast）。

    第一個擷取例外即停止、不續掃、不將失敗累積成清單（Req 4.6）。

    Args:
        repo_root: repo 根目錄。

    Returns:
        (已成功擷取的來源清單, 首個錯誤說明 or None)。
    """
    collected: list[VersionSource] = []
    for name, is_derived, extractor in SOURCE_SPECS:
        try:
            version = extractor(repo_root)
        except Exception as exc:
            # true fail-fast：首個擷取例外即停止，回傳「已成功來源」與首個錯誤說明；
            # return 直接跳出迴圈，後續 extractor 從不被呼叫（不續掃、不累積失敗清單）。
            return collected, f"{name}：{exc}"
        collected.append(VersionSource(name=name, version=version, is_derived=is_derived))
    return collected, None


# ---------------------------------------------------------------------------
# 判定層（checking）—— 純函式，無 I/O；實作於任務 4
# ---------------------------------------------------------------------------


def is_semver(version: str) -> bool:
    """判定版本字串是否符合 Semver_Format。

    Args:
        version: 待驗證的版本字串。

    Returns:
        符合 ``^\\d+\\.\\d+\\.\\d+$`` 時為 True。
    """
    return SEMVER_PATTERN.match(version) is not None


def check_consistency(authoritative: str, sources: list[VersionSource]) -> CheckResult:
    """比對每個來源是否等於權威版本且皆為合法 semver（純資料轉換）。

    產生 mismatches（不等於權威者）與 invalid_format（格式違規者），並設定 ok。

    Args:
        authoritative: 權威版本字串。
        sources: 已成功擷取的來源清單。

    Returns:
        完整的 CheckResult。
    """
    # 純資料轉換：逐一分類每個來源。一個來源可能同時落入兩類
    # （例如 "abc" 既非 semver、又不等於權威值），此為刻意設計。
    mismatches: dict[str, str] = {}
    invalid_format: dict[str, str] = {}
    for source in sources:
        if source.version != authoritative:
            mismatches[source.name] = source.version
        if not is_semver(source.version):
            invalid_format[source.name] = source.version

    # ok 僅在完全無不一致且無格式違規時為 True（Req 4.3）；
    # 任一違規（Req 4.4 不相等 / Req 4.5 格式違規）即為 False。
    ok = len(mismatches) == 0 and len(invalid_format) == 0
    return CheckResult(
        authoritative=authoritative,
        sources=sources,
        ok=ok,
        mismatches=mismatches,
        invalid_format=invalid_format,
        extraction_error=None,  # 判定層無擷取行為，恆為 None
    )


# ---------------------------------------------------------------------------
# 報告層（report）—— 實作於任務 5
# ---------------------------------------------------------------------------


# render_report 各分支的共用標籤：權威版本來源的顯示名，統一措辭避免各分支 drift。
_AUTHORITATIVE_LABEL = "src/keeplink_mcp/__init__.py 的 __version__"


def _render_extraction_error(result: CheckResult) -> str:
    """渲染 true fail-fast 報告：僅指出首個擷取失敗的來源（Req 4.6）。

    WHY 只列單一來源：collect 於首個擷取例外即停止、不續掃，故報告不應暗示
    後續來源已被檢查；只點名該失敗來源方符合 true fail-fast 語意。
    """
    return (
        "❌ 版本一致性檢查失敗：來源擷取錯誤（fail-fast，已於首個失敗來源停止）\n"
        f"權威版本（{_AUTHORITATIVE_LABEL}）：{result.authoritative}\n"
        f"首個擷取失敗來源：{result.extraction_error}"
    )


def _render_ok(result: CheckResult) -> str:
    """渲染一致情境的簡短 OK 訊息（含權威版本以利閱讀者確認）。"""
    return f"✅ 版本一致：所有來源皆為 {result.authoritative}"


def _render_source_lines(sources: list[VersionSource]) -> str:
    """渲染每個受檢來源的 {來源: 版本} 列表（Req 4.4）。"""
    return "\n".join(f"  - {src.name}: {src.version}" for src in sources)


def _render_mapping_section(title: str, mapping: dict[str, str]) -> str:
    """渲染 mismatches / invalid_format 之類的 {來源: 版本} 分節；空則回空字串。"""
    if not mapping:
        return ""
    lines = "\n".join(f"  - {name}: {version}" for name, version in mapping.items())
    return f"{title}\n{lines}"


def _render_inconsistent(result: CheckResult) -> str:
    """渲染不一致情境：全來源清單 + 不相符分節（Req 4.4）+ 格式違規分節（Req 4.5）。"""
    parts = [
        "❌ 版本一致性檢查失敗：來源版本不一致",
        f"權威版本（{_AUTHORITATIVE_LABEL}）：{result.authoritative}",
        "受檢來源清單 {來源: 版本}：",
        _render_source_lines(result.sources),
    ]
    # 僅在有內容時附上對應分節，避免空節干擾閱讀。
    mismatch_section = _render_mapping_section(
        "與權威版本不相符的來源：", result.mismatches
    )
    invalid_section = _render_mapping_section(
        "格式違規（不符 Semver_Format）的來源：", result.invalid_format
    )
    parts.extend(section for section in (mismatch_section, invalid_section) if section)
    return "\n".join(parts)


def render_report(result: CheckResult) -> str:
    """將判定結果渲染為 Version_Report 文字。

    不一致時含每個受檢來源的 {來源: 版本} 列表、格式違規來源，或首個擷取失敗來源；
    一致時回簡短 OK 訊息。

    Args:
        result: check_consistency 或 collect 階段產出的判定結果。

    Returns:
        供人閱讀的 Version_Report 字串。
    """
    # 分派順序即優先序：擷取失敗（fail-fast）> 一致 > 不一致/格式違規。
    # 擷取失敗優先，因 collect 於首個失敗即停止、sources 不完整，只能走單一來源報告。
    if result.extraction_error is not None:
        return _render_extraction_error(result)
    if result.ok:
        return _render_ok(result)
    return _render_inconsistent(result)


# ---------------------------------------------------------------------------
# CLI 層（cli）—— 唯一與 stdout/stderr、sys.exit 互動的層；實作於任務 5
# ---------------------------------------------------------------------------


def _evaluate(repo_root: Path) -> tuple[int, str]:
    """協調 collect -> check -> render，回傳 (exit code, Version_Report 文字)。

    WHY 抽出此內部 helper：run_check 的公開契約是 ``-> int``（供 hook/CI 依 exit
    code 判斷），但 main 需要同一份判定所產生的 Version_Report 才能輸出。若各自
    重跑 collect/check 會有重算與 drift 風險，故將「協調 + 產報告」集中於此，
    run_check 只取 exit code、main 取 (exit code, 報告)，維持 DRY 與單一真相。

    擷取失敗（true fail-fast）時 authoritative 取已成功來源的首筆（即權威來源
    __init__.py，若其擷取成功）；若失敗發生在讀取權威來源前則以空字串表示，報告
    仍會如實指出首個擷取失敗來源。
    """
    sources, extraction_error = collect_sources(repo_root)

    if extraction_error is not None:
        # collect 於首個失敗即停止，sources 只含到失敗點為止；authoritative 取
        # 首筆成功來源（權威來源置於 SOURCE_SPECS 之首）或空字串。
        authoritative = sources[0].version if sources else ""
        result = CheckResult(
            authoritative=authoritative,
            sources=sources,
            ok=False,
            mismatches={},
            invalid_format={},
            extraction_error=extraction_error,
        )
        return 1, render_report(result)

    # 權威來源恆為 SOURCE_SPECS 首筆（src/keeplink_mcp/__init__.py 的 __version__）。
    authoritative = sources[0].version
    result = check_consistency(authoritative, sources)
    exit_code = 0 if result.ok else 1
    return exit_code, render_report(result)


def run_check(repo_root: Path | None = None) -> int:
    """協調 collect -> check -> render，回傳 exit code。

    Args:
        repo_root: repo 根目錄；None 時預設使用 REPO_ROOT。

    Returns:
        exit code：0 表示一致；非零表示不一致 / 格式違規 / 擷取失敗。
    """
    exit_code, _report = _evaluate(repo_root if repo_root is not None else REPO_ROOT)
    return exit_code


def main() -> None:
    """CLI 進入點。

    開頭將 stdout/stderr 重設為 UTF-8（Windows console 對策），no-args 走
    SOURCE_SPECS 預設檢查，輸出 Version_Report 後 ``sys.exit(run_check())``。
    """
    # Req 8.4：Version_Report 含中文與 emoji，Windows console 預設非 UTF-8
    # （cp950/cp1252）會導致 UnicodeEncodeError 而遺失內容。開頭即把兩串流重設
    # 為 UTF-8；以 hasattr 防護非可重設串流（如測試以 StringIO 取代時無 reconfigure）。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    # no-args 預設檢查（Req 8.5）：以內部集中維護的 SOURCE_SPECS 走預設流程。
    exit_code, report = _evaluate(REPO_ROOT)

    if exit_code == 0:
        # 一致情境：簡短寫 stdout。
        print(report)
    else:
        # 不一致 / 格式違規 / 擷取失敗（Req 8.3）：把 Version_Report 寫 stderr，
        # 並 flush() 確保呼叫端（hook/CI/skill）可靠擷取、不因緩衝遺失（Req 8.4）。
        print(report, file=sys.stderr)
        sys.stderr.flush()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
