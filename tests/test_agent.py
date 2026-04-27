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
    from unittest.mock import MagicMock
    part = MagicMock()
    part.text = text
    part.thought = False
    content = MagicMock()
    content.parts = [part]
    candidate = MagicMock()
    candidate.content = content
    r = MagicMock()
    r.text = text
    r.candidates = [candidate]
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


def _make_agent_ctx():
    from datetime import datetime, timezone
    from app.agents.coach.utils import AgentContext
    return AgentContext(
        user_id="12345",
        now=datetime(2026, 4, 26, 7, 0, tzinfo=timezone.utc),
        phase_text="Base | Cycle: W1",
        countdown_text="Còn 6 tuần đến ngày đua.",
        acwr_text="0.85 (Optimal)",
        actual_volume=30.0,
        weekly_decision_context="Target: 50km",
        system_inst="Be concise.",
        shared_context="[Context block]",
    )


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
            "g_mems":  patch("app.agents.coach.agent.get_all_active_memories", return_value=[]),
            "client":  patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT),
            "is_setup": patch("app.agents.coach.agent.is_setup_in_progress", return_value=False),
            "gen_plan": patch("app.agents.coach.agent.generate_weekly_plan", return_value=None),
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

    def test_plan_command_sends_upcoming_schedule(self):
        ps = self._patches()
        mocks = self._start_patches(ps)
        mocks["g_plans"].return_value = "- Ngày 2026-04-27: Easy Run"
        try:
            from app.agents.coach.agent import handle_telegram_chat
            handle_telegram_chat("u1", "/plan", _make_config())
            # New flow sends 2 messages: "generating" + fallback schedule
            calls = mocks["send_tg"].call_args_list
            self.assertGreaterEqual(len(calls), 1)
            all_text = " ".join(c[0][1] for c in calls)
            self.assertIn("Easy Run", all_text)
        finally:
            self._stop_patches(ps)

    def test_schedule_alias_also_shows_plan(self):
        ps = self._patches()
        mocks = self._start_patches(ps)
        try:
            from app.agents.coach.agent import handle_telegram_chat
            handle_telegram_chat("u1", "/schedule", _make_config())
            # fallback to get_upcoming_plans when plan gen returns None
            mocks["g_plans"].assert_called_with("u1", limit_days=7)
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

    def test_active_memories_injected_into_standard_chat(self):
        """Memories from DB are injected into chat prompt for standard messages."""
        ps = self._patches()
        ps["g_mems"] = patch(
            "app.agents.coach.agent.get_all_active_memories",
            return_value=[{"category": "injury_status", "fact": "Right knee pain since last week"}],
        )
        mocks = self._start_patches(ps)

        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("Take care of that knee!")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        try:
            from app.agents.coach.agent import handle_telegram_chat
            # Long message → standard path
            handle_telegram_chat("u1", "Tôi muốn biết kế hoạch chạy tuần này như thế nào?", _make_config())
            mocks["g_mems"].assert_called_once_with("u1")
            mocks["send_tg"].assert_called()
        finally:
            self._stop_patches(ps)

    def test_short_message_uses_fast_path(self):
        """Exact whitelist greetings bypass RAG (fast path)."""
        ps = self._patches()
        mocks = self._start_patches(ps)

        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("Xin chào!")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        try:
            from app.agents.coach.agent import handle_telegram_chat
            handle_telegram_chat("u1", "hi", _make_config())
            # Fast path: should NOT call get_all_active_memories (RAG skipped),
            # but SHOULD fetch lightweight local facts (weekly volume).
            mocks["g_mems"].assert_not_called()
            mocks["g_vol"].assert_called()
            mocks["send_tg"].assert_called()
        finally:
            self._stop_patches(ps)

    def test_classify_intent_fast_for_short_messages(self):
        """Only exact whitelist matches go to fast; unknowns default to standard."""
        from app.agents.coach.agent import _classify_intent
        # Exact whitelist hits → fast
        self.assertEqual(_classify_intent("ok"), "fast")
        self.assertEqual(_classify_intent("cảm ơn"), "fast")
        self.assertEqual(_classify_intent("👍"), "fast")
        # Non-whitelist (has punctuation or extra words) → standard (safe default)
        self.assertEqual(_classify_intent("Ok!"), "standard")
        self.assertEqual(_classify_intent("Cảm ơn nhé"), "standard")

    def test_classify_intent_standard_for_analysis(self):
        """Messages with analysis keywords are classified as standard."""
        from app.agents.coach.agent import _classify_intent
        self.assertEqual(_classify_intent("Phân tích bài chạy hôm nay"), "standard")
        self.assertEqual(_classify_intent("Kế hoạch tuần này như nào?"), "standard")
        self.assertEqual(_classify_intent("Đổi lịch ngày mai đi"), "standard")

    def test_classify_intent_standard_for_long_messages(self):
        """Messages longer than 60 chars are classified as standard."""
        from app.agents.coach.agent import _classify_intent
        long_msg = "Hôm nay tôi cảm thấy hơi mệt và muốn hỏi về bài tập ngày mai?"
        self.assertEqual(_classify_intent(long_msg), "standard")


