"""
test_scheduler.py — Tests for app/services/scheduler.py
=========================================================
Covers scheduled task functions:
  - task_morning_briefing: no chat_id → early return; with chat_id → calls generate_morning_briefing
  - task_weekly_reflection: calls extract_implicit_memory (if chat_id) then generate_weekly_reflection
  - task_proactive_coach_check: ACWR>1.5 critical alert; ACWR>1.3 warning; taper reminder; no chat_id silent
  - task_log_audit: no user_id or "None" → skips; valid user_id → calls run_audit
  - setup_jobs: news jobs added only when news_agent.enabled=True; non-news jobs always added;
                cron time fallback on invalid format
"""
import unittest
from unittest.mock import patch, MagicMock, call


def _make_config(news_enabled=False, briefing_time="06:00", backup_time="02:00",
                 morning_news_time="07:00", afternoon_news_time="17:00",
                 watch_interval=30):
    return {
        "model_name": "models/gemini-2.0-flash",
        "race_date": "2026-06-01",
        "race_distance_km": 21.1,
        "scheduler": {
            "briefing_time": briefing_time,
            "backup_time": backup_time,
            "harvest_hours": "0,6,12,18",
            "harvest_minute": "15",
        },
        "news_agent": {
            "enabled": news_enabled,
            "morning_time": morning_news_time,
            "afternoon_time": afternoon_news_time,
            "watch_interval_minutes": watch_interval,
        },
    }


# ==========================================
# task_morning_briefing
# ==========================================
class TestTaskMorningBriefing(unittest.TestCase):

    def test_no_chat_id_skips_briefing(self):
        with patch("app.services.scheduler.get_primary_user_id", return_value=None), \
             patch("app.services.scheduler.load_config") as mock_cfg, \
             patch("app.services.scheduler.generate_morning_briefing") as mock_gen:
            from app.services.scheduler import task_morning_briefing
            task_morning_briefing()
            mock_gen.assert_not_called()
            mock_cfg.assert_not_called()

    def test_with_chat_id_calls_generate_morning_briefing(self):
        cfg = _make_config()
        with patch("app.services.scheduler.get_primary_user_id", return_value="123456"), \
             patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.get_today_weather", return_value="Sunny 30°C"), \
             patch("app.services.scheduler.generate_morning_briefing") as mock_gen:
            from app.services.scheduler import task_morning_briefing
            task_morning_briefing()
            mock_gen.assert_called_once_with(cfg, "Sunny 30°C")

    def test_with_chat_id_calls_get_today_weather(self):
        cfg = _make_config()
        with patch("app.services.scheduler.get_primary_user_id", return_value="123456"), \
             patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.get_today_weather") as mock_weather, \
             patch("app.services.scheduler.generate_morning_briefing"):
            from app.services.scheduler import task_morning_briefing
            task_morning_briefing()
            mock_weather.assert_called_once()


# ==========================================
# task_weekly_reflection
# ==========================================
class TestTaskWeeklyReflection(unittest.TestCase):

    def test_with_chat_id_calls_extract_implicit_memory(self):
        cfg = _make_config()
        with patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.get_primary_user_id", return_value="123456"), \
             patch("app.services.scheduler.extract_implicit_memory") as mock_extract, \
             patch("app.services.scheduler.generate_weekly_reflection"):
            from app.services.scheduler import task_weekly_reflection
            task_weekly_reflection()
            mock_extract.assert_called_once_with("123456")

    def test_always_calls_generate_weekly_reflection(self):
        cfg = _make_config()
        with patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.get_primary_user_id", return_value="123456"), \
             patch("app.services.scheduler.extract_implicit_memory"), \
             patch("app.services.scheduler.generate_weekly_reflection") as mock_gen:
            from app.services.scheduler import task_weekly_reflection
            task_weekly_reflection()
            mock_gen.assert_called_once_with(cfg)

    def test_no_chat_id_skips_extract_but_still_reflects(self):
        cfg = _make_config()
        with patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.get_primary_user_id", return_value=None), \
             patch("app.services.scheduler.extract_implicit_memory") as mock_extract, \
             patch("app.services.scheduler.generate_weekly_reflection") as mock_gen:
            from app.services.scheduler import task_weekly_reflection
            task_weekly_reflection()
            mock_extract.assert_not_called()
            mock_gen.assert_called_once_with(cfg)


