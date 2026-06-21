"""Tests for app/services/coverage_metrics.py and GET /admin/metrics/coverage."""

import os
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Fixtures — minimal valid XML payloads
# ---------------------------------------------------------------------------

_COVERAGE_XML = textwrap.dedent("""\
    <?xml version="1.0" ?>
    <coverage version="7.0" timestamp="1776471036008"
              lines-valid="100" lines-covered="80" line-rate="0.8"
              branches-covered="0" branches-valid="0" branch-rate="0" complexity="0">
      <sources><source>/app</source></sources>
      <packages>
        <package name="core" line-rate="0.9" branch-rate="0" complexity="0">
          <classes>
            <class name="config.py" filename="core/config.py" line-rate="0.9"
                   branch-rate="0" complexity="0">
              <methods/>
              <lines>
                <line number="1" hits="1"/>
                <line number="2" hits="1"/>
                <line number="3" hits="0"/>
              </lines>
            </class>
          </classes>
        </package>
        <package name="routers" line-rate="0.5" branch-rate="0" complexity="0">
          <classes>
            <class name="admin.py" filename="routers/admin.py" line-rate="0.5"
                   branch-rate="0" complexity="0">
              <methods/>
              <lines>
                <line number="1" hits="1"/>
                <line number="2" hits="0"/>
              </lines>
            </class>
          </classes>
        </package>
      </packages>
    </coverage>
""")

_JUNIT_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <testsuites name="pytest tests">
      <testsuite name="pytest" errors="1" failures="2" skipped="3"
                 tests="50" time="12.5" timestamp="2026-04-22T00:00:00">
      </testsuite>
    </testsuites>
