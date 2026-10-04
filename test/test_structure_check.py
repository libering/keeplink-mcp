"""Example tests — 結構檢查層 check_structure 的正例與反例.

# Feature: version-consistency-gate

對應 spec version-gate-minimal-env-fix 任務 5.1。以 import
``scripts.check_version`` 的 ``check_structure`` / ``STRUCTURE_SPECS`` /
``StructureCheck`` 為受測對象（conftest.py 已把 repo root 加入 sys.path，
故可直接 ``from scripts.check_version import ...``；不重複實作擷取邏輯）。

兩類斷言：

1. **正例（真實 repo）**：``check_structure(REPO_ROOT)`` 對真實
   ``pyproject.toml``（已 ``dynamic = ["version"]``）與
   ``src/keeplink_mcp/api/app.py``（已 ``version=__version__``）回空 list。
2. **反例（合成 repo）**：以 pytest ``tmp_path`` 建合成 repo 結構（不碰真實
   檔案），分別驗證：
   - pyproject 含行首靜態 ``version = "..."`` → 回非空且含 pyproject 規則違規。
   - app.py 含字面量 ``version="..."`` → 回非空且含 app 規則違規。
   - 目標檔全缺 → 每條規則各收一條違規、不中止其餘（長度 == 規則數）。
   - 合成「正確」pyproject 另含 ``requires-python`` 與 ``target-version`` →
     仍回空（forbidden 的行首 version regex 不誤傷非版本欄位）。

Validates: Requirements 2.4, 3.4, 4.3, 9.5
"""

from __future__ import annotations

from pathlib import Path

from scripts.check_version import (
    REPO_ROOT,
    STRUCTURE_SPECS,
    StructureCheck,
    check_structure,
)

# 兩條結構規則的目標相對路徑（跨平台以 posix 風格集中為常數，寫檔時再組路徑）。
_PYPROJECT_REL = "pyproject.toml"
_APP_REL = "src/keeplink_mcp/api/app.py"

# 合成「正確」內容：pyproject 走 Hatch 動態版本、app.py 引用權威 __version__。
# 額外在 pyproject 放 requires-python 與 [tool.ruff] target-version，用以斷言
# forbidden 的行首 version regex 不誤傷這些含 "version" 字樣的非版本欄位。
_VALID_PYPROJECT = """\
[project]
name = "synthetic-pkg"
dynamic = ["version"]
requires-python = ">=3.10"
description = "synthetic"

[tool.ruff]
target-version = "py310"

[tool.hatch.version]
path = "src/synthetic/__init__.py"
"""

_VALID_APP = """\
from synthetic import __version__

app = FastAPI(title="Synthetic", version=__version__)
"""