class TestPastContextKeywordMatching(unittest.TestCase):
    """Fold + keyword list for past/memory/recap (VI có dấu, không dấu, EN)."""

    def test_fold_vietnamese_ascii(self):
        from app.agents.coach.agent import _fold_vietnamese_ascii
        self.assertEqual(_fold_vietnamese_ascii("Tuần trước"), "tuan truoc")
        self.assertEqual(_fold_vietnamese_ascii("HÔM QUA"), "hom qua")
        self.assertEqual(_fold_vietnamese_ascii("ký ức"), "ky uc")

    def test_standard_keywords_match_vietnamese_no_diacritics(self):
        from app.agents.coach.agent import _text_matches_keyword_list, _STANDARD_KEYWORDS
        self.assertTrue(_text_matches_keyword_list("tuan truoc chay bao nhieu km", _STANDARD_KEYWORDS))
        self.assertTrue(_text_matches_keyword_list("tong ket tuan nay", _STANDARD_KEYWORDS))
        self.assertTrue(_text_matches_keyword_list("lich trinh tap luyen", _STANDARD_KEYWORDS))

    def test_standard_keywords_match_vietnamese_with_diacritics(self):
        from app.agents.coach.agent import _text_matches_keyword_list, _STANDARD_KEYWORDS
        self.assertTrue(_text_matches_keyword_list("Tổng kết tuần vừa rồi", _STANDARD_KEYWORDS))
        self.assertTrue(_text_matches_keyword_list("Nhớ lại bài chạy hôm qua", _STANDARD_KEYWORDS))

    def test_standard_keywords_match_english(self):
        from app.agents.coach.agent import _text_matches_keyword_list, _STANDARD_KEYWORDS
        self.assertTrue(_text_matches_keyword_list("What did I run last week?", _STANDARD_KEYWORDS))
        self.assertTrue(_text_matches_keyword_list("weekly recap please", _STANDARD_KEYWORDS))

    def test_standard_keywords_no_false_positive_on_greeting(self):
        from app.agents.coach.agent import _text_matches_keyword_list, _STANDARD_KEYWORDS
        # Pure social greetings should not match training keywords
        self.assertFalse(_text_matches_keyword_list("hi there", _STANDARD_KEYWORDS))
        self.assertFalse(_text_matches_keyword_list("thanks a lot", _STANDARD_KEYWORDS))


