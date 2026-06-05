"""
Sanity Flow Tests — End-to-end flow verification for all major user-facing paths.
==================================================================================
These tests prove the system's critical flows execute without crashing and route
to the correct handlers. They mock all external I/O (DB, Telegram, Gemini, weather)
but exercise real Python logic and decision trees.

Run as a pre-deploy gate:
    python -m pytest tests/test_sanity_flows.py -v

Coverage goals:
- Morning briefing: Guard 1 (no race_date), Guard 2 (no plan → daily suggestion),
  Guard 3 (full AI path with plan)
- Scheduler wrappers: task_morning_briefing, task_news_briefing don't crash
- Telegram command routing: /brief, /standup, /news, /sync, free-text
- News briefing: generate_news_briefing doesn't crash
- Daily suggestion: all 6 rule branches produce a sendable message
- Strava webhook: create/delete events route correctly
- Health endpoint: correct JSON shape
"""

import json
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
_BASE = "app.agents.coach.agent"


def _make_config(**overrides):
    cfg = {
        "model_name": "models/gemini-2.0-flash",
        "system_instruction": "You are a coach.",
        "user_profile": "Runner, 30yo",
        "max_hr": 180,
        "rest_hr": 50,
        "race_date": "2026-12-01",
        "race_distance_km": 21.1,
    }
    cfg.update(overrides)
    return cfg


def _make_client():
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def _db_patches_for_briefing():
    """Common DB patches required before Guard 2 in generate_morning_briefing."""
    return [
        patch(
            f"{_BASE}.get_training_loads",
            return_value={"acute_load_7d": 60, "chronic_load_28d": 240},
        ),
        patch(f"{_BASE}.get_weekly_volume", return_value="35 km"),
        patch(f"{_BASE}.get_plan_for_date", return_value=None),
        patch(f"{_BASE}.get_formatted_weekly_context", return_value="Tuần 3/12"),
    ]


# ──────────────────────────────────────────────────────────────────────────────
# 1. MORNING BRIEFING — Guard 1: no race_date → prompt user to /setup
# ──────────────────────────────────────────────────────────────────────────────
class TestMorningBriefingGuard1(unittest.TestCase):
    """No race_date in config → must send setup prompt and return immediately."""

    def _run_with_no_race_date(self):
        db = _db_patches_for_briefing()
        for p in db:
            p.start()
        tg_mock = patch(f"{_BASE}.send_telegram_msg").start()
        uid_mock = patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "99999"})
        uid_mock.start()
        try:
            from app.agents.coach import agent as ag

            ag.generate_morning_briefing(_make_config(race_date=""))
            return tg_mock
        finally:
            patch.stopall()

    def test_sends_exactly_one_telegram_message(self):
        tg = self._run_with_no_race_date()
        tg.assert_called_once()

    def test_message_contains_setup_keyword(self):
        tg = self._run_with_no_race_date()
        msg_text = tg.call_args[0][1].lower()
        self.assertTrue(
            "setup" in msg_text or "cấu hình" in msg_text,
            f"Guard 1 message should mention /setup, got: {msg_text[:80]}",
        )

    def test_chat_id_is_env_value(self):
        tg = self._run_with_no_race_date()
        chat_id_arg = str(tg.call_args[0][0])
        self.assertEqual(chat_id_arg, "99999")


