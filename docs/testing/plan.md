# Test Plan — Personal AI OS

**Last Updated:** 2026-05-17

---

## Critical Paths

| Path | Test location | Status |
|------|--------------|--------|
| Strava webhook → Gemini analysis → Telegram report | `test_sanity_flows.py`, `test_e2e_local.py` | ✅ Covered |
| Morning briefing (cron 06:00) | `test_flow_morning_briefing.py`, `test_sanity_flows.py` | ✅ Covered |
| News briefing (cron 06:30 / 17:30 / 20:00) | `test_e2e_news_flows.py`, `test_news_agent_flows.py` | ✅ Covered |
| Weekly reflection (cron Sunday 20:00) | `test_flow_weekly_reflection.py` | ✅ Covered |
| Manual /sync command | `test_harvest.py`, `test_e2e_coach_commands.py` | ✅ Covered |
| Telegram chat (tool use) | `test_agent.py`, `test_telegram_router.py` | ✅ Covered |
| Retry analysis for missed runs (2h cron) | `test_scheduler.py` | ✅ Covered |
| Admin credential enforcement | — | 🔲 T2 deferred (P0.6 not implemented) |
| Strava HMAC signature verification | — | 🔲 T1 deferred (P3.4 not implemented) |

---

## Deferred Test Items

| ID | Description | Blocked by |
|----|-------------|------------|
| T1 | Strava webhook HMAC signature validation | P3.4 (impl not done) |
| T2 | Admin credential fail-fast validation | P0.6 (impl not done) |
| T5 | Database OperationalError retry | WAL+busy_timeout mitigates; retry impl not written |

---

## Test Spec Guide

When adding a new test:

1. Pick the right file — see [specs.md](specs.md) module index.
2. Use `conftest.py` fixtures for DB and config setup.
3. Mock all external services (no real API calls).
4. Follow AAA pattern: Arrange → Act → Assert.
5. Add import assertion in `test_smoke.py` for any new public symbol.
6. Run `python -m pytest tests/test_smoke.py -v` first before running the full suite.

---

## Regression Checklist (per change type)

See [../process/delivery-checklist.md](../process/delivery-checklist.md) for the full typed checklist.

Quick reference:

| Change type | Minimum tests to run |
|-------------|---------------------|
| `notification.py` | `test_telegram_chunking.py test_notification_document.py` |
| Agent flow | `test_sanity_flows.py test_smoke.py` |
| Scheduler | `test_scheduler.py test_smoke.py` |
| Database | `test_database.py test_smoke.py` |
| Webhook | `test_webhooks.py test_e2e_local.py` |
| Any code change | `test_smoke.py` first, then full suite |
