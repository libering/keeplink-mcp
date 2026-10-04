"""Example tests — 真實來源一致性、來源清單組成、失敗訊息與 handoverbook 專屬規則.

# Feature: version-consistency-gate

對應 spec version-consistency-gate 任務 10.2（修正後 version-gate-minimal-env-fix）。
以 import ``scripts.check_version`` 的函式為受測對象（Req 9.6，不重複實作），
涵蓋四個面向：

1. **真實來源一致性（Req 9.2）**：``collect_sources(REPO_ROOT)`` 無擷取錯誤，
   且每個 VersionSource 的版本值皆等於權威 ``__version__``。
2. **來源清單組成（Req 1.1, 1.3）**：檢視集中維護的 ``SOURCE_SPECS``——
   修正後恰 3 項 2-tuple，全為 Manual、零 Derived；首筆為權威來源
   ``src/keeplink_mcp/__init__.py``，非權威者恰為
   {``README.md``, ``docs/handoverbook.md``}。Derived 接線改由
   ``check_structure`` 於 commit 當下驗證，不再納入來源擷取。
3. **失敗訊息內容（Req 9.4）**：以合成的來源清單注入一處不一致，經
   ``check_consistency`` + ``render_report`` 後，報告含每個受檢來源名稱與其版本值。
4. **handoverbook 當前標記 vs 歷史（Req 9.5）**：合成一份頂部含當前標記、
   下方含多個歷史版本引用的 handoverbook，斷言 ``extract_handoverbook_version``
   只回傳當前標記值，不誤取歷史值。

Validates: Requirements 1.1, 1.3, 9.2, 9.4, 9.5
"""

from __future__ import annotations

from pathlib import Path

from keeplink_mcp import __version__
from scripts.check_version import (
    REPO_ROOT,
    SOURCE_SPECS,
    VersionSource,
    check_consistency,
    collect_sources,
    extract_handoverbook_version,
    render_report,
)

# 權威來源顯示名（SOURCE_SPECS 首筆），集中為常數避免各斷言 drift。
_AUTHORITATIVE_NAME = "src/keeplink_mcp/__init__.py"
# 能力 A 完成後，需人工維護（Manual）且非權威的來源恰為此二者。
_EXPECTED_NON_AUTHORITATIVE = {"README.md", "docs/handoverbook.md"}


class TestRealSourceConsistency:
    """真實來源一致性：collect_sources 所得全部 == __version__（Req 9.2）。"""

    def test_collect_sources_all_equal_authoritative(self) -> None:
        """每個蒐集到的 VersionSource 版本 SHALL 等於權威 __version__。

        Validates: Requirement 9.2
        """
        sources, extraction_error = collect_sources(REPO_ROOT)
        assert extraction_error is None, (
            f"來源擷取失敗（fail-fast）：{extraction_error}"
        )
        assert sources, "collect_sources 未回傳任何來源"
        for source in sources:
            assert source.version == __version__, (
                f"來源 '{source.name}' 版本 '{source.version}' "
                f"不等於權威 __version__ '{__version__}'"
            )


class TestSourceListComposition:
    """來源清單組成：恰 3 項 2-tuple，全為 Manual、首筆為權威（Req 1.1, 1.3）。"""

    def test_authoritative_source_is_init(self) -> None:
        """權威來源 SHALL 為 src/keeplink_mcp/__init__.py，且置於 SOURCE_SPECS 首位。

        修正後 SOURCE_SPECS 為 2-tuple (顯示名, 擷取函式)，首筆即權威來源。

        Validates: Requirement 1.1
        """
        first_name, _extractor = SOURCE_SPECS[0]
        assert first_name == _AUTHORITATIVE_NAME, (
            f"SOURCE_SPECS 首筆為 '{first_name}'，應為權威來源 "
            f"'{_AUTHORITATIVE_NAME}'"
        )

    def test_two_manual_non_authoritative_sources(self) -> None:
        """非權威來源 SHALL 恰為 {README.md, docs/handoverbook.md}（Req 1.3）。

        修正後所有來源皆為 Manual；排除權威來源後應恰為此二者。

        Validates: Requirement 1.3
        """
        non_authoritative = {
            name
            for name, _extractor in SOURCE_SPECS
            if name != _AUTHORITATIVE_NAME
        }
        assert len(non_authoritative) == 2, (
            f"非權威來源應為 2 個，實為 "
            f"{len(non_authoritative)}：{sorted(non_authoritative)}"
        )
        assert non_authoritative == _EXPECTED_NON_AUTHORITATIVE, (
            f"非權威來源為 {sorted(non_authoritative)}，"
            f"應為 {sorted(_EXPECTED_NON_AUTHORITATIVE)}"
        )

    def test_source_specs_partition_is_exhaustive(self) -> None:
        """權威 + 非權威 SHALL 恰好覆蓋全部納管來源（互斥且窮盡，共 3 項）。

        Validates: Requirements 1.1, 1.3
        """
        all_names = {name for name, _extractor in SOURCE_SPECS}
        partition = {_AUTHORITATIVE_NAME} | _EXPECTED_NON_AUTHORITATIVE
        assert all_names == partition, (
            f"SOURCE_SPECS 來源集 {sorted(all_names)} 與預期分類 "
            f"{sorted(partition)} 不一致"
        )
        assert len(SOURCE_SPECS) == 3, (
            f"SOURCE_SPECS 應恰 3 項，實為 {len(SOURCE_SPECS)}"
        )