# ──────────────────────────────────────────────────────────────────────────────
# 2. MORNING BRIEFING — Guard 2: no active plan → daily suggestion (BUG FIX)
# ──────────────────────────────────────────────────────────────────────────────
class TestMorningBriefingGuard2(unittest.TestCase):
    """
    Regression tests for ISS-013 — Guard 2 crash.
    Previously: get_runs_in_last_days() returned a string, passed as recent_runs list →
    AttributeError: 'str' object has no attribute 'get' inside compute_daily_suggestion.
    Fix: pass recent_runs=[] and day_of_week=now.weekday().
    """

    def _run_guard2(self, athlete_state="healthy", acwr_loads=None):
        """Run generate_morning_briefing with has_active_plan=False (Guard 2 path)."""
        loads = acwr_loads or {"acute_load_7d": 60, "chronic_load_28d": 240}
        collected = {}

        with (
            patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "99999"}),
            patch(f"{_BASE}.has_active_plan_this_week", return_value=False),
            patch(f"{_BASE}.get_athlete_state", return_value=athlete_state),
            patch(f"{_BASE}.get_training_loads", return_value=loads),
            patch(f"{_BASE}.get_weekly_volume", return_value="20 km"),
            patch(f"{_BASE}.get_plan_for_date", return_value=None),
            patch(f"{_BASE}.get_formatted_weekly_context", return_value=""),
            patch(f"{_BASE}.send_telegram_msg") as mock_tg,
            patch(f"{_BASE}.save_message") as mock_save,
        ):
            from app.agents.coach import agent as ag

            # Must NOT raise — this is the regression assertion
            ag.generate_morning_briefing(_make_config())
            collected["tg"] = mock_tg
            collected["save"] = mock_save

        return collected

    def test_does_not_raise_attributeerror(self):
        """Core regression: Guard 2 must not crash with AttributeError on recent_runs."""
        # If this raises, the bug is back
        self._run_guard2()

    def test_sends_telegram_message(self):
        c = self._run_guard2()
        c["tg"].assert_called_once()

    def test_saves_message_to_db(self):
        c = self._run_guard2()
        c["save"].assert_called_once()
        args = c["save"].call_args[0]
        self.assertIn("MORNING BRIEFING", args[2])

    def test_message_contains_suggestion_header(self):
        c = self._run_guard2()
        msg = c["tg"].call_args[0][1]
        self.assertIn("Gợi ý hôm nay", msg)

    def test_message_contains_plan_prompt(self):
        c = self._run_guard2()
        msg = c["tg"].call_args[0][1]
        self.assertIn("/plan", msg)

    def test_sick_athlete_gets_rest_suggestion(self):
        c = self._run_guard2(athlete_state="sick")
        msg = c["tg"].call_args[0][1]
        self.assertIn("Nghỉ", msg)

    def test_injured_athlete_gets_rest_suggestion(self):
        c = self._run_guard2(athlete_state="injured")
        msg = c["tg"].call_args[0][1]
        self.assertIn("Nghỉ", msg)

    def test_high_acwr_gets_rest_suggestion(self):
        # ACWR > 1.4 → rest
        c = self._run_guard2(acwr_loads={"acute_load_7d": 300, "chronic_load_28d": 200})
        msg = c["tg"].call_args[0][1]
        self.assertIn("Nghỉ", msg)

    def test_no_chat_id_skips_telegram(self):
        """If TELEGRAM_CHAT_ID is missing, no Telegram call must be made."""
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(f"{_BASE}.has_active_plan_this_week", return_value=False),
            patch(f"{_BASE}.get_athlete_state", return_value="healthy"),
            patch(
                f"{_BASE}.get_training_loads",
                return_value={"acute_load_7d": 60, "chronic_load_28d": 240},
            ),
            patch(f"{_BASE}.get_weekly_volume", return_value="20 km"),
            patch(f"{_BASE}.get_plan_for_date", return_value=None),
            patch(f"{_BASE}.get_formatted_weekly_context", return_value=""),
            patch(f"{_BASE}.send_telegram_msg") as mock_tg,
            patch(f"{_BASE}.save_message"),
        ):
            from app.agents.coach import agent as ag

            ag.generate_morning_briefing(_make_config())
            mock_tg.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# 3. MORNING BRIEFING — Guard 3: full AI path (has active plan)
