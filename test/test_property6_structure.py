"""Property test for Property 6 — 結構檢查正確性.

對應 spec version-gate-minimal-env-fix 任務 5.2。

Property 6（新增）：結構檢查正確性
    對任意針對 STRUCTURE_SPECS 各目標檔的內容組合（含「全部 required 命中且全部
    forbidden 不命中（good）」「至少一條 required 缺失（missing-required）」「至少
    一條 forbidden 命中（has-forbidden）」「目標檔缺失（absent）」等情境）：
    ``check_structure(repo_root)`` 回傳空清單 **當且僅當** 所有 StructureCheck 的
    required regex 全部命中、所有 forbidden regex 全部不命中、且所有目標檔皆可讀取；
    只要存在任一違規（含目標檔缺失），回傳清單應為非空，並不因任一違規提早中止對其餘
    StructureCheck 的處理（兩檔皆違規時違規清單同時涵蓋兩條規則）。

Validates: Requirements 2.1, 2.2, 2.4, 3.1, 3.2, 3.4, 4.2, 4.3

生成策略：
    對 STRUCTURE_SPECS 中每個目標檔獨立抽一個狀態 ∈
    {good, missing-required, has-forbidden, absent}，於每個 example 建立全新臨時
    repo（以每條 spec 自身的 file_path 組路徑寫檔，與受測實作讀檔路徑同源），absent
    狀態則不建該檔以模擬缺失；再呼叫 check_structure(synthetic_root) 並斷言「回空 ⇔
    兩檔皆 good」，且兩檔皆違規時違規清單以 spec.name 比對確認兩條規則皆被指出。

安全設計：
    - 內容片段依「狀態語義」組裝而非寫死檔名：good 用「required 命中且 forbidden 不
      命中」的內容；missing-required 用不含 required 樣式的內容；has-forbidden 用含
      行首 ``version = "x"``（pyproject）或 ``version="x"``（app）的內容。片段以每條
      spec 自身的 required/forbidden regex 實測確認其命中/不命中，確保生成資料與受測
      規則同源、不因 regex 細節漂移而失真。
    - 每個 example 以 tempfile 建立獨立臨時目錄並於結束時清理，彼此隔離、不觸及真實
      repo（function 範圍的 tmp_path fixture 不適用於 hypothesis 多 example 迭代，
      會跨 example 殘留狀態）。
    - 沿用 test_structure_check.py 的 import 模式：conftest.py 已把 repo root 加入
      sys.path，故可直接 ``from scripts.check_version import ...``。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from scripts.check_version import (
    STRUCTURE_SPECS,
    StructureCheck,
    check_structure,
)

# 四種目標檔狀態。good 為唯一「無違規」狀態；其餘三種各自製造一類違規。
_STATES = ("good", "missing-required", "has-forbidden", "absent")


def _good_content(spec: StructureCheck) -> str:
    """組出同時「所有 required 命中、所有 forbidden 不命中」的檔案內容。

    依 spec 還原一行可被 required 命中的宣告（``dynamic = ["version"]`` /
    ``version=__version__``），再以該 spec 自身的 required/forbidden regex 實測驗證
    該內容確為 good，確保生成資料與受測規則同源。

    Args:
        spec: 單條結構規則。

    Returns:
        滿足 good 語義的檔案內容。
    """
    if spec.file_path == "pyproject.toml":
        content = (
            '[project]\n'
            'name = "synthetic-pkg"\n'
            'dynamic = ["version"]\n'
            'requires-python = ">=3.10"\n'
        )
    else:  # app.py 接線
        content = (
            "from synthetic import __version__\n\n"
            'app = FastAPI(title="Synthetic", version=__version__)\n'
        )
    # 生成資料與受測規則同源的自我驗證：good 內容須 required 全命中、forbidden 全不命中。
    assert all(p.search(content) is not None for p in spec.required)
    assert all(p.search(content) is None for p in spec.forbidden)
    return content


def _missing_required_content(spec: StructureCheck) -> str:
    """組出「至少一條 required 缺失」的內容（且不觸發任何 forbidden）。

    僅移除 required 樣式、保留無害行，使此檔的唯一違規來自 required 缺失。
    """
    content = (
        "[project]\n"
        'name = "synthetic-pkg"\n'
        'description = "no version wiring here"\n'
    )
    assert any(p.search(content) is None for p in spec.required)
    assert all(p.search(content) is None for p in spec.forbidden)
    return content


def _has_forbidden_content(spec: StructureCheck) -> str:
    """組出「至少一條 forbidden 命中」的內容（同時仍保留 required 命中）。

    依 spec 製造：pyproject 以行首 ``version = "x"`` 觸發行首靜態 version
    forbidden；app 以 ``version="x"`` 觸發字面量 version forbidden。required 仍保留
    命中，確保此檔的違規確實來自 forbidden 命中而非 required 缺失。
    """
    if spec.file_path == "pyproject.toml":
        content = (
            "[project]\n"
            'name = "synthetic-pkg"\n'
            'dynamic = ["version"]\n'
            'version = "1.0.0"\n'
        )
    else:  # app.py
        content = (
            "from synthetic import __version__\n\n"
            'app = FastAPI(title="Synthetic", version=__version__)\n'
            'legacy = FastAPI(version="1.0.0")\n'
        )
    assert all(p.search(content) is not None for p in spec.required)
    assert any(p.search(content) is not None for p in spec.forbidden)
    return content


# 狀態 → 內容產生器（absent 無內容，由寫檔步驟略過建檔）。
_CONTENT_BUILDERS = {
    "good": _good_content,
    "missing-required": _missing_required_content,
    "has-forbidden": _has_forbidden_content,
}


def _materialize(repo_root: Path, states: list[str]) -> None:
    """依每條 spec 的狀態在合成 repo 建檔（absent 則不建該檔）。

    以每條 spec 自身的 ``file_path`` 組路徑寫檔，與受測實作讀檔路徑同源，確保測試
    不因硬編路徑而與實作漂移。

    Args:
        repo_root: 合成 repo 根（本 example 的臨時目錄）。
        states: 與 STRUCTURE_SPECS 等長、逐一對應的狀態清單。
    """
    for spec, state in zip(STRUCTURE_SPECS, states):
        if state == "absent":
            continue
        target = repo_root.joinpath(*spec.file_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_CONTENT_BUILDERS[state](spec), encoding="utf-8")


# ---------------------------------------------------------------------------
# Property 6 — 結構檢查正確性
# ---------------------------------------------------------------------------


# Feature: version-consistency-gate, Property 6: 結構檢查正確性
# deadline=None：此 property 每個 example 以 tempfile 建臨時目錄並寫真實檔案（檔案
# I/O），執行時間本就會抖動（曾見同一 example 首次 285ms、重跑 9.76ms），不應以執行
# 時間作為正確性判準，故關閉 hypothesis 死線；max_examples 與所有斷言維持不變。
@settings(max_examples=200, deadline=None)
@given(states=st.lists(st.sampled_from(_STATES), min_size=1).filter(bool))
def test_check_structure_empty_iff_all_targets_good(states: list[str]) -> None:
    """check_structure 回空 ⇔ 全部目標檔皆 good；否則回非空且不提早中止.

    Validates: Requirements 2.1, 2.2, 2.4, 3.1, 3.2, 3.4, 4.2, 4.3
    """
    # 將抽到的狀態序列對齊 STRUCTURE_SPECS 長度：循環補滿，確保每條 spec 皆有狀態、
    # 且狀態空間涵蓋 good / missing-required / has-forbidden / absent 的所有組合。
    aligned = [states[i % len(states)] for i in range(len(STRUCTURE_SPECS))]
    all_good = all(state == "good" for state in aligned)

    with tempfile.TemporaryDirectory() as raw_root:
        repo_root = Path(raw_root)
        _materialize(repo_root, aligned)
        errors = check_structure(repo_root)

    # 核心雙條件：回空 ⇔ 全部目標檔皆 good。
    if all_good:
        assert errors == [], (
            f"全部目標檔皆 good 時 check_structure 應回空，狀態={aligned}，實得 {errors}"
        )
        return

    # 存在任一違規 → 回非空（Req 4.2 的 iff 另一半）。
    assert errors, (
        f"存在非 good 目標檔時 check_structure 應回非空，狀態={aligned}，實得 {errors}"
    )

    # 不提早中止：每個「非 good」的 spec 皆應在違規清單被指出（以 spec.name 比對，
    # 避開缺檔訊息中絕對路徑的 OS 分隔符差異）。兩檔皆違規時即同時涵蓋兩條規則。
    for spec, state in zip(STRUCTURE_SPECS, aligned):
        if state == "good":
            continue
        assert any(spec.name in error for error in errors), (
            f"違規清單未涵蓋規則 '{spec.name}'（狀態={state}），"
            f"狀態序列={aligned}，實得 {errors}"
        )
