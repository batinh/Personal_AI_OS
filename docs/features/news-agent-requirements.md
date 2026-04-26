# News Agent — Product Requirements Document

**Role:** Product Owner + Business Analyst  
**Version:** 1.3  
**Date:** 2026-04-24  
**Status:** Final Review Complete — Ready for Implementation  

---

## Revision History

| Version | Date | Reviewer | Changes |
|---------|------|----------|---------|
| 1.0 | 2026-04-24 | PO/BA | Initial draft |
| 1.1 | 2026-04-24 | Enterprise Customer | Found: 5 missing gaps, 3 contradictions, 5 ambiguities |
| 1.2 | 2026-04-24 | PO + Architect + End User | Resolved contradictions, added E3/E6 user stories, NFR gaps |
| 1.3 | 2026-04-24 | QA/Tester | Added: test strategy, error catalog, test-level tagging, edge cases, NFR testability matrix |

---

## 1. Product Vision

> The News Agent is a **personal AI-powered news curator** that delivers timely, accurate, and relevant news briefings via Telegram — automatically on schedule and on demand. It must feel like a smart assistant who read the news for you and summarized only what matters, with real links, real dates, and zero noise.

**Anti-vision (what this is NOT):**
- A bot that sends training-data content from 2 years ago
- A system that leaks internal LLM reasoning to the user
- A system that silently fails without any trace
- A paywalled-article aggregator

---

## 2. Scope Boundaries

| Scope | Version |
|-------|---------|
| Scheduled briefings (3/day), on-demand queries, grounding validation, real links, rate limiting, timezone | **v1.0** |
| Story deduplication, user feedback loop, retry with admin alerting | **v1.1** |
| Breaking news interrupt, `/news quick` executive summary, source quality filter | **Future** |
| Email delivery, web UI, multi-user, multi-language | **Out of scope** |

---

## 3. User Persona

**Tinh** — Primary User (single-user personal AI OS)

| Attribute | Detail |
|-----------|--------|
| Role | Tech professional, active runner, retail investor |
| Language | Vietnamese (primary UI), English (technical terms acceptable) |
| Device | Mobile-first — reads Telegram on phone in transit |
| Reading contexts | 06:30 before work, 17:30 commute, 20:00 after dinner |
| Core expectation | "Tell me what happened today — briefly, accurately, with a link I can click" |
| Confirmed pain points | LLM thinking text in messages, news from 2024 in 2026, wrong/broken links |
| Topics of interest | AI/Tech, Geopolitics, Economics/Markets, Running/Sports |

---

## 4. Error Message Catalog

> **Tester note:** All user-facing error strings are defined here. Tests MUST reference these constants. No hardcoded strings in tests.

| ID | Trigger | Message (Vietnamese) |
|----|---------|----------------------|
| ERR-001 | All topic calls fail / grounding_used=False | `⚠️ Không lấy được tin tức thực tế lúc này. Thử lại sau.` |
| ERR-002 | On-demand grounding failed | `⚠️ Không tìm thấy kết quả cho yêu cầu này. Thử lại sau.` |
| ERR-003 | Rate limit exceeded | `⚠️ Bạn đã gửi quá nhiều yêu cầu. Thử lại sau 1 tiếng.` |
| ERR-004 | News Agent disabled | `⚠️ News Agent đang tắt. Bật lại trong phần cài đặt.` |
| ERR-005 | Invalid /news argument | `❌ Lệnh không hợp lệ: /news <arg>. [help message appended]` |
| ERR-006 | Exception during /news session | `❌ Lỗi khi lấy tin <session_label>. Xem log để biết thêm.` |
| ERR-007 | On-demand query returns < 100 chars | same as ERR-002 |

> **Implementation note:** Define these as module-level constants in `telegram_handler.py`. Tests import the constant — they do NOT assert on literal strings.

---

## 5. Epics Overview

| Epic | Description | v1.0 | v1.1 |
|------|-------------|------|------|
| E1 | Scheduled Briefings | ✅ | — |
| E2 | On-Demand Queries | ✅ | — |
| E3 | Configuration & Control | ✅ | — |
| E4 | Content Quality & Reliability | ✅ | — |
| E5 | Memory & Personalization | ✅ (basic) | ✅ (feedback) |
| E6 | Observability & Reliability | ✅ (logging) | ✅ (alerting, retry) |

---

## 6. Functional Requirements

### 6.1 Scheduled Briefings (E1)