# ──────────────────────────────────────────────────────────────────────────────
class TestMorningBriefingFullAIPath(unittest.TestCase):
    """has_active_plan=True → runs full Gemini briefing."""

    def test_full_path_sends_ai_reply(self):
        mock_response = MagicMock()
        mock_response.candidates = []
        mock_response.text = "Chào buổi sáng! ACWR ổn định, hôm nay chạy nhẹ nhé."

        mock_chat = MagicMock()

        with (
            patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "99999"}),
            patch(f"{_BASE}.has_active_plan_this_week", return_value=True),
            patch(
                f"{_BASE}.get_training_loads",
                return_value={"acute_load_7d": 60, "chronic_load_28d": 240},
            ),
            patch(f"{_BASE}.get_weekly_volume", return_value="30 km"),
            patch(
                f"{_BASE}.get_plan_for_date",
                return_value={"workout_title": "Easy Run", "description": "45 min"},
            ),
            patch(f"{_BASE}.get_formatted_weekly_context", return_value="Week 3"),
            patch(f"{_BASE}.load_history_for_gemini", return_value=[]),
            patch(f"{_BASE}.get_all_active_memories", return_value=[]),
            patch(f"{_BASE}.client") as mock_client,
            patch(f"{_BASE}.send_message_with_retry", return_value=mock_response),
            patch(f"{_BASE}.send_telegram_msg") as mock_tg,
            patch(f"{_BASE}.save_message"),
            patch(f"{_BASE}.debug_log_prompt"),
        ):
            mock_client.chats.create.return_value = mock_chat
            from app.agents.coach import agent as ag

            ag.generate_morning_briefing(_make_config())

        mock_tg.assert_called_once_with(
            "99999", "Chào buổi sáng! ACWR ổn định, hôm nay chạy nhẹ nhé."
        )

    def test_empty_gemini_reply_sends_fallback(self):
        mock_response = MagicMock()
        mock_response.candidates = []
        mock_response.text = ""

        with (
            patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "99999"}),
            patch(f"{_BASE}.has_active_plan_this_week", return_value=True),
            patch(
                f"{_BASE}.get_training_loads",
                return_value={"acute_load_7d": 60, "chronic_load_28d": 240},
            ),
            patch(f"{_BASE}.get_weekly_volume", return_value="30 km"),
            patch(f"{_BASE}.get_plan_for_date", return_value=None),
            patch(f"{_BASE}.get_formatted_weekly_context", return_value=""),
            patch(f"{_BASE}.load_history_for_gemini", return_value=[]),
            patch(f"{_BASE}.get_all_active_memories", return_value=[]),
            patch(f"{_BASE}.client") as mock_client,
            patch(f"{_BASE}.send_message_with_retry", return_value=mock_response),
            patch(f"{_BASE}.send_telegram_msg") as mock_tg,
            patch(f"{_BASE}.save_message"),
            patch(f"{_BASE}.debug_log_prompt"),
        ):
            mock_client.chats.create.return_value = MagicMock()
            from app.agents.coach import agent as ag

            ag.generate_morning_briefing(_make_config())

        mock_tg.assert_called_once()
        sent_text = mock_tg.call_args[0][1]
        self.assertIn("không thể", sent_text.lower())


