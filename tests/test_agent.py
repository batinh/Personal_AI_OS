"""
Layer 4 – Agent Flow Tests: app/agents/coach/agent.py
=======================================================
Gemini API client, DB, RAG và Telegram đều được mock.
Không tốn tiền API, không cần network.
NOTE: google.genai + chromadb stubs are injected by conftest.py at session
      level — no need to stub them again here.
"""
import json
import os
import unittest
from unittest.mock import MagicMock, patch

# ─────────────────────────────────────────────────────────────────────────────
# Module-level fake clients (reused across all test classes in this file)
# ─────────────────────────────────────────────────────────────────────────────
_FAKE_GEMINI_CLIENT = MagicMock()
_FAKE_RAG_DB = MagicMock()


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def _make_response(text: str):
    r = MagicMock()
    r.text = text
    return r


def _make_config():
    return {
        "model_name": "models/gemini-2.0-flash",
        "system_instruction": "Be concise.",
        "user_profile": "Runner, age 35",
        "max_hr": 185,
        "rest_hr": 55,
        "race_date": "2026-06-01",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 1. send_message_with_retry
# ══════════════════════════════════════════════════════════════════════════════
class TestSendMessageWithRetry(unittest.TestCase):

    def _get_fn(self):
        from app.agents.coach.agent import send_message_with_retry
        return send_message_with_retry

    def test_success_on_first_try(self):
        fn = self._get_fn()
        mock_session = MagicMock()
        mock_session.send_message.return_value = _make_response("OK")
        result = fn(mock_session, "hello", max_retries=3)
        self.assertEqual(result.text, "OK")
        mock_session.send_message.assert_called_once_with("hello")

    @patch("app.agents.coach.agent.time.sleep")
    def test_retries_on_503_then_succeeds(self, mock_sleep):
        fn = self._get_fn()
        mock_session = MagicMock()
        mock_session.send_message.side_effect = [
            Exception("503 Unavailable"),
            _make_response("Recovered"),
        ]
        result = fn(mock_session, "hello", max_retries=3)
        self.assertEqual(result.text, "Recovered")
        mock_sleep.assert_called_once_with(1)  # first backoff = 2^0 = 1s

    @patch("app.agents.coach.agent.time.sleep")
    def test_retries_on_429_then_succeeds(self, mock_sleep):
        fn = self._get_fn()
        mock_session = MagicMock()
        mock_session.send_message.side_effect = [
            Exception("429 Too Many Requests"),
            _make_response("OK"),
        ]
        result = fn(mock_session, "msg", max_retries=3)
        self.assertEqual(result.text, "OK")

    @patch("app.agents.coach.agent.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep):
        fn = self._get_fn()
        mock_session = MagicMock()
        mock_session.send_message.side_effect = Exception("503 Unavailable")
        with self.assertRaises(Exception):
            fn(mock_session, "msg", max_retries=3)
        self.assertEqual(mock_session.send_message.call_count, 3)

    @patch("app.agents.coach.agent.time.sleep")
    def test_non_503_error_raises_immediately_without_retry(self, mock_sleep):
        fn = self._get_fn()
        mock_session = MagicMock()
        mock_session.send_message.side_effect = Exception("Invalid API key")
        with self.assertRaises(Exception) as ctx:
            fn(mock_session, "msg", max_retries=3)
        # Should raise on first call, NOT retry
        mock_session.send_message.assert_called_once()
        mock_sleep.assert_not_called()
        self.assertIn("Invalid API key", str(ctx.exception))


# ══════════════════════════════════════════════════════════════════════════════
# 2. handle_telegram_chat
# ══════════════════════════════════════════════════════════════════════════════
class TestHandleTelegramChat(unittest.TestCase):

    def _patches(self):
        """Return a dict of all required patches for handle_telegram_chat."""
        return {
            "send_tg": patch("app.agents.coach.agent.send_telegram_msg"),
            "typing":  patch("app.agents.coach.agent.send_typing_action"),
            "clear_h": patch("app.agents.coach.agent.clear_history"),
            "save_m":  patch("app.agents.coach.agent.save_message"),
            "load_h":  patch("app.agents.coach.agent.load_history_for_gemini", return_value=[]),
            "g_plans": patch("app.agents.coach.agent.get_upcoming_plans", return_value="Rest day"),
            "g_vol":   patch("app.agents.coach.agent.get_weekly_volume", return_value=25.0),
            "g_loads": patch("app.agents.coach.agent.get_training_loads",
                             return_value={"acute_load_7d": 100, "chronic_load_28d": 400}),
            "g_target": patch("app.agents.coach.agent.get_weekly_target", return_value=None),
            "client":  patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT),
        }

    def _start_patches(self, patches):
        mocks = {}
        for k, p in patches.items():
            mocks[k] = p.start()
        return mocks

    def _stop_patches(self, patches):
        for p in patches.values():
            p.stop()

    def test_clear_command_clears_history_and_notifies(self):
        ps = self._patches()
        mocks = self._start_patches(ps)
        try:
            from app.agents.coach.agent import handle_telegram_chat
            handle_telegram_chat("u1", "/clear", _make_config())
            mocks["clear_h"].assert_called_once_with("u1")
            mocks["send_tg"].assert_called_once()
            sent_text = mocks["send_tg"].call_args[0][1]
            self.assertIn("xóa", sent_text.lower())
        finally:
            self._stop_patches(ps)

    def test_reset_command_also_clears_history(self):
        ps = self._patches()
        mocks = self._start_patches(ps)
        try:
            from app.agents.coach.agent import handle_telegram_chat
            handle_telegram_chat("u1", "/reset", _make_config())
            mocks["clear_h"].assert_called_once()
        finally:
            self._stop_patches(ps)

    def test_normal_chat_calls_gemini_and_sends_reply(self):
        ps = self._patches()
        mocks = self._start_patches(ps)

        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("Great workout!")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        try:
            from app.agents.coach.agent import handle_telegram_chat
            handle_telegram_chat("u1", "Hôm nay tôi chạy 10km", _make_config())
            mocks["send_tg"].assert_called()
            sent = mocks["send_tg"].call_args[0][1]
            self.assertEqual(sent, "Great workout!")
        finally:
            self._stop_patches(ps)

    def test_normal_chat_saves_both_user_and_model_messages(self):
        ps = self._patches()
        mocks = self._start_patches(ps)

        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("Nice!")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        try:
            from app.agents.coach.agent import handle_telegram_chat
            handle_telegram_chat("u1", "Chạy xong rồi", _make_config())
            calls = [c[0] for c in mocks["save_m"].call_args_list]
            roles = [c[1] for c in calls]
            self.assertIn("user", roles)
            self.assertIn("model", roles)
        finally:
            self._stop_patches(ps)

    def test_api_error_sends_fallback_message_to_user(self):
        ps = self._patches()
        mocks = self._start_patches(ps)

        fake_session = MagicMock()
        fake_session.send_message.side_effect = Exception("API error")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        try:
            from app.agents.coach.agent import handle_telegram_chat
            handle_telegram_chat("u1", "hello", _make_config())
            mocks["send_tg"].assert_called()
            fallback = mocks["send_tg"].call_args[0][1]
            # Vietnamese error message should be sent
            self.assertIn("⚠️", fallback)
        finally:
            self._stop_patches(ps)

    def test_typing_indicator_sent_before_processing(self):
        ps = self._patches()
        mocks = self._start_patches(ps)

        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("OK")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        try:
            from app.agents.coach.agent import handle_telegram_chat
            handle_telegram_chat("u1", "test", _make_config())
            mocks["typing"].assert_called_once_with("u1")
        finally:
            self._stop_patches(ps)


# ══════════════════════════════════════════════════════════════════════════════
# 3. generate_morning_briefing
# ══════════════════════════════════════════════════════════════════════════════
class TestGenerateMorningBriefing(unittest.TestCase):

    @patch("app.agents.coach.agent.send_telegram_msg")
    @patch("app.agents.coach.agent.save_message")
    @patch("app.agents.coach.agent.load_history_for_gemini", return_value=[])
    @patch("app.agents.coach.agent.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.agent.get_runs_in_last_days", return_value="- 2026-03-20: 10km")
    @patch("app.agents.coach.agent.get_plan_for_date", return_value=None)
    @patch("app.agents.coach.agent.get_weekly_volume", return_value=30.0)
    @patch("app.agents.coach.agent.get_training_loads",
           return_value={"acute_load_7d": 80, "chronic_load_28d": 320})
    @patch("app.agents.coach.agent.get_weekly_target", return_value=None)
    @patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT)
    @patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "12345"})
    def test_sends_briefing_to_telegram(self, *mocks):
        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("Good morning! ACWR is great.")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        from app.agents.coach.agent import generate_morning_briefing
        generate_morning_briefing(_make_config(), weather_data="28°C, Sunny")

        # The last mock in *mocks is send_telegram_msg (reversed order)
        send_tg = mocks[-1]
        send_tg.assert_called()
        sent = send_tg.call_args[0][1]
        self.assertIn("Good morning", sent)

    @patch("app.agents.coach.agent.send_telegram_msg")
    @patch("app.agents.coach.agent.save_message")
    @patch("app.agents.coach.agent.load_history_for_gemini", return_value=[])
    @patch("app.agents.coach.agent.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.agent.get_runs_in_last_days", return_value="")
    @patch("app.agents.coach.agent.get_plan_for_date", return_value=None)
    @patch("app.agents.coach.agent.get_weekly_volume", return_value=0.0)
    @patch("app.agents.coach.agent.get_training_loads",
           return_value={"acute_load_7d": 0, "chronic_load_28d": 0})
    @patch("app.agents.coach.agent.get_weekly_target", return_value=None)
    @patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT)
    @patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "12345"})
    def test_api_error_does_not_crash(self, *mocks):
        fake_session = MagicMock()
        fake_session.send_message.side_effect = Exception("API down")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        from app.agents.coach.agent import generate_morning_briefing
        try:
            generate_morning_briefing(_make_config())
        except Exception:
            self.fail("generate_morning_briefing should not raise on API error")


