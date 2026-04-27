"""Admin endpoint exposing unit-test and coverage metrics."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.admin_auth import verify_admin
from app.core.logging_conf import get_module_logger
from app.services.coverage_metrics import (
    load_coverage_report,
    report_to_dict,
)

logger = get_module_logger("metrics")

router = APIRouter()

# These module-level names are patched in tests to point at fixture files.
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_COVERAGE_XML = _BASE_DIR / "reports" / "coverage.xml"
_JUNIT_XML = _BASE_DIR / "reports" / "junit.xml"


@router.get("/admin/metrics/coverage")
def get_coverage_metrics(username: str = Depends(verify_admin)) -> dict:
    """Return line-coverage + test-count metrics from the last pytest run."""
    try:
        report = load_coverage_report(_COVERAGE_XML, _JUNIT_XML)
    except FileNotFoundError:
        logger.warning("[METRICS] coverage.xml not found at %s", _COVERAGE_XML)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Coverage report not found. Run pytest --cov first.",
        )
    except Exception as exc:
        logger.error("[METRICS] Failed to parse coverage report: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to parse coverage report.",
        )
    return report_to_dict(report)