class TestFailureMessageContent:
    """失敗訊息內容：報告含每個不一致來源與其版本值（Req 9.4）。"""

    def test_report_lists_every_source_and_version(self) -> None:
        """檢查失敗時 Version_Report SHALL 含每個受檢來源名稱與其版本值。

        以合成來源清單注入一處不一致，經 check_consistency + render_report。

        Validates: Requirement 9.4
        """
        authoritative = "1.2.0"
        # 合成受檢來源：一處刻意不一致（README 為 9.9.9），其餘等於權威值。
        sources = [
            VersionSource(name="src/keeplink_mcp/__init__.py", version="1.2.0"),
            VersionSource(name="README.md", version="9.9.9"),
            VersionSource(name="docs/handoverbook.md", version="1.2.0"),
        ]

        result = check_consistency(authoritative, sources, [])
        assert not result.ok, "注入不一致後 check_consistency.ok 應為 False"
        assert result.mismatches == {"README.md": "9.9.9"}, (
            f"mismatches 應恰含不一致來源，實為 {result.mismatches}"
        )

        report = render_report(result)
        # Req 9.4：{來源: 版本} 列表——每個受檢來源名稱與其版本值皆須出現於報告。
        for source in sources:
            assert source.name in report, (
                f"報告缺少來源名稱 '{source.name}'：\n{report}"
            )
            assert source.version in report, (
                f"報告缺少版本值 '{source.version}'：\n{report}"
            )


class TestHandoverbookCurrentMarker:
    """handoverbook 只納當前標記、不納歷史（Req 9.5）。"""

    def test_extracts_current_marker_not_historical(self, tmp_path: Path) -> None:
        """extract_handoverbook_version SHALL 回傳頂部當前標記，非歷史版本值。

        合成一份 handoverbook：頂部標頭含當前標記 `版本：v1.2.0`，第 10 行後
        散布多個歷史引用（`v1.1.1`、`升級至 1.1.0` 等）。斷言擷取結果為當前
        標記值 1.2.0，而非任何歷史值。

        Validates: Requirement 9.5
        """
        current = "1.2.0"
        # 頂部標頭區塊含當前標記（位於前 HEADER_SCAN_LINES=10 行內）。
        lines = [
            "# KeepLink-MCP 項目交接手冊",
            "",
            f"> 最後更新：2026-08-21 | 版本：v{current}（含 archive_and_cite）",
            "",
            "---",
            "",
            "## 概覽",
            "",
            "內容佔位。",
            "",
        ]
        # 第 10 行之後（掃描窗外）散布歷史版本引用，測試防污染。
        historical_block = [
            "## Release Notes",
            "",
            "- v1.1.1 僅檢查部分來源。",
            "- v1.1.1 修正若干問題。",
            "- 升級至 1.1.0 引入背景 Worker。",
            "- 早期 v1.0.0 為初版。",
            "- 再次提及 v1.1.1 以增加干擾。",
        ]
        content = "\n".join(lines + historical_block)

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "handoverbook.md").write_text(content, encoding="utf-8")

        marker = extract_handoverbook_version(tmp_path)
        assert marker == current, (
            f"擷取到 '{marker}'，應為頂部當前標記 '{current}'（不得取歷史值）"
        )
        # 明確排除歷史值被誤取。
        assert marker not in {"1.1.1", "1.1.0", "1.0.0"}, (
            f"擷取結果 '{marker}' 誤取了歷史版本引用"
        )
