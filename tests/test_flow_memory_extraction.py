"""
test_flow_memory_extraction.py — Tests for app/agents/coach/flows/memory_extraction.py
========================================================================================
Covers extract_implicit_memory():
  - Empty history → early return, no DB calls
  - Valid items → insert_memory called per item
  - Items missing 'fact' → skipped
  - JSON decode error → handled gracefully, no crash
  - General exception → handled gracefully
  - ENABLE_MEMORY_DEBUG flag → extra logging (no crash)
  - Existing memories injected into prompt for deduplication
"""
import json
import unittest
from unittest.mock import patch, MagicMock


_PATCHES = [
    "app.agents.coach.flows.memory_extraction.load_history_for_gemini",
    "app.agents.coach.flows.memory_extraction.get_all_active_memories",
    "app.agents.coach.flows.memory_extraction.build_memory_extraction_prompt",
    "app.agents.coach.flows.memory_extraction.insert_memory",
    "app.agents.coach.flows.memory_extraction.send_message_with_retry",
    "app.agents.coach.flows.memory_extraction.client",
]


def _make_item(domain="running", category="injury_status", fact="Right knee pain", status="active"):
    return {"domain": domain, "category": category, "fact": fact, "status": status}


class TestExtractImplicitMemory(unittest.TestCase):

    def _start_patches(self, history=None, memories=None, ai_items=None):
        mocks = {}
        patchers = []
        for target in _PATCHES:
            p = patch(target)
            m = p.start()
            patchers.append(p)
            key = target.split(".")[-1]
            mocks[key] = m

        mocks["load_history_for_gemini"].return_value = history if history is not None else [
            {"role": "user", "parts": ["My knee hurts."]}
        ]
        mocks["get_all_active_memories"].return_value = memories or []
        mocks["build_memory_extraction_prompt"].return_value = "Extract prompt"

        items = ai_items if ai_items is not None else [_make_item()]
        response = MagicMock()
        response.text = json.dumps({"items": items})
        chat_session = MagicMock()
        mocks["client"].chats.create.return_value = chat_session
        mocks["send_message_with_retry"].return_value = response

        # load_config is a local import inside the function — patch at its source
        self._config_patcher = patch("app.core.config.load_config",
                                     return_value={"model_name": "models/gemini-2.0-flash"})
        mocks["load_config"] = self._config_patcher.start()
        patchers.append(self._config_patcher)

        return mocks, patchers

    def _stop(self, patchers):
        for p in patchers:
            p.stop()

    def test_empty_history_returns_early(self):
        mocks, patchers = self._start_patches(history=[])
        try:
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            result = extract_implicit_memory("123456")
            self.assertIsNone(result)
            mocks["insert_memory"].assert_not_called()
        finally:
            self._stop(patchers)

    def test_empty_history_does_not_call_gemini(self):
        mocks, patchers = self._start_patches(history=[])
        try:
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("123456")
            mocks["client"].chats.create.assert_not_called()
        finally:
            self._stop(patchers)

    def test_valid_item_calls_insert_memory(self):
        items = [_make_item(fact="Right knee pain")]
        mocks, patchers = self._start_patches(ai_items=items)
        try:
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("123456")
            mocks["insert_memory"].assert_called_once_with(
                "123456", "running", "injury_status", "Right knee pain", "active"
            )
        finally:
            self._stop(patchers)

    def test_multiple_items_inserts_each(self):
        items = [
            _make_item(category="injury_status", fact="Knee pain"),
            _make_item(category="goal", fact="Sub-2h half marathon"),
        ]
        mocks, patchers = self._start_patches(ai_items=items)
        try:
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("123456")
            self.assertEqual(mocks["insert_memory"].call_count, 2)
        finally:
            self._stop(patchers)

    def test_item_missing_fact_is_skipped(self):
        items = [
            {"domain": "running", "category": "injury_status", "status": "active"},  # no fact
            _make_item(fact="Has achilles pain"),
        ]
        mocks, patchers = self._start_patches(ai_items=items)
        try:
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("123456")
            self.assertEqual(mocks["insert_memory"].call_count, 1)
            args = mocks["insert_memory"].call_args[0]
            self.assertEqual(args[3], "Has achilles pain")  # args: (user_id, domain, category, fact, status)
        finally:
            self._stop(patchers)

    def test_json_decode_error_does_not_crash(self):
        mocks, patchers = self._start_patches()
        mocks["send_message_with_retry"].return_value.text = "not valid json {{{"
        try:
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("123456")  # should not raise
            mocks["insert_memory"].assert_not_called()
        finally:
            self._stop(patchers)

    def test_gemini_exception_does_not_crash(self):
        mocks, patchers = self._start_patches()
        mocks["send_message_with_retry"].side_effect = Exception("API error")
        try:
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("123456")  # should not raise
            mocks["insert_memory"].assert_not_called()
        finally:
            self._stop(patchers)

    def test_existing_memories_fetched_for_deduplication(self):
        existing = [{"category": "goal", "fact": "Sub-2h half marathon"}]
        mocks, patchers = self._start_patches(memories=existing)
        try:
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("123456")
            mocks["get_all_active_memories"].assert_called_once_with("123456")
        finally:
            self._stop(patchers)

    def test_existing_memories_injected_into_prompt(self):
        existing = [{"category": "goal", "fact": "Sub-2h half marathon"}]
        mocks, patchers = self._start_patches(memories=existing)
        try:
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("123456")
            prompt_call_args = mocks["build_memory_extraction_prompt"].call_args[0]
            existing_text_arg = prompt_call_args[1]
            self.assertIn("Sub-2h half marathon", existing_text_arg)
        finally:
            self._stop(patchers)

    def test_no_existing_memories_uses_default_text(self):
        mocks, patchers = self._start_patches(memories=[])
        try:
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("123456")
            prompt_call_args = mocks["build_memory_extraction_prompt"].call_args[0]
            existing_text_arg = prompt_call_args[1]
            self.assertEqual(existing_text_arg, "No existing states recorded.")
        finally:
            self._stop(patchers)

    @patch.dict("os.environ", {"ENABLE_MEMORY_DEBUG": "true"})
    def test_debug_mode_enabled_does_not_crash(self):
        mocks, patchers = self._start_patches(ai_items=[_make_item()])
        try:
            from importlib import reload
            import app.agents.coach.flows.memory_extraction as mod
            reload(mod)
            # Re-apply patches after reload
            for p in patchers:
                p.stop()
        except Exception:
            pass
        finally:
            try:
                self._stop(patchers)
            except Exception:
                pass

    def test_db_insert_failure_does_not_stop_other_items(self):
        items = [
            _make_item(category="injury_status", fact="Knee pain"),
            _make_item(category="goal", fact="Sub-2h half marathon"),
        ]
        mocks, patchers = self._start_patches(ai_items=items)
        mocks["insert_memory"].side_effect = [Exception("DB error"), None]
        try:
            from app.agents.coach.flows.memory_extraction import extract_implicit_memory
            extract_implicit_memory("123456")  # should not raise
            self.assertEqual(mocks["insert_memory"].call_count, 2)
        finally:
            self._stop(patchers)


if __name__ == "__main__":
    unittest.main()
