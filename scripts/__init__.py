"""``scripts`` package marker.

Makes ``scripts`` an importable package so tests (and other tooling) can do
``from scripts.check_version import ...`` — the version-consistency test suite
imports the Version_Check_Script functions directly instead of duplicating the
extraction / consistency logic (DRY, Req 9.6).
"""
