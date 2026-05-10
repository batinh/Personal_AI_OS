# Issue Tracker — Personal AI OS

Track bugs, features, and implementation changes. Reported by user or AI.
**Reporter:** `U` = User · `AI` = AI analysis · `U+AI` = both

---

## How to add an issue

1. Pick the next ID from the open or closed table (`ISS-NNN`).
2. Add a row to the Open table.
3. Add a detail section at the bottom under the matching type heading.
4. When done: move row to Closed, add `Commit` and `Closed` date.

**Type:** `bug` · `feature` · `enhancement` · `refactor`
**Priority:** `Critical` · `High` · `Medium` · `Low`

---

## Open

| ID | Type | Title | Priority | Reporter | Date | Module |
|----|------|-------|----------|----------|------|--------|
| [ISS-013](#iss-013--multi-tenant-expansion-blocked-by-single-user-design) | feature | Multi-tenant expansion blocked by single-user design | Low | U+AI | 2026-04-26 | `app/agents/coach/` |
| [ISS-014](#iss-014--no-onboarding-guide-for-physiology-config-fields) | feature | No onboarding guide for physiology config fields (LTHR, rFTP) | Low | U+AI | 2026-04-26 | `docs/`, `config.example.json` |
---

## Closed

| ID | Type | Title | Priority | Reporter | Date Found | Closed | Commit | Module |
|----|------|-------|----------|----------|------------|--------|--------|--------|
| [ISS-017](#iss-017--news-briefing-ux-overhaul-compact-format--inline-links--per-session-control) | enhancement | News briefing UX: compact format, inline links, per-session control | Medium | U | 2026-05-10 | 2026-05-10 | feat/garmin-coach-planning | `app/agents/news/`, `templates/console.html`, `app/routers/console.py` |
| [ISS-016](#iss-016--garmin-login-blocked-from-server-ip-no-oauth-token-path) | bug | Garmin login times out from server IP — no OAuth token path | Critical | U+AI | 2026-05-10 | 2026-05-10 | feat/garmin-coach-planning | `garmin_client.py`, `console.py`, `console.html` |
| [ISS-015](#iss-015--morning-briefing-guard-2-crashes-type-mismatch) | bug | Morning briefing Guard 2 crashes — type mismatch `str` vs `list` | Critical | U+AI | 2026-05-02 | 2026-05-02 | `db34ebe` | `app/agents/coach/agent.py` |
| [ISS-001](#iss-001--alert-prompt-leaks-raw-template-to-telegram) | bug | Alert prompt leaks raw template to Telegram | Critical | U+AI | 2026-04-11 | 2026-04-11 | `47c63c7` | `alert_engine.py` |
| [ISS-002](#iss-002--alert-engine-sends-one-message-per-article-spam) | bug | Alert engine sends one message per article (spam) | High | U+AI | 2026-04-11 | 2026-04-11 | `47c63c7` | `alert_engine.py` |
| [ISS-003](#iss-003--digest-messages-have-no-embedded-links) | enhancement | Digest messages have no embedded links | High | U | 2026-04-11 | 2026-04-11 | `47c63c7` | `prompts.py`, `agent.py` |
| [ISS-004](#iss-004--telegram-messages-always-routed-to-coach-no-news-agent-routing) | feature | Telegram messages always routed to coach — no news agent routing | High | U | 2026-04-11 | 2026-04-11 | `47c63c7` | `webhooks.py` |
| [ISS-005](#iss-005--news-schedule-not-fixed-at-0630--1730--2000) | feature | News schedule not fixed at 06:30 / 17:30 / 20:00 | Medium | U | 2026-04-11 | 2026-04-11 | `47c63c7` | `scheduler.py` |
| [ISS-006](#iss-006--no-feature-design-doc-convention-or-location-standard) | refactor | No feature design doc convention or location standard | Low | U | 2026-04-11 | 2026-04-11 | `47c63c7` | `docs/` |
| [ISS-007](#iss-007--news-command-sends-two-messages-loading--result) | bug | `/news` command sends two messages (loading + result) | Medium | U | 2026-04-11 | 2026-04-11 | `f17313f` | `telegram_handler.py` |
| [ISS-008](#iss-008--news-help-message-missing-routing-and-schedule-info) | enhancement | `/news help` missing routing and schedule info | Low | U | 2026-04-11 | 2026-04-11 | `f17313f` | `telegram_handler.py` |
| [ISS-009](#iss-009--feature-design-doc-convention-not-documented) | refactor | Feature design doc convention not documented in CLAUDE.md | Low | U | 2026-04-11 | 2026-04-11 | `f17313f` | `CLAUDE.md`, `docs/` |
| [ISS-F01](#iss-f01--news-scorer-returns-invalid-json) | bug | News scorer returns invalid JSON | High | AI | 2026-03-xx | 2026-03-xx | `fe8d12c` | `scorer.py` |
| [ISS-F02](#iss-f02--reuters-rss-feed-dead) | bug | Reuters RSS feed dead / returns no articles | Medium | AI | 2026-03-xx | 2026-03-xx | `0b711ae` | `feeds.py` |
| [ISS-F03](#iss-f03--relative-file-paths-break-inside-docker) | bug | Relative file paths break inside Docker | Critical | AI | 2026-03-xx | 2026-03-xx | `63864b8` | `config.py`, `database.py` |
| [ISS-F04](#iss-f04--sqlite-wal-mode-not-set--markdown-in-telegram) | bug | SQLite WAL mode not set; Markdown rendering in Telegram | High | AI | 2026-03-xx | 2026-03-xx | `ec46ca2` | `database.py`, prompts |
| [ISS-F05](#iss-f05--duplicate-memory-writes--primary-user-id-scatter) | bug | Duplicate memory writes; primary user ID resolved in multiple places | Medium | AI | 2026-03-xx | 2026-03-xx | `bddd3d1` | `database.py`, `user_context.py` |
| [ISS-F06](#iss-f06--strava-sync-race-condition--wrong-timezone) | bug | Strava sync race condition; wrong timezone on activity timestamps | High | AI | 2026-03-xx | 2026-03-xx | `a474e78` | `webhooks.py`, `strava_client.py` |
| [ISS-F07](#iss-f07--gemini-model-selector-missing-new-models-in-console-ui) | enhancement | Gemini model selector missing new models in console UI | Low | U | 2026-03-xx | 2026-03-xx | `0d0ccb8` | `console.html` |
| [ISS-F08](#iss-f08--templateresponse-api-broke-after-starlette-upgrade) | bug | `TemplateResponse` API broke after Starlette upgrade | High | AI | 2026-03-xx | 2026-03-xx | `6ae9683` | `routers/*.py` |
| [ISS-010](#iss-010--url-cross-contamination-in-news-briefing) | bug | URL cross-contamination — wrong links on news articles | High | U+AI | 2026-04-14 | 2026-04-14 | `2d31b53` | `app/agents/news/agent.py`, `prompts.py` |
| [ISS-011](#iss-011--on-demand-news-query-via-news-query) | feature | On-demand news query via `@news <query>` | Medium | U | 2026-04-14 | 2026-04-14 | `2d31b53` | `app/agents/news/agent.py`, `telegram_handler.py` |
| [ISS-012](#iss-012--gemini-thoughtful-preamble-leaks-into-telegram-news-briefing) | bug | Gemini "thoughtful\n..." preamble leaks into Telegram news briefing | High | U | 2026-04-21 | 2026-04-21 | `1890a20` | `app/core/gemini_utils.py` |

---

## Detail: Closed

### ISS-007 — `/news` command sends two messages (loading + result)

**Type:** bug · **Priority:** Medium · **Reporter:** U · **Date:** 2026-04-11
**Module:** `app/agents/news/telegram_handler.py:84`

**Symptom:**
Every `/news` command produces 2 Telegram messages: `⏳ Đang lấy tin...` immediately, then the full digest.

**Root cause:**
`handle_news_command()` always sends a loading acknowledgement before dispatching to `generate_news_briefing()`. The final message already contains a header (session label), so the loading message is redundant.

**Fix:**
Remove `send_telegram_msg(chat_id, f"⏳ Đang lấy tin <b>{label}</b>...")` from `telegram_handler.py:84`.

---

### ISS-008 — `/news help` missing routing and schedule info

**Type:** enhancement · **Priority:** Low · **Reporter:** U · **Date:** 2026-04-11
**Module:** `app/agents/news/telegram_handler.py`

**Symptom:**
User didn't know they could chat with the news agent via `@news` / `@tin` prefix, or that the agent runs automatically at fixed times.

**Fix:**
Update `_HELP_MSG` to show the automatic schedule (06:30 / 17:30 / 20:00) and the `@news` / `@tin` routing syntax with an example.

---

### ISS-009 — Feature design doc convention not documented

**Type:** refactor · **Priority:** Low · **Reporter:** U · **Date:** 2026-04-11
**Module:** `CLAUDE.md`, `docs/feature_design_template.md`

**Symptom:**
No enforced standard for naming or placing feature design docs. `docs/feature_design_template.md` existed but had no header explaining the convention.

**Fix:**
- Add convention table header to `docs/feature_design_template.md`
- Add "Feature Design Doc Convention" section to `CLAUDE.md`

---

### ISS-001 — Alert prompt leaks raw template to Telegram

**Type:** bug · **Priority:** Critical · **Reporter:** U+AI · **Date:** 2026-04-11
**Module:** `app/agents/news/alert_engine.py:179`

**Symptom:**
User receives a Telegram message containing `"Hãy viết một thông báo cảnh báo ngắn gọn (1-2 dòng), dùng emoji phù hợp, không Markdown."` — the raw prompt template, not an AI-generated alert.

**Root cause:**
`run_news_watch()` calls `build_alert_prompt()` which returns a **prompt string** intended for Gemini.
The result is passed **directly** to `send_telegram_msg()` without going through Gemini first.

```python
# alert_engine.py:179 — BUG: sends prompt, not AI response
alert_msg = build_alert_prompt(scored.title, scored.source, scored.summary, date_str)
send_telegram_msg(chat_id, alert_msg)   # ← raw prompt goes to user
```

**Fix plan:** Batch all alert-worthy articles, call Gemini once, send the AI response. See ISS-002.

---

### ISS-002 — Alert engine sends one message per article (spam)

**Type:** bug · **Priority:** High · **Reporter:** U+AI · **Date:** 2026-04-11
**Module:** `app/agents/news/alert_engine.py`

**Symptom:** When multiple articles pass `alert_threshold` in one watch cycle the user gets N separate Telegram messages within seconds.

**Root cause:** `send_telegram_msg()` is called inside the per-article loop with no batching step.

```python
# alert_engine.py:163 — BUG: fires once per article
for link, scored in scored_articles_map.items():
    if scored.score >= alert_threshold:
        send_telegram_msg(chat_id, alert_msg)   # ← N times
```

**Fix plan:** Collect all passing articles → one `build_batch_alert_prompt()` → one Gemini call → one `send_telegram_msg()`. Fixes ISS-001 at the same time.

---

### ISS-003 — Digest messages have no embedded links

**Type:** enhancement · **Priority:** High · **Reporter:** U · **Date:** 2026-04-11
**Module:** `app/agents/news/prompts.py`, `app/agents/news/agent.py`

**Symptom:** Morning/afternoon digest Telegram messages contain article summaries but no clickable links to sources.

**Root cause:** `_NEWS_SYSTEM_INSTRUCTION` explicitly says _"Không đưa link URL vào bản tóm tắt"_. Article links are available in the data fed to the prompt but Gemini is instructed to omit them.

**Fix plan:**
1. Remove the no-link rule from `_NEWS_SYSTEM_INSTRUCTION`.
2. Add instruction to wrap each item title in `<a href="URL">title</a>` HTML.
3. Pass `article.link` through `build_categorized_digest_prompt()` alongside title/summary.

---

### ISS-004 — Telegram messages always routed to coach — no news agent routing

**Type:** feature · **Priority:** High · **Reporter:** U · **Date:** 2026-04-11
**Module:** `app/routers/webhooks.py`

**Symptom:** Any free-text message in Telegram (not a `/command`) goes to `handle_telegram_chat` (coach). There is no way to address the news agent conversationally.

**Fix plan:**
1. New `app/services/telegram_router.py` — `route_message(text) -> "coach" | "news"` based on `@news` / `@tin` prefix.
2. In `webhooks.py` step 4, call router before dispatching.
3. News-routed messages go to new `handle_news_chat()` in `telegram_handler.py`.

---

### ISS-005 — News schedule not fixed at 06:30 / 17:30 / 20:00

**Type:** feature · **Priority:** Medium · **Reporter:** U · **Date:** 2026-04-11
**Module:** `app/services/scheduler.py`, `config.example.json`

**Symptom:** Default schedule is `morning_time=07:00`, `afternoon_time=17:00`. No evening briefing. No quiet-hours enforcement for breaking alerts.

**Fix plan:**
1. Change defaults to `06:30` / `17:30`.
2. Add `task_evening_news()` + `evening_time: "20:00"` in config.
3. Add `shock_threshold` (default `9`): breaking alerts only fire outside scheduled windows when `score >= shock_threshold`.
4. Quiet hours 22:00–06:00 — alert engine skips sending entirely.

---

### ISS-006 — No feature design doc convention or location standard

**Type:** refactor · **Priority:** Low · **Reporter:** U · **Date:** 2026-04-11
**Module:** `docs/`

**Symptom:** New features have no designated place for design documents. Some designs exist inline in `architecture.md`, some in `changelog.md`, some nowhere.

**Fix plan:**
1. Create `docs/features/` directory.
2. Convention: one file per feature, named `docs/features/<slug>.md`, using `feature_design_template.md` structure.
3. Add entry to CLAUDE.md File Map.

---

## Detail: Closed

### ISS-F01 — News scorer returns invalid JSON

**Type:** bug · **Priority:** High · **Fixed:** `fe8d12c` · **Reporter:** AI
**Root cause:** Gemini flash occasionally returns JSON in markdown code fences or with trailing commas. `_extract_json()` had no fallback.
**Fix:** Forced `response_mime_type="application/json"`; added regex-based fallback parsing in `scorer.py`.

---

### ISS-F02 — Reuters RSS feed dead

**Type:** bug · **Priority:** Medium · **Fixed:** `0b711ae` · **Reporter:** AI
**Root cause:** Reuters retired the RSS endpoint in `config.example.json` — HTTP 404.
**Fix:** Replaced with working alternative feed URL.

---

### ISS-F03 — Relative file paths break inside Docker

**Type:** bug · **Priority:** Critical · **Fixed:** `63864b8` · **Reporter:** AI
**Root cause:** `config.py` and `database.py` used `./data/...` paths. Docker working directory differs from repo root → `FileNotFoundError` on startup.
**Fix:** All paths use `Path(__file__).resolve().parent...`. Now a Non-obvious Rule in `CLAUDE.md`.

---

### ISS-F04 — SQLite WAL mode not set; Markdown in Telegram

**Type:** bug · **Priority:** High · **Fixed:** `ec46ca2` · **Reporter:** AI
**Root cause (WAL):** `get_db_connection()` opened connections without `PRAGMA journal_mode=WAL` — write contention under concurrent scheduler jobs.
**Root cause (Markdown):** Prompts instructed Gemini to use `**bold**` / `##`. Telegram renders plain text by default; raw symbols appeared in messages.
**Fix:** `PRAGMA journal_mode=WAL` added to every connection; all prompts switched to HTML-only (`<b>`, `<i>`).

---

### ISS-F05 — Duplicate memory writes; primary user ID scatter

**Type:** bug · **Priority:** Medium · **Fixed:** `bddd3d1` · **Reporter:** AI
**Root cause:** Memory extraction triggered from multiple call sites without dedup. `TELEGRAM_CHAT_ID` read via `os.getenv()` directly in many modules.
**Fix:** Dedup check in `rag_memory.py`; centralised in `app/core/user_context.get_primary_user_id()`.

---

### ISS-F06 — Strava sync race condition; wrong timezone on timestamps

**Type:** bug · **Priority:** High · **Fixed:** `a474e78` · **Reporter:** AI
**Root cause:** Webhook fired immediately on `create` event before Strava finished processing — incomplete data. Timestamps stored as UTC, displayed as local without conversion.
**Fix:** Full activity fetch with retry; all timestamps in `Asia/Ho_Chi_Minh` via `pytz`.

---

### ISS-F07 — Gemini model selector missing new models in console UI

**Type:** enhancement · **Priority:** Low · **Fixed:** `0d0ccb8` · **Reporter:** U
**Root cause:** `console.html` had a hardcoded `<select>` with stale model IDs.
**Fix:** Added `gemini-2.0-flash`, `gemini-flash-latest` to the dropdown.

---

### ISS-F08 — TemplateResponse API broke after Starlette upgrade

**Type:** bug · **Priority:** High · **Fixed:** `6ae9683` · **Reporter:** AI
**Root cause:** Starlette 1.0 changed `TemplateResponse(name, context)` → `TemplateResponse(request, name, context)`. All console/audit routes raised `TypeError`.
**Fix:** Updated all `TemplateResponse` calls to include `request` as first argument.

---

### ISS-010 — URL cross-contamination in news briefing

**Type:** bug · **Priority:** High · **Reporter:** U+AI · **Date:** 2026-04-14
**Module:** `app/agents/news/agent.py`, `app/agents/news/prompts.py`

**Symptom:**
Telegram news briefing shows incorrect URLs attached to articles — e.g. `marathonhcmc.com` appearing as the "Đọc thêm" link on an AI/semiconductor article.

**Root cause:**
A single Gemini call was used for all topics simultaneously. With `google_search` grounding, Gemini's search results for all topics share the same context window. The model attaches URLs from one topic's search results to a different topic's article summaries.

**Fix:**
Replaced single-call architecture with `ThreadPoolExecutor` parallel per-topic calls (`_call_topic()` in `agent.py`). Each topic gets its own isolated Gemini call with its own `google_search` context — URLs can only come from that topic's search results. Added defensive URL instruction to `_NEWS_TOPIC_SYSTEM_INSTRUCTION` as belt-and-suspenders.

---

### ISS-011 — On-demand news query via `@news <query>`

**Type:** feature · **Priority:** Medium · **Reporter:** U · **Date:** 2026-04-14
**Module:** `app/agents/news/agent.py`, `app/agents/news/telegram_handler.py`, `app/agents/news/prompts.py`, `app/agents/news/memory.py`

**Request:**
User wants to send `@news trending AI` or `@news tình hình kinh tế hôm nay` and get a focused real-time search + synthesis in reply, rather than waiting for the scheduled briefing.

**Implementation:**
1. `generate_on_demand_briefing(query, chat_id, config)` added to `agent.py` — builds a focused system instruction + prompt, calls `_call_gemini_with_search()`, sends reply, returns reply text.
2. `build_on_demand_system_instruction()` and `build_on_demand_prompt(query, date)` added to `prompts.py`.
3. `handle_news_chat()` in `telegram_handler.py` simplified to delegate entirely to `generate_on_demand_briefing()`; memory extraction runs in background daemon thread via `run_extract_in_background()`.
4. `genai.Client` ownership moved to `memory.py` as module-level `_client` — removes coupling between `telegram_handler` and Gemini client initialisation.

---

### ISS-012 — Gemini "thoughtful\n..." preamble leaks into Telegram news briefing

**Type:** bug · **Priority:** High · **Reporter:** U · **Date:** 2026-04-21
**Module:** `app/core/gemini_utils.py`

**Symptom:**
Evening news briefing at 20:00 on 2026-04-21 sent a Telegram message containing the model's internal reasoning chain starting with "thoughtful\nNews Curator (specialist in analyzing a specific topic)...\nSelf-Correction/Reality Check:..." followed by search queries like `Let's search for "chạy bộ thể thao news"`. The actual news content (📊 Phân tích:...) appeared after the leaked reasoning.

**Root cause:**
`_THOUGHT_PREFIX_RE` regex in `gemini_utils.py` was `^thought[\n\r ]\w` — matches "thought\n..." but not "thoughtful\n...". Gemini emitted "thoughtful" as its opener (not the bare word "thought"), so `strip_thought_preamble` passed the text through unchanged.

**Fix:**
Changed regex to `^thought\w*[\n\r ]` — matches any word starting with "thought" ("thought", "thoughtful", "thoughtfully", "thoughts") followed by whitespace. The anchor search (`📊`, HTML tags) then correctly locates the real content start.

**Regression test:**
`tests/test_news_agent_thinking.py::TestStripThoughtPreamble::test_strips_thoughtful_preamble` — reproduces the exact production incident text.

---

## Detail: Open

### ISS-013 — Multi-tenant expansion blocked by single-user design

**Type:** feature · **Priority:** Low · **Reporter:** U+AI · **Date:** 2026-04-26
**Module:** `app/agents/coach/`

**Symptom:**
`get_primary_user_id()` is now the canonical resolver for the coach's target user (TD-001 fixed 3 direct `os.getenv("TELEGRAM_CHAT_ID")` calls). However all flows still assume a single primary user. Adding a second user would require per-flow user-ID injection and per-user config loading.

**Context:**
Noted in Coach Agent PRD v1.0 PO review: "blocks multi-user expansion". Deferred to v1.1 since single-user is the only current requirement.

**Acceptance criteria:**
- `handle_telegram_chat` routes to the correct user's config and history by `chat_id`
- Morning briefing / weekly reflection schedulable per-user
- All DB queries already multi-tenant (`user_id` column required on every table)

---

### ISS-014 — No onboarding guide for physiology config fields (LTHR, rFTP)

**Type:** feature · **Priority:** Low · **Reporter:** U+AI · **Date:** 2026-04-26
**Module:** `docs/`, `config.example.json`

**Symptom:**
`config.example.json` exposes `lthr_bpm`, `rftp_watts`, `threshold_pace_per_km` but gives no guidance on how to determine these values. Most users won't have lab results and won't know where to start, causing these fields to stay at `0` (Karvonen fallback).

**Context:**
Noted in Coach Agent PRD v1.0 PO review: "most users won't configure them". Deferred to v1.1 (nice-to-have).

**Acceptance criteria:**
- `docs/features/onboarding-physiology.md` explains how to derive LTHR (race result or Garmin estimate), rFTP (Stryd test), and threshold pace (5 km race result formula)
- `config.example.json` inline comments reference the guide
- Console admin UI shows field help text when value = 0

---

### ISS-015 — Morning briefing Guard 2 crashes — type mismatch `str` vs `list`

**Type:** bug · **Priority:** Critical · **Reporter:** U+AI · **Date:** 2026-05-02
**Module:** `app/agents/coach/agent.py` (Guard 2 in `generate_morning_briefing`)

**Symptom:**
No morning briefing delivered on 2026-05-02. `/brief` and `/standup` commands silently failed. Scheduler caught the exception and logged it as ERROR without re-raising.

**Root cause:**
`generate_morning_briefing` Guard 2 (no active weekly plan) calls:
```python
recent = get_runs_in_last_days(user_id_str, days=7)  # returns formatted str
compute_daily_suggestion(..., recent_runs=recent, ...)  # expects list[dict]
```
`compute_daily_suggestion` iterates `recent_runs` and calls `.get()` on each element. When passed a string, it iterates characters → `AttributeError: 'str' object has no attribute 'get'`.

**Fix (agent.py Guard 2):**
```python
# Before
state = get_athlete_state(user_id_str) or {}
recent = get_runs_in_last_days(user_id_str, days=7)
suggestion = compute_daily_suggestion(recent_runs=recent, athlete_state=state, ...)

# After
state = get_athlete_state(user_id_str) or "healthy"
suggestion = compute_daily_suggestion(
    recent_runs=[],
    athlete_state=state,
    day_of_week=now.weekday(),
    ...
)
```

**Regression test:** `tests/test_sanity_flows.py::TestMorningBriefingGuard2` (9 tests).

---

### ISS-016 — Garmin login blocked from server IP — no OAuth token path

**Type:** bug · **Priority:** Critical · **Reporter:** U+AI · **Date:** 2026-05-10
**Module:** `app/agents/coach/garmin_client.py`, `app/routers/console.py`, `templates/console.html`

**Symptom:**
"Kết nối thất bại sau 30.0s: Kết nối timeout sau 30s — Garmin đang giới hạn server IP." displayed in console UI when saving Garmin credentials.

**Root cause:**
Garmin's unofficial API returns 429 + CAPTCHA_REQUIRED for all 5 login strategies (mobile+cffi, mobile+requests, widget+cffi, portal+cffi, portal+requests) when called from VPS/server IPs. This is a Garmin-side rate limit — no amount of retry or credential changes will fix it.

**Fix:**
Added OAuth token-based authentication as the primary path:
1. `scripts/garmin_auth_local.py` — run on local machine to authenticate and export `client.dumps()` JSON
2. `save_oauth_token()` / `load_oauth_token()` / `has_oauth_token()` functions in `garmin_client.py`
3. `_get_client()` tries OAuth token first (no SSO needed, works from any IP), then legacy tokens, then full SSO
4. `POST /console/setup/garmin/upload-token` endpoint accepts the token JSON
5. Upload UI in Setup tab with instructions and inline verification

**Regression:** OAuth token path is now first priority in `_get_client()`. Legacy SSO path is preserved as fallback (for local dev).


### ISS-017 — News briefing UX: compact format, inline links, per-session control

**Type:** enhancement · **Priority:** Medium · **Reporter:** U · **Date:** 2026-05-10
**Module:** `app/agents/news/prompts.py`, `app/agents/news/agent.py`, `app/services/scheduler.py`, `app/routers/console.py`, `templates/console.html`, `config.example.json`

**Symptom:**
News briefings were too long (analysis + trend sections added ~40% extra text), source links were grouped at the bottom of each topic instead of inline with each article, and there was no way to disable individual sessions (morning/afternoon/evening) independently.

**Changes:**
1. **Compact format** — Removed `📊 Phân tích:` and `📈 Xu hướng:` sections from `_TOPIC_SYSTEM_INSTRUCTION`. Each article now outputs as a single line: `📰 <b>Title</b> — 1-sentence summary.`
2. **Inline links** — Replaced `_build_sources_block()` with `_inject_inline_links()` in `agent.py`. Each `📰` block gets its grounding URL appended inline as `<a href="...">→ đọc thêm</a>`, paired in order with grounding metadata.
3. **Per-session control** — Added `news_agent.sessions.{morning,afternoon,evening}` boolean config keys. Each scheduler task and `generate_news_briefing()` respects these independently (defaults `True` for backward compat).
4. **Topics manager UI** — Added drag-reorder (Up/Down buttons) topics editor in console News tab. Topics serialized as `topics_json` and saved to `config["news_agent"]["topics"]`.
5. **Session toggles UI** — Added per-session checkboxes below each time input in console News tab.

**Config change (config.example.json):**
```json
"sessions": { "morning": true, "afternoon": true, "evening": true }
```
