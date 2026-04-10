"""
test_flow_weekly_reflection.py — Tests for app/agents/coach/flows/weekly_reflection.py
========================================================================================
Covers generate_weekly_reflection():
  - rag_db.memorize called with correct doc_id format (reflection_{user_id}_{YYYY-MM-DD})
  - RAG failure does not abort flow (Telegram still sent, message still saved)
  - Telegram send called with reflection text
  - save_message called
  - No chat_id: Telegram not called, but message saved and RAG memorized
  - Gemini exception: handled gracefully
  - Memory data injected into prompt
"""
import unittest
from unittest.mock import patch, MagicMock


def _make_config():
    return {
        "model_name": "models/gemini-2.0-flash",
        "system_instruction": "You are a coach.",
        "user_profile": "Runner, age 30",
        "max_hr": 180,
        "rest_hr": 50,
        "race_date": "2026-06-01",
        "race_distance_km": 21.1,
    }


_PATCHES = [
    "app.agents.coach.flows.weekly_reflection.get_primary_user_id",
    "app.agents.coach.flows.weekly_reflection.get_training_loads",
    "app.agents.coach.flows.weekly_reflection.calculate_acwr",
    "app.agents.coach.flows.weekly_reflection.get_weekly_volume",
    "app.agents.coach.flows.weekly_reflection.calculate_training_phase",
    "app.agents.coach.flows.weekly_reflection.get_formatted_weekly_context",
    "app.agents.coach.flows.weekly_reflection.get_recent_runs_log",
    "app.agents.coach.flows.weekly_reflection.get_all_active_memories",
    "app.agents.coach.flows.weekly_reflection.build_system_instruction",
    "app.agents.coach.flows.weekly_reflection.get_shared_context_block",
    "app.agents.coach.flows.weekly_reflection.build_weekly_reflection_prompt",
    "app.agents.coach.flows.weekly_reflection.debug_log_prompt",
    "app.agents.coach.flows.weekly_reflection.send_message_with_retry",
    "app.agents.coach.flows.weekly_reflection.send_telegram_msg",
    "app.agents.coach.flows.weekly_reflection.save_message",
    "app.agents.coach.flows.weekly_reflection.rag_db",
    "app.agents.coach.flows.weekly_reflection.client",
]


