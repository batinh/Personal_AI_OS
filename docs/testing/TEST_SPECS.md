# Test Specifications — Personal AI OS

**Total Tests:** 216 (202 passing / 14 failing pre-existing)  
**Last Updated:** 2026-03-23

---

## Module Index

| Module | File | Tests | Pass | Fail |
|---|---|---|---|---|
| Agent Flows | `test_agent.py` | 20 | 12 | 8 |
| Config Management | `test_config.py` | 8 | 8 | 0 |
| Database CRUD | `test_database.py` | 38 | 35 | 3 |
| Database Raw Activity | `test_database_run_activity_raw.py` | 3 | 1 | 2 |
| Harvest / Sync | `test_harvest.py` | 13 | 13 | 0 |
| Notifications | `test_notification.py` | 20 | 20 | 0 |
| Stream Storage | `test_stream_storage.py` | 12 | 12 | 0 |
| Strava Client | `test_strava_client.py` | 20 | 20 | 0 |
| Tools | `test_tools.py` | 21 | 21 | 0 |
| Tools (Run Details) | `test_tools_get_run_full_details.py` | 3 | 3 | 0 |
| Utils (Science) | `test_utils.py` | 37 | 37 | 0 |
| Webhooks / Endpoints | `test_webhooks.py` | 18 | 18 | 0 |

---

## 1. test_webhooks.py — HTTP Endpoint Tests

### Class: TestStravaWebhookVerification

| ID | Test Name | Input | Expected | Status |
|---|---|---|---|---|
| WH-01 | `test_valid_token_returns_challenge` | GET /webhook with matching verify_token | `{"hub.challenge": "abc123"}` | ✅ |
| WH-02 | `test_invalid_token_returns_error` | GET /webhook with wrong token | `{"error": "Invalid token"}` | ✅ |
| WH-03 | `test_missing_params_returns_error` | GET /webhook with no params | `{"error": "Invalid token"}` | ✅ |

### Class: TestStravaWebhookCreate

| ID | Test Name | Input | Expected | Status |
|---|---|---|---|---|
| WH-04 | `test_create_activity_triggers_workflow` | POST activity create event | `run_strava_workflow("12345678")` called | ✅ |
| WH-05 | `test_non_activity_event_ignored` | POST athlete update event | No workflow triggered | ✅ |
| WH-06 | `test_update_event_ignored` | POST activity update event | No workflow triggered | ✅ |

### Class: TestStravaWebhookDelete

| ID | Test Name | Input | Expected | Status |
|---|---|---|---|---|
| WH-07 | `test_delete_activity_triggers_cleanup` | POST activity delete event | `handle_deleted_activity("99999999")` called | ✅ |

### Class: TestStravaWorkflowOrchestration

| ID | Test Name | Input | Expected | Status |
|---|---|---|---|---|
| WH-08 | `test_full_create_workflow` | activity_id with mocked Strava/Gemini | DB saved → Gemini analyzed → RAG memorized → Telegram + Email sent | ✅ |
| WH-09 | `test_workflow_skipped_when_paused` | service_active=False | load_config never called | ✅ |

### Class: TestDeletedActivityCleanup

| ID | Test Name | Input | Expected | Status |
|---|---|---|---|---|
| WH-10 | `test_delete_cleans_all_layers` | activity_id "99999" | DB delete + RAG forget + Telegram notification | ✅ |

### Class: TestTelegramWebhook

| ID | Test Name | Input | Expected | Status |
|---|---|---|---|---|
| WH-11 | `test_sync_command_default` | `/sync` | `execute_manual_sync("12345", 3, None)` | ✅ |
| WH-12 | `test_sync_command_with_limit` | `/sync 10` | `execute_manual_sync("12345", 10, None)` | ✅ |
| WH-13 | `test_sync_command_month` | `/sync month` | `execute_manual_sync("12345", 50, 30)` | ✅ |
| WH-14 | `test_standup_command` | `/standup` | Telegram progress msg + morning briefing triggered | ✅ |
| WH-15 | `test_regular_chat_routed_to_ai` | Regular text message | `handle_telegram_chat(...)` called | ✅ |
| WH-16 | `test_no_message_field_returns_ok` | Non-message update (edited_message) | `{"status": "ok"}` | ✅ |
| WH-17 | `test_empty_text_still_routes` | Message with no text (photo/sticker) | Routes to AI with empty string | ✅ |

