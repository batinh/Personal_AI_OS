# Feature: News Agent — LLM-Native Architecture

## Status: Implemented (branch: feat/coach-strava-improvement)

## Problem

### v1 → v2 (this document)

The original RSS-based pipeline was replaced by a single Gemini call with `google_search` grounding (v1). That fixed RSS quality issues but introduced a new bug: **URL cross-contamination** — when all topics are in one context window, Gemini attaches URLs from one topic's search results to articles in another topic (e.g. `marathonhcmc.com` appearing on an AI/semiconductor article).

Additionally, a single call produces shallower per-topic analysis because the model's output budget is spread across all topics simultaneously.

### v2 improvements

1. **Parallel per-topic calls** — each topic gets its own isolated Gemini call with `google_search`, eliminating URL cross-contamination and allowing deeper per-topic synthesis.
2. **On-demand briefing** — user sends `@news <query>` via Telegram; agent performs a focused search and replies with a structured analysis.

## Architecture

### Scheduled briefing (parallel per-topic)

```
Scheduled trigger (morning/afternoon/evening)
           ↓
    _resolve_topics(config)   ← topics list or fallback from interest_profile
           ↓
    ThreadPoolExecutor (max 4 workers)
    ┌──────────────────────────────────────┐
    │  _call_topic("AI & Công nghệ", ...)  │ ← Gemini call #1
    │  _call_topic("Kinh tế & ...", ...)   │ ← Gemini call #2  (parallel)
    │  _call_topic("Chạy bộ", ...)         │ ← Gemini call #3
    │  ...                                 │
    └──────────────────────────────────────┘
           ↓ as_completed() → dict indexed by topic order
    Merge: header + blocks joined by "─────"
           ↓
    send_telegram_msg(chat_id, merged_message)

Fallback: if all parallel calls fail → _generate_legacy_briefing() (single call)
```

Each topic block format:
```
📊 Phân tích: [2-3 câu tổng hợp: tình hình, tầm quan trọng, bối cảnh]

📰 Tiêu đề tin 1 (DD/MM)
Tóm tắt 1 câu.
<a href="url">Đọc thêm</a>

📰 Tiêu đề tin 2 (DD/MM)  ← tuỳ chọn
...

📈 Xu hướng: [1 câu nhận xét signal đang nổi]
```

### On-demand briefing (`@news <query>`)

```
User sends "@news trending AI"
           ↓
    telegram_router strips prefix → route to news agent
           ↓
    telegram_handler.handle_news_chat(text, chat_id, config)
           ↓
    generate_on_demand_briefing(query, chat_id, config)
      └─ build_on_demand_system_instruction()
      └─ build_on_demand_prompt(query, date_str)
      └─ _call_gemini_with_search(model, system_inst, prompt, max_tokens=800)
      └─ send_telegram_msg(chat_id, reply)
      └─ returns reply text
           ↓
    run_extract_in_background(user_id, exchange, model)  ← daemon thread
```

On-demand reply format:
```
🔍 [Chủ đề người dùng hỏi]

📊 Tổng hợp: [2-3 câu: tình hình hiện tại, điểm nổi bật]

📰 Tiêu đề tin 1 (DD/MM)
...

📈 Nhận xét: [1 câu xu hướng cần theo dõi]
```

## Key Files

| File | Role |
|------|------|
| `app/agents/news/agent.py` | Core: parallel topic calls, on-demand, legacy fallback |
| `app/agents/news/prompts.py` | 6 prompt builders: legacy, per-topic, on-demand, memory extraction |
| `app/agents/news/memory.py` | Load/save user preferences from SQLite; owns `genai.Client` for extraction |
| `app/agents/news/telegram_handler.py` | `/news` command handler + `@news` free-text delegation |
| `app/core/database.py` | `news_agent_state` table |
| `app/services/scheduler.py` | 3 scheduled jobs: morning / afternoon / evening |

## Config

```json
"news_agent": {
    "enabled": true,
    "news_model": "models/gemini-flash-latest",
    "morning_time": "06:30",
    "afternoon_time": "17:30",
    "evening_time": "20:00",
    "telegram_chat_id": "",
    "topics": [
        { "name": "AI & Công nghệ", "emoji": "🤖" },
        { "name": "Địa chính trị & Thế giới", "emoji": "🌏" },
        { "name": "Kinh tế & Thị trường", "emoji": "📊" },
        { "name": "Chạy bộ & Thể thao", "emoji": "🏃" }
    ],
    "interest_profile": {
        "technology": 10,
        "sports_running": 8,
        "it_workforce": 9,
        "economics_politics": 7
    }
}
```

`topics` is required for v2 parallel mode. If omitted, falls back to deriving topic names from `interest_profile` keys (backward compat).

## Prompt Builders (`app/agents/news/prompts.py`)

| Function | Purpose |
|----------|---------|
| `build_news_system_instruction()` | Legacy single-call system instruction |
| `build_session_prompt()` | Legacy single-call user prompt |
| `build_topic_system_instruction()` | Per-topic parallel briefing system instruction |
| `build_topic_prompt(name, emoji, session, date)` | Per-topic focused user prompt |
| `build_on_demand_system_instruction()` | On-demand ad-hoc query system instruction |
| `build_on_demand_prompt(query, date)` | On-demand user query prompt |
| `build_memory_extraction_prompt(chat_text)` | Extract liked/disliked topic signals |

## Memory System

SQLite table `news_agent_state (user_id, key, value, updated_at)`.

Keys: `liked_topics` (JSON list, max 20), `disliked_topics` (JSON list, max 20), `extra_notes` (free text, max 500 chars).

`genai.Client` for extraction lives in `memory.py` as module-level `_client` — callers do not pass a client object.

Extraction runs in a background daemon thread after each on-demand reply.

## Trade-offs

| | v1 (single call) | v2 (parallel per-topic) |
|---|---|---|
| LLM calls/briefing | 1 | N (= number of topics, parallel) |
| URL contamination | Yes (shared context) | No (isolated per call) |
| Per-topic depth | Shallow (budget split) | Full (dedicated call) |
| Latency | 1× topic latency | ≈ 1× topic latency (parallel) |
| On-demand query | No | Yes (`@news <query>`) |
| Fallback | Knowledge-only | Legacy single-call → knowledge-only |

## Fallback Chain

```
Parallel topic calls
  → if all fail: _generate_legacy_briefing() (single call with grounding)
      → if grounding fails: _call_knowledge_only() (knowledge only)
          → if both fail: log error, skip send
```