""")

_MALFORMED_XML = "<<not valid xml>>"


def _write_tmp(tmp_dir: Path, filename: str, content: str) -> Path:
    p = tmp_dir / filename
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# Unit tests: coverage_metrics service
# ---------------------------------------------------------------------------


class TestLoadCoverageReport(unittest.TestCase):

    def setUp(self):
        import tempfile

        self._tmpdir = Path(tempfile.mkdtemp())
        self._cov = _write_tmp(self._tmpdir, "coverage.xml", _COVERAGE_XML)
        self._junit = _write_tmp(self._tmpdir, "junit.xml", _JUNIT_XML)

    def test_returns_coverage_report(self):
        from app.services.coverage_metrics import load_coverage_report

        report = load_coverage_report(self._cov, self._junit)
        self.assertAlmostEqual(report.line_rate, 0.8)
        self.assertEqual(report.lines_valid, 100)
        self.assertEqual(report.lines_covered, 80)

    def test_packages_parsed(self):
        from app.services.coverage_metrics import load_coverage_report

        report = load_coverage_report(self._cov, self._junit)
        names = {p.name for p in report.packages}
        self.assertIn("core", names)
        self.assertIn("routers", names)

    def test_package_line_rate(self):
        from app.services.coverage_metrics import load_coverage_report

        report = load_coverage_report(self._cov, self._junit)
        core = next(p for p in report.packages if p.name == "core")
        self.assertAlmostEqual(core.line_rate, 0.9)

    def test_coverage_timestamp_parsed(self):
        from app.services.coverage_metrics import load_coverage_report

        report = load_coverage_report(self._cov, self._junit)
        self.assertEqual(report.coverage_timestamp, 1776471036008)

    def test_test_counts_parsed(self):
        from app.services.coverage_metrics import load_coverage_report

        report = load_coverage_report(self._cov, self._junit)
        tc = report.test_counts
        self.assertIsNotNone(tc)
        self.assertEqual(tc.total, 50)
        self.assertEqual(tc.failed, 2)
        self.assertEqual(tc.errors, 1)
        self.assertEqual(tc.skipped, 3)
        self.assertEqual(tc.passed, 44)  # 50 - 2 - 1 - 3
        self.assertAlmostEqual(tc.duration_seconds, 12.5)

    def test_raises_when_coverage_file_missing(self):
        from app.services.coverage_metrics import load_coverage_report

        with self.assertRaises(FileNotFoundError):
            load_coverage_report(self._tmpdir / "nonexistent.xml", self._junit)

    def test_raises_when_coverage_file_malformed(self):
        from app.services.coverage_metrics import load_coverage_report
        import xml.etree.ElementTree as ET

        bad = _write_tmp(self._tmpdir, "bad.xml", _MALFORMED_XML)
        with self.assertRaises(ET.ParseError):
            load_coverage_report(bad, self._junit)

    def test_test_counts_none_when_junit_missing(self):
        from app.services.coverage_metrics import load_coverage_report

        report = load_coverage_report(self._cov, self._tmpdir / "missing.xml")
        self.assertIsNone(report.test_counts)


class TestReportToDict(unittest.TestCase):

    def setUp(self):
        import tempfile

        self._tmpdir = Path(tempfile.mkdtemp())
        self._cov = _write_tmp(self._tmpdir, "coverage.xml", _COVERAGE_XML)
        self._junit = _write_tmp(self._tmpdir, "junit.xml", _JUNIT_XML)

    def _get_dict(self):
        from app.services.coverage_metrics import load_coverage_report, report_to_dict

        return report_to_dict(load_coverage_report(self._cov, self._junit))

    def test_summary_keys_present(self):
        d = self._get_dict()
        self.assertIn("line_rate_pct", d["summary"])
        self.assertIn("lines_valid", d["summary"])
        self.assertIn("lines_covered", d["summary"])

    def test_summary_line_rate_pct(self):
        d = self._get_dict()
        self.assertAlmostEqual(d["summary"]["line_rate_pct"], 80.0)

    def test_by_package_is_list(self):
        d = self._get_dict()
        self.assertIsInstance(d["by_package"], list)
        self.assertGreater(len(d["by_package"]), 0)

    def test_by_package_entry_shape(self):
        d = self._get_dict()
        pkg = d["by_package"][0]
        self.assertIn("name", pkg)
        self.assertIn("line_rate_pct", pkg)
        self.assertIn("lines_valid", pkg)
        self.assertIn("lines_covered", pkg)

    def test_test_counts_present(self):
        d = self._get_dict()
        tc = d["test_counts"]
        self.assertIsNotNone(tc)
        self.assertEqual(tc["total"], 50)

    def test_output_json_serializable(self):
        import json

        d = self._get_dict()
        json.dumps(d)  # must not raise


# ---------------------------------------------------------------------------
# Integration tests: /admin/metrics/coverage endpoint
# ---------------------------------------------------------------------------


class TestCoverageEndpoint(unittest.TestCase):

    def setUp(self):
        import tempfile
        from fastapi.testclient import TestClient
        from app.main import app

        self._client = TestClient(app, raise_server_exceptions=False)
        self._tmpdir = Path(tempfile.mkdtemp())
        self._cov = _write_tmp(self._tmpdir, "coverage.xml", _COVERAGE_XML)
        self._junit = _write_tmp(self._tmpdir, "junit.xml", _JUNIT_XML)
        self._auth = ("admin", "adminpass123")

    def _get(self, *, auth=True, cov_path=None, junit_path=None):
        cov = cov_path or self._cov
        junit = junit_path or self._junit
        with patch("app.routers.metrics._COVERAGE_XML", cov), patch(
            "app.routers.metrics._JUNIT_XML", junit
        ):
            if auth:
                with patch.dict(
                    os.environ,
                    {
                        "ADMIN_USERNAME": self._auth[0],
                        "ADMIN_PASSWORD": self._auth[1],
                    },
                ):
                    return self._client.get(
                        "/admin/metrics/coverage",
                        auth=self._auth,
                    )
            else:
                return self._client.get("/admin/metrics/coverage")

    def test_returns_200_with_auth(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)

    def test_returns_401_without_auth(self):
        resp = self._get(auth=False)
        self.assertEqual(resp.status_code, 401)

    def test_response_has_summary(self):
        resp = self._get()
        body = resp.json()
        self.assertIn("summary", body)
        self.assertIn("line_rate_pct", body["summary"])

    def test_response_has_by_package(self):
        resp = self._get()
        body = resp.json()
        self.assertIn("by_package", body)
        self.assertIsInstance(body["by_package"], list)

    def test_response_has_test_counts(self):
        resp = self._get()
        body = resp.json()
        self.assertIn("test_counts", body)
        self.assertIsNotNone(body["test_counts"])

    def test_graceful_404_when_coverage_file_missing(self):
        resp = self._get(cov_path=self._tmpdir / "missing.xml")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("detail", resp.json())

    def test_graceful_500_when_coverage_file_malformed(self):
        bad = _write_tmp(self._tmpdir, "bad.xml", _MALFORMED_XML)
        resp = self._get(cov_path=bad)
        self.assertEqual(resp.status_code, 500)
        self.assertIn("detail", resp.json())


# ---------------------------------------------------------------------------
# Unit tests: internal helper functions
# ---------------------------------------------------------------------------

_JUNIT_WITH_CASES = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <testsuites>
      <testsuite name="pytest" errors="0" failures="1" skipped="1"
                 tests="5" time="2.0" timestamp="2026-01-01T00:00:00">
        <testcase classname="tests.test_smoke.TestCoreImports" name="test_config_importable" time="0.01"/>
        <testcase classname="tests.test_smoke.TestCoreImports" name="test_db_importable" time="0.01">
          <failure message="AssertionError">something failed</failure>
        </testcase>
        <testcase classname="tests.test_sanity_flows.TestMorningBriefing" name="test_skipped" time="0.0">
          <skipped/>
        </testcase>
        <testcase classname="tests.test_e2e_local.TestHealth" name="test_health" time="0.5"/>
        <testcase classname="tests.test_tools.TestTools" name="test_get_total_run_stats" time="0.1">
          <error message="RuntimeError">some error</error>
        </testcase>
      </testsuite>
    </testsuites>
""")

