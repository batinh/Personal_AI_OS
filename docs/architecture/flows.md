# Data Flows — Personal AI OS

Key end-to-end data paths through the 5-layer architecture.

---

## Flow 1: Strava run → Telegram report (webhook path)

```
Strava API
  └─► POST /webhook (Layer 1: Edge)
        ├─ validate event type == "activity" + aspect_type == "create"
        ├─ fetch activity detail from Strava API
        ├─ save_run_activity() → run_activities table
        ├─ compute metrics (TRIMP, ACWR, decoupling) → run_computed_metrics
        ├─ save_run_activity_raw() → run_activity_raw + data/streams/{user}/{id}.json
        │
        ├─► analyze_run_with_gemini() (Layer 2: Coach Agent)
        │     ├─ build_universal_run_analysis_prompt()
        │     │    ├─ get_training_loads() → acute/chronic load
        │     │    ├─ get_weekly_volume() → current week km
        │     │    ├─ get_plan_for_date() → today's target
        │     │    └─ get_active_memories() → core_memory injection
        │     ├─ Gemini generate_content() [3 retries, 5s/10s/20s backoff]
        │     └─ update gcs_score in run_activities
        │
        ├─► RAG memorize() → ChromaDB (Layer 3: Episodic Memory)
        ├─► update_activity_description() → Strava API
        ├─► send_telegram_msg() → Telegram API (full analysis)
        └─► send_rpe_keyboard() → Telegram API (inline keyboard)

Fallback (Gemini fails all retries):
  └─► send_telegram_msg() → basic stats notification (no analysis)

Recovery (scheduler, every 2h):
  └─► task_retry_pending_analyses() → re-attempt for gcs_score IS NULL activities
```

---

## Flow 2: Morning briefing (cron path)

```
APScheduler CronTrigger (06:00 VN)
  └─► task_morning_briefing() (Layer 1: Scheduler)
        ├─ get_today_weather() → OpenWeather API (Layer 4)
        └─► generate_morning_briefing(config, weather) (Layer 2: Coach Agent)
              ├─ build_standup_prompt()
              │    ├─ get_active_memories() → core_memory
              │    ├─ get_training_loads() → ACWR, acute/chronic
              │    ├─ get_weekly_volume() → current week
              │    └─ get_plan_for_date() → today's plan
              ├─ Gemini generate_content()
              └─► send_telegram_msg() → Telegram
```

---

## Flow 3: Telegram chat (user message path)

```
Telegram → POST /webhook
  └─► handle_telegram_chat() (Layer 2: Router)
        ├─ command routing:
        │    /sync    → execute_manual_sync()
        │    /plan    → handle_plan_command()
        │    /news    → generate_news_briefing()
        │    /summary → generate_weekly_reflection()
        │    @news    → news agent
        │    else     → Coach Agent chat
        │
        └─► Coach Agent chat (Layer 2)
              ├─ load chat_history (last 30 messages)
              ├─ build_agent_context() → full context bundle
              ├─ Gemini generate_content() with function_calling
              │    └─ Tool Use: search_memory, get_plan, update_plan, ...
              ├─ save_message() → chat_history
              └─► send_telegram_msg() → Telegram
```

---

## Flow 4: Manual sync (/sync command)

```
User: /sync [N]
  └─► execute_manual_sync(chat_id, n_days)
        ├─ send "⏳ Đang đồng bộ..." to Telegram
        ├─ fetch recent activities from Strava API
        ├─ for each run activity (reversed, oldest first):
        │    ├─ check RAG: skip if already memorized
        │    ├─ _ingest_one_activity()
        │    │    ├─ fetch detail + stream → save_run_activity_raw()
        │    │    ├─ compute metrics → upsert_run_computed_metrics()
        │    │    ├─ analyze_run_with_gemini()
        │    │    └─ RAG memorize()
        │    └─ collect per-activity status line
        └─► send completion summary with per-activity lines → Telegram
```

---

## Flow 5: Memory extraction (background / weekly)

```
APScheduler CronTrigger (Sunday 20:00 VN)
  └─► task_weekly_reflection()
        ├─ extract_implicit_memory(user_id) (background)
        │    ├─ load last 50 chat messages
        │    ├─ load active core_memory facts
        │    ├─ Gemini: differential extraction (new facts only)
        │    └─ insert_memory() → core_memory table
        └─► generate_weekly_reflection(config)
              ├─ get_weekly_volume(), get_training_loads()
              ├─ RAG search: past reflections for context
              ├─ Gemini generate_content()
              └─► send_telegram_msg() → Telegram
```

---

## Flow 6: News briefing (cron path)

```
APScheduler CronTrigger (06:30 / 17:30 / 20:00 VN)
  └─► task_morning/afternoon/evening_news()
        └─► generate_news_briefing(config, session)
              ├─ fetch RSS feeds (26 sources, 5 categories)
              ├─ deduplicate against news_sent_articles
              ├─ score articles via Gemini (cached in news_article_scores)
              ├─ filter by digest_threshold (≥4)
              ├─ call_topic_gemini() per topic (grounded search)
              │    └─ grounding gate: reject if no grounding_chunks
              ├─ insert sent articles → news_sent_articles
              └─► send_telegram_msg() → Telegram (chunked HTML)
```