# ==========================================
# task_proactive_coach_check
# ==========================================
class TestTaskProactiveCoachCheck(unittest.TestCase):

    def _run(self, chat_id, acwr, weeks_left=99):
        cfg = _make_config()
        loads = {"acwr": acwr}
        phase_info = {"weeks_left": weeks_left, "phase": "Build"}
        with patch("app.services.scheduler.get_primary_user_id", return_value=chat_id), \
             patch("app.services.scheduler.load_config", return_value=cfg), \
             patch("app.services.scheduler.get_training_loads", return_value=loads), \
             patch("app.services.scheduler.calculate_training_phase", return_value=phase_info), \
             patch("app.services.scheduler.send_telegram_msg") as mock_send:
            from app.services.scheduler import task_proactive_coach_check
            task_proactive_coach_check()
            return mock_send

    def test_no_chat_id_sends_no_alerts(self):
        mock_send = self._run(chat_id=None, acwr=2.0)
        mock_send.assert_not_called()

    def test_acwr_above_1_5_sends_critical_alert(self):
        mock_send = self._run(chat_id="123456", acwr=1.6)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][1]
        self.assertIn("CẢNH BÁO", msg)

    def test_acwr_above_1_3_sends_warning_alert(self):
        mock_send = self._run(chat_id="123456", acwr=1.4)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][1]
        self.assertIn("thận trọng", msg)

    def test_acwr_below_1_3_sends_no_load_alert(self):
        mock_send = self._run(chat_id="123456", acwr=1.1)
        mock_send.assert_not_called()

    def test_acwr_exactly_1_3_sends_no_alert(self):
        # Boundary: >1.3 triggers warning, =1.3 does not
        mock_send = self._run(chat_id="123456", acwr=1.3)
        mock_send.assert_not_called()

    def test_weeks_left_1_sends_taper_reminder(self):
        mock_send = self._run(chat_id="123456", acwr=1.0, weeks_left=1)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][1]
        self.assertIn("Taper", msg)
        self.assertIn("Race Week", msg)

    def test_weeks_left_2_sends_taper_reminder(self):
        mock_send = self._run(chat_id="123456", acwr=1.0, weeks_left=2)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][1]
        self.assertIn("Taper", msg)

    def test_weeks_left_3_sends_taper_reminder(self):
        mock_send = self._run(chat_id="123456", acwr=1.0, weeks_left=3)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][1]
        self.assertIn("Taper", msg)

    def test_weeks_left_4_no_taper_reminder(self):
        mock_send = self._run(chat_id="123456", acwr=1.0, weeks_left=4)
        mock_send.assert_not_called()

    def test_high_acwr_and_taper_sends_two_alerts(self):
        mock_send = self._run(chat_id="123456", acwr=1.6, weeks_left=1)
        self.assertEqual(mock_send.call_count, 2)

    def test_alert_sent_to_correct_chat_id(self):
        mock_send = self._run(chat_id="999888", acwr=1.6)
        args = mock_send.call_args[0]
        self.assertEqual(args[0], "999888")