### Class: TestDuplicateWebhookResilience

| ID | Test Name | Input | Expected | Status |
|---|---|---|---|---|
| WH-18 | `test_duplicate_creates_both_trigger` | Same create event posted twice | workflow called twice (no dedup — documented behavior) | ✅ |

---

## 2. test_strava_client.py — StravaClient Tests

### Class: TestTokenCaching

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| SC-01 | `test_first_call_refreshes_token` | Cold start | HTTP POST to Strava auth | ✅ |
| SC-02 | `test_second_call_uses_cache` | Token has 2h left | Only 1 HTTP call total | ✅ |
| SC-03 | `test_expired_token_triggers_refresh` | Token expires in 30s (< 60s buffer) | Triggers re-refresh | ✅ |
| SC-04 | `test_token_refresh_failure_returns_none` | Network exception | Returns `None` | ✅ |

### Class: TestGetActivityData

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| SC-05 | `test_non_run_activity_returns_none` | Activity type = "Ride" | Returns `(None, None, None, None)` | ✅ |
| SC-06 | `test_api_error_returns_none_tuple` | Strava returns 401 | Returns `(None, None, None, None)` | ✅ |
| SC-07 | `test_no_token_returns_none_tuple` | Token refresh fails | Returns `(None, None, None, None)` | ✅ |
| SC-08 | `test_no_streams_returns_meta_only` | Streams endpoint 404 | Returns `(name, None, meta, None)` | ✅ |

### Class: TestGetActivityStreamsRaw

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| SC-09 | `test_success_returns_json` | 200 OK | Returns JSON dict | ✅ |
| SC-10 | `test_error_status_returns_none` | 500 error | Returns `None` | ✅ |
| SC-11 | `test_network_error_returns_none` | ConnectionError | Returns `None` | ✅ |
| SC-12 | `test_no_token_returns_none` | No valid token | Returns `None` | ✅ |

### Class: TestUpdateActivityDescription

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| SC-13 | `test_success_returns_true` | 200 OK | Returns `True` | ✅ |
| SC-14 | `test_failure_returns_false` | 403 Forbidden | Returns `False` | ✅ |
| SC-15 | `test_network_error_returns_false` | ConnectionError | Returns `False` | ✅ |

### Class: TestGetRecentActivities

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| SC-16 | `test_success_returns_list` | 200 OK | Returns list of 2 activities | ✅ |
| SC-17 | `test_api_error_returns_empty_list` | 429 Too Many Requests | Returns `[]` | ✅ |
| SC-18 | `test_network_error_returns_empty_list` | DNS failure | Returns `[]` | ✅ |

### Class: TestGetAthleteStats

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| SC-19 | `test_success_returns_km_distances` | 200 with distance data | Distances converted to km | ✅ |
| SC-20 | `test_error_returns_none` | 500 error | Returns `None` | ✅ |

---

## 3. test_config.py — Config Management Tests

### Class: TestConfigLoadSave

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| CF-01 | `test_load_returns_config_data` | Valid config.json | Returns correct dict | ✅ |
| CF-02 | `test_save_then_load_roundtrip` | Save + reload | Loaded data matches saved data | ✅ |

### Class: TestConfigCaching

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| CF-03 | `test_second_load_uses_cache` | File changed on disk | Returns cached (old) value | ✅ |
| CF-04 | `test_save_invalidates_cache` | save_config() called | Next load_config() reads fresh | ✅ |

### Class: TestConfigAutoInit

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| CF-05 | `test_auto_init_from_example` | config.json missing, example exists | config.json auto-created | ✅ |
| CF-06 | `test_no_example_returns_empty` | Both files missing | Returns `{}` | ✅ |

### Class: TestCorruptedConfig

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| CF-07 | `test_invalid_json_returns_empty_dict` | `{invalid json` in file | Returns `{}` | ✅ |
| CF-08 | `test_empty_file_returns_empty_dict` | Empty file | Returns `{}` | ✅ |

---

## 4. test_harvest.py — Data Harvest Tests