def _write_file(root: Path, rel_path: str, content: str) -> None:
    """在 root 下依 posix 相對路徑建立檔案（含必要的父目錄），UTF-8 寫入。

    以 ``pathlib`` 組路徑確保跨平台；明確 ``encoding="utf-8"`` 與 check_structure
    的讀檔編碼一致。

    Args:
        root: 合成 repo 根目錄（pytest tmp_path）。
        rel_path: posix 風格相對路徑，如 "src/keeplink_mcp/api/app.py"。
        content: 檔案內容。
    """
    target = root.joinpath(*rel_path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


class TestCheckStructureInterface:
    """結構規則清單與資料結構的最小不變量（Req 4.1）。"""

    def test_structure_specs_entries_are_structurecheck(self) -> None:
        """STRUCTURE_SPECS SHALL 以 StructureCheck 條目集中維護且非空。

        Validates: Requirement 4.1
        """
        assert len(STRUCTURE_SPECS) >= 1, "STRUCTURE_SPECS 不應為空"
        assert all(isinstance(spec, StructureCheck) for spec in STRUCTURE_SPECS), (
            "STRUCTURE_SPECS 應全為 StructureCheck 條目"
        )

    def test_structure_specs_cover_both_capability_a_targets(self) -> None:
        """STRUCTURE_SPECS SHALL 同時涵蓋 pyproject 與 app.py 兩條能力 A 接線。"""
        covered = {spec.file_path for spec in STRUCTURE_SPECS}
        assert _PYPROJECT_REL in covered, "缺少 pyproject.toml 結構規則"
        assert _APP_REL in covered, "缺少 app.py 結構規則"


class TestCheckStructurePositive:
    """正例：真實 repo 的能力 A 接線正確 → 回空 list（Req 4.2）。"""

    def test_real_repo_returns_empty(self) -> None:
        """check_structure(REPO_ROOT) SHALL 對真實 repo 回空 list。

        真實 pyproject 已 ``dynamic = ["version"]``、app.py 已
        ``version=__version__``，故無任何結構違規。

        Validates: Requirements 2.4, 3.4, 4.2
        """
        errors = check_structure(REPO_ROOT)
        assert errors == [], (
            f"真實 repo 的能力 A 接線應無違規，實得 {errors}"
        )

    def test_synthetic_valid_repo_returns_empty(self, tmp_path: Path) -> None:
        """合成「正確」repo（含 requires-python / target-version）SHALL 回空 list。

        明確斷言 forbidden 的行首 version regex 不誤傷 ``requires-python`` 與
        ``target-version`` 等含 "version" 字樣的非版本欄位。

        Validates: Requirements 2.2, 2.4
        """
        _write_file(tmp_path, _PYPROJECT_REL, _VALID_PYPROJECT)
        _write_file(tmp_path, _APP_REL, _VALID_APP)

        errors = check_structure(tmp_path)
        assert errors == [], (
            "requires-python / target-version 不應被 forbidden regex 誤判為違規，"
            f"實得 {errors}"
        )


class TestCheckStructureNegative:
    """反例：合成違規內容 → 回非空且含對應規則違規說明（Req 2.4, 3.4, 4.3）。"""

    def test_pyproject_static_version_is_violation(self, tmp_path: Path) -> None:
        """pyproject 含行首靜態 version SHALL 回非空且含 pyproject 規則違規。

        app.py 保持正確，確保違規只來自 pyproject 規則。

        Validates: Requirement 2.4
        """
        static_pyproject = (
            '[project]\n'
            'name = "synthetic-pkg"\n'
            'version = "1.0.0"\n'
            'requires-python = ">=3.10"\n'
        )
        _write_file(tmp_path, _PYPROJECT_REL, static_pyproject)
        _write_file(tmp_path, _APP_REL, _VALID_APP)

        errors = check_structure(tmp_path)
        assert errors, "pyproject 行首靜態 version 應被判為違規"
        assert any(_PYPROJECT_REL in error for error in errors), (
            f"違規說明應指向 pyproject 規則，實得 {errors}"
        )
        assert not any(_APP_REL in error for error in errors), (
            f"app.py 正確時不應出現 app 規則違規，實得 {errors}"
        )

    def test_app_literal_version_is_violation(self, tmp_path: Path) -> None:
        """app.py 含字面量 version SHALL 回非空且含 app 規則違規。

        pyproject 保持正確，確保違規只來自 app 規則。

        Validates: Requirement 3.4
        """
        literal_app = (
            'from synthetic import __version__\n\n'
            'app = FastAPI(title="Synthetic", version="1.0.0")\n'
        )
        _write_file(tmp_path, _PYPROJECT_REL, _VALID_PYPROJECT)
        _write_file(tmp_path, _APP_REL, literal_app)

        errors = check_structure(tmp_path)
        assert errors, "app.py 字面量 version 應被判為違規"
        assert any(_APP_REL in error for error in errors), (
            f"違規說明應指向 app 規則，實得 {errors}"
        )
        assert not any(_PYPROJECT_REL in error for error in errors), (
            f"pyproject 正確時不應出現 pyproject 規則違規，實得 {errors}"
        )

    def test_missing_files_each_counted_without_abort(self, tmp_path: Path) -> None:
        """目標檔全缺 SHALL 每條規則各收一條違規、不中止其餘（Req 4.3）。

        合成 repo 不建立任何目標檔；斷言違規數恰等於 STRUCTURE_SPECS 規則數
        （每條規則各收一條違規、不因缺檔提早中止其餘），且每條規則皆被指出
        （以規則顯示名比對，避開缺檔訊息中絕對路徑的 OS 分隔符差異）。

        Validates: Requirement 4.3
        """
        errors = check_structure(tmp_path)
        assert len(errors) == len(STRUCTURE_SPECS), (
            f"缺檔情境每條規則應各收一條違規（共 {len(STRUCTURE_SPECS)} 條），"
            f"實得 {len(errors)}：{errors}"
        )
        for spec in STRUCTURE_SPECS:
            assert any(spec.name in error for error in errors), (
                f"缺檔違規清單未涵蓋規則 '{spec.name}'，實得 {errors}"
            )
