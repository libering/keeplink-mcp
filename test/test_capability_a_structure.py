"""Example tests for 能力 A — Derived_Version_Source 化的結構檢查與推導驗證.

對應 spec version-consistency-gate 任務 9。分兩類斷言：

1. **結構檢查（structural）**：直接讀 `pyproject.toml` 與 `app.py` 的內容，
   斷言能力 A 的檔案改動確實落地（不依賴 check_version.py 的擷取層實作）。
   - `pyproject.toml` 含 ``dynamic = ["version"]``、無靜態 ``version = "..."`` 行、
     ``[tool.hatch.version].path`` 指向權威 `__init__.py`、wheel 打包設定保留。
   - `app.py` ``import __version__`` 且不寫死版本字面量。
2. **接線驗證（wiring）**：呼叫 `check_structure(repo_root)` 斷言其對真實 repo
   回傳空 list，代表能力 A 的 pyproject/app 接線未被改回寫死（結構檢查層接線
   正確）。本修正剃除了 Derived 來源的執行值擷取（移除
   ``extract_pyproject_version`` / ``extract_app_version``），改以純文字結構
   檢查於 commit 當下守住能力 A，故此處由原本的 derivation-equality 斷言改述
   為呼叫 check_structure 斷言接線正確。

Validates: Requirements 2.1, 2.2, 2.4, 3.1, 3.2, 9.5
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

# repo 根目錄：本檔位於 <repo>/test/，上溯一層即 repo 根（跨平台以 pathlib 解析）。
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"
_APP_PATH = _REPO_ROOT / "src" / "keeplink_mcp" / "api" / "app.py"


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


class TestPyprojectStructure:
    """pyproject.toml 能力 A 結構斷言（Req 2.1, 2.2, 2.4）。"""

    def test_declares_dynamic_version(self) -> None:
        """`[project]` SHALL 宣告 dynamic = ["version"]（Req 2.1）。"""
        content = _PYPROJECT_PATH.read_text(encoding="utf-8")
        assert re.search(r'dynamic\s*=\s*\[\s*"version"\s*\]', content), (
            'pyproject.toml 未宣告 dynamic = ["version"]'
        )

    def test_has_no_static_version(self) -> None:
        """`[project]` SHALL 不再寫死靜態 version = "X.Y.Z"（Req 2.1）。

        僅比對行首的 ``version = "..."``（`[project]` 靜態版本），不誤傷
        ``requires-python``、``target-version`` 或 ``[tool.hatch.version]``
        區段標頭（後者不含等號賦值字串）。
        """
        content = _PYPROJECT_PATH.read_text(encoding="utf-8")
        static_version = re.search(
            r'^version\s*=\s*"[^"]+"', content, re.MULTILINE
        )
        assert static_version is None, (
            "pyproject.toml 仍存在靜態 version 賦值行，違反 dynamic 版本要求"
        )

    def test_hatch_version_path_points_to_authoritative(self) -> None:
        """`[tool.hatch.version].path` SHALL 指向權威 __init__.py（Req 2.2）。"""
        content = _PYPROJECT_PATH.read_text(encoding="utf-8")
        # 斷言 [tool.hatch.version] 區段存在且 path 指向權威來源檔。
        assert "[tool.hatch.version]" in content, (
            "pyproject.toml 缺少 [tool.hatch.version] 區段"
        )
        path_match = re.search(
            r'\[tool\.hatch\.version\][^\[]*?path\s*=\s*"([^"]+)"',
            content,
            re.DOTALL,
        )
        assert path_match is not None, "[tool.hatch.version] 未設定 path"
        # 以 posix 正規化比較，容忍分隔符差異（跨平台）。
        configured = path_match.group(1).replace("\\", "/")
        assert configured == "src/keeplink_mcp/__init__.py", (
            f"hatch version path 為 '{configured}'，應指向 "
            f"'src/keeplink_mcp/__init__.py'"
        )

    def test_wheel_packaging_config_preserved(self) -> None:
        """既有 wheel 打包設定 SHALL 保留不變（Req 2.4）。"""
        content = _PYPROJECT_PATH.read_text(encoding="utf-8")
        assert "[tool.hatch.build.targets.wheel]" in content, (
            "pyproject.toml 缺少 [tool.hatch.build.targets.wheel] 打包設定"
        )
        assert re.search(
            r'packages\s*=\s*\[\s*"src/keeplink_mcp"\s*\]', content
        ), "wheel packages 設定未保留 'src/keeplink_mcp'"

    def test_build_backend_is_hatchling(self) -> None:
        """build backend SHALL 保持為 hatchling（Req 2.4）。"""
        content = _PYPROJECT_PATH.read_text(encoding="utf-8")
        assert re.search(
            r'build-backend\s*=\s*"hatchling\.build"', content
        ), "build-backend 未保持為 hatchling.build"


class TestAppMetadataStructure:
    """app.py 能力 A 結構斷言（Req 3.1, 3.2）。"""

    def test_imports_authoritative_version(self) -> None:
        """app.py SHALL `from keeplink_mcp import __version__`（Req 3.1）。"""
        content = _APP_PATH.read_text(encoding="utf-8")
        assert re.search(
            r"from\s+keeplink_mcp\s+import\s+(?:[^\n]*,\s*)?__version__", content
        ), "app.py 未匯入權威 __version__"

    def test_app_metadata_uses_version_symbol(self) -> None:
        """FastAPI app metadata SHALL 以 __version__ 作為 version 值（Req 3.1）。"""
        content = _APP_PATH.read_text(encoding="utf-8")
        assert re.search(
            r"FastAPI\([^)]*version\s*=\s*__version__", content, re.DOTALL
        ), "app.py 的 FastAPI(...) 未以 __version__ 作為 version"

    def test_no_hardcoded_version_literal(self) -> None:
        """app.py SHALL 不寫死任何版本字面量作為 metadata version（Req 3.2）。"""
        content = _APP_PATH.read_text(encoding="utf-8")
        hardcoded = re.search(
            r'FastAPI\([^)]*version\s*=\s*"[^"]+"', content, re.DOTALL
        )
        assert hardcoded is None, (
            "app.py 的 FastAPI(...) 仍寫死版本字面量，違反 import 權威版本要求"
        )


class TestStructureWiringIntact:
    """接線驗證：check_structure 對真實 repo 回空 list（Req 9.5）。

    本修正剃除了 Derived 來源的執行值擷取（移除 ``extract_pyproject_version`` /
    ``extract_app_version``），改以純文字結構檢查層 ``check_structure`` 於 commit
    當下驗證能力 A 接線未被改回寫死。對未被破壞的真實 repo，``check_structure``
    應回傳空清單——這取代了原本的 derivation-equality 斷言，同為「能力 A 接線
    正確」把關，但不執行 app、不解析 toml，符合最小環境定位。
    """

    def test_check_structure_returns_empty_for_real_repo(self) -> None:
        """check_structure(repo_root) 對真實 repo 回傳空 list（接線正確）。

        Validates: Requirements 9.5
        """
        check_version = _load_check_version()

        errors = check_version.check_structure(_REPO_ROOT)

        assert errors == [], (
            f"check_structure 對真實 repo 應回空 list（能力 A 接線正確），"
            f"實際回傳違規：{errors}"
        )
