# Feature: News Agent — LLM-Native Architecture

## Status: Implemented (branch: feat/sync-reconcile)

## Problem

The original news agent used an RSS-based multi-step pipeline that was token-wasteful and architecturally complex:

1. Fetch N RSS feeds (N HTTP requests)
2. Filter and dedup articles
3. Call LLM to score/rank articles (Gemini call #1)
4. Call LLM to generate digest from scored articles (Gemini call #2)
5. Separate shock-news alert engine polling every 30 minutes

This resulted in:
- 2 LLM calls per briefing session (sometimes more for alerts)
- N RSS HTTP requests with unreliable article quality (missing summaries, bad titles)
- A polling loop (`run_news_watch`) consuming a scheduler job slot 24/7
- Multiple Telegram messages per day from the alert engine
- No memory of user preferences across sessions

## Solution

Replace the entire pipeline with a single Gemini call using `google_search` grounding.

### Architecture

```
Scheduled trigger (morning/afternoon/evening)
           ↓
    load_news_memory(user_id)       ← SQLite key-value store
           ↓
    build_session_prompt(session, interest_profile, date, memory)
           ↓
    _call_with_search(model, system_inst, prompt)
      └─ types.Tool(google_search=types.GoogleSearch())
      └─ Gemini fetches live articles internally, cites with real URLs
           ↓ (on failure)
    _call_knowledge_only(...)       ← fallback, notes search unavailability
           ↓
    send_telegram_msg(chat_id, reply)
```

### Manual chat flow (user sends `/news morning|afternoon|evening`)

```
User Telegram message
           ↓
    telegram_handler.handle_news_command()
           ↓
    load_news_memory(user_id)
           ↓
    Same _call_with_search() path
           ↓
    send_telegram_msg()
           ↓
    run_extract_in_background()     ← daemon thread, non-blocking
      └─ extract_and_save_signals() ← Gemini extracts liked/disliked topics
      └─ save_news_memory()        ← persists to SQLite
```

## Key Files

| File | Role |
|------|------|
| `app/agents/news/agent.py` | Core agent: grounding call, fallback, send |
| `app/agents/news/prompts.py` | 3 session templates + memory extraction prompt |
| `app/agents/news/memory.py` | Load/save/merge user preferences from SQLite |
| `app/agents/news/telegram_handler.py` | Manual `/news` command handler + memory learning |
| `app/core/database.py` | `news_agent_state` table, `get_news_state`, `set_news_state` |
| `app/services/scheduler.py` | 3 scheduled jobs: news_morning, news_afternoon, news_evening |

## Deleted Files (RSS architecture)

- `app/agents/news/feeds.py` — RSS fetcher, Article dataclass
- `app/agents/news/scorer.py` — LLM-based article scoring
- `app/agents/news/alert_engine.py` — 30-min shock news polling
- `tests/test_news_alert_engine.py`, `test_news_feeds.py`, `test_news_prompts.py`, `test_news_telegram.py`, `test_news.py`, `test_news_agent.py`

## Config

```json
"news_agent": {
    "enabled": true,
    "news_model": "models/gemini-flash-latest",
    "morning_time": "06:30",
    "afternoon_time": "17:30",
    "evening_time": "20:00",
    "telegram_chat_id": "",
    "interest_profile": {
        "technology": 10,
        "sports_running": 8,
        "it_workforce": 9,
        "economics_politics": 7
    }
}
```

**Removed config keys**: `max_articles_per_feed`, `watch_interval_minutes`, `alert_threshold`, `shock_threshold`, `digest_threshold`, `topic_cooldown_hours`, `feeds` array, nested `{weight, keywords}` interest format.

## Memory System

SQLite table `news_agent_state (user_id, key, value, updated_at)` with `PRIMARY KEY (user_id, key)`.

Keys stored per user:
- `liked_topics` — JSON list, max 20, merged from Gemini extraction
- `disliked_topics` — JSON list, max 20
- `extra_notes` — free text, last 500 chars

Extraction runs in a background daemon thread after each Telegram reply to avoid blocking.

## Trade-offs

| | Old RSS | New LLM-native |
|---|---|---|
| LLM calls/briefing | 2 | 1 |
| HTTP requests | N (feeds) | 0 (Gemini handles internally) |
| Alert noise | Yes (shock news) | No |
| Article URLs | Feed-sourced (often broken) | Gemini-cited (grounding) |
| User memory | None | SQLite (liked/disliked topics) |
| Offline fallback | None | Knowledge-only digest |
| Scheduler jobs (news) | 4 (morning+afternoon+evening+watch) | 3 (morning+afternoon+evening) |

## Fallback Behavior

If `google_search` grounding raises an exception (network, quota, etc.):
1. Log the error with `[NEWS]` prefix
2. Call `_call_knowledge_only()` — same prompt without `google_search` tool
3. Vietnamese note appended: "_(Lưu ý: tìm kiếm trực tuyến không khả dụng...)_"
4. If both fail: log error, return without sending Telegram message