class TestGenerateWeeklyReflection(unittest.TestCase):

    def _start_patches(self, reply_text="Tuần tốt!", chat_id="123456", memories=None):
        mocks = {}
        patchers = []
        for target in _PATCHES:
            p = patch(target)
            m = p.start()
            patchers.append(p)
            key = target.split(".")[-1]
            mocks[key] = m

        mocks["get_primary_user_id"].return_value = chat_id
        mocks["get_training_loads"].return_value = {"acute_load_7d": 140, "chronic_load_28d": 130}
        mocks["calculate_acwr"].return_value = {"acwr": 1.08, "status": "Optimal"}
        mocks["get_weekly_volume"].return_value = 40.0
        mocks["calculate_training_phase"].return_value = {
            "phase": "Build", "microcycle": "Week 4", "weeks_left": 6, "taper_factor": 1.0
        }
        mocks["get_formatted_weekly_context"].return_value = "Week context"
        mocks["get_recent_runs_log"].return_value = "Run log"
        mocks["get_all_active_memories"].return_value = memories or []
        mocks["build_system_instruction"].return_value = "Sys instruction"
        mocks["get_shared_context_block"].return_value = "Shared context"
        mocks["build_weekly_reflection_prompt"].return_value = "Full reflection prompt"
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

    def test_rag_memorize_called_with_correct_doc_id(self):
        mocks, patchers = self._start_patches(reply_text="Tuần tốt!")
        try:
            from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
            generate_weekly_reflection(_make_config())
            mocks["rag_db"].memorize.assert_called_once()
            call_kwargs = mocks["rag_db"].memorize.call_args[1]
            doc_id = call_kwargs["doc_id"]
            # Format: reflection_{user_id}_{YYYY-MM-DD}
            self.assertRegex(doc_id, r"^reflection_123456_\d{4}-\d{2}-\d{2}$")
        finally:
            self._stop(patchers)

    def test_rag_memorize_domain_is_coach(self):
        mocks, patchers = self._start_patches()
        try:
            from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
            generate_weekly_reflection(_make_config())
            kwargs = mocks["rag_db"].memorize.call_args[1]
            self.assertEqual(kwargs["domain"], "coach")
        finally:
            self._stop(patchers)

    def test_rag_memorize_content_contains_reflection_text(self):
        mocks, patchers = self._start_patches(reply_text="Tuần tuyệt vời!")
        try:
            from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
            generate_weekly_reflection(_make_config())
            kwargs = mocks["rag_db"].memorize.call_args[1]
            self.assertIn("Tuần tuyệt vời!", kwargs["content"])
        finally:
            self._stop(patchers)

    def test_rag_failure_does_not_abort_telegram(self):
        mocks, patchers = self._start_patches(reply_text="Tuần tốt!")
        mocks["rag_db"].memorize.side_effect = Exception("ChromaDB error")
        try:
            from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
            generate_weekly_reflection(_make_config())
            mocks["send_telegram_msg"].assert_called_once_with("123456", "Tuần tốt!")
        finally:
            self._stop(patchers)

    def test_rag_failure_does_not_abort_save_message(self):
        mocks, patchers = self._start_patches(reply_text="Tuần tốt!")
        mocks["rag_db"].memorize.side_effect = Exception("ChromaDB error")
        try:
            from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
            generate_weekly_reflection(_make_config())
            mocks["save_message"].assert_called_once()
        finally:
            self._stop(patchers)

    def test_telegram_called_with_reflection_text(self):
        mocks, patchers = self._start_patches(reply_text="Phản ánh tuần này...")
        try:
            from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
            generate_weekly_reflection(_make_config())
            mocks["send_telegram_msg"].assert_called_once_with("123456", "Phản ánh tuần này...")
        finally:
            self._stop(patchers)

    def test_save_message_called(self):
        mocks, patchers = self._start_patches(reply_text="Tuần tốt!")
        try:
            from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
            generate_weekly_reflection(_make_config())
            mocks["save_message"].assert_called_once()
            args = mocks["save_message"].call_args[0]
            self.assertEqual(args[0], "123456")
            self.assertEqual(args[1], "model")
            self.assertIn("[WEEKLY REFLECTION]", args[2])
        finally:
            self._stop(patchers)

    def test_no_chat_id_skips_telegram(self):
        mocks, patchers = self._start_patches(chat_id=None)
        try:
            from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
            generate_weekly_reflection(_make_config())
            mocks["send_telegram_msg"].assert_not_called()
        finally:
            self._stop(patchers)

    def test_no_chat_id_still_calls_rag_memorize(self):
        mocks, patchers = self._start_patches(chat_id=None)
        try:
            from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
            generate_weekly_reflection(_make_config())
            mocks["rag_db"].memorize.assert_called_once()
        finally:
            self._stop(patchers)

    def test_gemini_exception_does_not_crash(self):
        mocks, patchers = self._start_patches()
        mocks["send_message_with_retry"].side_effect = Exception("API error")
        try:
            from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
            generate_weekly_reflection(_make_config())  # should not raise
            mocks["send_telegram_msg"].assert_not_called()
        finally:
            self._stop(patchers)

    def test_memories_passed_to_prompt_builder(self):
        memories = [{"category": "goal", "fact": "Sub-2h half marathon"}]
        mocks, patchers = self._start_patches(memories=memories)
        try:
            from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
            generate_weekly_reflection(_make_config())
            kwargs = mocks["build_weekly_reflection_prompt"].call_args[1]
            active_memories = kwargs.get("active_memories", "")
            self.assertIn("Sub-2h half marathon", active_memories)
        finally:
            self._stop(patchers)

    def test_no_memories_uses_default_text(self):
        mocks, patchers = self._start_patches(memories=[])
        try:
            from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
            generate_weekly_reflection(_make_config())
            kwargs = mocks["build_weekly_reflection_prompt"].call_args[1]
            active_memories = kwargs.get("active_memories", "")
            self.assertIn("chưa ghi nhận", active_memories)
        finally:
            self._stop(patchers)


if __name__ == "__main__":
    unittest.main()