# ══════════════════════════════════════════════════════════════════════════════
# 3. generate_morning_briefing
# ══════════════════════════════════════════════════════════════════════════════
class TestGenerateMorningBriefing(unittest.TestCase):

    # NOTE: generate_morning_briefing lives in flows/morning_briefing.py
    # After TD-003 refactor, context assembly is delegated to build_agent_context().
    # Patch targets reference that module's namespace.
    @patch("app.agents.coach.flows.morning_briefing.send_telegram_msg")
    @patch("app.agents.coach.flows.morning_briefing.save_message")
    @patch("app.agents.coach.flows.morning_briefing.load_history_for_gemini", return_value=[])
    @patch("app.agents.coach.flows.morning_briefing.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.flows.morning_briefing.get_runs_in_last_days", return_value="- 2026-03-20: 10km")
    @patch("app.agents.coach.flows.morning_briefing.get_plan_for_date", return_value=None)
    @patch("app.agents.coach.flows.morning_briefing.build_agent_context")
    @patch("app.agents.coach.flows.morning_briefing.client", _FAKE_GEMINI_CLIENT)
    @patch("app.agents.coach.flows.morning_briefing.get_primary_user_id", return_value="12345")
    def test_sends_briefing_to_telegram(self, mock_uid, mock_ctx, *mocks):
        # Arg order (innermost→outermost, skipping new= patches):
        #   mock_uid = get_primary_user_id, mock_ctx = build_agent_context
        mock_ctx.return_value = _make_agent_ctx()
        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("Good morning! ACWR is great.")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        from app.agents.coach.flows.morning_briefing import generate_morning_briefing
        generate_morning_briefing(_make_config(), weather_data="28°C, Sunny")

        send_tg = mocks[-1]
        send_tg.assert_called()
        sent = send_tg.call_args[0][1]
        self.assertIn("Good morning", sent)

    @patch("app.agents.coach.flows.morning_briefing.send_telegram_msg")
    @patch("app.agents.coach.flows.morning_briefing.save_message")
    @patch("app.agents.coach.flows.morning_briefing.load_history_for_gemini", return_value=[])
    @patch("app.agents.coach.flows.morning_briefing.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.flows.morning_briefing.get_runs_in_last_days", return_value="")
    @patch("app.agents.coach.flows.morning_briefing.get_plan_for_date", return_value=None)
    @patch("app.agents.coach.flows.morning_briefing.build_agent_context")
    @patch("app.agents.coach.flows.morning_briefing.client", _FAKE_GEMINI_CLIENT)
    @patch("app.agents.coach.flows.morning_briefing.get_primary_user_id", return_value="12345")
    def test_api_error_does_not_crash(self, mock_uid, mock_ctx, *mocks):
        mock_ctx.return_value = _make_agent_ctx()
        fake_session = MagicMock()
        fake_session.send_message.side_effect = Exception("API down")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        from app.agents.coach.flows.morning_briefing import generate_morning_briefing
        try:
            generate_morning_briefing(_make_config())
        except Exception:
            self.fail("generate_morning_briefing should not raise on API error")


# ══════════════════════════════════════════════════════════════════════════════
# 4. extract_implicit_memory
# ══════════════════════════════════════════════════════════════════════════════
class TestExtractImplicitMemory(unittest.TestCase):

    # NOTE: After refactor, extract_implicit_memory lives in flows/memory_extraction.py
    @patch("app.agents.coach.flows.memory_extraction.load_history_for_gemini", return_value=[])
    def test_empty_history_returns_early(self, mock_history):
        """No chat history → should not call Gemini at all."""
        with patch("app.agents.coach.flows.memory_extraction.client", _FAKE_GEMINI_CLIENT):
            _FAKE_GEMINI_CLIENT.chats.create.reset_mock()
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("u1")
            _FAKE_GEMINI_CLIENT.chats.create.assert_not_called()

    @patch("app.agents.coach.flows.memory_extraction.insert_memory")
    @patch("app.agents.coach.flows.memory_extraction.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.flows.memory_extraction.load_history_for_gemini", return_value=[
        {"role": "user", "parts": ["My right knee hurts after yesterday's run"]}
    ])
    @patch("app.agents.coach.flows.memory_extraction.client", _FAKE_GEMINI_CLIENT)
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

        with patch("app.core.config.load_config", return_value=_make_config()):
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("u1")

        mock_insert.assert_called_once_with(
            "u1", "health", "injury_status", "Right knee pain after long run", "active"
        )

    @patch("app.agents.coach.flows.memory_extraction.insert_memory")
    @patch("app.agents.coach.flows.memory_extraction.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.flows.memory_extraction.load_history_for_gemini", return_value=[
        {"role": "user", "parts": ["My knee is fine now"]}
    ])
    @patch("app.agents.coach.flows.memory_extraction.client", _FAKE_GEMINI_CLIENT)
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

        with patch("app.core.config.load_config", return_value=_make_config()):
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("u1")

        mock_insert.assert_called_once()
        _, _, _, _, status = mock_insert.call_args[0]
        self.assertEqual(status, "inactive")

    @patch("app.agents.coach.flows.memory_extraction.insert_memory")
    @patch("app.agents.coach.flows.memory_extraction.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.flows.memory_extraction.load_history_for_gemini", return_value=[
        {"role": "user", "parts": ["Random message"]}
    ])
    @patch("app.agents.coach.flows.memory_extraction.client", _FAKE_GEMINI_CLIENT)
    def test_invalid_json_does_not_crash(self, mock_history, mock_memories, mock_insert):
        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("NOT VALID JSON {{{{")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        with patch("app.core.config.load_config", return_value=_make_config()):
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            try:
                extract_implicit_memory("u1")
            except Exception:
                self.fail("extract_implicit_memory must not raise on bad JSON")
        mock_insert.assert_not_called()

    @patch("app.agents.coach.flows.memory_extraction.insert_memory")
    @patch("app.agents.coach.flows.memory_extraction.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.flows.memory_extraction.load_history_for_gemini", return_value=[
        {"role": "user", "parts": ["I ran 10km"]}
    ])
    @patch("app.agents.coach.flows.memory_extraction.client", _FAKE_GEMINI_CLIENT)
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

        with patch("app.core.config.load_config", return_value=_make_config()):
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("u1")

        self.assertEqual(mock_insert.call_count, 2)