| ID | Requirement | Testability |
|----|-------------|-------------|
| FR-1.1 | System MUST attempt to send morning briefing at configured time (default 06:30 ICT) | Integration / Monitoring |
| FR-1.2 | System MUST attempt to send afternoon briefing at configured time (default 17:30 ICT) | Integration / Monitoring |
| FR-1.3 | System MUST attempt to send evening briefing at configured time (default 20:00 ICT) | Integration / Monitoring |
| FR-1.4 | Prompt MUST instruct LLM to search news from the last 24-48h — *best-effort; runtime date verification not possible* | Unit (prompt content) |
| FR-1.5 | System MUST attempt all configured topics; MAY omit topic with no grounded content | Unit |
| FR-1.6 | Each topic section MUST contain 1-3 article summaries from grounded results | Unit (mocked LLM) |
| FR-1.7 | Each article: headline ≤80 chars, summary ≤120 chars — *prompt constraint (best-effort), NOT programmatic truncation* | Unit (prompt content) |
| FR-1.8 | Each article SHOULD include a real source link from grounding metadata | Unit (mocked grounding) |
| FR-1.9 | Morning tone: concise, energizing | Unit (prompt content) |
| FR-1.10 | Afternoon tone: analytical, factual | Unit (prompt content) |
| FR-1.11 | Evening tone: reflective, deeper context | Unit (prompt content) |
| FR-1.12 | If grounded search fails → send ERR-001; MUST NOT send training data | Unit |
| FR-1.13 | If trigger fires >30 min late → skip, do not send delayed briefing | Unit (mock scheduler) |

> **FR-1.7 Tester Note:** Character limits are enforced via prompt instruction, not Python code. Tests validate the prompt text contains the constraint, not that the LLM output respects it. LLM compliance is monitored manually.

---

### 6.2 On-Demand Queries (E2)

| ID | Requirement | Testability |
|----|-------------|-------------|
| FR-2.1 | User MUST be able to trigger any briefing via `/news [session]` | Unit |
| FR-2.2 | Session values: `morning`, `afternoon`, `evening`; default = `morning` | Unit |
| FR-2.3 | Free-text query via `@news <query>` MUST perform real-time web search | Unit (mocked LLM) |
| FR-2.4 | Query result MUST come from grounded search (grounding_used=True) | Unit |
| FR-2.5 | Response MUST include ≥1 source link from grounding metadata | Unit (mocked grounding) |
| FR-2.6 | `/news help` or `/news HELP` MUST display command reference | Unit |
| FR-2.7 | Rate limit: max 10 on-demand queries per user per hour — *in-memory counter, resets on restart, no persistence* | Unit |

> **FR-2.7 Implementation Spec (for testers):**
> - Counter stored as `dict[str, list[float]]` in module-level variable: `{user_id: [timestamp1, timestamp2, ...]}`
> - Check: count timestamps within last 3600 seconds
> - Counter is **in-memory only** — resets on server restart (acceptable for v1.0)
> - Counter key: `str(chat_id)`
> - Resets automatically as old timestamps age out — no explicit reset needed

---

### 6.3 Configuration & Control (E3)

| ID | Requirement | Testability |
|----|-------------|-------------|
| FR-3.1 | Admin MUST enable/disable via `news_agent.enabled` in config | Unit |
| FR-3.2 | Admin MUST configure schedule times per session | Integration |
| FR-3.3 | Admin MUST configure topics list (name + emoji) | Unit |
| FR-3.4 | Admin MUST configure Telegram chat ID | Unit |
| FR-3.5 | Admin MUST select LLM model | Unit |
| FR-3.6 | Admin MUST configure timezone (default: `Asia/Ho_Chi_Minh`) | Unit |
| FR-3.7 | User MUST pause delivery via `@news pause <N> days` | Unit (v1.1) |
| FR-3.8 | Admin MAY configure weekday-only scheduling | Unit (v1.1) |

---

### 6.4 Content Quality & Reliability (E4)

| ID | Requirement | Testability |
|----|-------------|-------------|
| FR-4.1 | MUST NEVER send LLM thinking/reasoning text to user | Unit |
| FR-4.2 | MUST NEVER send content known to be from training data — *enforced via grounding gate FR-4.4* | Unit (grounding gate) |
| FR-4.3 | All links MUST come from `grounding_chunks[].web.uri` — NOT from LLM text | Unit |
| FR-4.4 | If `grounding_used=False` → MUST reject response, return `(None, [])` | Unit |
| FR-4.5 | Every LLM call MUST produce structured log entry | Unit (assert log calls) |
| FR-4.6 | LLM-authored `<a href>` tags MUST be stripped from response text | Unit |
| FR-4.7 | Sources block: max 3 links, ordered by grounding_chunks index, capped if >3 | Unit |

> **FR-4.7 Tester Note on ordering:** "Ordered by grounding_chunks index" means take first N entries from the list as returned by `_extract_grounding_urls()`. Do NOT assert on specific URL values — assert on count (≤3) and that all URLs come from the provided mock grounding data.

---

### 6.5 Memory & Personalization (E5)

