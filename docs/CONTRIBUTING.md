# Contributing — Personal AI OS

## Development Setup

```bash
git clone https://github.com/batinh/Personal_AI_OS.git
cd Personal_AI_OS
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp config.example.json data/config.json
```

See [setup.md](./setup.md) for full environment variable setup.

---

## Language Zones — STRICTLY ENFORCED

| Zone | Scope | Language |
|---|---|---|
| **Zone 1** | Source code, DB schemas, logs, git commits, docstrings | English only |
| **Zone 2** | AI prompt output, Telegram/Strava/email messages, UI text | Vietnamese only |
| **Zone 3** | Prompt builder functions: Python logic in English, injected f-string content in Vietnamese | Mixed |

**Violations are rejected at review.** Examples:
```python
# ❌ Zone 1 violation — Vietnamese function name
def tinhTRIMP():

# ❌ Zone 2 violation — English in Telegram output
send_telegram("Training load is HIGH today")

# ✅ Correct
def calculate_trimp():
    ...  # English

# ✅ Correct — Zone 3 boundary
template = "Hôm nay tải luyện tập rất CAO ({acwr:.2f})"
msg = template.replace("{acwr}", str(acwr))  # .replace() not f-string
```

---

## Architecture Rules

- **Dependency direction**: `routers/ → agents/ → core/` — never import upward
- **Scheduler tasks**: always `def`, never `async def` — `BackgroundScheduler` is a thread pool
- **File paths**: always `Path(__file__).resolve().parent...` — never relative paths (Docker WORKDIR=/app breaks them)
- **Agent context**: always use `build_agent_context()` — never duplicate context building in flows
- **Patch paths in tests**: target where the symbol is **imported**, not where it's defined:
  ```python
  # ✅ correct
  @patch("app.agents.coach.tools.calculate_acwr")
  # ❌ wrong
  @patch("app.agents.coach.utils.calculate_acwr")
  ```

---

## Testing Workflow

Tests must pass before every push:

```bash
python -m pytest tests/ -q
# Expected: 273 passed, 0 failed
```

### TDD — Required for new features

1. **RED**: Write a failing test first
2. **GREEN**: Implement minimum code to pass
3. **REFACTOR**: Clean up while tests stay green
4. Verify coverage: `python -m pytest tests/ --cov=app`

### Test structure

- `tests/conftest.py` — session-level stubs for `google.genai` and `chromadb` (both are MagicMock — cannot inspect internal call arguments; patch builders directly)
- Each test file maps to one module (e.g., `test_news_agent.py` → `app/agents/news/agent.py`)
- See [DELIVERY_CHECKLIST.md](./testing/DELIVERY_CHECKLIST.md) for the minimum test set per change type

---

## Pre-Commit Checklist

Run mentally against [pragmatic_review_checklist.md](./pragmatic_review_checklist.md) before every commit:

- [ ] Zone 1 compliance: all function names, variables, docstrings in English
- [ ] No hardcoded secrets — all in `.env`
- [ ] `try/except` around all external calls (Gemini, Telegram, Strava)
- [ ] `WHERE user_id = ?` in every SQL query
- [ ] `.replace()` not f-strings for injecting external content into prompts
- [ ] `python -m pytest tests/ -q` → 273 passed, 0 failed

---

## Commit Format

```
type: short description in English

Optional body explaining the why.
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`

Examples:
```
feat: add news agent with morning/afternoon Telegram briefings
fix: patch path mismatch for flows/ submodule tests
docs: update README test count badge to 273
```

---

## Pull Request Process

1. Create a feature branch from `main`
2. Make changes with tests
3. Run `./scripts/pre-deploy-check.sh` — must exit 0
4. Open PR against `main`
5. PR description should include:
   - What changed and why
   - Test modules run
   - Manual verification steps (if any)
