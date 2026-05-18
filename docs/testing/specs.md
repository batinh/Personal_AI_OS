# Test Specifications — Personal AI OS

**Total Tests:** 1010 passed, 5 skipped, 0 failures
**Last Updated:** 2026-05-17
**Runner:** `python -m pytest tests/ -q`

---

## Module Index

| Module | File | Description |
|--------|------|-------------|
| Smoke | `test_smoke.py` | Import assertions — runs first, < 2s |
| Sanity | `test_sanity_flows.py` | 58 flow-level regression tests |
| E2E (local) | `test_e2e_local.py` | 28 HTTP-level tests via TestClient |
| E2E (coach commands) | `test_e2e_coach_commands.py` | Coach Telegram command flows |
| E2E (console UI) | `test_e2e_console_ui.py` | Admin console endpoint tests |
| E2E (edge cases) | `test_e2e_integration_edge_cases.py` | Error/boundary condition E2E tests |
| E2E (news flows) | `test_e2e_news_flows.py` | News agent end-to-end flows |
| E2E (scheduler tasks) | `test_e2e_scheduler_tasks.py` | Scheduler task execution tests |
| Agent | `test_agent.py` | Coach agent: retry, backoff, analysis flows |
| Athlete State | `test_athlete_state.py` | Athlete state model tests |
| Audit | `test_audit.py` | Log audit entry persistence |
| Config | `test_config.py` | Config load, cache, thread-safety |
| Coverage Metrics | `test_coverage_metrics.py` | Coverage service tests |
| Daily Suggestion | `test_daily_suggestion.py` | Daily training suggestion logic |
| Database | `test_database.py` | CRUD, multi-tenant isolation, WAL |
| Database Raw | `test_database_run_activity_raw.py` | Raw activity storage |
| Flow: Memory Extraction | `test_flow_memory_extraction.py` | Memory extraction flow |
| Flow: Morning Briefing | `test_flow_morning_briefing.py` | Morning briefing generation |
| Flow: Run Analysis | `test_flow_run_analysis.py` | Run analysis flow |
| Flow: Weekly Plan | `test_flow_weekly_plan.py` | Weekly plan generation |
| Flow: Weekly Reflection | `test_flow_weekly_reflection.py` | Weekly reflection flow |
| Garmin Client | `test_garmin_client.py` | Garmin API client |
| Gear Tracker | `test_gear_tracker.py` | Shoe/gear tracking |
| Harvest | `test_harvest.py` | Manual sync, ingest, dedup |
| Intent Classification | `test_intent_classification.py` | Tool-use intent routing |
| Metrics Engine | `test_metrics_engine.py` | TRIMP, ACWR, GCS calculations |
| News | `test_news.py` | News feed fetch and dedup |
| News Agent Flows | `test_news_agent_flows.py` | Full news agent flow |
| News Agent Helpers | `test_news_agent_helpers.py` | Link injection, HTML helpers |
| News Agent Thinking | `test_news_agent_thinking.py` | Extended thinking mode |
| News Alert Engine | `test_news_alert_engine.py` | Breaking news detection |
| News Memory | `test_news_memory.py` | News article dedup memory |
| News Prompts | `test_news_prompts.py` | News prompt builder |
| News Telegram | `test_news_telegram.py` | News Telegram handler |
| Notification | `test_notification.py` | HTML send, fallback, telemetry |
| Notification Document | `test_notification_document.py` | Attachment fallback via sendDocument |
| Scheduler | `test_scheduler.py` | Job registration, exception recovery |
| SDK Contracts | `test_sdk_contracts.py` | HttpOptions timeout unit assertions (ms) |
| Setup Flow | `test_setup_flow.py` | Setup UI and Garmin auth flow |
| Strava Client | `test_strava_client.py` | OAuth token refresh, API calls |
| Stream Storage | `test_stream_storage.py` | JSON stream file read/write |
| Telegram Chunking | `test_telegram_chunking.py` | HTML-balanced chunk splitting |
| Telegram Router | `test_telegram_router.py` | Command routing, write-intent detection |
| Tools | `test_tools.py` | Agent tool functions |
| Tools (Run Details) | `test_tools_get_run_full_details.py` | Full run detail fetching |
| Utils | `test_utils.py` | Sports science: TRIMP, decoupling, zones |
| Webhooks | `test_webhooks.py` | Strava + Telegram webhook endpoints |

---

## Coverage by Area

| Area | Coverage | Notes |
|------|----------|-------|
| Core notification | High | HTML chunking, attachment fallback, env vars |
| Coach agent flows | High | Analysis, briefing, reflection, memory |
| News agent | High | Alerts, scoring, dedup, Telegram handler |
| Database | High | CRUD, multi-tenant, WAL, raw activity |
| Scheduler | High | All 16 jobs registered, exception recovery |
| Webhook routing | High | Strava + Telegram, JSON error handling |
| SDK contracts | Targeted | HttpOptions timeout in ms, safe-range assertion |
| Admin credentials | Not tested (T2 deferred) | P0.6 not yet implemented |
| Strava HMAC verify | Not tested (T1 deferred) | P3.4 not yet implemented |

---

## Execution Order

```bash
python -m pytest tests/test_smoke.py -v          # 1st — catches import errors (< 2s)
python -m pytest tests/test_sanity_flows.py -v   # 2nd — flow regression (~ 5s)
python -m pytest tests/test_e2e_local.py -v      # 3rd — HTTP paths (no Docker)
python -m pytest tests/ -q                        # Full suite — gate before commit
```

See `CLAUDE.md` for the full Dev → Test → Deploy workflow.