| ID | Ver | Requirement | Testability |
|----|-----|-------------|-------------|
| FR-5.1 | v1.0 | Extract preference signals from on-demand conversations (background thread) | Unit (join thread in test) |
| FR-5.2 | v1.0 | Extracted signals influence topic emphasis in future scheduled prompts | Unit |
| FR-5.3 | v1.0 | User can express preferences conversationally | Integration |
| FR-5.4 | v1.1 | User can give negative feedback: `@news sai rồi` | Unit |
| FR-5.5 | v1.1 | Negative signals reduce similar content for next 24h | Unit |
| FR-5.6 | v1.1 | Memory signals expire after 90 days of inactivity | Unit |

> **FR-5.1 Tester Note:** `run_extract_in_background()` spawns a daemon thread. In tests, call the underlying sync function directly or use `threading.Thread` with `join(timeout=5)` to avoid flaky async behavior. See `tests/test_news_telegram.py` for existing pattern.

---

### 6.6 Observability & Reliability (E6)

| ID | Ver | Requirement | Testability |
|----|-----|-------------|-------------|
| FR-6.1 | v1.0 | Log every briefing: session, topics_attempted, topics_succeeded, chars_sent, duration_ms | Unit (assert log content) |
| FR-6.2 | v1.0 | Log every LLM call: model, grounding_used, response_length, source_count, latency_ms | Unit |
| FR-6.3 | v1.0 | One topic failure MUST NOT block other topics | Unit |
| FR-6.4 | v1.0 | Silent failure is NOT acceptable — if briefing not sent, CRITICAL log MUST be written | Unit |
| FR-6.5 | v1.1 | 2 consecutive silent failures → alert to admin chat | Unit |
| FR-6.6 | v1.1 | Last successful delivery timestamp queryable via admin interface | Integration |
| FR-6.7 | v1.1 | Failed delivery retries up to 2 times with 5-min delay | Unit |

---

## 7. User Stories

### Epic E1 — Scheduled Briefings

---

**US-1.1: Morning Briefing Delivery**

```
As Tinh
I want to receive a curated morning news briefing automatically at 06:30
So that I can start my day informed without spending time searching for news
```

**Acceptance Criteria** — Test level in brackets:

```gherkin
[UNIT] Scenario: Agent disabled — no LLM call made
  Given news_agent.enabled = false
  When task_morning_news() is called
  Then no LLM call is made
  And no Telegram message is sent
  And INFO log contains "Agent disabled — skipping morning briefing"

[UNIT] Scenario: Enabled — briefing assembled and sent
  Given news_agent.enabled = true
  And _call_topic is mocked to return ("topic", "📰 <b>Title</b>\nSummary")
  And send_telegram_msg is mocked
  When generate_news_briefing(config, session="morning") is called
  Then send_telegram_msg is called exactly once
  And the sent text contains the topic block
  And the sent text starts with "📰"

[UNIT] Scenario: Trigger too late — briefing skipped
  Given current time is 07:15 ICT and morning_time is 06:30 ICT
  And scheduled job checks if trigger is >30 min late
  When task_morning_news() runs
  Then no LLM call is made
  And WARNING log contains "Briefing skipped — trigger late"

[UNIT] Scenario: All grounding fails — ERR-001 sent, no training data
  Given _call_topic is mocked to return (topic, None) for all topics
  And send_telegram_msg is mocked
  When generate_news_briefing(config, session="morning") is called
  Then send_telegram_msg is called with ERR_001 constant text
  And the sent text does NOT contain any article content
  And CRITICAL log is written

[UNIT] Scenario: No topics configured — error sent
  Given config has topics = []
  And send_telegram_msg is mocked
  When generate_news_briefing(config, session="morning") is called
  Then send_telegram_msg is called with ERR_001 constant text
```

---

**US-1.2: Per-Topic Sections**

```
As Tinh
I want each briefing organized by topic (AI, Geopolitics, Economics, Running)
So that I can quickly jump to the topic I care about most on mobile
```

**Acceptance Criteria:**

```gherkin
[UNIT] Scenario: All topics succeed — sections in config order
  Given 3 topics configured in order: [A, B, C]
  And _call_topic returns blocks for all 3
  When briefing is assembled
  Then sent message contains topic A before topic B before topic C
  And sections are separated by "─────"

[UNIT] Scenario: One topic returns None — omitted, others present
  Given topics = [A, B, C]
  And _call_topic returns (topic, None) for topic B
  And returns block content for A and C
  When briefing is assembled
  Then topic B section is absent from sent text
  And topics A and C are present
  And log contains "omitted" and topic B name

[UNIT] Scenario: All topics return None — ERR-001 sent
  Given all _call_topic mocks return (topic, None)
  When generate_news_briefing is called
  Then send_telegram_msg called once with ERR_001 text
  And CRITICAL log written

[UNIT] Scenario: Empty topics config — ERR-001 sent
  Given config.news_agent.topics = []
  When generate_news_briefing is called
  Then ERR-001 is sent and no LLM calls are made

[UNIT] Scenario: Invalid telegram_chat_id — no message sent
  Given _resolve_chat_id returns None (no chat_id configured, no primary user)
  When generate_news_briefing is called
  Then no LLM calls are made
  And no Telegram message is sent
  And WARNING log written
```

