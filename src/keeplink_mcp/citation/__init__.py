"""Citation package: deterministic, pure-function citation building.

Exposes the Citation dataclass and build_citation helper used by the
Cite_Endpoint and the archive_and_cite MCP tool. Performs no I/O.
"""

from __future__ import annotations

from keeplink_mcp.citation.builder import Citation, build_citation, normalize_title

__all__ = ["Citation", "build_citation", "normalize_title"]
