"""Property test for version consistency across all sources.

Validates: Requirements 8.1, 8.2, 8.3

Property 7: Version consistency across sources
For all releases, the version string in pyproject.toml, keeplink_mcp/__init__.py,
and FastAPI app metadata SHALL be identical.
"""

import re
from pathlib import Path

from keeplink_mcp import __version__


def get_pyproject_version() -> str:
    """Extract version from pyproject.toml [project] section.

    Returns:
        Version string from pyproject.toml.
    """
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    content = pyproject_path.read_text(encoding="utf-8")

    # Match version = "X.Y.Z" in [project] section
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not match:
        raise ValueError("Could not find version in pyproject.toml")
    return match.group(1)


def get_init_version() -> str:
    """Get version from keeplink_mcp.__init__.__version__.

    Returns:
        Version string from __init__.py.
    """
    return __version__


def get_app_version() -> str:
    """Extract version from FastAPI app metadata in app.py.

    Returns:
        Version string from FastAPI app definition.
    """
    app_path = Path(__file__).parent.parent / "src" / "keeplink_mcp" / "api" / "app.py"
    content = app_path.read_text(encoding="utf-8")

    # Match version="X.Y.Z" in FastAPI(...) call
    match = re.search(r'FastAPI\([^)]*version\s*=\s*"([^"]+)"', content)
    if not match:
        raise ValueError("Could not find FastAPI version in app.py")
    return match.group(1)


class TestVersionConsistency:
    """Test suite for Property 7: Version consistency across sources."""

    def test_version_format_is_valid(self) -> None:
        """All version strings should follow semantic versioning X.Y.Z."""
        semver_pattern = re.compile(r"^\d+\.\d+\.\d+$")

        pyproject_version = get_pyproject_version()
        init_version = get_init_version()
        app_version = get_app_version()

        assert semver_pattern.match(pyproject_version), (
            f"pyproject.toml version '{pyproject_version}' is not valid semver"
        )
        assert semver_pattern.match(init_version), (
            f"__init__.py version '{init_version}' is not valid semver"
        )
        assert semver_pattern.match(app_version), (
            f"app.py version '{app_version}' is not valid semver"
        )

    def test_pyproject_matches_init(self) -> None:
        """pyproject.toml version SHALL match __init__.py __version__.

        Validates: Requirement 8.1, 8.2
        """
        pyproject_version = get_pyproject_version()
        init_version = get_init_version()

        assert pyproject_version == init_version, (
            f"Version mismatch: pyproject.toml has '{pyproject_version}', "
            f"but __init__.py has '{init_version}'"
        )

    def test_pyproject_matches_app(self) -> None:
        """pyproject.toml version SHALL match FastAPI app metadata version.

        Validates: Requirement 8.1, 8.3
        """
        pyproject_version = get_pyproject_version()
        app_version = get_app_version()

        assert pyproject_version == app_version, (
            f"Version mismatch: pyproject.toml has '{pyproject_version}', "
            f"but app.py has '{app_version}'"
        )

    def test_init_matches_app(self) -> None:
        """__init__.py __version__ SHALL match FastAPI app metadata version.

        Validates: Requirement 8.2, 8.3
        """
        init_version = get_init_version()
        app_version = get_app_version()

        assert init_version == app_version, (
            f"Version mismatch: __init__.py has '{init_version}', "
            f"but app.py has '{app_version}'"
        )

    def test_all_sources_consistent(self) -> None:
        """All three version sources SHALL be identical.

        Validates: Requirements 8.1, 8.2, 8.3

        This is the primary property test that validates Property 7
        from the design document.
        """
        versions = {
            "pyproject.toml": get_pyproject_version(),
            "__init__.py": get_init_version(),
            "app.py": get_app_version(),
        }

        unique_versions = set(versions.values())

        assert len(unique_versions) == 1, (
            f"Version inconsistency detected: {versions}. "
            f"All sources must have the same version."
        )
