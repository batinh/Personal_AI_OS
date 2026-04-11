# AGENTS.md — Cursor & AI harness

This file orients **Cursor** (and other agents that read `AGENTS.md`). The repository’s full engineering playbook is **`CLAUDE.md`** — keep them aligned when you change workflows or conventions.

## Read first

| Document | Purpose |
|----------|---------|
| **[CLAUDE.md](CLAUDE.md)** | Commands, dev→deploy gate, file map, language zones, DB/prompt rules, ISSUES/features |
| **[.cursor/rules/personal-ai-os.mdc](.cursor/rules/personal-ai-os.mdc)** | Short always-on rules for Cursor |
| **[.cursorignore](.cursorignore)** | Paths excluded from Cursor indexing (mirrors `.claudeignore`) |

## Cursor layout

```
.cursor/
  rules/
    personal-ai-os.mdc   # alwaysApply — core gates & zones
```

## Parity with Claude Code

| Claude | Cursor equivalent |
|--------|-------------------|
| `CLAUDE.md` | Same file + this `AGENTS.md` pointer |
| `.claudeignore` | `.cursorignore` (same patterns) |
| `.claude.json` | Optional: Cursor MCP in Settings / project config |

When you update global project rules, prefer editing **`CLAUDE.md`** and refreshing **`.cursor/rules/personal-ai-os.mdc`** if the summary there should change.

## Quick commands (from CLAUDE.md)

```bash
python -m pytest tests/test_smoke.py -v
python -m pytest tests/ -q
bash scripts/pre-deploy-check.sh
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
docker compose up --build
```

Before commit: `docs/pragmatic_review_checklist.md`. New feature (≥2 files): `docs/features/{slug}.md`. Tracking: `docs/ISSUES.md`.
