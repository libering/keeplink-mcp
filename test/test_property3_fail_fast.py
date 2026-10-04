"""Property test for Property 3 — true fail-fast 於首個擷取失敗即停止.

對應 spec version-consistency-gate 任務 3.2。

Property 3: True fail-fast 於首個擷取失敗即停止
    對任意來源清單與任一「首個會擷取失敗的位置 k」：`collect_sources` 應在索引 k
    停止，回傳的已成功來源恰為索引 0..k-1（數量等於 k），錯誤說明只提及索引 k 的
    那一個來源，且索引 k 之後的來源擷取函式從未被呼叫（不續掃、不將失敗累積成清單）。

Validates: Requirements 4.6

生成策略（見下方 strategies）：
    - 生成來源總數 N（1..32）與失敗索引 k（0..N-1）。
      N 上界取較大值以擴大 (N, k) 組合空間，確保 hypothesis 能跑滿 ≥100 迭代
      （空間過小時 hypothesis 會因「nothing left to do」提早停止）。
    - 為每個索引 i 建一個 spy 擷取 callable：呼叫時自增其 call_count。
      索引 < k 的 spy 回傳合法 semver；索引 == k 的 spy 拋 ValueError；
      索引 > k 的 spy 若被呼叫即為違規（不應發生）。
    - 以合成的 SOURCE_SPECS 替換模組的 SOURCE_SPECS，呼叫 collect_sources 後：
        * 回傳的 collected 長度 == k（恰為 0..k-1）。
        * 索引 k 的 spy 恰被呼叫一次。
        * 索引 > k 的每個 spy call_count == 0（從未被呼叫）。
        * 錯誤說明提及索引 k 來源名，且不提及索引 k 之後的任何來源名。

安全設計：
    - 以 try/finally 手動保存/還原 SOURCE_SPECS，確保每個 hypothesis 例子互不污染，
      且不使用 function-scoped fixture（避免 @given 下的 HealthCheck）。
    - spy 忽略 repo_root 參數，故傳入 Path('.') 即可；不觸及真實檔案系統。
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

    沿用 test_capability_a_structure.py / test_property4_handoverbook.py 的載入模式：
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


# 於 module import 時載入一次並重複使用；SOURCE_SPECS 於各測試以 monkeypatch 於此
# module 物件上覆寫（collect_sources 讀模組全域 SOURCE_SPECS）。
_CHECK_VERSION = _load_check_version()


class _SpyExtractor:
    """計數用 spy 擷取 callable：記錄被呼叫次數，並依設定回傳或拋錯.

    Attributes:
        result: 成功時回傳的版本字串（should_fail 為 False 時使用）。
        should_fail: True 表示被呼叫時拋 ValueError（模擬擷取失敗）。
        call_count: 迄今被呼叫的次數（斷言用）。
    """

    def __init__(self, result: str, *, should_fail: bool) -> None:
        self.result = result
        self.should_fail = should_fail
        self.call_count = 0

    def __call__(self, repo_root: Path) -> str:
        # 每次呼叫先自增計數，讓「索引 > k 的 spy 從未被呼叫」可被精準斷言。
        self.call_count += 1
        if self.should_fail:
            raise ValueError("合成擷取失敗（spy）")
        return self.result


# ---------------------------------------------------------------------------
# Property 3 test
# ---------------------------------------------------------------------------


# Feature: version-consistency-gate, Property 3: True fail-fast 於首個擷取失敗即停止
@settings(max_examples=200)
@given(data=st.data())
def test_collect_sources_stops_at_first_failure(data: st.DataObject) -> None:
    """collect_sources 於首個擷取失敗即停止，不呼叫其後的擷取函式.

    Validates: Requirements 4.6
    """
    # N 個來源、失敗索引 k ∈ [0, N-1]：k 之前成功、k 失敗、k 之後不應被觸及。
    total = data.draw(st.integers(min_value=1, max_value=32), label="total_sources")
    fail_index = data.draw(
        st.integers(min_value=0, max_value=total - 1), label="fail_index"
    )

    # 為每個索引建一個具唯一名稱的 spy，並組出合成 SOURCE_SPECS。
    spies: list[_SpyExtractor] = [
        _SpyExtractor(f"1.0.{i}", should_fail=(i == fail_index)) for i in range(total)
    ]
    synthetic_specs = [(f"source_{i}", spies[i]) for i in range(total)]

    original_specs = _CHECK_VERSION.SOURCE_SPECS
    _CHECK_VERSION.SOURCE_SPECS = synthetic_specs
    try:
        # repo_root 對 spy 無意義（spy 忽略之），傳 Path('.') 即可，不觸及檔案系統。
        collected, error_msg = _CHECK_VERSION.collect_sources(Path("."))
    finally:
        # 手動還原，確保例子間互不污染（不使用 function-scoped fixture）。
        _CHECK_VERSION.SOURCE_SPECS = original_specs

    # (1) 已成功來源恰為索引 0..k-1（數量 == k）。
    assert len(collected) == fail_index, (
        f"已成功來源數 {len(collected)} 應等於失敗索引 k={fail_index}"
    )
    assert [s.name for s in collected] == [f"source_{i}" for i in range(fail_index)], (
        "已成功來源名應恰為索引 0..k-1"
    )

    # (2) 錯誤說明存在且只提及索引 k 的來源，不提及 k 之後任何來源。
    assert error_msg is not None, "首個擷取失敗時 error_msg 不應為 None"
    assert f"source_{fail_index}" in error_msg, (
        f"錯誤說明應提及失敗來源 source_{fail_index}"
    )
    for later in range(fail_index + 1, total):
        assert f"source_{later}" not in error_msg, (
            f"錯誤說明不應提及索引 k 之後的來源 source_{later}"
        )

    # (3) 索引 < k 的 spy 各被呼叫一次；索引 k 恰一次；索引 > k 從未被呼叫。
    for i in range(fail_index):
        assert spies[i].call_count == 1, (
            f"索引 {i}（< k）的 spy 應被呼叫一次，實際 {spies[i].call_count}"
        )
    assert spies[fail_index].call_count == 1, (
        f"失敗索引 k={fail_index} 的 spy 應恰被呼叫一次，"
        f"實際 {spies[fail_index].call_count}"
    )
    for later in range(fail_index + 1, total):
        assert spies[later].call_count == 0, (
            f"索引 {later}（> k）的 spy 從未被呼叫，實際 {spies[later].call_count}"
        )
