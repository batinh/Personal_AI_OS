# Adaptive personal endurance coach

## Goal and operating scope

Serve one athlete on T440 through Vietnamese Telegram conversations. Running is the primary sport.
Support a race goal or a maintenance phase without a target race. Strength, recovery and nutrition
should inform the running plan. The athlete delegates schedule adjustments to the coach and can
correct or undo them. A personal goal change comes from the athlete.

## Architecture and delivery order

1. Establish a dated athlete context through the existing context builder. Distinguish missing,
   stale and measured values. Fix the proactive load data contract (ISS-027) and test the real
   database-to-scheduler path. Historical replay must only read evidence available by the replay date.
2. Deliver two complete Telegram flows: missed workout → targeted conversation → adjustment, and
   weekly reflection → concrete next action → review of its outcome. Reuse the existing Gemini provider,
   structured outputs, stream metrics and Vietnamese prompt builders.
3. Route plan writes from chat, post-run analysis and scheduled jobs through a shared operation.
   Validate the entire affected week before committing the plan, daily sessions, target, reason
   and revision atomically. Deduplicate by event and check the expected revision. Notify after commit.
   Support undo without overwriting subsequent athlete edits.
4. Extend strength, recovery and nutrition in that order of need for the athlete. When moving a run,
   check adjacent sessions and update dependent reminders.

## Conversation and memory

Track confirmed preferences, dated temporary constraints, unanswered questions and coach hypotheses
separately. Check sync freshness before treating an absent workout as missed. Ask a question when its
answer can change the recommendation. Recognize that a conversational message may require no plan edit.
Coordinate reminders across chat, briefing and scheduled checks. Reuse SQLite for authoritative facts
and decisions, and semantic retrieval for relevant past conversations.

## Evaluation, rollout and fallback

Replay 20–30 representative situations with notifications intercepted and the production database
excluded. Cover race and maintenance phases, missing Garmin data, a busy athlete versus a fatigued
athlete, repeated missed sessions, tool retries, concurrent edits and undo. Compare baseline and new
replies for context accuracy, usefulness of questions and specificity of follow-up. Record disagreements
for athlete review. Run proposed decisions in shadow mode before enabling automatic writes. Keep the
last valid plan readable during provider outages and allow a switch back to the existing workflow.