_JUNIT_NO_SUITE = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <testsuites/>
""")


class TestHelperFunctions(unittest.TestCase):
    def test_humanize(self):
        from app.services.coverage_metrics import _humanize

        assert _humanize("test_config_importable") == "Config importable"

    def test_classify_level_smoke(self):
        from app.services.coverage_metrics import _classify_level

        assert _classify_level("tests.test_smoke.X") == "smoke"

    def test_classify_level_sanity(self):
        from app.services.coverage_metrics import _classify_level

        assert _classify_level("tests.test_sanity_flows.X") == "sanity"

    def test_classify_level_e2e(self):
        from app.services.coverage_metrics import _classify_level

        assert _classify_level("tests.test_e2e_local.X") == "e2e"

    def test_classify_level_unit(self):
        from app.services.coverage_metrics import _classify_level

        assert _classify_level("tests.test_tools.X") == "unit"

    def test_short_class(self):
        from app.services.coverage_metrics import _short_class

        assert _short_class("tests.test_smoke.TestCoreImports") == "TestCoreImports"

    def test_module_from_classname(self):
        from app.services.coverage_metrics import _module_from_classname

        assert _module_from_classname("tests.test_tools.TestTools") == "test_tools"

    def test_module_from_classname_no_dot(self):
        from app.services.coverage_metrics import _module_from_classname

        assert _module_from_classname("TestTools") == "TestTools"


class TestClassBucket(unittest.TestCase):
    def setUp(self):
        from app.services.coverage_metrics import _ClassBucket, TestCase

        self.bucket = _ClassBucket(name="TestSmoke")
        self.bucket.cases = [
            TestCase("TestSmoke", "test_a", "A", "passed", 0.1, None),
            TestCase("TestSmoke", "test_b", "B", "failed", 0.2, "oops"),
            TestCase("TestSmoke", "test_c", "C", "error", 0.0, "err"),
            TestCase("TestSmoke", "test_d", "D", "skipped", 0.0, None),
        ]

    def test_total(self):
        assert self.bucket.total == 4

    def test_passed(self):
        assert self.bucket.passed == 1

    def test_failed(self):
        assert self.bucket.failed == 2  # "failed" + "error"

    def test_skipped(self):
        assert self.bucket.skipped == 1


class TestJUnitParsing(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmpdir = Path(tempfile.mkdtemp())
        self._cov = _write_tmp(self._tmpdir, "coverage.xml", _COVERAGE_XML)
        self._junit_cases = _write_tmp(
            self._tmpdir, "junit_cases.xml", _JUNIT_WITH_CASES
        )
        self._junit_no_suite = _write_tmp(
            self._tmpdir, "junit_no_suite.xml", _JUNIT_NO_SUITE
        )

    def test_by_level_populated(self):
        from app.services.coverage_metrics import load_coverage_report

        report = load_coverage_report(self._cov, self._junit_cases)
        levels = {lv.level for lv in report.by_level}
        assert "smoke" in levels
        assert "e2e" in levels
        assert "sanity" in levels

    def test_failed_status_counted(self):
        from app.services.coverage_metrics import load_coverage_report

        report = load_coverage_report(self._cov, self._junit_cases)
        smoke = next(lv for lv in report.by_level if lv.level == "smoke")
        assert smoke.failed >= 1

    def test_skipped_status_counted(self):
        from app.services.coverage_metrics import load_coverage_report

        report = load_coverage_report(self._cov, self._junit_cases)
        sanity = next(lv for lv in report.by_level if lv.level == "sanity")
        assert sanity.skipped >= 1

    def test_error_counted_as_failed(self):
        from app.services.coverage_metrics import load_coverage_report

        report = load_coverage_report(self._cov, self._junit_cases)
        unit = next((lv for lv in report.by_level if lv.level == "unit"), None)
        assert unit is not None
        assert unit.failed >= 1

    def test_no_suite_element_returns_none_counts(self):
        from app.services.coverage_metrics import load_coverage_report

        report = load_coverage_report(self._cov, self._junit_no_suite)
        assert report.test_counts is None

    def test_level_drilldown_has_smoke(self):
        from app.services.coverage_metrics import load_coverage_report

        report = load_coverage_report(self._cov, self._junit_cases)
        assert "smoke" in report.level_drilldown or "sanity" in report.level_drilldown
