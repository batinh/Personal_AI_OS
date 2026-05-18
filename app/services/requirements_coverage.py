"""Parse requirements.yaml and match to test execution results from junit.xml.

This service:
1. Loads tests/requirements.yaml with requirement IDs, descriptions, priority, module
2. Extracts test references from covered_by list (format: test_file.py::ClassName::method)
3. Matches to JUnit XML test results for status and duration
4. Extracts docstrings from test source files using AST
5. Computes coverage summary by requirement, module, and test level
"""

from __future__ import annotations

import ast
import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_REQUIREMENTS_YAML = _BASE_DIR / "tests" / "requirements.yaml"
_JUNIT_XML = _BASE_DIR / "reports" / "junit.xml"
_TESTS_DIR = _BASE_DIR / "tests"

# Module-level cache for requirement matrix and parsed ASTs
_requirements_cache: Optional[RequirementsMatrix] = None
_requirements_cache_mtime: float = 0.0
_ast_cache: dict[Path, Optional[ast.Module]] = {}


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestCaseRef:
    """Reference to a test case with execution details."""

    file: str  # e.g. "test_sanity_flows.py"
    class_name: str  # e.g. "TestMorningBriefingGuard1"
    method: str  # e.g. "test_message_contains_setup_keyword"
    docstring: str  # extracted from AST, empty string if not found
    status: str  # "passed" | "failed" | "skipped" | "missing" (not in JUnit)
    duration: float  # from JUnit, 0.0 if not found


@dataclass
class RequirementCoverage:
    """Coverage status for one requirement."""

    req_id: str
    description: str
    priority: str  # CRITICAL | HIGH | MEDIUM | LOW
    module: str  # coach | news | webhooks | system | console | notifications
    coverage_status: str  # covered | partial | missing
    test_level: str  # e2e | sanity | unit | mixed | none
    test_cases: list[TestCaseRef] = field(default_factory=list)


@dataclass
class RequirementsMatrix:
    """Aggregated requirements coverage analysis."""

    requirements: list[RequirementCoverage]
    summary: dict[str, int]  # {total, covered, partial, missing, by_priority: {...}}
    by_module: dict[str, dict]  # {module_name: {total, covered, partial, missing}}
    generated_at: str  # ISO timestamp


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_requirements_matrix(
    yaml_path: Optional[Path] = None,
    junit_path: Optional[Path] = None,
    tests_dir: Optional[Path] = None,
) -> RequirementsMatrix:
    """Load and match requirements to test results.

    Args:
        yaml_path: Path to requirements.yaml (defaults to tests/requirements.yaml)
        junit_path: Path to junit.xml (defaults to reports/junit.xml)
        tests_dir: Path to tests directory (defaults to tests/)

    Returns:
        RequirementsMatrix with matched test cases and summary statistics
    """
    global _requirements_cache, _requirements_cache_mtime

    yaml_path = yaml_path or _REQUIREMENTS_YAML
    junit_path = junit_path or _JUNIT_XML
    tests_dir = tests_dir or _TESTS_DIR

    # Check cache
    try:
        current_mtime = os.path.getmtime(yaml_path)
        if (
            _requirements_cache is not None
            and current_mtime == _requirements_cache_mtime
        ):
            return _requirements_cache
    except OSError:
        pass

    # Load fresh
    req_dict = _load_requirements_yaml(yaml_path)
    junit_results = _load_junit_results(junit_path)

    requirements: list[RequirementCoverage] = []
    for req_id, req_data in req_dict.items():
        description = req_data.get("description", "")
        priority = req_data.get("priority", "MEDIUM")
        module = req_data.get("module", "system")
        coverage_status = req_data.get("coverage_status", "missing")
        covered_by = req_data.get("covered_by", [])

        test_cases: list[TestCaseRef] = []
        for ref in covered_by:
            tc = _resolve_test_case(ref, junit_results, tests_dir)
            if tc is not None:
                test_cases.append(tc)

        test_level = _compute_test_level([tc.file for tc in test_cases])

        req = RequirementCoverage(
            req_id=req_id,
            description=description,
            priority=priority,
            module=module,
            coverage_status=coverage_status,
            test_level=test_level,
            test_cases=test_cases,
        )
        requirements.append(req)

    # Compute summary
    summary = _compute_summary(requirements)
    by_module = _compute_by_module(requirements)

    matrix = RequirementsMatrix(
        requirements=requirements,
        summary=summary,
        by_module=by_module,
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )

    # Cache
    try:
        _requirements_cache = matrix
        _requirements_cache_mtime = os.path.getmtime(yaml_path)
    except OSError:
        pass

    return matrix


# ---------------------------------------------------------------------------
# Private Functions
# ---------------------------------------------------------------------------


def _load_requirements_yaml(yaml_path: Path) -> dict[str, dict]:
    """Load requirements.yaml and return the requirements dict."""
    if yaml is None:
        logger.warning("PyYAML not installed; cannot load requirements")
        return {}

    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning(f"requirements.yaml not found: {yaml_path}")
        return {}
    except Exception as e:
        logger.warning(f"Failed to parse requirements.yaml: {e}")
        return {}

    return data.get("requirements", {})