# ==========================================
# task_log_audit
# ==========================================
class TestTaskLogAudit(unittest.TestCase):

    def test_none_user_id_skips_audit(self):
        with patch("app.services.scheduler.get_primary_user_id", return_value=None), \
             patch("app.services.scheduler.run_audit") as mock_audit:
            from app.services.scheduler import task_log_audit
            task_log_audit()
            mock_audit.assert_not_called()

    def test_string_none_user_id_skips_audit(self):
        # get_primary_user_id() returns None → str(None) == "None" → skip
        with patch("app.services.scheduler.get_primary_user_id", return_value=None), \
             patch("app.services.scheduler.run_audit") as mock_audit:
            from app.services.scheduler import task_log_audit
            task_log_audit()
            mock_audit.assert_not_called()

    def test_valid_user_id_calls_run_audit(self):
        with patch("app.services.scheduler.get_primary_user_id", return_value="123456"), \
             patch("app.services.scheduler.run_audit", return_value=3) as mock_audit:
            from app.services.scheduler import task_log_audit
            task_log_audit()
            mock_audit.assert_called_once_with("123456")

    def test_run_audit_returns_count(self):
        with patch("app.services.scheduler.get_primary_user_id", return_value="123456"), \
             patch("app.services.scheduler.run_audit", return_value=5) as mock_audit:
            from app.services.scheduler import task_log_audit
            task_log_audit()  # should not raise; count is logged, not returned
            mock_audit.assert_called_once()

    def test_zero_audit_entries_does_not_crash(self):
        with patch("app.services.scheduler.get_primary_user_id", return_value="123456"), \
             patch("app.services.scheduler.run_audit", return_value=0):
            from app.services.scheduler import task_log_audit
            task_log_audit()  # should not raise


# ==========================================
# setup_jobs
# ==========================================
class TestSetupJobs(unittest.TestCase):

    def _run_setup(self, config, mock_scheduler=None):
        if mock_scheduler is None:
            mock_scheduler = MagicMock()
        with patch("app.services.scheduler.load_config", return_value=config), \
             patch("app.services.scheduler.scheduler", mock_scheduler):
            from app.services.scheduler import setup_jobs
            setup_jobs()
        return mock_scheduler

    def test_news_disabled_does_not_add_news_jobs(self):
        cfg = _make_config(news_enabled=False)
        mock_sched = self._run_setup(cfg)
        job_ids = [call[1]["id"] for call in mock_sched.add_job.call_args_list
                   if "id" in call[1]]
        self.assertNotIn("news_morning", job_ids)
        self.assertNotIn("news_afternoon", job_ids)
        self.assertNotIn("news_watch", job_ids)

    def test_news_enabled_adds_news_jobs(self):
        cfg = _make_config(news_enabled=True)
        mock_sched = self._run_setup(cfg)
        job_ids = [call[1]["id"] for call in mock_sched.add_job.call_args_list
                   if "id" in call[1]]
        self.assertIn("news_morning", job_ids)
        self.assertIn("news_afternoon", job_ids)
        self.assertIn("news_watch", job_ids)

    def test_core_jobs_always_added(self):
        cfg = _make_config(news_enabled=False)
        mock_sched = self._run_setup(cfg)
        job_ids = [call[1]["id"] for call in mock_sched.add_job.call_args_list
                   if "id" in call[1]]
        for expected_id in ("briefing", "backup", "harvest", "weekly_reflection",
                            "proactive_check", "log_audit"):
            self.assertIn(expected_id, job_ids)

    def test_invalid_briefing_time_falls_back_to_default(self):
        cfg = _make_config(briefing_time="not-a-time")
        # Should not raise — fallback is 6:00
        mock_sched = self._run_setup(cfg)
        mock_sched.add_job.assert_called()

    def test_invalid_backup_time_falls_back_to_default(self):
        cfg = _make_config(backup_time="bad")
        mock_sched = self._run_setup(cfg)
        mock_sched.add_job.assert_called()

    def test_total_jobs_with_news_enabled(self):
        cfg = _make_config(news_enabled=True)
        mock_sched = self._run_setup(cfg)
        # 6 core + 3 news = 9
        self.assertEqual(mock_sched.add_job.call_count, 9)

    def test_total_jobs_without_news(self):
        cfg = _make_config(news_enabled=False)
        mock_sched = self._run_setup(cfg)
        # 6 core only
        self.assertEqual(mock_sched.add_job.call_count, 6)


if __name__ == "__main__":
    unittest.main()
