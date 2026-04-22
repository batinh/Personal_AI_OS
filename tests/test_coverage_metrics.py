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
        with patch("app.routers.metrics._COVERAGE_XML", cov), \
             patch("app.routers.metrics._JUNIT_XML", junit):
            if auth:
                with patch.dict(os.environ, {
                    "ADMIN_USERNAME": self._auth[0],
                    "ADMIN_PASSWORD": self._auth[1],
                }):
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