# ══════════════════════════════════════════════════════════════════════════════
# 5. generate_weekly_reflection
# ══════════════════════════════════════════════════════════════════════════════
class TestGenerateWeeklyReflection(unittest.TestCase):

    # NOTE: After refactor, generate_weekly_reflection lives in flows/weekly_reflection.py
    @patch("app.agents.coach.flows.weekly_reflection.send_telegram_msg")
    @patch("app.agents.coach.flows.weekly_reflection.save_message")
    @patch("app.agents.coach.flows.weekly_reflection.rag_db", _FAKE_RAG_DB)
    @patch("app.agents.coach.flows.weekly_reflection.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.flows.weekly_reflection.get_recent_runs_log", return_value="- 2026-03-20: 10km")
    @patch("app.agents.coach.flows.weekly_reflection.get_weekly_volume", return_value=42.0)
    @patch("app.agents.coach.flows.weekly_reflection.get_training_loads",
           return_value={"acute_load_7d": 90, "chronic_load_28d": 360})
    @patch("app.agents.coach.flows.weekly_reflection.get_formatted_weekly_context", return_value="Target: 45km")
    @patch("app.agents.coach.flows.weekly_reflection.client", _FAKE_GEMINI_CLIENT)
    @patch("app.agents.coach.flows.weekly_reflection.get_primary_user_id", return_value="12345")
    def test_sends_reflection_to_telegram_and_memorizes(self, *mocks):
        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("Week summary: good progress!")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session
        _FAKE_RAG_DB.memorize.reset_mock()

        from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
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

    @patch("app.agents.coach.flows.weekly_reflection.send_telegram_msg")
    @patch("app.agents.coach.flows.weekly_reflection.save_message")
    @patch("app.agents.coach.flows.weekly_reflection.rag_db", _FAKE_RAG_DB)
    @patch("app.agents.coach.flows.weekly_reflection.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.flows.weekly_reflection.get_recent_runs_log", return_value="")
    @patch("app.agents.coach.flows.weekly_reflection.get_weekly_volume", return_value=0.0)
    @patch("app.agents.coach.flows.weekly_reflection.get_training_loads",
           return_value={"acute_load_7d": 0, "chronic_load_28d": 0})
    @patch("app.agents.coach.flows.weekly_reflection.get_formatted_weekly_context", return_value="")
    @patch("app.agents.coach.flows.weekly_reflection.client", _FAKE_GEMINI_CLIENT)
    @patch("app.agents.coach.flows.weekly_reflection.get_primary_user_id", return_value="12345")
    def test_api_error_does_not_crash(self, *mocks):
        fake_session = MagicMock()
        fake_session.send_message.side_effect = Exception("Gemini down")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        from app.agents.coach.flows.weekly_reflection import generate_weekly_reflection
        try:
            generate_weekly_reflection(_make_config())
        except Exception:
            self.fail("generate_weekly_reflection must not crash on API error")


