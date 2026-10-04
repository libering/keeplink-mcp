# Contributing

## Ground rules

This project has some non-negotiable design decisions. Please don't send PRs that go against these:

1. **SQLite only** — no Redis, no Postgres, no external message queues. The whole point is zero-infra.
2. **No telemetry** — nothing phones home. Ever. Local file logging only.
3. **Localhost by default** — the service binds to `127.0.0.1`. Don't change this.
4. **Minimal deps** — if stdlib can do it, use stdlib. New dependencies need a good reason.

## Setup

```bash
git clone <this repo>
cd KeepLink-MCP
python -m venv .venv
.venv\Scripts\activate   # Windows
source .venv/bin/activate # Unix
pip install -e ".[dev]"
```

Verify everything works:
```bash
ruff check src/ test/
pytest test/ -v
```

## 啟用版本一致性 pre-commit hook

本專案以 `src/keeplink_mcp/__init__.py` 的 `__version__` 作為唯一權威版本來源，並提供受版控的 git pre-commit hook（`.githooks/pre-commit`），於 commit 當下呼叫 `python scripts/check_version.py` 檢查各處版本是否一致。版本不一致時會阻擋該次 commit。

啟用方式（一行，於 repo 根目錄執行）：

```bash
git config core.hooksPath .githooks
```

說明：

- **前置需求**：`python` 需在 PATH 上（若環境的 `python` 指向 Python 2，hook 會自動改用 `python3`）。
- **跨平台**：hook 為 POSIX sh 薄殼，於 Windows 11（透過 git 隨附的 Git Bash）與 Linux（系統 sh）皆可執行；所有跨平台細節（路徑、編碼）由 `scripts/check_version.py` 負責。
- **零外部依賴**：僅需 git 內建設定與本機 `python`，不依賴任何需連外網下載的第三方 pre-commit 框架。
- **第二道防線**：即使未於本機啟用 hook，CI 也會執行同一支 `scripts/check_version.py`，於 push/PR 階段攔截版本不一致。

## Submitting changes

1. Fork & branch from `main`
2. Follow existing patterns — type hints, docstrings, single responsibility
3. Write tests for anything new
4. Make sure all tests pass and lint is clean
5. Open a PR with a short description of what and why

## Code conventions

- Type hints on everything (Python 3.10+ syntax)
- Docstrings on public functions
- Files stay under 500 lines, functions under 50 lines
- `ruff` handles formatting and linting
- Look at existing code and match the style

## Good PRs

- Bug fixes (with a test that reproduces the bug)
- Performance wins (show a benchmark)
- Doc improvements
- CI improvements
- New archive backends — open an issue to discuss first (v2.0 scope)

## Bad PRs

- Adding Redis/RabbitMQ/Postgres
- Adding telemetry or analytics
- Binding to `0.0.0.0`
- Code without tests
- Breaking the MCP tool interface without prior discussion

## Security bugs

Report privately via GitHub Security Advisories. Don't open a public issue.

## License

Your contributions are MIT-licensed, same as the rest of the project.
