"""Parse coverage.xml and junit.xml reports into a structured metric report."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_COVERAGE_XML = _BASE_DIR / "reports" / "coverage.xml"
_JUNIT_XML = _BASE_DIR / "reports" / "junit.xml"


@dataclass(frozen=True)
class PackageCoverage:
    name: str
    line_rate: float
    lines_valid: int
    lines_covered: int


@dataclass(frozen=True)
class TestCounts:
    total: int
    passed: int
    failed: int
    skipped: int
    errors: int
    duration_seconds: float


@dataclass(frozen=True)
class CoverageReport:
    line_rate: float
    lines_valid: int
    lines_covered: int
    packages: list[PackageCoverage]
    test_counts: TestCounts | None
    coverage_timestamp: int | None  # epoch ms from coverage.xml
    reports_path: str


def _parse_coverage(path: Path) -> tuple[float, int, int, list[PackageCoverage]]:
    tree = ET.parse(str(path))  # nosec B314
    root = tree.getroot()
    line_rate = float(root.get("line-rate", 0))
    lines_valid = int(root.get("lines-valid", 0))
    lines_covered = int(root.get("lines-covered", 0))
    packages: list[PackageCoverage] = []
    for pkg in root.iter("package"):
        name = pkg.get("name", ".")
        pkg_rate = float(pkg.get("line-rate", 0))
        # Sum lines from child classes
        valid = sum(len(list(cls.find("lines") or [])) for cls in pkg.iter("class"))
        covered = sum(
            sum(1 for ln in (cls.find("lines") or []) if int(ln.get("hits", 0)) > 0)
            for cls in pkg.iter("class")
        )
        packages.append(
            PackageCoverage(
                name=name,
                line_rate=round(pkg_rate, 4),
                lines_valid=valid,
                lines_covered=covered,
            )
        )
    return line_rate, lines_valid, lines_covered, packages


def _parse_junit(path: Path) -> TestCounts | None:
    if not path.exists():
        return None
    tree = ET.parse(str(path))  # nosec B314
    root = tree.getroot()
    # JUnit XML may have <testsuites> → <testsuite> or just <testsuite>
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return None
    total = int(suite.get("tests", 0))
    failed = int(suite.get("failures", 0))
    errors = int(suite.get("errors", 0))
    skipped = int(suite.get("skipped", 0))
    passed = total - failed - errors - skipped
    duration = float(suite.get("time", 0))
    return TestCounts(
        total=total,
        passed=max(passed, 0),
        failed=failed,
        skipped=skipped,
        errors=errors,
        duration_seconds=round(duration, 2),
    )


def load_coverage_report(
    coverage_path: Path = _COVERAGE_XML,
    junit_path: Path = _JUNIT_XML,
) -> CoverageReport:
    """Parse coverage.xml + junit.xml and return a CoverageReport.

    Raises FileNotFoundError if coverage_path does not exist.
    """
    tree = ET.parse(str(coverage_path))  # nosec B314
    root = tree.getroot()
    ts = root.get("timestamp")
    line_rate, lines_valid, lines_covered, packages = _parse_coverage(coverage_path)
    test_counts = _parse_junit(junit_path)
    return CoverageReport(
        line_rate=round(line_rate, 4),
        lines_valid=lines_valid,
        lines_covered=lines_covered,
        packages=packages,
        test_counts=test_counts,
        coverage_timestamp=int(ts) if ts else None,
        reports_path=str(coverage_path),
    )


def report_to_dict(report: CoverageReport) -> dict:
    """Convert CoverageReport to a JSON-serializable dict."""
    pkg_list = [
        {
            "name": p.name,
            "line_rate_pct": round(p.line_rate * 100, 1),
            "lines_valid": p.lines_valid,
            "lines_covered": p.lines_covered,
        }
        for p in report.packages
    ]
    tc = report.test_counts
    return {
        "summary": {
            "line_rate_pct": round(report.line_rate * 100, 1),
            "lines_valid": report.lines_valid,
            "lines_covered": report.lines_covered,
        },
        "by_package": pkg_list,
        "test_counts": (
            {
                "total": tc.total,
                "passed": tc.passed,
                "failed": tc.failed,
                "skipped": tc.skipped,
                "errors": tc.errors,
                "duration_seconds": tc.duration_seconds,
            }
            if tc
            else None
        ),
        "coverage_timestamp_ms": report.coverage_timestamp,
    }
