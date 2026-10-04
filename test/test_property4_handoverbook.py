"""Property test for Property 4 — handoverbook 當前標記防歷史引用污染.

對應 spec version-consistency-gate 任務 2.5。

Property 4: Handoverbook 只擷取當前標記、不受歷史引用污染
    對任意 handoverbook 內容——其頂部標頭區塊含當前標記 `版本：v<X.Y.Z>`，
    其後段附加任意數量、任意值的 Historical_Version_Reference（如 `v1.1.1`、
    `升級至 1.1.0`）——`extract_handoverbook_version` 回傳的版本字串應恆等於
    頂部當前標記的版本值，與歷史引用的數量與內容無關。

Validates: Requirements 5.1, 5.3, 9.5

生成策略（見下方 strategies）：
    - 頂部：生成一個當前標記 semver，放進前 10 行內的標頭行，形如
      `> 最後更新：... | 版本：v{semver}（...）`（重現真實 handoverbook 標頭）。
    - 後段：附加隨機數量（0..20）、隨機值的歷史片段，形式 `v{other}` 或
      `升級至 {other}`，且刻意推到第 100 行之後以貼近真實檔案佈局。
    - 斷言：`extract_handoverbook_version(repo_root)` == 頂部當前標記 semver，
      無論歷史片段的數量與內容為何。

安全設計（避免測試自我污染）：
    - 歷史片段一律不含 `版本：v` 前綴，且被推到掃描窗（前 10 行）之外，
      故無論如何都不會落入擷取窗、也不會意外命中 regex。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# repo 根目錄：本檔位於 <repo>/test/，上溯一層即 repo 根（跨平台以 pathlib 解析）。
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_check_version() -> ModuleType:
    """Load scripts/check_version.py as a module (scripts 尚未註冊為 package).

    沿用 test_capability_a_structure.py 的載入模式：以 importlib 依檔案路徑載入，
    並「先註冊 sys.modules 再 exec」——check_version.py 用
    `from __future__ import annotations`（字串型別註記），dataclass 處理時需經
    `cls.__module__` 於 sys.modules 反查模組命名空間來解析註記，未註冊會 AttributeError。

    Returns:
        已載入的 check_version 模組。
    """
    module_path = _REPO_ROOT / "scripts" / "check_version.py"
    spec = importlib.util.spec_from_file_location("check_version", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"無法從 {module_path} 建立 module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_CHECK_VERSION = _load_check_version()

# ---------------------------------------------------------------------------
# 生成策略（hypothesis strategies）
# ---------------------------------------------------------------------------

# Semver_Format：以有界整數生成合法 `\d+\.\d+\.\d+`（避免過大整數拖慢生成）。
_SEMVER = st.builds(
    lambda major, minor, patch: f"{major}.{minor}.{patch}",
    st.integers(min_value=0, max_value=999),
    st.integers(min_value=0, max_value=999),
    st.integers(min_value=0, max_value=999),
)

# 歷史片段形式：`v{semver}` 或 `升級至 {semver}`——皆刻意不含 `版本：v` 前綴，
# 故即使意外落入掃描窗也不會命中擷取 regex（第二層保險）。
_HISTORICAL_FRAGMENT = st.builds(
    lambda semver, form: (
        f"- 由前版 v{semver} 起的行為調整說明"
        if form == 0
        else f"- 升級至 {semver} 的遷移注意事項"
    ),
    _SEMVER,
    st.integers(min_value=0, max_value=1),
)


def _build_handoverbook(current: str, historical: list[str]) -> str:
    """組出一份 handoverbook 內容：頂部當前標記 + 後段歷史片段.

    佈局刻意貼近真實檔案：
        - 前 10 行為受控標頭區塊，當前標記放在第 3 行（同真實檔）。
        - 歷史片段被大量填充行推到第 100 行之後（遠超 HEADER_SCAN_LINES 掃描窗）。

    Args:
        current: 頂部當前標記的 semver 值。
        historical: 附加於後段的歷史片段清單（可為空）。

    Returns:
        組合後的 handoverbook 完整內容字串。
    """
    header = [
        "# KeepLink-MCP 項目交接手冊",
        "",
        f"> 最後更新：2026-08-21 | 版本：v{current}（測試生成標頭）",
        "",
        "---",
        "",
        "## 1. 項目概覽",
        "",
        "測試用途的當前版本標頭區塊，以下為填充內容以模擬真實檔案長度。",
        "",
    ]
    # 填充行把歷史片段推到第 100 行之後（貼近真實：歷史引用最早於第 100+ 行）。
    padding = [f"填充段落第 {i} 行，無版本資訊。" for i in range(100)]
    tail = ["", "## 附錄：歷史版本記錄（Release Notes / ADR）", ""]
    tail.extend(historical)
    return "\n".join(header + padding + tail)


@pytest.fixture(scope="module")
def repo_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """提供一個 module 級暫存 repo 根，內含 docs/ 目錄。

    以 module scope 建一次目錄、每個 hypothesis 例子覆寫同一個
    docs/handoverbook.md 檔（內容隨例子變化），避免 function-scoped fixture
    於 @given 下觸發 HealthCheck，同時免除逐例建目錄的成本。
    """
    root = tmp_path_factory.mktemp("handoverbook_repo")
    (root / "docs").mkdir()
    return root


# ---------------------------------------------------------------------------
# Property 4 test
# ---------------------------------------------------------------------------


# Feature: version-consistency-gate, Property 4: Handoverbook 只擷取當前標記、不受歷史引用污染
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    current=_SEMVER,
    historical=st.lists(_HISTORICAL_FRAGMENT, min_size=0, max_size=20),
)
def test_extract_handoverbook_ignores_historical(
    repo_root: Path,
    current: str,
    historical: list[str],
) -> None:
    """extract_handoverbook_version 恆回傳頂部當前標記值，不受歷史片段污染.

    Validates: Requirements 5.1, 5.3, 9.5
    """
    handoverbook = repo_root / "docs" / "handoverbook.md"
    handoverbook.write_text(
        _build_handoverbook(current, historical), encoding="utf-8"
    )

    result = _CHECK_VERSION.extract_handoverbook_version(repo_root)

    assert result == current, (
        f"擷取結果 '{result}' 應等於頂部當前標記 '{current}'，"
        f"歷史片段數={len(historical)} 不應影響結果"
    )