def _load_junit_results(junit_path: Path) -> dict[tuple[str, str], dict]:
    """Parse junit.xml and return dict of (class_name, method_name) -> {status, duration}.

    Status: "passed" | "failed" | "skipped"
    Duration: float (seconds)
    """
    results: dict[tuple[str, str], dict] = {}

    try:
        tree = ET.parse(junit_path)
    except FileNotFoundError:
        logger.warning(f"junit.xml not found: {junit_path}")
        return results
    except ET.ParseError as e:
        logger.warning(f"Failed to parse junit.xml: {e}")
        return results

    root = tree.getroot()

    # Iterate testcases in testsuite
    for testcase in root.findall(".//testcase"):
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        duration = float(testcase.get("time", "0.0"))

        # Extract short class name (last part of full path)
        short_class = classname.split(".")[-1]

        # Determine status
        status = "passed"
        if testcase.find("failure") is not None:
            status = "failed"
        elif testcase.find("error") is not None:
            status = "failed"
        elif testcase.find("skipped") is not None:
            status = "skipped"

        results[(short_class, name)] = {"status": status, "duration": duration}

    return results


def _resolve_test_case(
    ref: str, junit_results: dict, tests_dir: Path
) -> Optional[TestCaseRef]:
    """Resolve a test reference to a TestCaseRef with docstring and status.

    Format: "test_file.py::ClassName" or "test_file.py::ClassName::method_name"
    """
    parts = ref.split("::")
    if len(parts) < 2:
        logger.warning(f"Invalid test reference format: {ref}")
        return None

    file = parts[0]
    class_name = parts[1]
    method = parts[2] if len(parts) > 2 else ""

    # Extract docstring
    docstring = _extract_docstring(tests_dir / file, class_name, method)

    # Look up status in junit results
    status = "missing"
    duration = 0.0
    if (class_name, method) in junit_results:
        status = junit_results[(class_name, method)]["status"]
        duration = junit_results[(class_name, method)]["duration"]
    elif method == "":
        # Class-level reference: check if any method matches
        for (jc, jm), result in junit_results.items():
            if jc == class_name:
                status = result["status"]
                duration = result["duration"]
                break

    return TestCaseRef(
        file=file,
        class_name=class_name,
        method=method,
        docstring=docstring,
        status=status,
        duration=duration,
    )


def _extract_docstring(
    test_file_path: Path, class_name: str, method_name: str
) -> str:
    """Extract docstring from test file using AST.

    Returns:
        First line of docstring (up to 160 chars), or empty string if not found
    """
    if not test_file_path.exists():
        logger.warning(f"Test file not found: {test_file_path}")
        return ""

    # Parse AST (cached)
    tree = _get_cached_ast(test_file_path)
    if tree is None:
        return ""

    # Find class
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            # If method_name is empty, use class docstring
            if not method_name:
                doc = ast.get_docstring(node)
                if doc:
                    first_line = doc.split("\n")[0].strip()
                    return first_line[:160]
                return ""

            # Find method in class
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    doc = ast.get_docstring(item)
                    if doc:
                        first_line = doc.split("\n")[0].strip()
                        return first_line[:160]
                    return ""

            return ""

    logger.warning(
        f"Class {class_name} not found in {test_file_path.name}"
    )
    return ""


def _get_cached_ast(file_path: Path) -> Optional[ast.Module]:
    """Parse and cache AST for a file."""
    if file_path in _ast_cache:
        return _ast_cache[file_path]

    try:
        with open(file_path) as f:
            source = f.read()
        tree = ast.parse(source)
        _ast_cache[file_path] = tree
        return tree
    except (OSError, SyntaxError) as e:
        logger.warning(f"Failed to parse {file_path}: {e}")
        _ast_cache[file_path] = None
        return None


def _compute_test_level(files: list[str]) -> str:
    """Determine test level from file names.

    Returns:
        "e2e" | "sanity" | "unit" | "mixed" | "none"
    """
    if not files:
        return "none"

    levels = set()
    for file in files:
        if file.startswith("test_e2e_"):
            levels.add("e2e")
        elif file == "test_sanity_flows.py":
            levels.add("sanity")
        else:
            levels.add("unit")

    if len(levels) == 1:
        return levels.pop()
    elif levels:
        return "mixed"
    else:
        return "none"


def _compute_summary(requirements: list[RequirementCoverage]) -> dict[str, int]:
    """Compute summary counts."""
    summary: dict[str, int] = {
        "total": len(requirements),
        "covered": 0,
        "partial": 0,
        "missing": 0,
        "by_priority": {
            "CRITICAL": 0,
            "HIGH": 0,
            "MEDIUM": 0,
            "LOW": 0,
        },
    }

    for req in requirements:
        if req.coverage_status == "covered":
            summary["covered"] += 1
        elif req.coverage_status == "partial":
            summary["partial"] += 1
        else:
            summary["missing"] += 1

        priority = req.priority
        if priority in summary["by_priority"]:
            summary["by_priority"][priority] += 1

    return summary


def _compute_by_module(requirements: list[RequirementCoverage]) -> dict[str, dict]:
    """Compute coverage breakdown by module."""
    by_module: dict[str, dict] = {}

    for req in requirements:
        module = req.module
        if module not in by_module:
            by_module[module] = {"total": 0, "covered": 0, "partial": 0, "missing": 0}

        by_module[module]["total"] += 1
        if req.coverage_status == "covered":
            by_module[module]["covered"] += 1
        elif req.coverage_status == "partial":
            by_module[module]["partial"] += 1
        else:
            by_module[module]["missing"] += 1

    return by_module
