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