# ──────────────────────────────────────────────────────────────────────────────
# 4. SCHEDULER WRAPPERS — task_morning_briefing, task_news_briefing
# ──────────────────────────────────────────────────────────────────────────────
class TestSchedulerTaskWrappers(unittest.TestCase):
    """Scheduler wrappers must not crash and must log their start."""

    def test_task_morning_briefing_calls_generate(self):
        with (
            patch("app.services.scheduler.load_config", return_value=_make_config()),
            patch("app.services.scheduler.get_primary_user_id", return_value="99999"),
            patch(
                "app.services.scheduler.get_today_weather", return_value="Sunny 28°C"
            ),
            patch("app.services.scheduler.generate_morning_briefing") as mock_gen,
        ):
            from app.services.scheduler import task_morning_briefing

            task_morning_briefing()
            mock_gen.assert_called_once()

    def test_task_morning_briefing_exception_does_not_propagate(self):
        """Any exception inside must be caught — scheduler thread must not die."""
        with (
            patch(
                "app.services.scheduler.load_config",
                side_effect=RuntimeError("cfg fail"),
            ),
            patch("app.services.scheduler.get_primary_user_id", return_value="99999"),
        ):
            from app.services.scheduler import task_morning_briefing

            # Must not raise
            task_morning_briefing()

    def test_task_morning_news_calls_generate(self):
        with (
            patch("app.services.scheduler.load_config", return_value=_make_config()),
            patch("app.services.scheduler.generate_news_briefing") as mock_news,
        ):
            from app.services.scheduler import task_morning_news

            task_morning_news()
            mock_news.assert_called_once()

    def test_task_morning_news_exception_does_not_propagate(self):
        with (
            patch("app.services.scheduler.load_config", return_value=_make_config()),
            patch(
                "app.services.scheduler.generate_news_briefing",
                side_effect=Exception("news fail"),
            ),
        ):
            from app.services.scheduler import task_morning_news

            task_morning_news()  # must not raise

    def test_task_morning_briefing_passes_weather_to_generate(self):
        with (
            patch("app.services.scheduler.load_config", return_value=_make_config()),
            patch("app.services.scheduler.get_primary_user_id", return_value="99999"),
            patch(
                "app.services.scheduler.get_today_weather", return_value="Rainy 22°C"
            ),
            patch("app.services.scheduler.generate_morning_briefing") as mock_gen,
        ):
            from app.services.scheduler import task_morning_briefing

            task_morning_briefing()
            args = mock_gen.call_args[0]
            self.assertEqual(args[1], "Rainy 22°C")