---

**US-1.3: Article Format**

```
As Tinh
I want each article to have a short title, brief summary, and a real link
So that I can decide whether to read more in under 5 seconds on my phone
```

**Acceptance Criteria:**

```gherkin
[UNIT] Scenario: LLM-authored links are stripped
  Given raw LLM text contains '<a href="https://hallucinated.com">Đọc thêm</a>'
  When _strip_llm_links(text) is called  [or whatever the stripping function is]
  Then the returned text does NOT contain 'hallucinated.com'
  And does NOT contain any <a href> tags

[UNIT] Scenario: Sources block built from grounding metadata — max 3
  Given grounding_urls = [("Reuters", "url1"), ("VnEx", "url2"), ("Bloomberg", "url3"), ("CNN", "url4")]
  When _build_sources_block(grounding_urls, max_sources=3) is called
  Then result contains "url1", "url2", "url3"
  And result does NOT contain "url4"
  And result starts with "📎"

[UNIT] Scenario: Sources block empty when no URLs
  Given grounding_urls = []
  When _build_sources_block(grounding_urls) is called
  Then result is empty string ""

[UNIT] Scenario: Sources block with long title truncated
  Given a grounding URL with title 200 chars long
  When _build_sources_block is called
  Then label in message is ≤60 chars
```

---

**US-1.4: Session-Specific Tone**

```
As Tinh
I want morning, afternoon, and evening briefings to feel distinct
So that each fits the energy and context of that time of day
```

**Acceptance Criteria:**

```gherkin
[UNIT] Scenario: Morning prompt contains morning context keywords
  Given session = "morning"
  When build_topic_prompt(topic_name, emoji, "morning", date_str) is called
  Then returned string contains "buổi sáng" or "bắt đầu ngày"
  And does NOT contain "buổi chiều" or "buổi tối"

[UNIT] Scenario: Evening prompt contains evening context keywords
  Given session = "evening"
  When build_topic_prompt(topic_name, emoji, "evening", date_str) is called
  Then returned string contains "buổi tối" or "tổng kết"

[UNIT] Scenario: Session header returns correct emoji
  Given _session_header("morning", "24/04/2026")
  Then result contains "📰" and "24/04/2026" and "SÁNG"
  Given _session_header("evening", "24/04/2026")
  Then result contains "🌙" and "CUỐI NGÀY"
  Given _session_header("unknown", "24/04/2026")
  Then result contains "📰" and "TIN TỨC" (fallback)
```

---

### Epic E2 — On-Demand Queries

---

**US-2.1: Manual Briefing Trigger**

```
As Tinh
I want to trigger any briefing on demand via /news command
So that I can get news at any time outside the scheduled windows
```

**Acceptance Criteria:**

```gherkin
[UNIT] Scenario: /news no args → morning session
  Given generate_news_briefing is mocked
  When handle_news_command("123", [], enabled_config) is called
  Then generate_news_briefing called with session="morning"

[UNIT] Scenario: /news afternoon → afternoon session
  When handle_news_command("123", ["afternoon"], enabled_config) is called
  Then generate_news_briefing called with session="afternoon"

[UNIT] Scenario: /news invalid arg → ERR-005 + help message
  When handle_news_command("123", ["weekly"], enabled_config) is called
  Then send_telegram_msg called once
  And sent text contains "❌" and "weekly"
  And sent text contains "News Agent" (help content)

[UNIT] Scenario: /news when disabled → ERR-004
  Given news_agent.enabled = false
  When handle_news_command("123", ["morning"], disabled_config) is called
  Then send_telegram_msg called with ERR_004 text
  And generate_news_briefing NOT called

[UNIT] Scenario: generate_news_briefing raises exception → ERR-006
  Given generate_news_briefing raises RuntimeError("boom")
  When handle_news_command("123", ["morning"], enabled_config) is called
  Then send_telegram_msg called with ERR_006 text (contains "❌" and session label)
  And exception is caught (does not propagate)
```

---

**US-2.2: Free-Text News Query**

```
As Tinh
I want to ask "@news ETF Việt Nam hôm nay" and get a focused, real-time answer
So that I can investigate any topic without waiting for the next scheduled briefing
```

**Acceptance Criteria:**