# ══════════════════════════════════════════════════════════════════════════════
# 4. extract_implicit_memory
# ══════════════════════════════════════════════════════════════════════════════
class TestExtractImplicitMemory(unittest.TestCase):

    @patch("app.agents.coach.agent.load_history_for_gemini", return_value=[])
    def test_empty_history_returns_early(self, mock_history):
        """No chat history → should not call Gemini at all."""
        with patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT):
            _FAKE_GEMINI_CLIENT.chats.create.reset_mock()
            from app.agents.coach.agent import extract_implicit_memory
            extract_implicit_memory("u1")
            _FAKE_GEMINI_CLIENT.chats.create.assert_not_called()

    @patch("app.agents.coach.agent.insert_memory")
    @patch("app.agents.coach.agent.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.agent.load_history_for_gemini", return_value=[
        {"role": "user", "parts": ["My right knee hurts after yesterday's run"]}
    ])
    @patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT)
    def test_valid_json_response_inserts_memory(self, mock_history, mock_memories, mock_insert):
        fake_payload = json.dumps({
            "items": [{
                "domain": "health",
                "category": "injury_status",
                "fact": "Right knee pain after long run",
                "status": "active"
            }]
        })
        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response(fake_payload)
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        with patch("app.agents.coach.agent.load_config", return_value=_make_config()):
            from app.agents.coach.agent import extract_implicit_memory
            extract_implicit_memory("u1")

        mock_insert.assert_called_once_with(
            "u1", "health", "injury_status", "Right knee pain after long run", "active"
        )

    @patch("app.agents.coach.agent.insert_memory")
    @patch("app.agents.coach.agent.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.agent.load_history_for_gemini", return_value=[
        {"role": "user", "parts": ["My knee is fine now"]}
    ])
    @patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT)
    def test_inactive_status_is_passed_to_db(self, mock_history, mock_memories, mock_insert):
        fake_payload = json.dumps({
            "items": [{
                "domain": "health",
                "category": "injury_status",
                "fact": "Knee healed",
                "status": "inactive"
            }]
        })
        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response(fake_payload)
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        with patch("app.agents.coach.agent.load_config", return_value=_make_config()):
            from app.agents.coach.agent import extract_implicit_memory
            extract_implicit_memory("u1")

        mock_insert.assert_called_once()
        _, _, _, _, status = mock_insert.call_args[0]
        self.assertEqual(status, "inactive")

    @patch("app.agents.coach.agent.insert_memory")
    @patch("app.agents.coach.agent.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.agent.load_history_for_gemini", return_value=[
        {"role": "user", "parts": ["Random message"]}
    ])
    @patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT)
    def test_invalid_json_does_not_crash(self, mock_history, mock_memories, mock_insert):
        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("NOT VALID JSON {{{{")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        with patch("app.agents.coach.agent.load_config", return_value=_make_config()):
            from app.agents.coach.agent import extract_implicit_memory
            try:
                extract_implicit_memory("u1")
            except Exception:
                self.fail("extract_implicit_memory must not raise on bad JSON")
        mock_insert.assert_not_called()

    @patch("app.agents.coach.agent.insert_memory")
    @patch("app.agents.coach.agent.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.agent.load_history_for_gemini", return_value=[
        {"role": "user", "parts": ["I ran 10km"]}
    ])
    @patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT)
    def test_multiple_items_all_inserted(self, mock_history, mock_memories, mock_insert):
        fake_payload = json.dumps({
            "items": [
                {"domain": "sports", "category": "main_goal", "fact": "Sub 4h marathon", "status": "active"},
                {"domain": "sports", "category": "gear_preference", "fact": "Vaporfly 3", "status": "active"},
            ]
        })
        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response(fake_payload)
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        with patch("app.agents.coach.agent.load_config", return_value=_make_config()):
            from app.agents.coach.agent import extract_implicit_memory
            extract_implicit_memory("u1")

        self.assertEqual(mock_insert.call_count, 2)


