"""Parse coverage.xml and junit.xml reports into a structured metric report."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_COVERAGE_XML = _BASE_DIR / "reports" / "coverage.xml"
_JUNIT_XML = _BASE_DIR / "reports" / "junit.xml"

_LEVEL_MAP: dict[str, str] = {
    "tests.test_smoke": "smoke",
    "tests.test_sanity_flows": "sanity",
    "tests.test_e2e_local": "e2e",
    "tests.test_e2e_coach_commands": "e2e",
    "tests.test_e2e_scheduler_tasks": "e2e",
    "tests.test_e2e_integration_edge_cases": "e2e",
    "tests.test_e2e_console_ui": "e2e",
    "tests.test_e2e_news_flows": "e2e",
}

_LEVEL_ORDER = ["smoke", "sanity", "e2e", "unit"]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

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
class TestCase:
    class_name: str       # "TestCoreImports"
    name: str             # raw pytest name, e.g. "test_config_importable"
    label: str            # humanized, e.g. "Config importable"
    status: str           # "passed" | "failed" | "skipped" | "error"
    duration_seconds: float
    failure_message: str | None


@dataclass
class _ClassBucket:
    """Mutable accumulator, converted to dict at serialization time."""
    name: str
    cases: list[TestCase] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for c in self.cases if c.status in ("failed", "error"))

    @property
    def skipped(self) -> int:
        return sum(1 for c in self.cases if c.status == "skipped")


@dataclass(frozen=True)
class TestLevelSummary:
    level: str
    total: int
    passed: int
    failed: int
    skipped: int
    duration_seconds: float


@dataclass(frozen=True)
class CoverageReport:
    line_rate: float
    lines_valid: int
    lines_covered: int
    packages: list[PackageCoverage]
    test_counts: TestCounts | None
    by_level: list[TestLevelSummary]
    # per-level drilldown: keyed by level name
    level_drilldown: dict[str, object]
    coverage_timestamp: int | None
    junit_timestamp: str | None
    reports_path: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _humanize(name: str) -> str:
    """'test_config_importable' → 'Config importable'."""
    return name.removeprefix("test_").replace("_", " ").capitalize()


def _classify_level(classname: str) -> str:
    for prefix, level in _LEVEL_MAP.items():
        if classname.startswith(prefix):
            return level
    return "unit"


def _short_class(classname: str) -> str:
    """'tests.test_smoke.TestCoreImports' → 'TestCoreImports'."""
    return classname.rsplit(".", 1)[-1]


def _module_from_classname(classname: str) -> str:
    """'tests.test_notification.TestSplit' → 'test_notification'."""
    parts = classname.split(".")
    return parts[1] if len(parts) >= 2 else classname


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

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


def _parse_junit(
    path: Path,
) -> tuple[TestCounts | None, list[TestLevelSummary], dict[str, object], str | None]:
    """Parse junit.xml.

    Returns (TestCounts, by_level, level_drilldown, timestamp_iso).

    level_drilldown structure:
      smoke/sanity/e2e → {"classes": [{name, total, passed, failed, skipped, cases: [...]}]}
      unit             → {"modules": [{name, total, passed, failed, skipped}]}
    """
    if not path.exists():
        return None, [], {}, None

    tree = ET.parse(str(path))  # nosec B314
    root = tree.getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return None, [], {}, None

    total = int(suite.get("tests", 0))
    failed = int(suite.get("failures", 0))
    errors = int(suite.get("errors", 0))
    skipped = int(suite.get("skipped", 0))
    passed = max(total - failed - errors - skipped, 0)
    duration = float(suite.get("time", 0))
    timestamp = suite.get("timestamp")

    counts = TestCounts(
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=errors,
        duration_seconds=round(duration, 2),
    )

    # --- Accumulate per-level data ---
    # For smoke/sanity/e2e: class_buckets[level][classname] = _ClassBucket
    class_buckets: dict[str, dict[str, _ClassBucket]] = {
        lv: {} for lv in _LEVEL_ORDER if lv != "unit"
    }
    # For unit: module_buckets[module_name] = {total, passed, failed, skipped}
    unit_modules: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "passed": 0, "failed": 0, "skipped": 0}
    )
    level_counters: dict[str, dict] = {}

    for tc in suite.iter("testcase"):
        classname = tc.get("classname", "")
        name = tc.get("name", "")
        dur = round(float(tc.get("time", 0)), 4)
        level = _classify_level(classname)

        if tc.find("skipped") is not None:
            status = "skipped"
            msg = None
        elif tc.find("failure") is not None:
            status = "failed"
            raw = tc.find("failure").get("message", "") or ""
            msg = raw[:200].strip() or None
        elif tc.find("error") is not None:
            status = "error"
            raw = tc.find("error").get("message", "") or ""
            msg = raw[:200].strip() or None
        else:
            status = "passed"
            msg = None

        # Level counters (for by_level summary)
        if level not in level_counters:
            level_counters[level] = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "duration": 0.0}
        b = level_counters[level]
        b["total"] += 1
        b["duration"] += float(tc.get("time", 0))
        b["passed" if status == "passed" else "failed" if status in ("failed", "error") else "skipped"] += 1

        if level == "unit":
            mod = _module_from_classname(classname)
            um = unit_modules[mod]
            um["total"] += 1
            if status == "passed":
                um["passed"] += 1
            elif status in ("failed", "error"):
                um["failed"] += 1
            else:
                um["skipped"] += 1
        else:
            short_class = _short_class(classname)
            if short_class not in class_buckets[level]:
                class_buckets[level][short_class] = _ClassBucket(name=short_class)
            class_buckets[level][short_class].cases.append(
                TestCase(
                    class_name=short_class,
                    name=name,
                    label=_humanize(name),
                    status=status,
                    duration_seconds=dur,
                    failure_message=msg,
                )
            )

    # Build by_level
    by_level = [
        TestLevelSummary(
            level=lv,
            total=level_counters[lv]["total"],
            passed=level_counters[lv]["passed"],
            failed=level_counters[lv]["failed"],
            skipped=level_counters[lv]["skipped"],
            duration_seconds=round(level_counters[lv]["duration"], 2),
        )
        for lv in _LEVEL_ORDER
        if lv in level_counters
    ]

    # Build drilldown
    level_drilldown: dict[str, object] = {}
    for lv in ("smoke", "sanity", "e2e"):
        if lv not in class_buckets:
            continue
        classes_out = []
        for cls_name, bucket in class_buckets[lv].items():
            classes_out.append({
                "name": cls_name,
                "total": bucket.total,
                "passed": bucket.passed,
                "failed": bucket.failed,
                "skipped": bucket.skipped,
                "cases": [
                    {
                        "name": c.name,
                        "label": c.label,
                        "status": c.status,
                        "duration": c.duration_seconds,
                        "message": c.failure_message,
                    }
                    for c in bucket.cases
                ],
            })
        level_drilldown[lv] = {"classes": classes_out}

    # Unit drilldown: sorted by module name
    level_drilldown["unit"] = {
        "modules": [
            {
                "name": mod,
                "total": data["total"],
                "passed": data["passed"],
                "failed": data["failed"],
                "skipped": data["skipped"],
            }
            for mod, data in sorted(unit_modules.items(), key=lambda x: -x[1]["total"])
        ]
    }

    return counts, by_level, level_drilldown, timestamp


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
    test_counts, by_level, level_drilldown, junit_ts = _parse_junit(junit_path)
    return CoverageReport(
        line_rate=round(line_rate, 4),
        lines_valid=lines_valid,
        lines_covered=lines_covered,
        packages=packages,
        test_counts=test_counts,
        by_level=by_level,
        level_drilldown=level_drilldown,
        coverage_timestamp=int(ts) if ts else None,
        junit_timestamp=junit_ts,
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
    level_list = [
        {
            "level": lv.level,
            "total": lv.total,
            "passed": lv.passed,
            "failed": lv.failed,
            "skipped": lv.skipped,
            "duration_seconds": lv.duration_seconds,
            "drilldown": report.level_drilldown.get(lv.level, {}),
        }
        for lv in report.by_level
    ]
    return {
        "summary": {
            "line_rate_pct": round(report.line_rate * 100, 1),
            "lines_valid": report.lines_valid,
            "lines_covered": report.lines_covered,
        },
        "by_package": pkg_list,
        "by_level": level_list,
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
        "junit_timestamp": report.junit_timestamp,
    }
