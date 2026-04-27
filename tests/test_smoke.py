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

    def test_timezone_utils_importable(self):
        from app.core.timezone_utils import get_local_tz  # noqa: F401

    def test_gemini_utils_importable(self):
        from app.core.gemini_utils import (
            extract_text,
            strip_thought_preamble,
        )  # noqa: F401


class TestRouterImports(unittest.TestCase):
    """All routers must be importable — FastAPI registers them at startup."""

    def test_webhooks_router_importable(self):
        from app.routers.webhooks import router  # noqa: F401

    def test_execute_sync_all_importable(self):
        from app.agents.coach.harvest import execute_sync_all  # noqa: F401

    def test_audit_router_importable(self):
        from app.routers.audit import router  # noqa: F401

    def test_metrics_router_importable(self):
        from app.routers.metrics import router  # noqa: F401

    def test_admin_auth_importable(self):
        from app.core.admin_auth import verify_admin  # noqa: F401


class TestServiceImports(unittest.TestCase):
    """Services must be importable — scheduler registers jobs at startup."""

    def test_scheduler_importable(self):
        from app.services.scheduler import start_scheduler  # noqa: F401

    def test_telegram_router_importable(self):
        from app.services.telegram_router import route_message  # noqa: F401

    def test_coverage_metrics_importable(self):
        from app.services.coverage_metrics import (
            load_coverage_report,
            report_to_dict,
        )  # noqa: F401


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

    def test_coach_agent_intent_symbols(self):
        from app.agents.coach.agent import (  # noqa: F401
            _classify_intent,
            _FAST_EXACT,
            _STANDARD_KEYWORDS,
            _is_degenerate_response,
        )

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
        from app.agents.news.prompts import (
            build_on_demand_system_instruction,
        )  # noqa: F401
        from app.agents.news.prompts import build_on_demand_prompt  # noqa: F401

    def test_generate_on_demand_briefing_importable(self):
        from app.agents.news.agent import generate_on_demand_briefing  # noqa: F401

    def test_news_chat_handler_importable(self):
        from app.agents.news.telegram_handler import handle_news_chat  # noqa: F401

    def test_news_error_constants_importable(self):
        from app.agents.news.telegram_handler import (  # noqa: F401
            ERR_001,
            ERR_002,
            ERR_003,
            ERR_004,
            ERR_005,
            ERR_006,
            ERR_007,
        )

    def test_rate_limit_importable(self):
        from app.agents.news.telegram_handler import (
            _check_rate_limit,
            RATE_LIMIT,
            RATE_WINDOW,
        )  # noqa: F401

    def test_scheduler_late_trigger_importable(self):
        from app.services.scheduler import _is_late_trigger  # noqa: F401


class TestGarminCoachPlanningImports(unittest.TestCase):
    """Garmin + coach planning symbols — all new modules from feat/garmin-coach-planning."""

    def test_setup_validators_importable(self):
        from app.agents.coach.setup_validators import (  # noqa: F401
            validate_distance,
            validate_date,
            validate_time,
            validate_kmweek,
            validate_days,
            validate_rest_days,
            validate_hr,
        )

    def test_setup_flow_importable(self):
        from app.agents.coach.setup_flow import (  # noqa: F401
            start_setup,
            advance_setup,
            finalize_setup,
            is_setup_in_progress,
            cleanup_stale_setup_sessions,
        )

    def test_garmin_client_importable(self):
        from app.agents.coach.garmin_client import (
            GarminClient,
            get_garmin_client,
        )  # noqa: F401

    def test_schemas_importable(self):
        from app.agents.coach.schemas import WorkoutDay, WeeklyPlanResult  # noqa: F401

    def test_daily_suggestion_importable(self):
        from app.agents.coach.daily_suggestion import (  # noqa: F401
            compute_daily_suggestion,
            format_daily_suggestion_for_briefing,
        )

    def test_weekly_plan_generation_importable(self):
        from app.agents.coach.flows.weekly_plan_generation import (  # noqa: F401
            generate_weekly_plan,
            accept_weekly_plan,
            reject_weekly_plan,
        )

    def test_database_garmin_functions_importable(self):
        from app.core.database import (  # noqa: F401
            upsert_garmin_daily_metrics,
            get_garmin_daily_metrics,
            get_athlete_state,
            set_athlete_state,
            upsert_weekly_plan,
            get_pending_weekly_plan,
            update_weekly_plan_status,
            has_active_plan_this_week,
            get_setup_session,
            upsert_setup_session,
            complete_setup_session,
        )

    def test_agent_command_aliases_importable(self):
        from app.agents.coach.agent import (
            COMMAND_ALIASES,
            resolve_command,
        )  # noqa: F401

    def test_scheduler_new_tasks_importable(self):
        from app.services.scheduler import (  # noqa: F401
            task_garmin_sync,
            task_weekly_plan_generation,
            task_cleanup_stale_setup,
        )


class TestSDKContracts(unittest.TestCase):
    """SDK parameter unit contracts — catch wrong units before they hit production."""

    def test_gemini_http_options_timeout_unit_unchanged(self):
        """Regression: HttpOptions.timeout is MILLISECONDS not seconds.
        If SDK upgrades change the unit, this fails immediately.
        Incident: timeout=30 → 30ms → X-Server-Timeout:1 → 400 rejected (2026-04-21)."""
        import pathlib
        import sys as _sys

        types_path = None
        for p in _sys.path:
            candidate = pathlib.Path(p) / "google" / "genai" / "types.py"
            if candidate.exists():
                types_path = candidate
                break
        self.assertIsNotNone(types_path, "google-genai not installed")
        src = types_path.read_text(encoding="utf-8")
        self.assertIn(
            "milliseconds",
            src,
            "HttpOptions.timeout unit changed in SDK — audit all genai.Client() usages.",
        )


class TestLoggingConfImports(unittest.TestCase):
    """Logging infrastructure symbols — used at startup and by console router."""

    def test_get_module_logger_importable(self):
        from app.core.logging_conf import get_module_logger  # noqa: F401

    def test_apply_log_levels_importable(self):
        from app.core.logging_conf import apply_log_levels  # noqa: F401

    def test_get_effective_log_levels_importable(self):
        from app.core.logging_conf import get_effective_log_levels  # noqa: F401

    def test_known_domains_importable(self):
        from app.core.logging_conf import KNOWN_DOMAINS  # noqa: F401

        assert isinstance(KNOWN_DOMAINS, list)
        assert len(KNOWN_DOMAINS) > 0

    def test_console_save_log_levels_importable(self):
        from app.routers.console import console_save_log_levels  # noqa: F401


if __name__ == "__main__":
    unittest.main(verbosity=2)
