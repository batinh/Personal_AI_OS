from unittest.mock import Mock, patch

import pytest

from app.core.gemini_utils import GeminiFailure
from app.services import scheduler


@pytest.mark.parametrize(
    "code, message, blocks, expected",
    [
        (402, "RESOURCE_EXHAUSTED", True, "số dư"),
        (404, "model not found", True, "cấu hình"),
        (504, "deadline exceeded", False, "quá hạn"),
        (429, "rate limit", False, "thử lại"),
    ],
)
def test_provider_failure(code, message, blocks, expected):
    error = Exception(message)
    error.code = code
    failure = GeminiFailure.from_error(error)
    assert failure.blocks_batch is blocks
    assert expected in str(failure)


@pytest.mark.parametrize("blocks, calls", [(True, 1), (False, 2)])
def test_scheduler_batch(monkeypatch, blocks, calls):
    act = {"activity_id": "a", "name": "Run"}
    analyze = Mock(side_effect=GeminiFailure("error", blocks_batch=blocks))
    monkeypatch.setattr(scheduler, "get_primary_user_id", lambda: "1")
    monkeypatch.setattr(scheduler, "load_config", dict)
    monkeypatch.setattr(
        scheduler, "get_activities_needing_analysis", lambda *a, **kw: [act, act]
    )
    monkeypatch.setattr(scheduler, "get_run_activity_raw", lambda _: {"full_meta": {}})
    monkeypatch.setattr(scheduler, "analyze_run_with_gemini", analyze)
    with patch.object(scheduler, "send_telegram_msg") as send:
        scheduler.task_retry_pending_analyses()
    assert analyze.call_count == calls
    send.assert_not_called()


@pytest.mark.parametrize(
    "module, handler", [("admin", "save_settings"), ("console", "console_save")]
)
def test_model_validation_precedes_settings_changes(monkeypatch, module, handler):
    import asyncio
    from importlib import import_module
    from fastapi import HTTPException

    router = import_module(f"app.routers.{module}")
    config = {"model_name": "previous", "system_instruction": "original"}
    monkeypatch.setattr(router, "load_config", lambda: config)
    probe = Mock(side_effect=TimeoutError())
    monkeypatch.setattr(router, "validate_coach_model", probe)
    save = Mock()
    monkeypatch.setattr(router, "save_config", save)
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            getattr(router, handler)(None, "new", "runner", model_name="candidate")
        )
    assert error.value.status_code == 422
    assert config == {"model_name": "previous", "system_instruction": "original"}
    save.assert_not_called()