# ══════════════════════════════════════════════════════════════════════════════
# 5. generate_weekly_reflection
# ══════════════════════════════════════════════════════════════════════════════
class TestGenerateWeeklyReflection(unittest.TestCase):

    @patch("app.agents.coach.agent.send_telegram_msg")
    @patch("app.agents.coach.agent.save_message")
    @patch("app.agents.coach.agent.rag_db", _FAKE_RAG_DB)
    @patch("app.agents.coach.agent.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.agent.get_recent_runs_log", return_value="- 2026-03-20: 10km")
    @patch("app.agents.coach.agent.get_weekly_volume", return_value=42.0)
    @patch("app.agents.coach.agent.get_training_loads",
           return_value={"acute_load_7d": 90, "chronic_load_28d": 360})
    @patch("app.agents.coach.agent.get_weekly_target", return_value=None)
    @patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT)
    @patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "12345"})
    def test_sends_reflection_to_telegram_and_memorizes(self, *mocks):
        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("Week summary: good progress!")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session
        _FAKE_RAG_DB.memorize.reset_mock()

        from app.agents.coach.agent import generate_weekly_reflection
        generate_weekly_reflection(_make_config())

        send_tg = mocks[-1]
        send_tg.assert_called()
        sent = send_tg.call_args[0][1]
        self.assertIn("Week summary", sent)

        # Verify RAG memorization was called
        _FAKE_RAG_DB.memorize.assert_called_once()
        mem_call = _FAKE_RAG_DB.memorize.call_args[1]
        self.assertIn("reflection", mem_call.get("doc_id", ""))
        self.assertEqual(mem_call.get("domain"), "coach")

    @patch("app.agents.coach.agent.send_telegram_msg")
    @patch("app.agents.coach.agent.save_message")
    @patch("app.agents.coach.agent.rag_db", _FAKE_RAG_DB)
    @patch("app.agents.coach.agent.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.agent.get_recent_runs_log", return_value="")
    @patch("app.agents.coach.agent.get_weekly_volume", return_value=0.0)
    @patch("app.agents.coach.agent.get_training_loads",
           return_value={"acute_load_7d": 0, "chronic_load_28d": 0})
    @patch("app.agents.coach.agent.get_weekly_target", return_value=None)
    @patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT)
    @patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "12345"})
    def test_api_error_does_not_crash(self, *mocks):
        fake_session = MagicMock()
        fake_session.send_message.side_effect = Exception("Gemini down")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        from app.agents.coach.agent import generate_weekly_reflection
        try:
            generate_weekly_reflection(_make_config())
        except Exception:
            self.fail("generate_weekly_reflection must not crash on API error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