```gherkin
[UNIT] Scenario: Successful query — grounded, long reply — sent to user
  Given _call_gemini_with_search mocked to return ("x" * 200, [("Reuters", "url1")])
  And _inject_grounding_urls_into_text mocked to return (same_text, 1)
  And send_telegram_msg mocked
  When generate_on_demand_briefing("ETF Việt Nam", "123", enabled_config) is called
  Then send_telegram_msg called once with chat_id="123"
  And function returns the reply text (not None)

[UNIT] Scenario: Reply too short (< 100 chars) → ERR-002
  Given _call_gemini_with_search returns ("short reply", [])
  When generate_on_demand_briefing("query", "123", enabled_config) is called
  Then send_telegram_msg called with ERR_002 text
  And function returns None

[UNIT] Scenario: Agent disabled → returns None immediately
  When generate_on_demand_briefing("query", "123", disabled_config) is called
  Then function returns None
  And no LLM call made
  And no Telegram message sent

[UNIT] Scenario: Rate limit — 10th query allowed, 11th blocked
  Given chat_id="123" has sent 10 queries in the last 3600 seconds
  When the 11th @news query is processed
  Then send_telegram_msg called with ERR_003 text
  And no LLM call is made

[UNIT] Scenario: Rate limit resets after 1 hour
  Given chat_id="123" sent 10 queries 3601 seconds ago
  When a new query arrives
  Then the query IS processed (counter effectively reset)

[UNIT] Scenario: No grounding urls — sources block not appended
  Given _call_gemini_with_search returns ("x" * 200, [])
  And _inject_grounding_urls_into_text returns (same_text, 0)
  When generate_on_demand_briefing is called
  Then sent text does NOT contain "📎 Nguồn"
  And function returns the reply text

[UNIT] Scenario: 0 links replaced + sources available → sources block appended
  Given _inject_grounding_urls_into_text returns (text, 0) [no inline links replaced]
  And grounding_urls = [("Reuters", "url1")]
  And _build_sources_block returns "📎 sources block"
  When generate_on_demand_briefing is called
  Then sent text ends with "📎 sources block"
```

---

**US-2.3: Help Command**

```
As Tinh
I want to see all available commands and the delivery schedule at a glance
```

**Acceptance Criteria:**

```gherkin
[UNIT] Scenario: /news help — content check
  When handle_news_command("123", ["help"], enabled_config) is called
  Then send_telegram_msg called once
  And sent text contains "News Agent"
  And sent text contains "06:30"
  And sent text contains "morning"
  And sent text contains "@news"

[UNIT] Scenario: /news HELP — case insensitive
  When handle_news_command("123", ["HELP"], enabled_config) is called
  Then same behavior as "help"
```

---

### Epic E3 — Configuration & Control

---

**US-3.1: Enable / Disable Agent**

```gherkin
[UNIT] Scenario: Disabled → no LLM calls
  Given config.news_agent.enabled = false
  When generate_news_briefing(disabled_config, "morning") is called
  Then no _call_topic invocations
  And no send_telegram_msg invocations
  And function returns immediately

[UNIT] Scenario: Enabled → proceeds to topic calls
  Given config.news_agent.enabled = true
  And topics = [one topic]
  And _call_topic mocked to return (topic, "block")
  When generate_news_briefing is called
  Then _call_topic called at least once
```

---

**US-3.2: Configure Topics**

```gherkin
[UNIT] Scenario: _resolve_topics returns configured topics
  Given config.news_agent.topics = [{"name": "Crypto", "emoji": "₿"}]
  When _resolve_topics(config) is called
  Then result = [{"name": "Crypto", "emoji": "₿"}]

[UNIT] Scenario: Empty topics, interest_profile fallback
  Given config.news_agent.topics = []
  And config.news_agent.interest_profile = {"technology": 8}
  When _resolve_topics(config) is called
  Then result contains topic with name "Technology"
  And result[0]["emoji"] is the mapped emoji for "technology"

[UNIT] Scenario: Empty everything → empty list
  Given config.news_agent = {} (no topics, no interest_profile)
  When _resolve_topics({}) is called
  Then result = []
```

---

**US-3.3: Configure Schedule and Timezone**

```gherkin
[UNIT] Scenario: _get_model returns configured model
  Given config.news_agent.news_model = "models/gemini-pro"
  When _get_model(config) is called
  Then result = "models/gemini-pro"

[UNIT] Scenario: _get_model returns default when blank
  Given config.news_agent.news_model = ""
  When _get_model(config) is called
  Then result = "models/gemini-2.5-flash" (default)

[UNIT] Scenario: _now_date_str returns DD/MM/YYYY in local timezone
  When _now_date_str() is called
  Then result matches pattern r"^\d{2}/\d{2}/\d{4}$"
```

---

### Epic E4 — Content Quality & Reliability

---

**US-4.1: No Thinking Text in Telegram**

```gherkin
[UNIT] Scenario: _call_gemini_with_search uses thinking_budget=0
  Given the genai client is mocked
  When _call_gemini_with_search(model, system_inst, prompt) is called
  Then client.models.generate_content called with ThinkingConfig(thinking_budget=0)

[UNIT] Scenario: _strip_thought_preamble removes thought text
  Given text = "Suy nghĩ của tôi...\n\nActual news content here"
  When _strip_thought_preamble(text) is called
  Then result = "Actual news content here"
  And result does NOT contain "Suy nghĩ"
```

---

**US-4.2: Grounding Validation Gate**

