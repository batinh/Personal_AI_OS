# Test Strategy — Personal AI OS

**Last Updated:** 2026-05-17

---

## Philosophy

**"Python does math, AI does prose."** Tests verify Python logic; AI output is validated via integration tests with mocked Gemini responses.

- **No live API calls** in any test — all external services (Gemini, Strava, Telegram) are mocked.
- **No Docker required** for unit or integration tests — `TestClient` covers HTTP paths.
- **80% coverage minimum** — enforced by ADR-009.

---

## Test Pyramid

| Layer | Tool | When to run | Files |
|-------|------|-------------|-------|
| Smoke (top gate) | pytest | Always first, < 2s | `test_smoke.py` |
| Sanity / regression | pytest | After any agent/flow change | `test_sanity_flows.py` |
| E2E (local, no Docker) | pytest + TestClient | After any refactor | `test_e2e_local.py`, `test_e2e_*.py` |
| Unit | pytest | Targeted module changes | `test_<module>.py` |
| Integration | pytest | Before commit | `tests/ -q` |

---

## Mocking Strategy

| Dependency | How mocked |
|------------|-----------|
| Google Gemini API | `unittest.mock.patch` on `generate_content` |
| Strava API | `responses` library or `patch("requests.get")` |
| Telegram API | `patch("app.core.notification.send_telegram_msg")` |
| SQLite DB | In-memory `:memory:` fixture in `conftest.py` |
| ChromaDB | Patched in-memory collection |

---

## External Dependencies

| Dependency | Type | Risk level |
|------------|------|------------|
| Google Gemini API | AI/LLM | Rate limit (429), timeout (504) |
| Strava API | REST | OAuth expiry, 503 |
| Telegram Bot API | REST | Rate limit (429), flood |
| SQLite (WAL) | Local DB | Lock contention under concurrent load |
| ChromaDB | Local vector | Embedding model load time |

---

## Test Gate (pre-commit)

```bash
python -m pytest tests/test_smoke.py -v         # MUST pass — < 2s
python -m pytest tests/test_sanity_flows.py -v  # MUST pass
python -m pytest tests/ -q                       # 0 failures required
bash scripts/pre-deploy-check.sh                 # pytest + config + compose syntax
```

---

## Coverage

Current: **1010 passed, 5 skipped, 0 failures** (2026-05-17).

Coverage gate: `pytest --cov-fail-under=80` (enabled per ADR-009 since 2026-04-26).

Areas not yet covered: Strava HMAC verification (T1), Admin credential validation (T2), DB OperationalError retry (T5).

See [specs.md](specs.md) for full module inventory.