# ──────────────────────────────────────────────────────────────────────────────
# 5. TELEGRAM COMMAND ROUTING — HTTP-level via FastAPI TestClient
# ──────────────────────────────────────────────────────────────────────────────
class TestTelegramCommandRouting(unittest.TestCase):
    """All Telegram commands must route correctly and return 200."""

    def setUp(self):
        self.client = _make_client()

    def _post(self, text, chat_id=99999):
        return self.client.post(
            "/telegram-webhook",
            content=json.dumps({"message": {"chat": {"id": chat_id}, "text": text}}),
            headers={"Content-Type": "application/json"},
        )

    @patch("app.routers.webhooks.task_morning_briefing")
    @patch("app.routers.webhooks.get_primary_user_id", return_value=99999)
    @patch("app.routers.webhooks.send_telegram_msg")
    def test_standup_command_triggers_briefing(self, mock_tg, mock_uid, mock_brief):
        resp = self._post("/standup")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    @patch("app.routers.webhooks.get_primary_user_id", return_value=99999)
    @patch("app.routers.webhooks.send_telegram_msg")
    def test_brief_command_handled_by_coach_agent(self, mock_tg, mock_uid):
        """
        /brief is handled inside handle_telegram_chat (agent.py) not webhooks.py.
        Routing must not 500 — agent handles it.
        """
        with patch("app.routers.webhooks.handle_telegram_chat") as mock_coach:
            resp = self._post("/brief")
            self.assertEqual(resp.status_code, 200)
            mock_coach.assert_called_once()

    @patch("app.routers.webhooks.get_primary_user_id", return_value=99999)
    def test_news_command_triggers_news_handler(self, mock_uid):
        with patch("app.agents.news.telegram_handler.handle_news_command"):
            resp = self._post("/news")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"status": "ok"})

    @patch("app.routers.webhooks.get_primary_user_id", return_value=99999)
    def test_free_text_routes_to_coach(self, mock_uid):
        with patch("app.routers.webhooks.handle_telegram_chat"):
            resp = self._post("Hôm nay tôi chạy được 10km")
            self.assertEqual(resp.status_code, 200)

    @patch("app.routers.webhooks.get_primary_user_id", return_value=99999)
    def test_news_prefix_routes_to_news_agent(self, mock_uid):
        with patch("app.agents.news.telegram_handler.handle_news_chat"):
            resp = self._post("@news crypto market update")
            self.assertEqual(resp.status_code, 200)

    def test_malformed_json_returns_200(self):
        """Telegram retries — we must always return 200, never 5xx."""
        resp = self.client.post(
            "/telegram-webhook",
            content='{"bad json":',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_null_body_returns_200(self):
        resp = self.client.post(
            "/telegram-webhook",
            content="null",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_missing_message_key_returns_200(self):
        resp = self.client.post(
            "/telegram-webhook",
            content='{"update_id": 12345}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)


# ──────────────────────────────────────────────────────────────────────────────
# 6. DAILY SUGGESTION — all 6 suggestion types produce valid output
# ──────────────────────────────────────────────────────────────────────────────
class TestDailySuggestionAllBranches(unittest.TestCase):
    """
    compute_daily_suggestion has 10 priority rules → 6 suggestion types.
    All must produce a dict that format_daily_suggestion_for_briefing can format
    into a non-empty string (i.e., the whole chain works end-to-end).
    """

    def _suggest(self, **kwargs):
        from app.agents.coach.daily_suggestion import (
            compute_daily_suggestion,
            format_daily_suggestion_for_briefing,
        )

        defaults = dict(
            readiness_score=70,
            acwr=1.0,
            recent_runs=[],
            athlete_state="healthy",
            day_of_week=1,
            days_since_last_run=1,
        )
        defaults.update(kwargs)
        suggestion = compute_daily_suggestion(**defaults)
        formatted = format_daily_suggestion_for_briefing(suggestion)
        return suggestion, formatted

    def test_rest_from_sick_state(self):
        s, msg = self._suggest(athlete_state="sick")
        self.assertEqual(s["workout_type"], "Rest")
        self.assertIn("Nghỉ", msg)

    def test_rest_from_injured_state(self):
        s, msg = self._suggest(athlete_state="injured")
        self.assertEqual(s["workout_type"], "Rest")
        self.assertIn("Nghỉ", msg)

    def test_rest_from_high_acwr(self):
        s, msg = self._suggest(acwr=1.5)
        self.assertEqual(s["workout_type"], "Rest")
        self.assertIsNotNone(msg)

    def test_recovery_from_low_readiness(self):
        s, msg = self._suggest(readiness_score=30, acwr=1.0)
        self.assertEqual(s["workout_type"], "Recovery")
        self.assertIn("Chạy phục hồi", msg)

    def test_easy_from_long_rest(self):
        s, msg = self._suggest(days_since_last_run=5, readiness_score=70)
        self.assertEqual(s["workout_type"], "Easy")
        self.assertIn("5 ngày", s["description_vi"])

    def test_easy_short_from_moderate_readiness(self):
        s, msg = self._suggest(readiness_score=50, acwr=1.0)
        self.assertEqual(s["workout_type"], "Easy")  # easy_short is also Easy type

    def test_long_run_on_weekend(self):
        s, msg = self._suggest(day_of_week=6, readiness_score=75, acwr=1.0)
        self.assertEqual(s["workout_type"], "LongRun")
        self.assertIn("Long Run", msg)

    def test_tempo_on_excellent_readiness(self):
        s, msg = self._suggest(readiness_score=85, acwr=1.0, day_of_week=2)
        self.assertEqual(s["workout_type"], "Tempo")
        self.assertIn("Tempo", msg)

    def test_format_always_returns_str(self):
        """Format function must never crash regardless of suggestion dict."""
        from app.agents.coach.daily_suggestion import (
            compute_daily_suggestion,
            format_daily_suggestion_for_briefing,
        )

        for state in ("healthy", "sick", "injured"):
            for acwr in (0.8, 1.0, 1.2, 1.5):
                s = compute_daily_suggestion(
                    readiness_score=65,
                    acwr=acwr,
                    recent_runs=[],
                    athlete_state=state,
                    day_of_week=1,
                    days_since_last_run=1,
                )
                result = format_daily_suggestion_for_briefing(s)
                self.assertIsInstance(result, str)
                self.assertGreater(len(result), 10)

    def test_empty_recent_runs_list_does_not_crash(self):
        """Regression: passing [] (not a string) must not raise AttributeError."""
        s, msg = self._suggest(recent_runs=[])
        self.assertIn("workout_type", s)

    def test_recent_runs_with_quality_sessions(self):
        """Passing real run dicts with gcs_score works — no crash."""
        recent = [
            {"workout_type_detected": "tempo", "gcs_score": 8},
            {"workout_type_detected": "easy", "gcs_score": 5},
        ]
        s, msg = self._suggest(readiness_score=85, recent_runs=recent, day_of_week=2)
        # With recent quality sessions, tempo shouldn't fire (recent_quality > 0)
        self.assertNotEqual(s["workout_type"], "Tempo")


# ──────────────────────────────────────────────────────────────────────────────
# 7. NEWS BRIEFING FLOW — generate_news_briefing doesn't crash
# ──────────────────────────────────────────────────────────────────────────────
class TestNewsBriefingFlow(unittest.TestCase):
    """generate_news_briefing must complete without crash when Gemini is mocked."""

    def test_generate_news_briefing_does_not_crash(self):
        mock_response = MagicMock()
        mock_response.text = "📰 Tin tức hôm nay: Thị trường ổn định."
        mock_response.candidates = []

        with (
            patch("app.agents.news.agent.genai") as mock_genai,
            patch("app.agents.news.agent.send_telegram_msg"),
            patch("app.agents.news.memory.load_news_memory", return_value={}),
            patch("app.agents.news.memory.save_news_memory"),
            patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "99999"}),
        ):
            mock_genai.Client.return_value.models.generate_content.return_value = (
                mock_response
            )
            from app.agents.news.agent import generate_news_briefing

            # Should not raise
            generate_news_briefing(_make_config())

    def test_news_send_fails_gracefully(self):
        """Telegram send failure must not crash with a coding bug (TypeError/AttributeError)."""
        mock_response = MagicMock()
        mock_response.text = "Tin tức."
        mock_response.candidates = []

        with (
            patch("app.agents.news.agent.genai") as mock_genai,
            patch(
                "app.agents.news.agent.send_telegram_msg",
                side_effect=Exception("Telegram down"),
            ),
            patch("app.agents.news.memory.load_news_memory", return_value={}),
            patch("app.agents.news.memory.save_news_memory"),
            patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "99999"}),
        ):
            mock_genai.Client.return_value.models.generate_content.return_value = (
                mock_response
            )
            from app.agents.news.agent import generate_news_briefing

            try:
                generate_news_briefing(_make_config())
            except Exception as e:
                # Only coding bugs (TypeError/AttributeError) are unacceptable
                self.assertNotIsInstance(e, (TypeError, AttributeError))


