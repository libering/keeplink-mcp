"""Example tests for the Version_Check_CLI contract (子行程呼叫).

對應 spec version-consistency-gate 任務 5.4。

以 `subprocess` 實際啟動子行程呼叫 `scripts/check_version.py`，驗證 Req 8 的
對外 CLI 契約——呼叫端（hook/CI/skill）只依 exit code 與擷取到的 Version_Report
判斷結果，無需理解內部實作：

    Test 1（no-args / 一致情境）：於 repo 根以「無參數」呼叫，斷言 exit code 0。
        對應 Requirements 8.1（單一命令於 repo 根呼叫）、8.2（一致 → 0）、
        8.5（no-args 以內部集中來源清單完成預設檢查）。

    Test 2（不一致情境）：斷言非零 exit code 且輸出非空並含 {來源: 版本} 列表。
        對應 Requirements 8.2（不一致 → 非零）、8.3（emission：確保有輸出）。

不一致情境所採用的做法與理由（見 test_cli_inconsistent 之 docstring）：
    check_version.py 的 REPO_ROOT 由「其自身檔案位置」上溯兩層推導，且
    `extract_app_version` 以模組名 import `keeplink_mcp`（非路徑式），故單純把腳本
    複製到臨時 repo 並不能可靠製造「自足且僅某來源不一致」的情境。為維持「真正的
    子行程呼叫」又不變動真實 repo 檔案，Test 2 以子行程執行一段內嵌 Python：依
    路徑載入 check_version.py，將其集中維護的 SOURCE_SPECS 換成指向 tmp_path 內
    臨時檔的 file-based 擷取函式（其中一個刻意版本不符），再呼叫其真正的 CLI 進入
    點 main()。如此仍是「以子行程呼叫檢查腳本」，且完整行經 CLI 層（UTF-8 重設、
    Version_Report 寫 stderr、以 exit code 收斂），忠實驗證不一致契約。

安全設計：
    - 一律以 sys.executable 作為直譯器（不寫死 "python"），路徑以 pathlib 組出，
      確保 Windows 11 與 Linux 皆正確。
    - 擷取子行程輸出時明確 encoding="utf-8"、errors="replace"（Windows 上
      Version_Report 含中文與 emoji，避免解碼失敗）。
    - Test 2 只在 tmp_path 建立臨時檔，絕不改動真實 repo 檔案。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# repo 根目錄：本檔位於 <repo>/test/，上溯一層即 repo 根（跨平台以 pathlib 解析）。
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_version.py"


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """以子行程呼叫 check_version.py，回傳擷取好的結果（統一編碼處理）.

    Args:
        args: 傳給直譯器的引數（第一個通常是腳本路徑）。
        cwd: 子行程工作目錄。

    Returns:
        CompletedProcess，其 stdout/stderr 已以 UTF-8 解碼（errors="replace"）。
    """
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",  # Windows console 預設非 UTF-8，明確指定以正確擷取中文/emoji
        errors="replace",  # 保底：任何無法解碼位元組以替代字元呈現，不讓測試崩潰
    )


# ---------------------------------------------------------------------------
# Test 1 — no-args / 一致情境：exit code 0
# ---------------------------------------------------------------------------


def test_cli_no_args_consistent_exits_zero() -> None:
    """於 repo 根以無參數呼叫 CLI，一致情境應以 exit code 0 結束.

    Validates: Requirements 8.1, 8.2, 8.5
    """
    result = _run_cli([str(_SCRIPT_PATH)], cwd=_REPO_ROOT)

    # Req 8.1 / 8.5：單一命令、無額外參數即可於 repo 根完成預設檢查；
    # Req 8.2：目前 repo 所有來源一致（皆 1.2.0）→ exit code 0。
    assert result.returncode == 0, (
        f"一致情境應 exit 0，實際 {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Test 2 — 不一致情境：非零 exit code + 非空輸出且含來源清單
# ---------------------------------------------------------------------------

# 以子行程執行的內嵌 Python：依路徑載入 check_version.py，換掉其 SOURCE_SPECS 為
# 指向臨時檔的 file-based 擷取函式（其中 README.md 刻意版本不符），再呼叫真正的
# CLI 進入點 main()。占位符 __TMP__ 於執行前以實際 tmp_path 的 posix 路徑填入。
_INCONSISTENT_DRIVER = r'''
import importlib.util
import re
import sys
from pathlib import Path

TMP = Path(r"__TMP__")
SCRIPT = Path(r"__SCRIPT__")

spec = importlib.util.spec_from_file_location("check_version", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def _read_semver(path, pattern):
    """由臨時檔以 regex 擷取 semver（重現腳本 file-based 擷取層的行為）。"""
    content = path.read_text(encoding="utf-8")
    match = re.search(pattern, content)
    if match is None:
        raise ValueError(f"於 {path} 找不到版本字串")
    return match.group(1)


# 權威來源 __init__.py 為 1.2.0；README.md 刻意設為 9.9.9 製造不一致。
module.SOURCE_SPECS = [
    (
        "src/keeplink_mcp/__init__.py",
        False,
        lambda root: _read_semver(TMP / "init.py", r'__version__\s*=\s*"(\d+\.\d+\.\d+)"'),
    ),
    (
        "README.md",
        False,
        lambda root: _read_semver(TMP / "README.md", r"Version:\s*(\d+\.\d+\.\d+)"),
    ),
]

# 呼叫真正的 CLI 進入點：完整行經 UTF-8 重設、報告寫 stderr、sys.exit(exit_code)。
module.main()
'''


def test_cli_inconsistent_exits_nonzero_and_emits_report(tmp_path: Path) -> None:
    """不一致情境應以非零 exit code 結束，且輸出非空並列出各來源.

    做法：以子行程執行內嵌 driver，依路徑載入 check_version.py 後，把其集中維護的
    SOURCE_SPECS 換成指向 tmp_path 臨時檔的 file-based 擷取函式（README 版本刻意
    不符），再呼叫其真正的 CLI 進入點 main()。此舉仍是「以子行程呼叫檢查腳本」，
    並完整行經 CLI 層（Req 8.3 的 stderr emission、以 exit code 收斂），同時完全
    不觸及真實 repo 檔案。之所以不直接把腳本複製到臨時 repo：腳本的 REPO_ROOT 由
    自身檔案位置推導、且 extract_app_version 以模組名 import keeplink_mcp（非路徑
    式），複製法難以構成「自足且僅某來源不一致」的乾淨情境。

    Validates: Requirements 8.2, 8.3
    """
    # 只在 tmp_path 建立臨時來源檔，絕不改動真實 repo。
    (tmp_path / "init.py").write_text('__version__ = "1.2.0"\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("Version: 9.9.9\n", encoding="utf-8")

    # 將占位符替換為實際路徑（以 as_posix 產生對雙引號原始字串安全的路徑字面值）。
    driver = _INCONSISTENT_DRIVER.replace("__TMP__", tmp_path.as_posix()).replace(
        "__SCRIPT__", _SCRIPT_PATH.as_posix()
    )

    result = _run_cli(["-c", driver], cwd=tmp_path)

    # Req 8.2：存在不一致來源（README 9.9.9 != 權威 1.2.0）→ 非零 exit code。
    assert result.returncode != 0, (
        f"不一致情境應以非零 exit code 結束，實際 {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    # Req 8.3：不一致時 Version_Report 寫至 stderr，且必須非空（emission 義務）。
    report = result.stderr
    assert report.strip() != "", (
        f"不一致時 Version_Report 不應為空。stdout:\n{result.stdout}"
    )

    # 報告應含 {來源: 版本} 列表——至少出現受檢來源名與各自的版本值。
    assert "README.md" in report, f"報告應列出來源名 README.md，實際：\n{report}"
    assert "src/keeplink_mcp/__init__.py" in report, (
        f"報告應列出權威來源名，實際：\n{report}"
    )
    assert "9.9.9" in report, f"報告應列出不一致來源的版本值 9.9.9，實際：\n{report}"
    assert "1.2.0" in report, f"報告應列出權威版本值 1.2.0，實際：\n{report}"
