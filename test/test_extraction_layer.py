"""Unit / edge case tests for 擷取層（extraction layer）of check_version.py.

對應 spec version-consistency-gate 任務 2.4。兩類斷言：

1. **example（對真實檔）**：對 repo 內真實的 `docs/handoverbook.md`、
   `src/keeplink_mcp/__init__.py`、`README.md` 斷言擷取成功，且回傳值符合
   Semver_Format、與權威 `__version__` 一致。
2. **edge case（合成內容 + fail-fast）**：以 pytest `tmp_path` 建構臨時
   repo_root/docs/handoverbook.md，生成「缺當前版本標記」與「標記落在掃描窗外
   （第 100+ 行）」兩種內容，斷言 `extract_handoverbook_version` `raise ValueError`
   （對應 design 待決點 2 的 fail-fast：不 fallback 掃全檔、不回空值）。

Validates: Requirements 5.2, 5.4
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from keeplink_mcp import __version__

# repo 根目錄：本檔位於 <repo>/test/，上溯一層即 repo 根（跨平台以 pathlib 解析）。
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Semver_Format：與 check_version.py 常數一致，供 example 斷言擷取值格式合法。
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _load_check_version() -> ModuleType:
    """Load scripts/check_version.py as a module (scripts 尚未註冊為 package).

    任務 10.1 才會把 `scripts` 註冊為可 import 路徑；在此之前，以 importlib
    直接依檔案路徑載入，避免對 sys.path 佈局做假設。此為載入機制，不弱化斷言。

    Returns:
        已載入的 check_version 模組。
    """
    module_path = _REPO_ROOT / "scripts" / "check_version.py"
    spec = importlib.util.spec_from_file_location("check_version", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"無法從 {module_path} 建立 module spec")
    module = importlib.util.module_from_spec(spec)
    # 先註冊到 sys.modules 再 exec：check_version.py 用 `from __future__ import
    # annotations`（字串型別註記），dataclass 於處理時需經 cls.__module__ 於
    # sys.modules 反查該模組命名空間來解析註記，未註冊會導致 AttributeError。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_handoverbook(repo_root: Path, content: str) -> None:
    """在臨時 repo_root 下建立 docs/handoverbook.md（明確 UTF-8）。

    重現真實 repo 佈局，讓 extract_handoverbook_version(repo_root) 能依其
    內部 `repo_root / "docs" / "handoverbook.md"` 組路徑找到檔案。
    """
    docs_dir = repo_root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "handoverbook.md").write_text(content, encoding="utf-8")


class TestExtractRealSources:
    """example：對真實來源檔斷言擷取成功且與權威版本一致（Req 5.2）。"""

    def test_extract_init_version_matches_authoritative(self) -> None:
        """extract_init_version 對真實 __init__.py 擷取到權威版本。"""
        check_version = _load_check_version()
        version = check_version.extract_init_version(_REPO_ROOT)
        assert _SEMVER_RE.match(version), f"擷取值 '{version}' 不符 Semver_Format"
        assert version == __version__, (
            f"__init__.py 擷取版本 '{version}' 不等於權威 '{__version__}'"
        )

    def test_extract_readme_version_matches_authoritative(self) -> None:
        """extract_readme_version 對真實 README.md 擷取成功且與權威一致。"""
        check_version = _load_check_version()
        version = check_version.extract_readme_version(_REPO_ROOT)
        assert _SEMVER_RE.match(version), f"擷取值 '{version}' 不符 Semver_Format"
        assert version == __version__, (
            f"README.md 擷取版本 '{version}' 不等於權威 '{__version__}'"
        )

    def test_extract_handoverbook_current_marker(self) -> None:
        """extract_handoverbook_version 對真實 handoverbook 擷取到當前版本標記。"""
        check_version = _load_check_version()
        version = check_version.extract_handoverbook_version(_REPO_ROOT)
        assert _SEMVER_RE.match(version), f"擷取值 '{version}' 不符 Semver_Format"
        assert version == __version__, (
            f"handoverbook 當前標記 '{version}' 不等於權威 '{__version__}'"
        )


class TestExtractHandoverbookEdgeCases:
    """edge case：handoverbook 缺標記 / 標記在掃描窗外皆 fail-fast（Req 5.4）。"""

    def test_missing_marker_raises_value_error(self, tmp_path: Path) -> None:
        """缺當前版本標記時 SHALL raise ValueError（Req 5.4 fail-fast）。

        內容含正常標頭但完全沒有 `版本：v<semver>` 標記，斷言擷取層以
        ValueError 快速失敗，而非回傳空值或靜默通過。
        """
        check_version = _load_check_version()
        content = (
            "# KeepLink-MCP 項目交接手冊\n"
            "\n"
            "> 最後更新：2026-08-21（此行刻意缺少版本標記）\n"
            "\n"
            "---\n"
            "\n"
            "## 1. 項目概覽\n"
        )
        _write_handoverbook(tmp_path, content)

        with pytest.raises(ValueError):
            check_version.extract_handoverbook_version(tmp_path)

    def test_marker_beyond_scan_window_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        """標記落在掃描窗外（第 100+ 行）時 SHALL raise ValueError（Req 5.4）。

        前 HEADER_SCAN_LINES 行皆無標記，真正的 `版本：v1.2.0` 標記被推到
        第 100 行之後（模擬歷史記錄位置）。依 design 待決點 2，只掃描檔首前
        N 行，故窗外標記不應被擷取，擷取層應 fail-fast。
        """
        check_version = _load_check_version()
        # 前段填充遠超 HEADER_SCAN_LINES 的無標記行，把標記推出掃描窗。
        filler = "\n".join(f"這是第 {i} 行填充內容，無版本標記。" for i in range(120))
        content = (
            "# KeepLink-MCP 項目交接手冊\n"
            "\n"
            f"{filler}\n"
            "> 最後更新：2026-08-21 | 版本：v1.2.0（標記被推到掃描窗外）\n"
        )
        _write_handoverbook(tmp_path, content)

        with pytest.raises(ValueError):
            check_version.extract_handoverbook_version(tmp_path)