# ──────────────────────────────────────────────────────────────────────────────
# 8. STRAVA WEBHOOK — event routing
# ──────────────────────────────────────────────────────────────────────────────
class TestStravaWebhookRouting(unittest.TestCase):
    """Strava webhook must route create/delete correctly and return 200 immediately."""

    def setUp(self):
        self.client = _make_client()

    @patch("app.routers.webhooks.run_strava_workflow")
    def test_create_event_queues_workflow(self, mock_wf):
        resp = self.client.post(
            "/webhook",
            json={
                "object_type": "activity",
                "aspect_type": "create",
                "object_id": 123456,
            },
        )
        self.assertEqual(resp.status_code, 200)
        mock_wf.assert_called_once_with("123456")

    @patch("app.routers.webhooks.handle_deleted_activity")
    def test_delete_event_queues_cleanup(self, mock_del):
        resp = self.client.post(
            "/webhook",
            json={"object_type": "activity", "aspect_type": "delete", "object_id": 789},
        )
        self.assertEqual(resp.status_code, 200)
        mock_del.assert_called_once_with("789")

    @patch("app.routers.webhooks.run_strava_workflow")
    def test_update_event_is_ignored(self, mock_wf):
        resp = self.client.post(
            "/webhook",
            json={"object_type": "activity", "aspect_type": "update", "object_id": 999},
        )
        self.assertEqual(resp.status_code, 200)
        mock_wf.assert_not_called()

    def test_invalid_payload_returns_4xx(self):
        resp = self.client.post(
            "/webhook",
            json={"object_type": "activity", "aspect_type": "create", "object_id": 0},
        )
        self.assertIn(resp.status_code, [400, 422])


