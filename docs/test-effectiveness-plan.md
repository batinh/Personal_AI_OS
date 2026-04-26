# Test Effectiveness Improvement Plan

**Status:** Pending implementation  
**Created:** 2026-04-24  
**Context:** After Phase 2 coverage push (80.18%), 250 new UTs found zero bugs — coverage ≠ effectiveness.

---

## Problem Statement

Coverage-driven test writing (writing tests after code to hit a number) rarely finds bugs because:
- Tests are written to pass, not to fail
- We know the implementation, so we unconsciously test the happy path
- Missing: boundary probing, mutation killing, property invariants

---

## 3 Core Effectiveness Metrics

| Metric | Tool | Baseline | Target |
|--------|------|----------|--------|
| **Mutation Score** | `mutmut` | ~unknown | ≥ 70% |
| **Assertion Density** | manual/script | ~1.4/test | ≥ 3.0/test |
| **Error Path Coverage %** | coverage.py branch | ~35% | ≥ 50% |

---

## 5-Phase Implementation Plan

### Phase 1 — Baseline Measurement (1–2h)
- Install `mutmut` + `hypothesis` into dev dependencies
- Run `mutmut run --paths-to-mutate app/` to get mutation baseline
- Run `pytest --cov=app --cov-branch` to get branch coverage (error paths)
- Calculate assertion density: `grep -r "assert" tests/ | wc -l` / test count
- Document baselines in this file

### Phase 2 — Kill Survived Mutations (2–4h)
- `mutmut results` → list survived mutations
- For each survived mutation: add a test that would fail if production code is wrong
- Focus modules: `news/agent.py`, `coach/utils.py`, `core/notification.py`
- Target: mutation score ≥ 70%

### Phase 3 — Property-Based Tests for Pure Math (1–2h)
- Use `hypothesis` on pure math functions:
  - `calculate_trimp()` — TRIMP never negative, linear with duration
  - `calculate_acwr()` — ACWR bounded [0, ∞), NaN-safe
  - `score_topic()` in `news/scorer.py` — score in [0.0, 1.0]
  - `_resolve_topics()` — output length ≤ input keys
- Target: ≥ 5 property tests across these functions

### Phase 4 — Agent Code Branch Coverage (2–3h)
- `coach/agent.py`: 37% → 60% (add tests for error branches, missing memory, partial Strava data)
- `news/agent.py`: 56% → 75% (add `_call_topic` error paths, grounding URL edge cases)
- Focus on error/exception branches that currently have 0% hit rate

### Phase 5 — Metrics Dashboard (1h)
- Add `scripts/test-metrics.sh`:
  ```bash
  #!/bin/bash
  echo "=== Mutation Score ==="
  mutmut results 2>/dev/null | tail -5
  echo "=== Branch Coverage ==="
  python -m pytest tests/ --cov=app --cov-branch --cov-report=term-missing -q 2>/dev/null | grep TOTAL
  echo "=== Assertion Density ==="
  ASSERTS=$(grep -r "assert" tests/ --include="*.py" | wc -l)
  TESTS=$(grep -r "def test_" tests/ --include="*.py" | wc -l)
  echo "Assertions: $ASSERTS / Tests: $TESTS = $(python3 -c "print(round($ASSERTS/$TESTS, 2))")"
  ```
- Optionally: add mutation score check to pre-deploy gate (Phase 5b, optional)

---

## Why Coverage ≠ Effectiveness

```
Coverage tells you: "this line ran"
Mutation score tells you: "this line is actually checked"

Example:
  def add(a, b): return a + b

  # 100% coverage, 0% mutation score:
  def test_add():
      result = add(1, 2)  # ran the line — covered!
      # forgot to assert anything

  # 100% coverage, 100% mutation score:
  def test_add():
      assert add(1, 2) == 3
      assert add(0, 0) == 0
      assert add(-1, 1) == 0
```

---

## Priority Order for Implementation

1. **Phase 1** — must do first to know where we stand
2. **Phase 2** — highest ROI: directly kills real bugs
3. **Phase 3** — best for math-heavy modules (TRIMP, ACWR)
4. **Phase 4** — improves agent branch coverage
5. **Phase 5** — automation, do last

---

## Notes

- `mutmut` is slow on large codebases — scope it with `--paths-to-mutate app/agents/` first
- `hypothesis` requires deterministic functions; skip functions with `random` or `datetime.now()`
- Assertion density ≥ 3.0 means avg 3 asserts per test — achievable by adding negative/boundary assertions
- Branch coverage (`--cov-branch`) is different from line coverage — it counts if/else branches separately