# ══════════════════════════════════════════════════════════════════════════════
# Tiered System Prompt
# ══════════════════════════════════════════════════════════════════════════════
class TestTieredSystemPrompt(unittest.TestCase):
    """Verify the fast/standard prompt tier split in chat_with_coach."""

    def test_core_prompt_excludes_gcs_rubric(self):
        from app.agents.coach.prompts import build_core_system_instruction
        prompt = build_core_system_instruction("Be brief.")
        self.assertNotIn("GCS", prompt)
        self.assertNotIn("THANG ĐIỂM", prompt)
        self.assertNotIn("BẢNG HR ZONES", prompt)
        self.assertNotIn("BẢNG POWER ZONES", prompt)
        self.assertNotIn("BẢNG PACE ZONES", prompt)
        self.assertNotIn("KỶ LUẬT SỬ DỤNG TOOL", prompt)

    def test_core_prompt_includes_identity_and_psychology(self):
        from app.agents.coach.prompts import build_core_system_instruction
        prompt = build_core_system_instruction("Custom rule.")
        self.assertIn("Coach Dyno", prompt)
        self.assertIn("Custom rule.", prompt)
        self.assertIn("TÂM LÝ VẬN ĐỘNG VIÊN", prompt)

    def test_full_prompt_includes_gcs_rubric(self):
        from app.agents.coach.prompts import build_system_instruction
        prompt = build_system_instruction(
            custom_instruction="Be concise.",
            user_profile="Runner, age 35",
            max_hr=185,
            rest_hr=55,
        )
        self.assertIn("THANG ĐIỂM GCS", prompt)
        self.assertIn("BẢNG HR ZONES", prompt)
        self.assertIn("KỶ LUẬT SỬ DỤNG TOOL", prompt)

    @patch("app.agents.coach.agent.build_core_system_instruction")
    @patch("app.agents.coach.agent.build_system_instruction")
    @patch("app.agents.coach.agent.send_telegram_msg")
    @patch("app.agents.coach.agent.send_message_with_retry")
    @patch("app.agents.coach.agent.load_history_for_gemini", return_value=[])
    @patch("app.agents.coach.agent.calculate_training_phase",
           return_value={"phase": "Base", "microcycle": "W1", "weeks_left": 8})
    @patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT)
    def test_fast_path_uses_core_prompt(
        self, mock_phase, mock_hist, mock_send, mock_tg, mock_full, mock_core,
    ):
        mock_core.return_value = "CORE_SYSTEM"
        mock_full.return_value = "FULL_SYSTEM"
        mock_send.return_value = _make_response("Hi!")
        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("Hi!")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        from app.agents.coach.agent import handle_telegram_chat
        handle_telegram_chat("TEST_CHAT_ID", "hello", _make_config())

        mock_core.assert_called_once()
        mock_full.assert_not_called()

    @patch("app.agents.coach.agent.build_core_system_instruction")
    @patch("app.agents.coach.agent.build_system_instruction")
    @patch("app.agents.coach.agent.send_telegram_msg")
    @patch("app.agents.coach.agent.send_message_with_retry")
    @patch("app.agents.coach.agent.load_history_for_gemini", return_value=[])
    @patch("app.agents.coach.agent.get_upcoming_plans", return_value="")
    @patch("app.agents.coach.agent.get_weekly_volume", return_value=0.0)
    @patch("app.agents.coach.agent.get_formatted_weekly_context", return_value="")
    @patch("app.agents.coach.agent.get_all_active_memories", return_value=[])
    @patch("app.agents.coach.agent.calculate_training_phase",
           return_value={"phase": "Base", "microcycle": "W1", "weeks_left": 8})
    @patch("app.agents.coach.agent.client", _FAKE_GEMINI_CLIENT)
    def test_standard_path_uses_full_prompt(
        self, mock_phase, mock_mem, mock_weekly_ctx,
        mock_vol, mock_plans, mock_hist, mock_send, mock_tg,
        mock_full, mock_core,
    ):
        mock_core.return_value = "CORE_SYSTEM"
        mock_full.return_value = "FULL_SYSTEM"
        fake_session = MagicMock()
        fake_session.send_message.return_value = _make_response("Analysis done.")
        _FAKE_GEMINI_CLIENT.chats.create.return_value = fake_session

        # >60 chars → triggers standard path
        long_msg = "Phân tích bài chạy hôm qua cho tôi, xem ACWR và GCS thế nào?"
        from app.agents.coach.agent import handle_telegram_chat
        handle_telegram_chat("TEST_CHAT_ID", long_msg, _make_config())

        mock_full.assert_called_once()
        mock_core.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