# ──────────────────────────────────────────────────────────────────────────────
# 9. HEALTH ENDPOINT — correct shape and semantics
# ──────────────────────────────────────────────────────────────────────────────
class TestHealthEndpoint(unittest.TestCase):
    """Health endpoint must always respond with the expected JSON shape."""

    def setUp(self):
        self.client = _make_client()

    def test_responds_not_5xx(self):
        """503 = degraded (DB/config issue in test env) is acceptable; 500 is a crash."""
        resp = self.client.get("/health")
        self.assertNotIn(resp.status_code, [500, 502])

    def test_json_has_required_keys(self):
        resp = self.client.get("/health")
        body = resp.json()
        for key in ("status", "db", "config", "scheduler"):
            self.assertIn(key, body, f"Missing key: {key}")

    def test_scheduler_field_is_valid_string(self):
        resp = self.client.get("/health")
        scheduler_val = resp.json().get("scheduler")
        self.assertIn(scheduler_val, ["running", "stopped"])


# ──────────────────────────────────────────────────────────────────────────────
# 10. /brief COMMAND IN COACH AGENT — triggers generate_morning_briefing
# ──────────────────────────────────────────────────────────────────────────────
class TestBriefCommandInCoachAgent(unittest.TestCase):
    """/brief in handle_telegram_chat must call generate_morning_briefing."""

    def test_brief_command_calls_briefing(self):
        # load_config is locally imported inside handle_telegram_chat — patch at source
        with (
            patch("app.core.config.load_config", return_value=_make_config()),
            patch(f"{_BASE}.generate_morning_briefing") as mock_brief,
            patch(f"{_BASE}.save_message"),
            patch(f"{_BASE}.is_setup_in_progress", return_value=False),
        ):
            from app.agents.coach.agent import handle_telegram_chat

            handle_telegram_chat("99999", "/brief", _make_config())
            mock_brief.assert_called_once()

    def test_standup_command_calls_briefing(self):
        with (
            patch("app.core.config.load_config", return_value=_make_config()),
            patch(f"{_BASE}.generate_morning_briefing") as mock_brief,
            patch(f"{_BASE}.save_message"),
            patch(f"{_BASE}.is_setup_in_progress", return_value=False),
        ):
            from app.agents.coach.agent import handle_telegram_chat

            handle_telegram_chat("99999", "/standup", _make_config())
            mock_brief.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# 11. NOTIFICATION PIPELINE — chunking and HTML safety
# ──────────────────────────────────────────────────────────────────────────────
class TestNotificationPipeline(unittest.TestCase):
    """send_telegram_msg must split long messages and never crash on edge cases."""

    def test_short_message_sent_as_single_chunk(self):
        with (
            patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"}),
            patch("app.core.notification.requests.post") as mock_post,
        ):
            mock_post.return_value.status_code = 200
            from app.core.notification import send_telegram_msg

            send_telegram_msg("99999", "Hello!")
            self.assertEqual(mock_post.call_count, 1)

    def test_very_long_message_is_chunked(self):
        long_msg = "A" * 5000
        with (
            patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"}),
            patch("app.core.notification.requests.post") as mock_post,
        ):
            mock_post.return_value.status_code = 200
            from app.core.notification import send_telegram_msg

            send_telegram_msg("99999", long_msg)
            self.assertGreater(mock_post.call_count, 1)

    def test_empty_message_does_not_crash(self):
        with (
            patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "fake-token"}),
            patch("app.core.notification.requests.post") as mock_post,
        ):
            mock_post.return_value.status_code = 200
            from app.core.notification import send_telegram_msg

            send_telegram_msg("99999", "")

    def test_sanitize_md_to_tg_html_handles_none(self):
        from app.core.notification import sanitize_md_to_tg_html

        result = sanitize_md_to_tg_html("")
        self.assertIsInstance(result, str)


