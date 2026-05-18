# Delivery Checklist

Use this checklist before every commit and PR. Select the matching change type.

---

## Bug Fix (1–2 files changed)

```
[ ] Unit test written FIRST (RED → GREEN — no exceptions)
[ ] Test name describes behavior: "returns None when..." / "raises when..."
[ ] Smoke tests pass:  python -m pytest tests/test_smoke.py -v
[ ] Full suite passes: python -m pytest tests/ -q  (0 failures)
[ ] Coverage held:     python -m pytest tests/ --cov=app --cov-fail-under=60
[ ] Pragmatic review checklist run: docs/pragmatic_review_checklist.md
[ ] Issue row moved: Open → Closed in docs/ISSUES.md (with commit hash)
[ ] Commit message: "fix: <description>. Closes ISS-NNN"
```

---

## Feature (≥ 2 files changed)

```
[ ] Design doc created BEFORE code: docs/features/{slug}.md
    (Copy from docs/feature_design_template.md — slug = lowercase-hyphens)
[ ] Design doc linked from docs/ISSUES.md issue row
[ ] Design doc reviewed for YAGNI / KISS violations
[ ] Tests written FIRST (RED → GREEN)
[ ] Unit tests for each new public function/class
[ ] Integration test for new API endpoint or scheduler task
[ ] New public symbols added to tests/test_smoke.py
[ ] Smoke tests pass:  python -m pytest tests/test_smoke.py -v
[ ] Full suite passes: python -m pytest tests/ -q  (0 failures)
[ ] Coverage held:     python -m pytest tests/ --cov=app --cov-fail-under=60
[ ] Pragmatic review checklist run: docs/pragmatic_review_checklist.md
[ ] Pre-deploy gate:   bash scripts/pre-deploy-check.sh  (exit 0)
[ ] Issue row moved: Open → Closed in docs/ISSUES.md (with commit hash)
[ ] Commit message: "feat: <description>"
```

---

## Refactor / Chore

```
[ ] Behavior unchanged — no new test logic needed, but existing tests must pass
[ ] Full suite passes: python -m pytest tests/ -q  (0 failures)
[ ] Coverage not regressed (must be >= previous run)
[ ] Commit message: "refactor: <description>"
```

---

## Prompt / AI Change

```
[ ] Pragmatic review checklist, Section 3 (AI & Prompt Engineering) completed
[ ] LLM output passes sanitize_md_to_tg_html (no raw Markdown in Telegram)
[ ] Thinking model CoT filtering verified (no "thought\n" in forwarded text)
[ ] Manual spot-check: triggered the flow manually and read the Telegram output
[ ] Commit message: "feat(prompts): <description>" or "fix(prompts): <description>"
```

---

## Deploy Checklist (every deploy to T440)

```
[ ] bash scripts/pre-deploy-check.sh  (exit 0)
[ ] bash scripts/deploy-t440.sh
    - git pull passed
    - docker compose up --build succeeded
    - /health returned 200 within 90s
    - All 6 E2E smoke tests passed
    - deployments.log entry appended
[ ] Monitor logs for 5 minutes after deploy:
    bash scripts/fetch-logs.sh --live -l ERROR -m news
[ ] No new ERROR lines in first 5 minutes
```

---

## Rollback (on failed deploy)

```
[ ] bash scripts/rollback-t440.sh    (restores airunningcoach:backup)
[ ] Health check passed after rollback
[ ] Root cause identified in logs:
    bash scripts/fetch-logs.sh -l ERROR --since 30m
[ ] Fix applied locally, tests pass
[ ] Re-deploy: bash scripts/deploy-t440.sh
```

---

## Coverage Targets

| Quarter | Minimum | Target |
|---------|---------|--------|
| Q2 2026 | 60% | 65% |
| Q3 2026 | 65% | 70% |
| Q4 2026 | 70% | 80% |

Modules with low coverage to prioritize: `console.py` (30%), `admin.py` (37%), `backup.py` (27%), `rag_memory.py` (0%).
