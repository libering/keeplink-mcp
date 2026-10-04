"""Property test for Property 7 — 結構違規報告分節.

對應 spec version-gate-minimal-env-fix 任務 5.3。

Property 7: 結構違規報告分節
    對任意一致（所有來源版本皆等於權威且皆為合法 semver、無格式違規）的
    CheckResult：當其 ``structure_errors`` 為非空清單時，``render_report``
    產生的 Version_Report 應包含「能力 A 接線結構違規」分節字樣，且每一條
    Structure_Error 字串都出現在報告中；當 ``structure_errors`` 為空清單時，
    報告應不含該分節字樣。

Validates: Requirements 7.1, 7.2

生成策略：
    - authoritative 用合法 semver V；sources 為 1..數個 VersionSource 皆帶版本 V
      （故無 mismatch、無 invalid_format）。
    - structure_errors 為 list[str]，元素以固定可辨識前綴 "違規-" 開頭再接隨機
      文字，數量 0..N。固定前綴確保生成字串不與分節標題或既有報告字樣碰撞，
      避免假命中（false positive）或假陰性（false negative）。
    - ok 依「structure_errors 是否為空」決定（其餘違規皆已排除），確保非空時走
      不一致/結構違規分支、空時走一致分支，貼合 render_report 的分派優先序。
    - 呼叫 render_report(result) 後依 structure_errors 是否為空分別斷言。

安全設計：
    - 受測對象 ``render_report`` 為純函式、無 I/O，直接餵合成 CheckResult 即可，
      不觸及真實檔案系統。
    - 從 scripts.check_version import（conftest 已把 repo root 加入 sys.path）。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from scripts.check_version import CheckResult, VersionSource, render_report

# ---------------------------------------------------------------------------
# 生成策略（strategies）
# ---------------------------------------------------------------------------

# 合法 semver：三段有界非負整數以 "." 相連，符合 SEMVER_PATTERN `^\d+\.\d+\.\d+$`。
_valid_semver = st.builds(
    lambda a, b, c: f"{a}.{b}.{c}",
    st.integers(min_value=0, max_value=999),
    st.integers(min_value=0, max_value=999),
    st.integers(min_value=0, max_value=999),
)

# Structure_Error 字串：固定可辨識前綴 "違規-" + 非空隨機文字（中文/ASCII 皆可）。
# 固定前綴 + 唯一隨機尾段，確保每條違規字串皆可被精確地在報告中定位，不與
# 分節標題「能力 A 接線結構違規」或其他既有報告字樣碰撞。
_structure_error = st.builds(
    lambda tail: f"違規-{tail}",
    st.text(
        alphabet=st.characters(
            min_codepoint=0x20,
            blacklist_categories=("Cs",),
            # 排除會干擾比對的換行與冒號（分節逐行列出時以冒號分隔）。
            blacklist_characters="\n\r\t：:",
        ),
        min_size=1,
        max_size=20,
    ),
)

# 分節標題字樣，與 render_report 內 _render_structure_section 一致。
_STRUCTURE_SECTION_TITLE = "能力 A 接線結構違規"


def _make_consistent_result(
    authoritative: str,
    total_sources: int,
    structure_errors: list[str],
) -> CheckResult:
    """建一致（無 mismatch、無格式違規）、僅由 structure_errors 決定 ok 的 CheckResult.

    所有來源皆帶權威版本 → mismatches/invalid_format 必為空；ok 僅在
    structure_errors 為空時為 True，貼合 check_consistency 的語義。

    Args:
        authoritative: 權威版本（合法 semver）。
        total_sources: 來源數量（≥1）。
        structure_errors: 結構違規清單（空或非空）。

    Returns:
        合成的 CheckResult。
    """
    sources = [
        VersionSource(name=f"source_{i}", version=authoritative)
        for i in range(total_sources)
    ]
    return CheckResult(
        authoritative=authoritative,
        sources=sources,
        ok=len(structure_errors) == 0,
        mismatches={},
        invalid_format={},
        structure_errors=structure_errors,
        extraction_error=None,
    )


# ---------------------------------------------------------------------------
# Property 7 — 非空分支：含分節且逐條列出每個 Structure_Error（Req 7.1）
# ---------------------------------------------------------------------------


# Feature: version-consistency-gate, Property 7: 結構違規報告分節
@settings(max_examples=150)
@given(data=st.data())
def test_nonempty_structure_errors_renders_section_with_every_error(
    data: st.DataObject,
) -> None:
    """structure_errors 非空時，報告含分節標題且每條違規皆出現.

    Validates: Requirements 7.1
    """
    authoritative = data.draw(_valid_semver, label="authoritative")
    total = data.draw(st.integers(min_value=1, max_value=5), label="total_sources")
    # 非空：1..數條 Structure_Error（固定前綴確保可辨識）。
    structure_errors = data.draw(
        st.lists(_structure_error, min_size=1, max_size=5),
        label="structure_errors",
    )

    result = _make_consistent_result(authoritative, total, structure_errors)
    report = render_report(result)

    # (1) 報告含「能力 A 接線結構違規」分節字樣（Req 7.1）。
    assert _STRUCTURE_SECTION_TITLE in report, (
        f"structure_errors 非空時報告應含分節標題 {_STRUCTURE_SECTION_TITLE!r}，"
        f"實際報告：\n{report}"
    )

    # (2) 每一條 Structure_Error 字串都出現在報告中（Req 7.1 逐條列出）。
    for error in structure_errors:
        assert error in report, (
            f"報告應列出 Structure_Error {error!r}，實際報告：\n{report}"
        )


# ---------------------------------------------------------------------------
# Property 7 — 空分支：不含分節字樣（Req 7.2）
# ---------------------------------------------------------------------------


# Feature: version-consistency-gate, Property 7: 結構違規報告分節
@settings(max_examples=150)
@given(data=st.data())
def test_empty_structure_errors_omits_section(data: st.DataObject) -> None:
    """structure_errors 為空時，報告不含分節標題字樣.

    Validates: Requirements 7.2
    """
    authoritative = data.draw(_valid_semver, label="authoritative")
    total = data.draw(st.integers(min_value=1, max_value=5), label="total_sources")

    result = _make_consistent_result(authoritative, total, [])
    report = render_report(result)

    # structure_errors 為空 → 不附「能力 A 接線結構違規」分節（Req 7.2）。
    assert _STRUCTURE_SECTION_TITLE not in report, (
        f"structure_errors 為空時報告不應含分節標題 {_STRUCTURE_SECTION_TITLE!r}，"
        f"實際報告：\n{report}"
    )
