# Feature Design: News Agent Overhaul

**Slug:** `news-agent-overhaul`
**Status:** Implemented
**Issues:** ISS-001, ISS-002, ISS-003, ISS-004, ISS-005, ISS-006
**Date:** 2026-04-11

---

## Problem Statement

The news agent had several critical bugs and missing features:

1. **Alert spam**: One Telegram message per article on each watch cycle.
2. **Prompt leak**: Raw Gemini prompt text appeared in Telegram instead of AI response.
3. **No links in digest**: System instruction explicitly banned URLs from summaries.
4. **No agent routing**: All free-text Telegram messages went to the coach only.
5. **Wrong schedule**: Morning at 07:00, afternoon at 17:00, no evening briefing.
6. **No design doc convention**: No standard location for feature documentation.

---

## Design Decisions

### Alert batching (ISS-001, ISS-002)

**Before:** `for article in articles: build_alert_prompt(article) → send_telegram_msg()`  
**After:** `collect all alert articles → one build_batch_alert_prompt() → one Gemini call → one send_telegram_msg()`

The batch approach also fixes the prompt leak: the prompt now goes to Gemini first and only the AI response is forwarded to Telegram.

### Quiet hours + shock threshold (ISS-005)

- `alert_threshold` (default 7): normal threshold during active hours (06:00–22:00)
- `shock_threshold` (default 9): stricter threshold applied during quiet hours (22:00–06:00)
- This allows truly breaking news (score 9–10) to wake the user while suppressing noise at night.

### Agent routing via prefix (ISS-004)

Opt-in design: users must explicitly prefix `@news` or `@tin` to reach the news agent.  
Default remains coach to avoid false positives on Vietnamese text that might incidentally mention news.

The router (`app/services/telegram_router.py`) is decoupled from both agents for future extensibility.

### Embedded links (ISS-003)

Articles' `link` field is now passed into both digest and alert prompts. The system instruction is updated to require `<a href="URL">Đọc thêm</a>` HTML links for each article (Telegram HTML mode).

### Evening briefing (ISS-005)

Third scheduled session at 20:00 using `session="evening"`. Agent maps session → Vietnamese label: `morning → sáng`, `afternoon → chiều`, `evening → tối`.

### Documentation convention (ISS-006)

Design docs live in `docs/features/<slug>.md` (this file). CLAUDE.md File Map updated.

---

## Files Changed

| File | Change |
|------|--------|
| `app/agents/news/prompts.py` | Remove link ban; add links to digest template; replace single-article `build_alert_prompt` with `build_batch_alert_prompt` |
| `app/agents/news/alert_engine.py` | Add Gemini client; `_is_quiet_hours()`; `shock_threshold`; batch collection; single consolidated send |
| `app/agents/news/agent.py` | Add `evening` → `tối` session label mapping |
| `app/agents/news/telegram_handler.py` | Add `handle_news_chat()`; add `evening` to valid flows; update help text |
| `app/services/telegram_router.py` | New — `route_message()` dispatches `@news`/`@tin` to news agent |
| `app/routers/webhooks.py` | Step 4 uses `telegram_router` before dispatching |
| `app/services/scheduler.py` | Add `task_evening_news()`; update defaults to 06:30/17:30; add evening job |
| `config.example.json` | Add `evening_time`, `shock_threshold`; update morning/afternoon defaults |
| `tests/test_news_alert_engine.py` | Replace `build_alert_prompt` mocks; add multi-article, quiet hours tests |
| `tests/test_telegram_router.py` | New — full coverage of `route_message()` |
| `tests/test_scheduler.py` | Update job count from 9 → 10 |

---

## Config Reference

```json
"news_agent": {
    "morning_time": "06:30",
    "afternoon_time": "17:30",
    "evening_time": "20:00",
    "alert_threshold": 7,
    "shock_threshold": 9,
    "topic_cooldown_hours": 2
}
```

---

## Telegram Usage

| Input | Routed to |
|-------|-----------|
| `/news` | News agent — morning briefing |
| `/news afternoon` | News agent — afternoon briefing |
| `/news evening` | News agent — evening briefing |
| `/news watch` | News agent — immediate alert scan |
| `@news câu hỏi` | News agent chat |
| `@tin câu hỏi` | News agent chat |
| Any other text | Coach (default) |

---

## ISS-017 — UX Overhaul: Compact Format, Inline Links, Per-Session Control (2026-05-10)

### Changes

**1. Compact 1-line summaries (removed trend/analysis sections)**
- Rewrote `_TOPIC_SYSTEM_INSTRUCTION` in `prompts.py` to output only `📰 <b>Title</b> — 1-sentence summary.` per article
- Removed `📊 Phân tích:` and `📈 Xu hướng:` blocks entirely

**2. Inline links per article**
- Replaced `_build_sources_block()` with `_inject_inline_links(body, urls)` in `agent.py`
- Post-processing: splits on `📰` regex markers, pairs grounding URLs in order, appends `<a href="...">→ đọc thêm</a>` inline after each article

**3. Per-session enable/disable toggle**
- `config.example.json`: added `news_agent.sessions.{morning,afternoon,evening}` booleans (default `true`)
- `agent.py` `generate_news_briefing()`: checks `sessions_cfg.get(session, True)` before calling Gemini
- `scheduler.py`: added `_is_session_enabled()` helper; all 3 task functions call it before running
- Web UI (`console.html`): checkbox per session label; `console.py` `POST /console/save-news` reads `session_morning`, `session_afternoon`, `session_evening` form fields

### Config Schema

```json
"news_agent": {
  "sessions": {
    "morning": true,
    "afternoon": true,
    "evening": false
  }
}
```
