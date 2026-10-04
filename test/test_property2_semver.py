"""Property test for Property 2 — Semver 格式違規偵測.

對應 spec version-consistency-gate 任務 4.3。

Property 2: Semver 格式違規偵測
    對任意一組納管來源版本，只要有任一來源的版本字串不符合 Semver_Format
    (`^\\d+\\.\\d+\\.\\d+$`)，`check_consistency` 的結果 `ok` 應為 `False`，
    且 `invalid_format` 應包含該來源與其版本值。

Validates: Requirements 1.2, 4.5

生成策略（見下方 strategies）：
    - 生成一個合法 semver 作為權威版本（authoritative）。
    - 生成一組來源，其中至少一個來源帶有「非 semver」版本字串：以 `st.text()`
      過濾掉任何符合 SEMVER_PATTERN 的字串（含空字串——空字串本就不符 semver，
      但明確過濾避免與合法值混淆），確保注入的值確為格式違規。
    - 將該非法字串注入某一來源後呼叫 `check_consistency`，斷言：
        * result.ok 為 False（Req 4.5：格式違規即不通過）。
        * 該非法來源之 name 於 result.invalid_format 中對映到其（非法）版本值。

安全設計：
    - 受測對象 `check_consistency` 為純函式、無 I/O，直接餵合成 VersionSource 即可，
      不觸及真實檔案系統。
    - 沿用既有測試（test_capability_a_structure.py / test_property3_fail_fast.py）的
      importlib 載入模式：先註冊 sys.modules 再 exec，讓 dataclass 能解析字串型別註記。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from hypothesis import given, settings
from hypothesis import strategies as st

# repo 根目錄：本檔位於 <repo>/test/，上溯一層即 repo 根（跨平台以 pathlib 解析）。
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_check_version() -> ModuleType:
    """Load scripts/check_version.py as a module (scripts 尚未註冊為 package).

    沿用 test_capability_a_structure.py / test_property3_fail_fast.py 的載入模式：
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


# 於 module import 時載入一次並重複使用（判定層為純函式，可安全共用模組實例）。
_CHECK_VERSION = _load_check_version()


# ---------------------------------------------------------------------------
# 生成策略（strategies）
# ---------------------------------------------------------------------------

# 合法 semver：三段非負整數以 "." 相連，符合 SEMVER_PATTERN `^\d+\.\d+\.\d+$`。
_valid_semver = st.builds(
    lambda a, b, c: f"{a}.{b}.{c}",
    st.integers(min_value=0, max_value=999),
    st.integers(min_value=0, max_value=999),
    st.integers(min_value=0, max_value=999),
)

# 非 semver 字串：任意文字過濾掉任何符合 SEMVER_PATTERN 者。
# 直接以模組的 SEMVER_PATTERN 過濾，確保與受測程式碼使用「同一把尺」判定格式，
# 空字串與任何巧合合法的 semver 皆被排除。
_non_semver = st.text(max_size=20).filter(
    lambda s: _CHECK_VERSION.SEMVER_PATTERN.match(s) is None
)


# ---------------------------------------------------------------------------
# Property 2 test
# ---------------------------------------------------------------------------


# Feature: version-consistency-gate, Property 2: Semver 格式違規偵測
@settings(max_examples=200)
@given(data=st.data())
def test_invalid_semver_source_is_flagged(data: st.DataObject) -> None:
    """任一來源版本非 semver 時，ok 為 False 且 invalid_format 含該來源與其版本值.

    Validates: Requirements 1.2, 4.5
    """
    # 權威版本為合法 semver（本 property 聚焦「格式違規」而非「不相等」）。
    authoritative = data.draw(_valid_semver, label="authoritative")

    # 生成來源總數 N（1..8）與注入非法值的位置 bad_index ∈ [0, N-1]。
    total = data.draw(st.integers(min_value=1, max_value=8), label="total_sources")
    bad_index = data.draw(
        st.integers(min_value=0, max_value=total - 1), label="bad_index"
    )

    # 生成注入的非 semver 版本字串（確保確實違反 Semver_Format）。
    bad_version = data.draw(_non_semver, label="bad_version")

    # 組出來源清單：bad_index 位置注入非法值，其餘位置使用權威值（合法且相等），
    # 藉此隔離出「格式違規」訊號——其餘來源皆一致且合法，唯一違規只在 bad_index。
    sources = []
    for i in range(total):
        version = bad_version if i == bad_index else authoritative
        sources.append(
            _CHECK_VERSION.VersionSource(name=f"source_{i}", version=version)
        )

    result = _CHECK_VERSION.check_consistency(authoritative, sources, [])

    # (1) 有格式違規來源時，整體判定不通過（Req 4.5）。
    assert result.ok is False, (
        f"注入非 semver 值 {bad_version!r} 後 ok 應為 False，實際 {result.ok}"
    )

    # (2) invalid_format 應含該非法來源，且對映到其（非法）版本值。
    bad_name = f"source_{bad_index}"
    assert bad_name in result.invalid_format, (
        f"invalid_format 應包含格式違規來源 {bad_name}，實際鍵={list(result.invalid_format)}"
    )
    assert result.invalid_format[bad_name] == bad_version, (
        f"invalid_format[{bad_name!r}] 應為注入的非法版本值 {bad_version!r}，"
        f"實際 {result.invalid_format[bad_name]!r}"
    )