### Class: TestBuildActivityRecord

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| HV-01 | `test_basic_conversion` | Full Strava activity dict | Correct km/min/TRIMP values | ✅ |
| HV-02 | `test_zero_distance` | distance=0 (indoor treadmill) | distance_km=0, no crash | ✅ |
| HV-03 | `test_missing_optional_fields` | No suffer_score, no max_hr | suffer_score=0, max_hr=0 | ✅ |
| HV-04 | `test_none_suffer_score` | suffer_score=None from API | suffer_score=0 | ✅ |
| HV-05 | `test_activity_id_from_activity_id_key` | Dict has `activity_id` not `id` | Correctly maps field | ✅ |

### Class: TestHarvestData

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| HV-06 | `test_harvest_saves_only_runs` | Mix of Run, Ride, TrailRun | Only Run + TrailRun saved (2 of 3) | ✅ |
| HV-07 | `test_harvest_aborts_without_chat_id` | No TELEGRAM_CHAT_ID | get_recent_activities never called | ✅ |

### Class: TestExecuteManualSync

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| HV-08 | `test_sync_skips_existing_rag_memories` | Activity already in ChromaDB | DB updated, streams NOT fetched | ✅ |
| HV-09 | `test_sync_fetches_streams_for_missing_rag` | Activity NOT in ChromaDB | Streams fetched, RAG memorized | ✅ |
| HV-10 | `test_sync_with_no_activities_sends_warning` | Empty activity list | 2 Telegram messages sent | ✅ |
| HV-11 | `test_sync_filters_non_run_activities` | Only Ride + Swim | Nothing saved | ✅ |
| HV-12 | `test_sync_month_filters_by_date` | Old activity (2020) + future | Only future-dated activity processed | ✅ |

### Class: TestManualSyncRateLimiting

| ID | Test Name | Scenario | Expected | Status |
|---|---|---|---|---|
| HV-13 | `test_sleep_between_activities` | 3 activities needing sync | `time.sleep(1)` called 3 times | ✅ |

---

## 5. Pre-existing Known Failures (Tracked, Not Regressions)

These 14 tests were failing before Phase 3 work. Root cause: import path mismatch after agent.py refactor split flows into separate modules.

| ID | Test | Root Cause | Priority |
|---|---|---|---|
| KF-01 | `test_agent::TestGenerateMorningBriefing::test_sends_briefing_to_telegram` | Patch path targets old `agent.generate_morning_briefing`, now in `flows/morning_briefing.py` | High |
| KF-02 | `test_agent::TestGenerateMorningBriefing::test_api_error_does_not_crash` | Same as KF-01 | High |
| KF-03 | `test_agent::TestExtractImplicitMemory::test_valid_json_response_inserts_memory` | Patch path mismatch post-refactor | High |
| KF-04 | `test_agent::TestExtractImplicitMemory::test_inactive_status_is_passed_to_db` | Same as KF-03 | High |
| KF-05 | `test_agent::TestExtractImplicitMemory::test_invalid_json_does_not_crash` | Same as KF-03 | High |
| KF-06 | `test_agent::TestExtractImplicitMemory::test_multiple_items_all_inserted` | Same as KF-03 | High |
| KF-07 | `test_agent::TestGenerateWeeklyReflection::test_sends_reflection_to_telegram_and_memorizes` | Patch path mismatch post-refactor | High |
| KF-08 | `test_agent::TestGenerateWeeklyReflection::test_api_error_does_not_crash` | Same as KF-07 | High |
| KF-09 | `test_database::TestRunActivities::test_gcs_placeholder_created_before_harvest` | Schema mismatch: expects `gcs_score` placeholder column | Medium |
| KF-10 | `test_database::TestChatHistory::test_save_and_load_messages` | Timestamp format difference in temp DB vs main DB | Medium |
| KF-11 | `test_database::TestChatHistory::test_load_returns_chronological_order` | Same as KF-10 | Medium |
| KF-12 | `test_database::TestCoreMemory::test_inactive_overrides_active_for_same_category` | Dedup logic changed: now inserts both rows, latest wins via MAX(rowid) | Medium |
| KF-13 | `test_database_run_activity_raw::test_save_and_get_run_activity_raw_with_stream_file_path` | Schema mismatch: `stream_file_path` column missing in test DB init | Medium |
| KF-14 | `test_database_run_activity_raw::test_save_run_activity_raw_upserts_and_updates_stream_file_path` | Same as KF-13 | Medium |
