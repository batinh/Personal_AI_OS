# Documentation — Personal AI OS

Navigation hub for all project documentation. Every doc has a defined audience and purpose.

---

## Quick orientation

| Where to look | If you need to… |
|---------------|-----------------|
| [RUNBOOK.md](RUNBOOK.md) | Deploy, restart, rollback, debug a production issue |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Set up your dev environment, run tests, open a PR |
| [ISSUES.md](ISSUES.md) | Check known bugs and feature requests |
| [CHANGELOG.md](CHANGELOG.md) | Understand what changed and when |
| [architecture/overview.md](architecture/overview.md) | Understand the 5-layer system design |
| [engineering/setup.md](engineering/setup.md) | Configure from scratch (env vars, Docker, Strava auth) |
| [process/delivery-checklist.md](process/delivery-checklist.md) | Pre-commit gate: which tests to run for which change type |

---

## Folder map

### Root — operational docs (audience: ops / maintainer)

| File | Purpose |
|------|---------|
| [RUNBOOK.md](RUNBOOK.md) | Deploy procedures, health checks, rollback |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, test workflow, PR standards |
| [ISSUES.md](ISSUES.md) | Bug and feature tracker |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [DELIVERY_CHECKLIST.md](DELIVERY_CHECKLIST.md) | Per-change-type test gate |

---

### architecture/ — system design (audience: engineer / architect)

| File | Purpose |
|------|---------|
| [overview.md](architecture/overview.md) | 5-layer architecture, Mermaid diagram, core principles |
| [database.md](architecture/database.md) | All SQLite table schemas |
| [memory.md](architecture/memory.md) | 4-tier memory model, deduplication workflow |
| [stream-storage.md](architecture/stream-storage.md) | Strava stream file layout and re-analysis guide |
| [flows.md](architecture/flows.md) | Data flow diagrams: webhook → agent → storage |

---

### engineering/ — technical guides (audience: developer)

| File | Purpose |
|------|---------|
| [setup.md](engineering/setup.md) | Full setup guide: env vars, OAuth, Docker |
| [coaching-science.md](engineering/coaching-science.md) | TRIMP, ACWR, GCS, taper constants |
| [telegram.md](engineering/telegram.md) | Notification chunking, env vars, attachment threshold |
| [refactor-roadmap.md](engineering/refactor-roadmap.md) | Architecture audit findings, completed vs open items |

---

### features/ — feature design docs (audience: product / engineer)

| File | Purpose |
|------|---------|
| [_template.md](features/_template.md) | Template for new feature docs |
| [garmin-coach-planning.md](features/garmin-coach-planning.md) | Garmin-integrated adaptive planning (current sprint) |
| [coach-agent.md](features/coach-agent.md) | Coach Dyno PRD: requirements, acceptance criteria |
| [coach-strava-metrics.md](features/coach-strava-metrics.md) | Strava stream metrics upgrade design |
| [news-agent.md](features/news-agent.md) | News Agent PRD: LLM-native architecture, requirements |

---

### process/ — checklists and standards (audience: all contributors)

| File | Purpose |
|------|---------|
| [delivery-checklist.md](process/delivery-checklist.md) | Which tests to run per change type |
| [feature-template.md](process/feature-template.md) | How to write a feature design doc |
| [review-checklist.md](process/review-checklist.md) | Pre-commit quality, AI, DB, and domain checklist |

---

### testing/ — test documentation (audience: QA / engineer)

| File | Purpose |
|------|---------|
| [strategy.md](testing/strategy.md) | Test philosophy, pyramid, tools |
| [specs.md](testing/specs.md) | Test module inventory, coverage by area |
| [plan.md](testing/plan.md) | Critical paths, test spec guide |

---

## Language zones

| Zone | Scope | Language |
|------|-------|----------|
| **1** | Code, DB, logs, git commits, doc structure/headers | English |
| **2** | Telegram messages, AI output to user | Vietnamese |
| **3** | Prompt builders: logic=English, injected strings=Vietnamese | Mixed |

Documentation lives in Zone 1: headers, structure, and field names in English. Explanatory prose may include Vietnamese where it improves clarity for the primary audience.
