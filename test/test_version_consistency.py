"""Version consistency tests — migrated to import the Version_Check_Script.

# Feature: version-consistency-gate

Per 待決點 3 (design.md), the extraction / consistency logic now lives in
``scripts/check_version.py`` (single source of truth). This test module imports
those functions instead of re-implementing them (Req 9.6, DRY):

Migration map (design.md 待決點 3):
    get_pyproject_version     -> removed; pyproject is a Derived_Version_Source
                                 now, verified via ``extract_pyproject_version``
                                 (hatchling 推導 == authoritative).
    get_app_version           -> removed; app.py no longer hardcodes a literal,
                                 verified via ``extract_app_version`` (import
                                 __version__).
    get_init_version          -> merged into ``extract_init_version``.
    get_readme_version        -> merged into ``extract_readme_version``.
    (new)                     -> ``extract_handoverbook_version`` coverage added.
    check_version_consistency -> the script's ``check_consistency``.

The Property 1 property test drives the SCRIPT's ``check_consistency`` (no
duplicated logic) with >=100 hypothesis iterations.

Validates: Requirements 9.1, 9.6
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from keeplink_mcp import __version__
from scripts.check_version import (
    REPO_ROOT,
    VersionSource,
    check_consistency,
    collect_sources,
    extract_app_version,
    extract_handoverbook_version,
    extract_init_version,
    extract_pyproject_version,
    extract_readme_version,
    is_semver,
    render_report,
)


class TestVersionConsistency:
    """Real-source consistency via the imported Version_Check_Script functions."""

    def test_version_format_is_valid(self) -> None:
        """Every extracted real source SHALL be valid semver (X.Y.Z).

        Validates: Requirements 4.5, 9.2
        """
        for name, version in (
            ("__init__.py", extract_init_version(REPO_ROOT)),
            ("pyproject.toml", extract_pyproject_version(REPO_ROOT)),
            ("app.py", extract_app_version(REPO_ROOT)),
            ("README.md", extract_readme_version(REPO_ROOT)),
            ("handoverbook.md", extract_handoverbook_version(REPO_ROOT)),
        ):
            assert is_semver(version), (
                f"{name} version '{version}' is not valid semver (X.Y.Z)"
            )

    def test_pyproject_derived_matches_init(self) -> None:
        """pyproject hatchling-derived version SHALL match Authoritative_Version.

        pyproject is a Derived_Version_Source (能力 A); its value is verified
        against __version__ via ``extract_pyproject_version``.

        Validates: Requirement 2.3
        """
        derived = extract_pyproject_version(REPO_ROOT)
        assert derived == __version__, (
            f"pyproject derived version '{derived}' != __version__ '{__version__}'"
        )

    def test_app_metadata_matches_init(self) -> None:
        """FastAPI app metadata version SHALL match Authoritative_Version.

        app.py imports __version__ (能力 A); verified via ``extract_app_version``.

        Validates: Requirement 3.3
        """
        app_version = extract_app_version(REPO_ROOT)
        assert app_version == __version__, (
            f"app metadata version '{app_version}' != __version__ '{__version__}'"
        )

    def test_all_real_sources_consistent(self) -> None:
        """All managed real sources SHALL equal Authoritative_Version.

        Drives the script's ``collect_sources`` + ``check_consistency`` against
        the live repo; a drift fails loudly with the full {source: version} list.

        Validates: Requirements 4.2, 4.3, 9.2
        """
        sources, extraction_error = collect_sources(REPO_ROOT)
        assert extraction_error is None, (
            f"Source extraction failed (fail-fast): {extraction_error}"
        )

        result = check_consistency(__version__, sources)
        assert result.ok, f"Version inconsistency detected:\n{render_report(result)}"


# Target_Version carried over from keeplink-v1x-improvements (versions aligned to
# 1.2.0). Authoritative source is keeplink_mcp.__version__; these example/golden
# tests pin the concrete literal so a drift away from 1.2.0 fails loudly.
_TARGET_VERSION = "1.2.0"


class TestVersionExamples:
    """Example (golden) tests pinning the concrete Target_Version '1.2.0'."""

    def test_init_version_is_target(self) -> None:
        """__version__ SHALL be the concrete Target_Version '1.2.0'.

        Validates: Requirement 1.1
        """
        assert __version__ == _TARGET_VERSION, (
            f"keeplink_mcp.__version__ is '{__version__}', "
            f"expected Target_Version '{_TARGET_VERSION}'"
        )

    def test_handoverbook_current_marker_is_target(self) -> None:
        """handoverbook Handoverbook_Current_Marker SHALL carry the Target_Version.

        Uses the script's ``extract_handoverbook_version`` (only the top current
        marker `版本：v1.2.0`, never a Historical_Version_Reference) so a stale
        handoverbook version fails loudly (fail-fast).

        Validates: Requirements 5.1, 5.2, 9.5
        """
        marker_version = extract_handoverbook_version(REPO_ROOT)
        assert marker_version == _TARGET_VERSION, (
            f"handoverbook current marker is '{marker_version}', "
            f"expected Target_Version '{_TARGET_VERSION}'"
        )


# Strategy: semver strings (bounded components) so hypothesis exercises both the
# consistent case (all sources equal) and the mismatch case against the SAME
# script-provided check_consistency logic used on the real sources (Req 9.6).
_SEMVER_STRATEGY = st.from_regex(r"\d{1,3}\.\d{1,3}\.\d{1,3}", fullmatch=True)
_SOURCE_NAMES = ["pyproject.toml", "__init__.py", "app.py", "README.md"]


def _make_sources(mapping: dict[str, str]) -> list[VersionSource]:
    """Build a VersionSource list from a {name: version} mapping (test helper)."""
    return [
        VersionSource(name=name, version=version, is_derived=False)
        for name, version in mapping.items()
    ]


class TestVersionConsistencyProperty:
    """Property 1: 一致性判定與不一致報告 (version-consistency-gate)."""

    # Feature: version-consistency-gate, Property 1: 一致性判定與不一致報告
    # The four real Version_Sources are re-read on every example and, driven
    # through the script's check_consistency, must be consistent (ok=True).
    @settings(max_examples=100)
    @given(_seed=st.integers())
    def test_property_1_real_sources_consistent(self, _seed: int) -> None:
        """Real sources stay consistent across repeated collection.

        Validates: Requirements 4.2, 4.3, 9.2, 9.3
        """
        sources, extraction_error = collect_sources(REPO_ROOT)
        assert extraction_error is None, (
            f"Source extraction failed (fail-fast): {extraction_error}"
        )
        result = check_consistency(__version__, sources)
        assert result.ok, f"Version inconsistency detected:\n{render_report(result)}"

    # Feature: version-consistency-gate, Property 1: 一致性判定與不一致報告
    # Drives the SCRIPT's check_consistency: all-equal + semver -> ok True;
    # a single differing source -> ok False and the Version_Report lists every
    # checked source with its value ({來源: 版本}, Req 9.4).
    @settings(max_examples=100)
    @given(
        version=_SEMVER_STRATEGY,
        mismatch_index=st.integers(min_value=0, max_value=len(_SOURCE_NAMES) - 1),
        differing_version=_SEMVER_STRATEGY,
    )
    def test_property_1_check_logic_detects_mismatch(
        self, version: str, mismatch_index: int, differing_version: str
    ) -> None:
        """Consistency judgement + inconsistency report via check_consistency.

        Validates: Requirements 4.2, 4.3, 4.4, 9.3, 9.4
        """
        # Consistent set: every source shares one semver version -> ok True.
        consistent = {name: version for name in _SOURCE_NAMES}
        consistent_result = check_consistency(version, _make_sources(consistent))
        assert consistent_result.ok, (
            f"Expected ok=True for all-equal sources, got:\n"
            f"{render_report(consistent_result)}"
        )

        # Equal-by-chance is not a mismatch; the consistent case already covers it.
        if differing_version == version:
            return

        # Introduce one mismatch -> ok False; report lists the full mapping.
        inconsistent = dict(consistent)
        inconsistent[_SOURCE_NAMES[mismatch_index]] = differing_version
        result = check_consistency(version, _make_sources(inconsistent))

        assert not result.ok, (
            f"Expected ok=False after introducing a mismatch, got:\n"
            f"{render_report(result)}"
        )
        report = render_report(result)
        # Req 9.4: the report lists each checked source and its version value.
        for source, value in inconsistent.items():
            assert source in report, f"Report missing source '{source}':\n{report}"
            assert value in report, f"Report missing value '{value}':\n{report}"