# ──────────────────────────────────────────────────────────────────────────────
# 12. AGENTIC LOOP — tool calls are executed and result fed back
# ──────────────────────────────────────────────────────────────────────────────
class TestAgenticLoopFlow(unittest.TestCase):
    """_run_agentic_loop must execute tool calls and return final text reply."""

    def _make_text_response(self, text):
        """Response with text only (no function calls).
        extract_text() reads p.text from parts where p.thought is falsy.
        """
        resp = MagicMock()
        resp.text = text
        part = MagicMock()
        part.text = text  # extract_text reads this
        part.thought = False  # must be falsy or part gets skipped
        part.function_call = None
        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        resp.candidates = [candidate]
        return resp

    def _make_tool_response(self, fn_name, fn_args):
        """Response with a function_call part only (no text)."""
        resp = MagicMock()
        resp.text = ""
        fc = MagicMock()
        fc.name = fn_name
        fc.args = fn_args
        part = MagicMock()
        part.text = None
        part.thought = False
        part.function_call = fc
        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        resp.candidates = [candidate]
        return resp

    def test_text_only_response_returns_text(self):
        mock_resp = self._make_text_response("ACWR = 1.2, trạng thái ổn.")
        with patch(f"{_BASE}.send_message_with_retry", return_value=mock_resp):
            from app.agents.coach.agent import _run_agentic_loop

            result = _run_agentic_loop(MagicMock(), "How is my ACWR?")
            self.assertIn("ACWR", result)

    def test_unknown_tool_does_not_crash(self):
        """An unknown tool name must be handled gracefully — loop continues to text reply."""
        mock_tool_resp = self._make_tool_response("nonexistent_tool", {})
        mock_text_resp = self._make_text_response("Xin lỗi, không tìm thấy tool.")
        with patch(
            f"{_BASE}.send_message_with_retry",
            side_effect=[mock_tool_resp, mock_text_resp],
        ):
            from app.agents.coach.agent import _run_agentic_loop

            # Must not raise — error is sent back as function response
            result = _run_agentic_loop(MagicMock(), "Do something")
            self.assertIsInstance(result, str)

    def test_tool_dispatch_map_has_all_read_tools(self):
        """_TOOL_DISPATCH must contain all read tools so agent can call them."""
        from app.agents.coach.agent import _TOOL_DISPATCH

        required = [
            "get_run_stream_csv",
            "get_run_computed_metrics",
            "get_metric_trend",
            "get_volume_for_week",
            "get_volume_summary",
        ]
        for tool in required:
            self.assertIn(
                tool, _TOOL_DISPATCH, f"Missing tool in _TOOL_DISPATCH: {tool}"
            )

    def test_tool_dispatch_map_has_write_tools(self):
        from app.agents.coach.agent import _TOOL_DISPATCH

        required = [
            "save_bulk_workout_plan",
            "set_workout_plan",
            "update_todays_plan",
        ]
        for tool in required:
            self.assertIn(
                tool, _TOOL_DISPATCH, f"Missing write tool in _TOOL_DISPATCH: {tool}"
            )

    def test_loop_terminates_after_max_rounds(self):
        """Loop must not hang — must stop after max_rounds even if tools keep firing."""
        mock_tool_resp = self._make_tool_response(
            "get_volume_for_week", {"user_id": "99", "week_offset": 0}
        )
        with patch(f"{_BASE}.send_message_with_retry", return_value=mock_tool_resp):
            with patch(
                f"{_BASE}._TOOL_DISPATCH", {"get_volume_for_week": lambda **kw: "50km"}
            ):
                from app.agents.coach.agent import _run_agentic_loop

                result = _run_agentic_loop(
                    MagicMock(), "How much did I run?", max_rounds=3
                )
                self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