```gherkin
[UNIT] Scenario: grounding_used=True → text returned
  Given mocked response with grounding_metadata present on candidate
  When _call_gemini_with_search is called
  Then function returns (non-empty text, grounding_urls)

[UNIT] Scenario: grounding_used=False → (None, []) returned
  Given mocked response with NO grounding_metadata on ANY candidate
  When _call_gemini_with_search is called
  Then function returns (None, [])
  And ERROR log contains "Grounding not invoked — REJECTING"
  
[UNIT] Scenario: _call_topic with grounding_used=False → returns (topic, None)
  Given _call_gemini_with_search mocked to return (None, [])
  When _call_topic(topic, session, date_str, model) is called
  Then return value is (topic, None)
  And WARNING log written

[UNIT] Scenario: Gemini API raises exception → (None, []) returned
  Given client.models.generate_content raises Exception("API error")
  When _call_gemini_with_search is called
  Then function returns (None, [])
  And WARNING log contains "Gemini call failed"
  And exception does NOT propagate to caller
```

---

**US-4.3: Real Article Links Only**

```gherkin
[UNIT] Scenario: _extract_grounding_urls returns deduped (title, uri) pairs
  Given 2 grounding chunks with same URI
  When _extract_grounding_urls([candidate]) is called
  Then result contains exactly 1 entry

[UNIT] Scenario: _extract_grounding_urls skips chunks with empty URI
  Given chunk with uri = ""
  When _extract_grounding_urls([candidate]) is called
  Then result = []

[UNIT] Scenario: _extract_grounding_urls handles candidate without grounding_metadata
  Given candidate with spec=[] (no grounding_metadata attribute)
  When _extract_grounding_urls([candidate]) is called
  Then result = []
  And no exception raised

[UNIT] Scenario: _build_sources_block formats correctly
  Given grounding_urls = [("Reuters", "https://reuters.com/a")]
  When _build_sources_block(grounding_urls) is called
  Then result contains "📎"
  And result contains "reuters.com/a"

[UNIT] Scenario: _build_sources_block caps at max_sources
  Given 5 grounding URLs, max_sources=3
  When _build_sources_block(urls, max_sources=3) is called
  Then result contains exactly 3 bullet lines
```

---

### Epic E5 — Memory & Personalization

---

**US-5.1: Preference Learning**

```gherkin
[UNIT] Scenario: Successful on-demand response triggers memory extraction
  Given generate_on_demand_briefing returns "reply content" (≥100 chars)
  And run_extract_in_background is mocked
  When handle_news_chat("123", "query text", enabled_config) is called
  Then run_extract_in_background called once with (user_id, combined_chat_text, model)

[UNIT] Scenario: Failed on-demand (returns None) — no memory extraction
  Given generate_on_demand_briefing returns None
  When handle_news_chat("123", "query text", enabled_config) is called
  Then run_extract_in_background NOT called

[UNIT] Scenario: Empty query text → help message sent, no memory extraction
  When handle_news_chat("123", "", enabled_config) is called
  Then send_telegram_msg called with help message content
  And run_extract_in_background NOT called
```

---

### Epic E6 — Observability & Reliability

---

**US-6.1: Delivery Tracking**

```gherkin
[UNIT] Scenario: Successful briefing logs structured entry
  Given briefing sends successfully
  When generate_news_briefing completes
  Then log contains "Sent" + session + "briefing" + "chat_id"

[UNIT] Scenario: All topics fail → CRITICAL log written
  Given all _call_topic return (topic, None)
  When generate_news_briefing is called
  Then logging.critical or logger.error called at least once

[UNIT] Scenario: One topic exception → caught at ThreadPoolExecutor, other topics unaffected
  Given _call_topic raises RuntimeError for topic index 1
  And _call_topic returns valid block for topics 0 and 2
  When generate_news_briefing is called
  Then topics 0 and 2 are included in sent message
  And no unhandled exception propagates from generate_news_briefing
```

---

## 8. Non-Functional Requirements

### 8.1 NFR Table

