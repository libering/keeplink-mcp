"""Smoke tests for version-consistency-gate infrastructure existence.

# Feature: version-consistency-gate

這些是「基礎設施存在性 / 檔案內容」的 smoke 測試（單次、非 property test）：
斷言能力 B 的檔案都到位且彼此接線正確——檢查腳本存在且可 import、pre-commit
hook 呼叫該腳本、CI workflow 含版本檢查步驟並保留既有 lint/test/audit 與
ubuntu+windows matrix、CONTRIBUTING 有 core.hooksPath 啟用說明。

行為不隨輸入變化（純基礎設施），故以子串內容檢查即足夠，不做 property test
（見 design.md 的 Testing Strategy / Smoke 段落）。

Requirements: 4.1, 6.1, 6.5, 6.6, 7.1, 7.4, 7.5
"""

from pathlib import Path

# 以本測試檔位置上溯至 repo 根（<repo>/test/test_infrastructure_smoke.py），
# 用 pathlib 解析以確保跨平台（Windows 11 + Linux）路徑正確。
REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(relative_path: str) -> str:
    """讀取 repo 內檔案內容（明確 UTF-8，跨平台一致）。"""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_check_version_script_exists_and_importable() -> None:
    """scripts/check_version.py 存在且可 import（Req 4.1）。

    conftest 已將 repo 根註冊到 sys.path，故 `from scripts.check_version import
    run_check` 應可解析；能 import 即證明腳本存在且語法可載入。
    """
    script_path = REPO_ROOT / "scripts" / "check_version.py"
    assert script_path.is_file(), "scripts/check_version.py 應存在"

    from scripts.check_version import run_check

    assert callable(run_check), "run_check 應為可呼叫的 CLI 協調函式"


def test_pre_commit_hook_exists_and_calls_script() -> None:
    """.githooks/pre-commit 存在且呼叫檢查腳本（Req 6.1, 6.5, 6.6）。"""
    hook_path = REPO_ROOT / ".githooks" / "pre-commit"
    assert hook_path.is_file(), ".githooks/pre-commit 應存在"

    content = _read(".githooks/pre-commit")
    assert "scripts/check_version.py" in content, (
        "pre-commit hook 應呼叫 scripts/check_version.py"
    )


def test_ci_workflow_has_version_check_step() -> None:
    """.github/workflows/ci.yml 含版本檢查步驟（Req 7.1）。"""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.is_file(), ".github/workflows/ci.yml 應存在"

    content = _read(".github/workflows/ci.yml")
    assert "scripts/check_version.py" in content, (
        "CI 應呼叫 scripts/check_version.py 作為版本檢查步驟"
    )


def test_ci_matrix_includes_ubuntu_and_windows() -> None:
    """CI matrix 同時含 ubuntu-latest 與 windows-latest（Req 7.4）。"""
    content = _read(".github/workflows/ci.yml")
    assert "ubuntu-latest" in content, "CI matrix 應含 ubuntu-latest"
    assert "windows-latest" in content, "CI matrix 應含 windows-latest"


def test_ci_workflow_retains_lint_test_audit_steps() -> None:
    """CI 保留既有 lint / test / security audit 步驟（Req 7.5）。"""
    content = _read(".github/workflows/ci.yml")
    assert "ruff check" in content, "CI 應保留 lint 步驟（ruff check）"
    assert "pytest" in content, "CI 應保留 test 步驟（pytest）"
    assert "pip-audit" in content, "CI 應保留 security audit 步驟（pip-audit）"


def test_contributing_documents_hookspath_enablement() -> None:
    """CONTRIBUTING.md 含 core.hooksPath 啟用說明（Req 6.6）。"""
    contributing_path = REPO_ROOT / "CONTRIBUTING.md"
    assert contributing_path.is_file(), "CONTRIBUTING.md 應存在"

    content = _read("CONTRIBUTING.md")
    assert "core.hooksPath" in content, (
        "CONTRIBUTING.md 應含 core.hooksPath 的 pre-commit hook 啟用說明"
    )
