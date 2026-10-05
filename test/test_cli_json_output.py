"""Tests for the --json output mode of the Version_Check_Script.

對應 spec version-gate-json-output 任務 2.3 / 3.2 / 3.3 / 3.4。

本檔涵蓋四個互補面向：

    test_json_mode_consistent_repo（integration，Req 1.1/1.2/2.1/2.4/3.3）：
        以子行程於當前一致 repo 呼叫 ``check_version.py --json``，驗證 exit 0、
        stdout 為合法 JSON、各欄位符合 JSON_Contract。

    test_render_json_inconsistent_unit（unit，Req 1.3/2.2/2.5/2.6）：
        直接對 render_json 餵一個含 mismatches 與 structure_errors 的不一致
        CheckResult，驗證序列化後各欄位正確、ok==False。

    test_default_mode_regression（example，Req 4.1）：
        以子行程不帶 --json 呼叫，驗證 Default_Mode 行為未被破壞。

    test_render_json_roundtrip_property（property，Property 1）：
        以 hypothesis 生成任意合法 CheckResult，驗證 render_json 的 round-trip
        欄位保真（涵蓋 CJK/emoji 與空/非空邊界）。

安全設計：
    - 子行程一律以 ``sys.executable`` 啟動（不寫死 "python"），路徑以 pathlib 組出，
      確保 Windows 11 與 Linux 皆正確；擷取輸出明確 ``encoding="utf-8"``。
    - 單元/屬性測試經 conftest.py 將 repo 根註冊於 sys.path，直接
      ``from scripts.check_version import ...``（Req 9.6：測試引用腳本函式而非重實作）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from scripts.check_version import CheckResult, VersionSource, render_json

# repo 根目錄：本檔位於 <repo>/test/，上溯一層即 repo 根（跨平台以 pathlib 解析）。
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_version.py"

# JSON_Contract 的穩定鍵集合（對外契約；下游 skill 依賴）。
_CONTRACT_KEYS = {
    "ok",
    "authoritative",
    "sources",
    "mismatches",
    "invalid_format",
    "structure_errors",
    "extraction_error",
}


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """以子行程呼叫 check_version.py，回傳擷取好的結果（統一 UTF-8 編碼處理）。

    Args:
        args: 傳給直譯器的引數（第一個通常是腳本路徑）。
        cwd: 子行程工作目錄。

    Returns:
        CompletedProcess，其 stdout/stderr 已以 UTF-8 解碼（errors="replace"）。
    """
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",  # Windows console 預設非 UTF-8，明確指定以正確擷取中文/emoji
        errors="replace",  # 保底：任何無法解碼位元組以替代字元呈現，不讓測試崩潰
    )


# ---------------------------------------------------------------------------
# Integration — --json 於當前一致 repo
# ---------------------------------------------------------------------------


def test_json_mode_consistent_repo() -> None:
    """``--json`` 於當前一致 repo：exit 0、合法 JSON、各欄位符合 JSON_Contract。

    Validates: Requirements 1.1, 1.2, 2.1, 2.4, 3.3
    """
    result = _run_cli([str(_SCRIPT_PATH), "--json"], cwd=_REPO_ROOT)

    # Req 1.1：JSON 寫 stdout；Req 1.2：一致 → exit 0。
    assert result.returncode == 0, (
        f"一致情境應 exit 0，實際 {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    # Req 3.3：stdout 須為可被標準解析器解析的合法 JSON。
    payload = json.loads(result.stdout)

    # Req 2.1：七鍵齊備且恰為契約鍵集合。
    assert set(payload.keys()) == _CONTRACT_KEYS, f"鍵集合不符契約：{payload.keys()}"

    assert payload["ok"] is True, f"當前 repo 應一致，ok 應為 true：{payload}"
    assert payload["authoritative"] == "1.2.0", (
        f"權威版本應為 1.2.0：{payload['authoritative']}"
    )

    # Req 2.4：sources 為同序物件陣列，含三個 Manual 來源。
    source_names = [s["name"] for s in payload["sources"]]
    assert source_names == [
        "src/keeplink_mcp/__init__.py",
        "README.md",
        "docs/handoverbook.md",
    ], f"sources 名稱/順序不符：{source_names}"
    for src in payload["sources"]:
        assert set(src.keys()) == {"name", "version"}, f"source 元素鍵不符：{src}"
        assert src["version"] == "1.2.0", f"來源 {src['name']} 版本應為 1.2.0"

    # 一致情境下三個明細欄位皆空、extraction_error 為 null。
    assert payload["mismatches"] == {}, f"mismatches 應為空：{payload['mismatches']}"
    assert payload["invalid_format"] == {}, (
        f"invalid_format 應為空：{payload['invalid_format']}"
    )
    assert payload["structure_errors"] == [], (
        f"structure_errors 應為空：{payload['structure_errors']}"
    )
    assert payload["extraction_error"] is None, (
        f"extraction_error 應為 null：{payload['extraction_error']}"
    )


# ---------------------------------------------------------------------------
# Unit — render_json 不一致情境
# ---------------------------------------------------------------------------


def test_render_json_inconsistent_unit() -> None:
    """render_json 對不一致 CheckResult 的序列化：各欄位正確、ok==False。

    Validates: Requirements 1.3, 2.2, 2.5, 2.6
    """
    result = CheckResult(
        authoritative="1.2.0",
        sources=[
            VersionSource(name="src/keeplink_mcp/__init__.py", version="1.2.0"),
            VersionSource(name="README.md", version="9.9.9"),
        ],
        ok=False,
        mismatches={"README.md": "9.9.9"},
        invalid_format={},
        structure_errors=["app.py 引用權威版本接線：缺少必要接線"],
        extraction_error=None,
    )

    payload = json.loads(render_json(result))

    assert set(payload.keys()) == _CONTRACT_KEYS
    assert payload["ok"] is False, "含 mismatch/structure_error 時 ok 應為 false"
    assert payload["authoritative"] == "1.2.0"
    assert payload["sources"] == [
        {"name": "src/keeplink_mcp/__init__.py", "version": "1.2.0"},
        {"name": "README.md", "version": "9.9.9"},
    ]
    # Req 2.5：mismatches 為 string→string 物件。
    assert payload["mismatches"] == {"README.md": "9.9.9"}
    assert payload["invalid_format"] == {}
    # Req 2.6：structure_errors 為字串陣列。
    assert payload["structure_errors"] == [
        "app.py 引用權威版本接線：缺少必要接線"
    ]
    assert payload["extraction_error"] is None


# ---------------------------------------------------------------------------
# Example — Default_Mode 回歸（不帶 --json）
# ---------------------------------------------------------------------------


def test_default_mode_regression() -> None:
    """不帶 ``--json``：Default_Mode 行為未被破壞（exit 0、人讀報告穩定標記）。

    Validates: Requirements 4.1
    """
    result = _run_cli([str(_SCRIPT_PATH)], cwd=_REPO_ROOT)

    assert result.returncode == 0, (
        f"一致情境 Default_Mode 應 exit 0，實際 {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # 人讀報告的穩定一致標記（render_report 的 ✅ 分支），且非 JSON（不以 '{' 開頭）。
    assert "✅" in result.stdout, f"Default_Mode 應輸出人讀一致標記：\n{result.stdout}"
    assert "1.2.0" in result.stdout, f"報告應含權威版本值：\n{result.stdout}"
    assert not result.stdout.lstrip().startswith("{"), (
        f"Default_Mode 不應輸出 JSON：\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Property 1 — render_json round-trip 欄位保真
# ---------------------------------------------------------------------------

# 字元池涵蓋 ASCII、CJK（U+4E00..U+9FFF）與 emoji，貼近真實來源名/訊息內容，
# 驗證 ensure_ascii=False 下非 ASCII 不被破壞（Req 3.1）。
_text = st.text(
    alphabet=st.one_of(
        st.characters(min_codepoint=0x20, max_codepoint=0x7E),
        st.characters(min_codepoint=0x4E00, max_codepoint=0x9FFF),
        st.sampled_from(["✅", "❌", "🚀", "📦", "🔖"]),
    ),
    min_size=0,
    max_size=30,
)
_semverish = st.from_regex(r"\A\d{1,3}\.\d{1,3}\.\d{1,3}\Z")


@st.composite
def _check_results(draw: st.DrawFn) -> CheckResult:
    """生成合法 CheckResult：欄位涵蓋 ASCII/CJK/emoji 與空/非空邊界。

    sources / mismatches / invalid_format / structure_errors 皆可空或非空；
    extraction_error 可為 None 或字串——以覆蓋 JSON_Contract 所有欄位的空值表現。
    """
    sources = draw(
        st.lists(
            st.builds(VersionSource, name=_text, version=_semverish),
            min_size=0,
            max_size=4,
        )
    )
    mismatches = draw(st.dictionaries(_text, _semverish, max_size=4))
    invalid_format = draw(st.dictionaries(_text, _text, max_size=4))
    structure_errors = draw(st.lists(_text, max_size=4))
    extraction_error = draw(st.one_of(st.none(), _text))
    return CheckResult(
        authoritative=draw(_semverish),
        sources=sources,
        ok=draw(st.booleans()),
        mismatches=mismatches,
        invalid_format=invalid_format,
        structure_errors=structure_errors,
        extraction_error=extraction_error,
    )


# Feature: version-gate-json-output, Property 1: render_json round-trip 欄位保真
@settings(max_examples=200)
@given(result=_check_results())
def test_render_json_roundtrip_property(result: CheckResult) -> None:
    """render_json 序列化再經標準 JSON 解析，各欄位與原 CheckResult 保真。

    Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.3
    """
    parsed = json.loads(render_json(result))

    # 鍵集合恰為契約七鍵。
    assert set(parsed.keys()) == _CONTRACT_KEYS

    # 純量欄位保真。
    assert parsed["ok"] is result.ok
    assert isinstance(parsed["ok"], bool)
    assert parsed["authoritative"] == result.authoritative

    # sources：等長、同序，各元素 name/version 保真。
    assert len(parsed["sources"]) == len(result.sources)
    for parsed_src, src in zip(parsed["sources"], result.sources, strict=True):
        assert parsed_src == {"name": src.name, "version": src.version}

    # 映射與陣列欄位保真（空則自然為 {} / []）。
    assert parsed["mismatches"] == result.mismatches
    assert parsed["invalid_format"] == result.invalid_format
    assert parsed["structure_errors"] == result.structure_errors

    # extraction_error：None → JSON null，否則相等字串。
    assert parsed["extraction_error"] == result.extraction_error