| ID | Category | Requirement | Version |
|----|----------|-------------|---------|
| NFR-1 | Latency | Each topic LLM call has 30s timeout → on timeout: skip topic, log WARNING | v1.0 |
| NFR-2 | Concurrency | Topics fetched in parallel (ThreadPoolExecutor, max 4 workers) | v1.0 |
| NFR-3 | Resilience | Single topic failure MUST NOT block other topics | v1.0 |
| NFR-4 | Message length | Briefings MAY exceed 4096 chars — `send_telegram_msg()` handles chunking | v1.0 |
| NFR-5 | Format | ALL messages MUST use Telegram HTML — no Markdown | v1.0 |
| NFR-6 | Language | ALL user-facing messages MUST be Vietnamese | v1.0 |
| NFR-7 | Logging | Every LLM call MUST log: model, grounding_used, response_length, source_count, latency_ms | v1.0 |
| NFR-8 | Multi-tenant | All DB writes MUST include user_id | v1.0 |
| NFR-9 | Scheduler | Scheduled tasks MUST use `def` not `async def` | v1.0 |
| NFR-10 | Timezone | Schedule times interpreted in configured timezone (default: Asia/Ho_Chi_Minh) | v1.0 |
| NFR-11 | Delivery SLA | Briefing MUST be triggered within 1 min of schedule; if trigger >30 min late → skip | v1.0 |
| NFR-12 | Grounding rate | Target: grounding_used=True ≥ 95% of scheduled calls — **monitored, not CI-enforced** | v1.0 |
| NFR-13 | Cost baseline | 4 topics × 3 sessions × 1 call = max 12 grounded calls/day scheduled | v1.0 |
| NFR-14 | Rate limiting | On-demand: max 10 per chat_id per hour, in-memory counter | v1.0 |
| NFR-15 | Retry | On delivery fail: retry ≤2 times, 5-min delay, CRITICAL after 2nd fail | v1.1 |
| NFR-16 | Dedup | Same article (by URL) MUST NOT appear in morning AND evening briefings same day | v1.1 |

### 8.2 NFR Testability Matrix

| NFR | Can unit test? | Can integration test? | Requires monitoring? |
|-----|---------------|----------------------|----------------------|
| NFR-1 (30s timeout) | ✅ mock time.sleep or concurrent.futures | — | — |
| NFR-2 (parallel) | ⚠️ can assert ThreadPoolExecutor is used | — | — |
| NFR-3 (resilience) | ✅ mock one topic to raise | — | — |
| NFR-4 (chunking) | ✅ via test_telegram_chunking.py | — | — |
| NFR-5 (HTML format) | ✅ assert no `**`, `##` in output | — | — |
| NFR-6 (Vietnamese) | ✅ assert key strings in Vietnamese | — | — |
| NFR-7 (log entry) | ✅ capture log with caplog fixture | — | — |
| NFR-8 (user_id in DB) | ✅ via test_database.py | — | — |
| NFR-10 (timezone) | ✅ mock datetime + get_local_tz | — | — |
| NFR-11 (SLA) | ⚠️ test late-trigger skip logic | ✅ E2E smoke | 📊 log-based |
| NFR-12 (95% rate) | ❌ not CI-testable | ❌ | 📊 log analysis only |
| NFR-13 (cost) | ⚠️ count mock calls | ❌ | 📊 API billing |
| NFR-14 (rate limit) | ✅ inject 11 calls in mock | — | — |
| NFR-15 (retry) | ✅ mock send_telegram_msg to fail | — | — |
| NFR-16 (dedup) | ✅ v1.1 | — | — |

> **NFR-12 Note:** "95% grounding rate" is a quality metric monitored via `grep grounding_used=False logs/app.log`. It is NOT enforced in CI. Team reviews this metric weekly during first month post-launch.

---

## 9. Out of Scope (all versions)

- Web UI for browsing past briefings
- Email delivery of briefings
- Multi-user support
- Article full-text extraction
- Paywalled article handling
- Multi-language briefings (Vietnamese only)
- Push notification to native mobile app
- Social sharing of briefings

---

## 10. Known Defects (Current Implementation → Must Fix Before v1.0 Ship)

| ID | Severity | FR Violated | Description | Root Cause | Fix |
|----|----------|-------------|-------------|------------|-----|
| DEF-001 | CRITICAL | US-4.1 | LLM thinking text sent to Telegram | `thinking_budget=1024` | Set to `0` |
| DEF-002 | CRITICAL | US-4.2 | Training data sent when grounding fails | `_call_knowledge_only()` fallback exists | Delete function; send ERR-001 instead |
| DEF-003 | CRITICAL | US-4.3 | LLM-authored URLs in messages (wrong/fake) | `_inject_links_by_article()` fragile | Delete; use sources block from metadata |
| DEF-004 | HIGH | FR-1.4 | Article dates from 2024 in 2026 briefings | Symptom of DEF-002 | Auto-fixed by DEF-002 fix |
| DEF-005 | HIGH | US-4.2 | `_call_knowledge_only()` is training-data path | Design decision | Delete entire function |

**Fix sequence:** DEF-001 → DEF-002+DEF-005 (together) → DEF-003 → DEF-004 resolves automatically.

---

## 11. Definition of Done — News Agent v1.0

### Functional Checklist
- [ ] All 3 scheduled briefings delivered within 5 min of configured times (verified via logs)
- [ ] Zero instances of LLM thinking text in any Telegram message (verified via unit test)
- [ ] Zero training-data fallbacks — grounding gate hard-rejects ungrounded responses
- [ ] All links in messages sourced from grounding metadata (not LLM text)
- [ ] ERR-001 sent (not stale data) when all grounding fails
- [ ] ERR-002 sent for short/empty on-demand responses
- [ ] ERR-003 sent when rate limit exceeded (11th request/hour)
- [ ] `/news help` shows commands + schedule (verified via unit test)
- [ ] Rate limit counter works: 10 allowed, 11th blocked (unit tested)

