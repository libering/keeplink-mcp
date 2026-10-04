"""Property test for Property 1 — 一致性判定與不一致報告.

對應 spec version-gate-minimal-env-fix 任務 4.2。

Property 1: 一致性判定與不一致報告
    對任意權威版本字串與一組納管來源版本：當每個來源版本皆等於權威版本且皆為
    合法 semver 時，`check_consistency` 的結果 `ok` 應為 `True`；只要有任一來源
    版本不等於權威版本，`ok` 應為 `False`，且對該結果產生的 Version_Report 應
    列出每個受檢來源與其版本值（`{來源: 版本}`）。

Validates: Requirements 4.2, 4.3, 4.4, 9.3, 9.4

生成策略（兩個分支，各自 ≥100 迭代）：
    1. 一致分支（consistent arm）：抽一個合法 semver V，建 N 個全部帶版本 V 的
       來源，authoritative = V。斷言 check_consistency(...).ok 為 True，且
       mismatches / invalid_format 皆為空。
    2. 不一致分支（inconsistent arm）：抽合法 semver V 供 authoritative 與所有
       來源使用；再挑一個來源索引把其版本改為「不同的」合法 semver V2（V2 != V）。
       斷言 result.ok 為 False、該來源在 mismatches；接著呼叫 render_report(result)
       並斷言報告文字列出每個受檢來源與其版本值（每個 source.name 與 source.version
       皆出現於報告中）——此驗證 Req 4.4 / 9.4 的 {來源: 版本} 列表。

安全設計：
    - 受測對象 `check_consistency` 與 `render_report` 皆為純函式、無 I/O，直接餵
      合成 VersionSource 即可，不觸及真實檔案系統。
    - 沿用既有測試（test_property2_semver.py / test_capability_a_structure.py）的
      importlib 載入模式：先註冊 sys.modules 再 exec，讓 dataclass 能解析字串型別註記。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# repo 根目錄：本檔位於 <repo>/test/，上溯一層即 repo 根（跨平台以 pathlib 解析）。
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_check_version() -> ModuleType:
    """Load scripts/check_version.py as a module (scripts 尚未註冊為 package).

    沿用 test_property2_semver.py / test_capability_a_structure.py 的載入模式：
    以 importlib 依檔案路徑載入，並「先註冊 sys.modules 再 exec」——check_version.py
    用 `from __future__ import annotations`（字串型別註記），dataclass 處理時需經
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


# 於 module import 時載入一次並重複使用（判定/報告層為純函式，可安全共用模組實例）。
_CHECK_VERSION = _load_check_version()


# ---------------------------------------------------------------------------
# 生成策略（strategies）
# ---------------------------------------------------------------------------

# 合法 semver：三段有界非負整數以 "." 相連，符合 SEMVER_PATTERN `^\d+\.\d+\.\d+$`。
# 以有界整數（0..999）確保生成值恆為合法 semver，聚焦於「相等/不相等」判定本身。
_valid_semver = st.builds(
    lambda a, b, c: f"{a}.{b}.{c}",
    st.integers(min_value=0, max_value=999),
    st.integers(min_value=0, max_value=999),
    st.integers(min_value=0, max_value=999),
)


# ---------------------------------------------------------------------------
# Property 1 — 一致分支（consistent arm）
# ---------------------------------------------------------------------------


# Feature: version-consistency-gate, Property 1: 一致性判定與不一致報告
@settings(max_examples=150)
@given(data=st.data())
def test_all_sources_equal_authoritative_is_ok(data: st.DataObject) -> None:
    """所有來源版本皆 == 權威且皆合法 semver 時，ok 為 True 且無違規.

    Validates: Requirements 4.2, 4.3
    """
    # 抽一個合法 semver 作為權威版本，並套用到所有來源（一致集）。
    version = data.draw(_valid_semver, label="version")
    total = data.draw(st.integers(min_value=1, max_value=8), label="total_sources")

    sources = [
        _CHECK_VERSION.VersionSource(name=f"source_{i}", version=version)
        for i in range(total)
    ]

    result = _CHECK_VERSION.check_consistency(version, sources, [])

    # 全部相等且皆合法 semver → 判定通過，且不相符 / 格式違規清單皆為空（Req 4.3）。
    assert result.ok is True, (
        f"所有來源皆為 {version!r} 時 ok 應為 True，實際 {result.ok}"
    )
    assert result.mismatches == {}, (
        f"一致集不應有 mismatches，實際 {result.mismatches}"
    )
    assert result.invalid_format == {}, (
        f"合法 semver 不應有 invalid_format，實際 {result.invalid_format}"
    )


# ---------------------------------------------------------------------------
# Property 1 — 不一致分支（inconsistent arm）
# ---------------------------------------------------------------------------


# Feature: version-consistency-gate, Property 1: 一致性判定與不一致報告
@settings(max_examples=150)
@given(data=st.data())
def test_one_diverging_source_fails_and_report_lists_all(
    data: st.DataObject,
) -> None:
    """任一來源版本 != 權威時 ok 為 False，該來源入 mismatches，報告列出所有來源與值.

    Validates: Requirements 4.4, 9.3, 9.4
    """
    # 權威版本與所有來源共用的合法 semver V。
    version = data.draw(_valid_semver, label="version")

    # 挑一個「不同的」合法 semver V2（V2 != V），確保製造出真正的不一致。
    diverging = data.draw(_valid_semver, label="diverging")
    assume(diverging != version)

    # 生成來源總數 N（1..8）與被改動的來源索引 bad_index ∈ [0, N-1]。
    total = data.draw(st.integers(min_value=1, max_value=8), label="total_sources")
    bad_index = data.draw(
        st.integers(min_value=0, max_value=total - 1), label="bad_index"
    )

    # 組出來源清單：bad_index 位置改為 V2（不同 semver），其餘位置維持 V（一致）。
    sources = []
    for i in range(total):
        src_version = diverging if i == bad_index else version
        sources.append(
            _CHECK_VERSION.VersionSource(name=f"source_{i}", version=src_version)
        )

    result = _CHECK_VERSION.check_consistency(version, sources, [])

    # (1) 有來源不等於權威版本 → 整體判定不通過（Req 4.4）。
    assert result.ok is False, (
        f"注入不同 semver {diverging!r}（權威 {version!r}）後 ok 應為 False，"
        f"實際 {result.ok}"
    )

    # (2) 該不一致來源應出現在 mismatches，且對映到其（不同的）版本值。
    bad_name = f"source_{bad_index}"
    assert bad_name in result.mismatches, (
        f"mismatches 應包含不一致來源 {bad_name}，實際鍵={list(result.mismatches)}"
    )
    assert result.mismatches[bad_name] == diverging, (
        f"mismatches[{bad_name!r}] 應為 {diverging!r}，"
        f"實際 {result.mismatches[bad_name]!r}"
    )

    # (3) render_report 應列出每個受檢來源與其版本值（Req 4.4 / 9.4 的 {來源: 版本}）。
    report = _CHECK_VERSION.render_report(result)
    for source in sources:
        assert source.name in report, (
            f"Version_Report 應列出來源名 {source.name!r}，實際報告：\n{report}"
        )
        assert source.version in report, (
            f"Version_Report 應列出來源 {source.name!r} 的版本值 "
            f"{source.version!r}，實際報告：\n{report}"
        )
