"""
test_flow_morning_briefing.py — Tests for app/agents/coach/flows/morning_briefing.py
======================================================================================
Covers generate_morning_briefing():
  - Normal path: Telegram sent, message saved
  - Empty Gemini response: fallback message sent
  - No chat_id: no Telegram call
  - Memory injection into prompt
  - Weather data passed through
  - Chat history context built
  - Gemini exception: error logged, no crash
"""

import unittest
from unittest.mock import patch, MagicMock


def _make_config():
    return {
        "model_name": "models/gemini-2.0-flash",
        "system_instruction": "Be a great coach.",
        "user_profile": "Runner, age 30",
        "max_hr": 180,
        "rest_hr": 50,
        "race_date": "2026-06-01",
        "race_distance_km": 21.1,
    }


_PATCHES = [
    "app.agents.coach.flows.morning_briefing.get_primary_user_id",
    "app.agents.coach.flows.morning_briefing.get_plan_for_date",
    "app.agents.coach.flows.morning_briefing.load_history_for_gemini",
    "app.agents.coach.flows.morning_briefing.get_all_active_memories",
    "app.agents.coach.flows.morning_briefing.get_runs_in_last_days",
    "app.agents.coach.flows.morning_briefing.build_standup_prompt",
    "app.agents.coach.flows.morning_briefing.debug_log_prompt",
    "app.agents.coach.flows.morning_briefing.send_message_with_retry",
    "app.agents.coach.flows.morning_briefing.send_telegram_msg",
    "app.agents.coach.flows.morning_briefing.save_message",
    "app.agents.coach.flows.morning_briefing.client",
]