### Quality Checklist
- [ ] All 5 defects (DEF-001 to DEF-005) have passing unit tests proving they're fixed
- [ ] Smoke tests pass: `python -m pytest tests/test_smoke.py -v`
- [ ] Full suite passes: `python -m pytest tests/ -q` (0 failures)
- [ ] Pre-deploy check passes: `bash scripts/pre-deploy-check.sh`
- [ ] Error messages in tests use constants from error catalog (not hardcoded strings)

### Observability Checklist
- [ ] Every LLM call produces log with grounding_used, source_count, latency_ms
- [ ] CRITICAL log written when briefing not sent (no silent failures)
- [ ] First week post-launch: manual review of grounding_used rate in logs

---

## 12. Test File Map

| Functionality | Test File |
|---------------|-----------|
| Pure helpers: `_resolve_chat_id`, `_get_model`, `_resolve_topics`, `_session_header`, `_extract_grounding_urls` | `tests/test_news_agent_helpers.py` |
| Orchestration flows: `generate_news_briefing`, `generate_on_demand_briefing`, `_generate_legacy_briefing` | `tests/test_news_agent_flows.py` |
| Telegram handlers: `handle_news_command`, `handle_news_chat` | `tests/test_news_telegram.py` |
| Prompt builders: `build_topic_prompt`, `build_session_prompt`, etc. | `tests/test_news_prompts.py` |
| Error message catalog constants | Assert imported from `telegram_handler.py` in handler tests |
| Rate limiting logic | `tests/test_news_agent_flows.py` (new class) |
| Grounding gate: `_call_gemini_with_search` (mocked Gemini) | `tests/test_news_agent_flows.py` (new class) |

---

## 13. Architect Notes

### A. Why `thinking_budget=0` not just stripping
`thinking_budget=1024` causes Gemini Flash to sometimes return thought content without `thought=True` flag, defeating `_strip_thought_preamble()`. Setting `budget=0` prevents allocation at API level. Stripping remains as defense-in-depth only.

### B. Why delete `_call_knowledge_only()` entirely
Any fallback to training data is "sending 2-year-old news" from the user's perspective. No fallback is better than wrong content. The function's existence creates a code path that directly violated requirements.

### C. Why sources block beats per-article URL injection
`_inject_links_by_article()` (~100 lines) fails when article_count ≠ chunk_count, or when grounding_supports is absent. A clean sources block at the end is: simpler, always correct count (capped at 3), not brittle to LLM article ordering.

### D. Rate limiter implementation (in-memory is sufficient for v1.0)
```python
# Module-level dict: {chat_id: [timestamp, ...]}
_rate_limit_store: dict[str, list[float]] = {}
RATE_LIMIT = 10
RATE_WINDOW = 3600  # 1 hour in seconds

def _check_rate_limit(chat_id: str) -> bool:
    """Returns True if allowed, False if rate limited."""
    now = time.time()
    timestamps = _rate_limit_store.get(chat_id, [])
    recent = [t for t in timestamps if now - t < RATE_WINDOW]
    if len(recent) >= RATE_LIMIT:
        return False
    recent.append(now)
    _rate_limit_store[chat_id] = recent
    return True
```

### E. Parallel topic timing
4 topics × 30s timeout × 4 workers = 30s worst case (all parallel). Acceptable for `/news evening` manual trigger. Do not reduce below 4 workers or serialize calls.

### F. Timezone
Use `app.core.timezone_utils.get_local_tz()` (already exists) in all `datetime.now()` calls. APScheduler cron jobs: pass `timezone=get_local_tz()` to `add_job()`.

---

## 14. Config Schema (v1.0 Final)

```json
{
  "news_agent": {
    "enabled": true,
    "news_model": "models/gemini-2.5-flash",
    "timezone": "Asia/Ho_Chi_Minh",
    "morning_time": "06:30",
    "afternoon_time": "17:30",
    "evening_time": "20:00",
    "late_trigger_skip_minutes": 30,
    "max_topic_workers": 4,
    "topic_timeout_seconds": 30,
    "ondemand_rate_limit_per_hour": 10,
    "max_sources_per_topic": 3,
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
}
```

---

## 15. Related Documents

| Document | Purpose |
|----------|---------|
| `docs/features/news-agent-overhaul.md` | Original architecture design |
| `docs/test-effectiveness-plan.md` | Test quality improvement plan |
| `docs/ISSUES.md` | Active bug tracking |
| `app/agents/news/agent.py` | Orchestrator |
| `app/agents/news/prompts.py` | Prompt builders |
| `app/agents/news/telegram_handler.py` | Telegram command routing |
| `app/services/scheduler.py` | Scheduled task definitions |
