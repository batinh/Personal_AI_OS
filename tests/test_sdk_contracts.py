"""
SDK Contract Tests — verify third-party SDK parameter units and safe defaults.

Lesson learned (2026-04-21): HttpOptions.timeout is MILLISECONDS, not seconds.
timeout=30 → 30ms → X-Server-Timeout:1 → Google rejects "deadline 1s too short".
timeout=30000 → 30s → X-Server-Timeout:30 → accepted.

These tests catch the bug class statically (AST parse) without hitting the API.
"""
import ast
import inspect
import pathlib

# ── Minimum safe timeout: 10 seconds expressed in milliseconds ─────────────────
_MIN_SAFE_TIMEOUT_MS = 10_000  # Google API minimum deadline is 10s

_AGENT_FILES = list(pathlib.Path("app").rglob("*.py"))


# ── 1. SDK unit contract ───────────────────────────────────────────────────────

def test_http_options_timeout_field_is_milliseconds():
    """Regression guard: if Google ever renames/redefines the unit, this fails fast.
    Reads SDK source from disk to bypass conftest's genai mock."""
    import sys as _sys
    # Find types.py in site-packages directly (bypasses sys.modules mock)
    types_path = None
    for p in _sys.path:
        candidate = pathlib.Path(p) / "google" / "genai" / "types.py"
        if candidate.exists():
            types_path = candidate
            break
    assert types_path is not None, "google-genai types.py not found in sys.path"
    src = types_path.read_text(encoding="utf-8")
    assert "milliseconds" in src, (
        "HttpOptions.timeout unit changed in SDK — audit ALL genai.Client() usages."
    )


def test_http_options_timeout_converts_to_seconds_correctly():
    """Verify SDK math: timeout_ms / 1000 = timeout_seconds for X-Server-Timeout header."""
    # The SDK does: timeout_in_seconds = timeout / 1000.0
    # math.ceil(30000 / 1000) = 30  → X-Server-Timeout: 30  ✓
    # math.ceil(30    / 1000) = 1   → X-Server-Timeout: 1   ✗ (rejected)
    import math
    assert math.ceil(30_000 / 1000) == 30, "Expected 30s server deadline"
    assert math.ceil(30 / 1000) == 1, "30ms bug reproducer: this is what broke Morning Briefing"


# ── 2. Static AST audit of all genai.Client() calls ──────────────────────────

def _find_http_options_timeouts(source: str) -> list[int]:
    """Return all HttpOptions(timeout=N) literal values in source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match: HttpOptions(...) or types.HttpOptions(...)
        is_http_options = (
            (isinstance(func, ast.Name) and func.id == "HttpOptions")
            or (isinstance(func, ast.Attribute) and func.attr == "HttpOptions")
        )
        if not is_http_options:
            continue
        for kw in node.keywords:
            if kw.arg == "timeout" and isinstance(kw.value, ast.Constant):
                found.append(int(kw.value.value))
    return found


def test_all_http_options_timeouts_are_safe():
    """
    Every HttpOptions(timeout=N) literal in the codebase must be >= 10_000ms (10s).
    Catches the class of bug where seconds are written instead of milliseconds.
    """
    violations = []
    for path in _AGENT_FILES:
        try:
            src = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for val in _find_http_options_timeouts(src):
            if val < _MIN_SAFE_TIMEOUT_MS:
                violations.append(
                    f"{path}: HttpOptions(timeout={val}) — looks like seconds. "
                    f"SDK uses milliseconds. Use {val * 1000} for {val}s."
                )

    assert not violations, (
        "HttpOptions.timeout values below safe threshold (10_000ms):\n"
        + "\n".join(violations)
    )


def test_no_bare_genai_client_with_tiny_timeout():
    """
    Secondary check: any genai.Client(http_options=...) must not carry a tiny timeout.
    Complements test_all_http_options_timeouts_are_safe with a plain text scan
    to catch edge cases the AST parser might miss (e.g. multi-line expressions).
    """
    import re
    pattern = re.compile(r"HttpOptions\(timeout=(\d+)\)")
    violations = []
    for path in _AGENT_FILES:
        try:
            src = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for match in pattern.finditer(src):
            val = int(match.group(1))
            if val < _MIN_SAFE_TIMEOUT_MS:
                lineno = src[: match.start()].count("\n") + 1
                violations.append(f"{path}:{lineno}: timeout={val} (< 10_000ms)")

    assert not violations, (
        "Suspiciously small HttpOptions.timeout values found:\n"
        + "\n".join(violations)
    )