class TestGenerateMorningBriefing(unittest.TestCase):

    def _start_patches(
        self,
        reply_text="Chào buổi sáng!",
        chat_id="123456",
        memories=None,
        history=None,
    ):
        mocks = {}
        patchers = []
        for target in _PATCHES:
            p = patch(target)
            m = p.start()
            patchers.append(p)
            key = target.split(".")[-1]
            mocks[key] = m

        mocks["get_primary_user_id"].return_value = chat_id
        mocks["get_plan_for_date"].return_value = {
            "workout_title": "Easy Run",
            "description": "45min easy",
        }
        mocks["load_history_for_gemini"].return_value = history or []
        mocks["get_all_active_memories"].return_value = memories or []
        mocks["get_runs_in_last_days"].return_value = []
        mocks["build_standup_prompt"].return_value = "Full standup prompt"
        mocks["debug_log_prompt"].return_value = None

        response = MagicMock()
        response.text = reply_text
        chat_session = MagicMock()
        mocks["client"].chats.create.return_value = chat_session
        mocks["send_message_with_retry"].return_value = response

        return mocks, patchers

    def _stop(self, patchers):
        for p in patchers:
            p.stop()

    def test_sends_telegram_message(self):
        mocks, patchers = self._start_patches(reply_text="Chào buổi sáng!")
        try:
            from app.agents.coach.flows.morning_briefing import (
                generate_morning_briefing,
            )

            generate_morning_briefing(_make_config(), weather_data="Sunny 28°C")
            mocks["send_telegram_msg"].assert_called_once_with(
                "123456", "Chào buổi sáng!"
            )
        finally:
            self._stop(patchers)

    def test_saves_message_to_db(self):
        mocks, patchers = self._start_patches(reply_text="Good morning!")
        try:
            from app.agents.coach.flows.morning_briefing import (
                generate_morning_briefing,
            )

            generate_morning_briefing(_make_config())
            mocks["save_message"].assert_called_once()
            args = mocks["save_message"].call_args[0]
            self.assertEqual(args[0], "123456")
            self.assertEqual(args[1], "model")
            self.assertIn("[MORNING BRIEFING]", args[2])
        finally:
            self._stop(patchers)

    def test_no_chat_id_skips_telegram(self):
        mocks, patchers = self._start_patches(chat_id=None)
        try:
            from app.agents.coach.flows.morning_briefing import (
                generate_morning_briefing,
            )

            generate_morning_briefing(_make_config())
            mocks["send_telegram_msg"].assert_not_called()
            mocks["save_message"].assert_not_called()
        finally:
            self._stop(patchers)

    def test_empty_gemini_response_sends_fallback(self):
        mocks, patchers = self._start_patches(reply_text="")
        try:
            from app.agents.coach.flows.morning_briefing import (
                generate_morning_briefing,
            )

            generate_morning_briefing(_make_config())
            call_args = mocks["send_telegram_msg"].call_args[0]
            self.assertIn("không thể Briefing", call_args[1])
        finally:
            self._stop(patchers)

    def test_none_gemini_response_text_sends_fallback(self):
        mocks, patchers = self._start_patches()
        mocks["send_message_with_retry"].return_value.text = None
        try:
            from app.agents.coach.flows.morning_briefing import (
                generate_morning_briefing,
            )

            generate_morning_briefing(_make_config())
            call_args = mocks["send_telegram_msg"].call_args[0]
            self.assertIn("không thể Briefing", call_args[1])
        finally:
            self._stop(patchers)

    def test_memories_fetched(self):
        memories = [
            {"category": "injury_status", "fact": "Right knee pain"},
            {"category": "goal", "fact": "Sub-2h half marathon"},
        ]
        mocks, patchers = self._start_patches(memories=memories)
        try:
            from app.agents.coach.flows.morning_briefing import (
                generate_morning_briefing,
            )

            generate_morning_briefing(_make_config())
            mocks["get_all_active_memories"].assert_called_once_with("123456")
        finally:
            self._stop(patchers)

    def test_build_standup_prompt_called(self):
        mocks, patchers = self._start_patches()
        try:
            from app.agents.coach.flows.morning_briefing import (
                generate_morning_briefing,
            )

            generate_morning_briefing(_make_config(), weather_data="Rainy 22°C")
            mocks["build_standup_prompt"].assert_called_once()
            kwargs = mocks["build_standup_prompt"].call_args[1]
            self.assertEqual(kwargs.get("weather_data"), "Rainy 22°C")
        finally:
            self._stop(patchers)

    def test_history_builds_chat_context(self):
        history = [
            {"role": "user", "parts": ["How was my run?"]},
            {"role": "model", "parts": ["Great pace today!"]},
        ]
        mocks, patchers = self._start_patches(history=history)
        try:
            from app.agents.coach.flows.morning_briefing import (
                generate_morning_briefing,
            )

            generate_morning_briefing(_make_config())
            mocks["build_standup_prompt"].assert_called_once()
            kwargs = mocks["build_standup_prompt"].call_args[1]
            chat_ctx = kwargs.get("chat_context", "")
            self.assertNotEqual(chat_ctx, "Không có tương tác trò chuyện nào gần đây.")
        finally:
            self._stop(patchers)

    def test_empty_history_uses_default_context(self):
        mocks, patchers = self._start_patches(history=[])
        try:
            from app.agents.coach.flows.morning_briefing import (
                generate_morning_briefing,
            )

            generate_morning_briefing(_make_config())
            kwargs = mocks["build_standup_prompt"].call_args[1]
            self.assertEqual(
                kwargs.get("chat_context"), "Không có tương tác trò chuyện nào gần đây."
            )
        finally:
            self._stop(patchers)

    def test_gemini_exception_does_not_crash(self):
        mocks, patchers = self._start_patches()
        mocks["send_message_with_retry"].side_effect = Exception("Network error")
        try:
            from app.agents.coach.flows.morning_briefing import (
                generate_morning_briefing,
            )

            generate_morning_briefing(_make_config())
            mocks["send_telegram_msg"].assert_not_called()
        finally:
            self._stop(patchers)

    def test_gemini_client_created_with_model_name(self):
        mocks, patchers = self._start_patches()
        try:
            from app.agents.coach.flows.morning_briefing import (
                generate_morning_briefing,
            )

            generate_morning_briefing(_make_config())
            create_call = mocks["client"].chats.create.call_args
            self.assertEqual(create_call[1]["model"], "models/gemini-2.0-flash")
        finally:
            self._stop(patchers)

    def test_no_plan_uses_free_run_context(self):
        mocks, patchers = self._start_patches()
        mocks["get_plan_for_date"].return_value = None
        try:
            from app.agents.coach.flows.morning_briefing import (
                generate_morning_briefing,
            )

            generate_morning_briefing(_make_config())
            kwargs = mocks["build_standup_prompt"].call_args[1]
            self.assertEqual(kwargs.get("today_plan"), "Chạy tự do.")
        finally:
            self._stop(patchers)


if __name__ == "__main__":
    unittest.main()
