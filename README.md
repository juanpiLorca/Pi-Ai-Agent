# Pi-Ai-Agent

A Raspberry Pi host for personal service agents. The first agent is an **expense
tracker for Chilean banks**. It reads purchase notification emails, classifies
each purchase (asking you on Telegram when unsure), and records it in Excel by
pay period.

- Design and roadmap: [PLAN.md](PLAN.md)
- Installing on the Pi: [docs/SETUP.md](docs/SETUP.md)

## Layout
```
packages/pi_core/   shared code: SQLite helpers, logging, mail sources (Graph, IMAP), redaction
agents/expenses/    the expense tracker agent (python -m expenses)
deploy/             systemd template unit (pi-agent@<agent>.service)
tools/              redact_eml.py: anonymize .eml files by hand
```

## Development
```bash
uv sync
uv run pytest
uv run python -m expenses --help
```
