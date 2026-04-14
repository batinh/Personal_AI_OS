"""
Smoke Tests — Import-level checks that catch missing symbols and broken imports.
================================================================
These tests run at the module level (no logic executed). Any ImportError or
AttributeError here means the app will crash on startup or on first real request.

Run first: python -m pytest tests/test_smoke.py -v
"""
import unittest


class TestCoreImports(unittest.TestCase):
    """Core infrastructure must be importable."""

    def test_config_importable(self):
        from app.core.config import load_config  # noqa: F401

    def test_database_importable(self):
        from app.core.database import get_db  # noqa: F401

    def test_notification_importable(self):
        from app.core.notification import (  # noqa: F401
            sanitize_md_to_tg_html,
            send_telegram_msg,
            send_typing_action,
            _strip_html,
        )

    def test_user_context_importable(self):
        from app.core.user_context import get_primary_user_id  # noqa: F401


class TestRouterImports(unittest.TestCase):
    """All routers must be importable — FastAPI registers them at startup."""

    def test_webhooks_router_importable(self):
        from app.routers.webhooks import router  # noqa: F401

    def test_execute_sync_all_importable(self):
        from app.agents.coach.harvest import execute_sync_all  # noqa: F401

    def test_audit_router_importable(self):
        from app.routers.audit import router  # noqa: F401


class TestServiceImports(unittest.TestCase):
    """Services must be importable — scheduler registers jobs at startup."""

    def test_scheduler_importable(self):
        from app.services.scheduler import start_scheduler  # noqa: F401

    def test_telegram_router_importable(self):
        from app.services.telegram_router import route_message  # noqa: F401


class TestCoachAgentImports(unittest.TestCase):
    """Coach agent symbols — all referenced from agent.py must exist in prompts.py."""

    def test_prompts_build_system_instruction(self):
        from app.agents.coach.prompts import build_system_instruction  # noqa: F401

    def test_prompts_build_core_system_instruction(self):
        # Added to fix ImportError (fast-chat path) — must stay importable
        from app.agents.coach.prompts import build_core_system_instruction  # noqa: F401

    def test_prompts_get_shared_context_block(self):
        from app.agents.coach.prompts import get_shared_context_block  # noqa: F401

    def test_coach_agent_importable(self):
        from app.agents.coach import agent  # noqa: F401

    def test_metrics_engine_importable(self):
        from app.agents.coach.metrics_engine import (  # noqa: F401
            compute_stream_metrics,
            build_run_metrics_block,
        )

    def test_new_tools_importable(self):
        from app.agents.coach.tools import (  # noqa: F401
            get_run_stream_csv,
            get_run_computed_metrics,
            get_metric_trend,
            get_volume_for_week,
            get_volume_summary,
        )

    def test_database_computed_metrics_functions(self):
        from app.core.database import (  # noqa: F401
            upsert_run_computed_metrics,
            get_run_metrics_from_db,
            get_metric_trend_data,
            get_monthly_volume,
            get_yearly_volume,
        )


class TestNewsAgentImports(unittest.TestCase):
    """News agent symbols must all be importable."""

    def test_news_agent_importable(self):
        from app.agents.news import agent  # noqa: F401

    def test_news_prompts_importable(self):
        from app.agents.news.prompts import build_news_system_instruction  # noqa: F401

    def test_session_prompt_importable(self):
        from app.agents.news.prompts import build_session_prompt  # noqa: F401

    def test_news_memory_importable(self):
        from app.agents.news.memory import load_news_memory  # noqa: F401

    def test_telegram_handler_importable(self):
        from app.agents.news.telegram_handler import handle_news_command  # noqa: F401

    def test_news_topic_prompt_importable(self):
        from app.agents.news.prompts import build_topic_system_instruction  # noqa: F401
        from app.agents.news.prompts import build_topic_prompt  # noqa: F401

    def test_news_on_demand_prompt_importable(self):
        from app.agents.news.prompts import build_on_demand_system_instruction  # noqa: F401
        from app.agents.news.prompts import build_on_demand_prompt  # noqa: F401

    def test_generate_on_demand_briefing_importable(self):
        from app.agents.news.agent import generate_on_demand_briefing  # noqa: F401

    def test_news_chat_handler_importable(self):
        from app.agents.news.telegram_handler import handle_news_chat  # noqa: F401


if __name__ == "__main__":
    unittest.main(verbosity=2)
