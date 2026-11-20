"""Runalyze MCP client — Personal API Token auth over MCP streamable HTTP.

Create the token at https://runalyze.com/settings/personal-api (Premium required) and
set ``RUNALYZE_API_TOKEN`` in ``.env``. It is sent in the ``token`` header; the MCP
rejects ``Authorization: Bearer`` for personal tokens (Bearer is OAuth-only).
"""

import json
import os
from datetime import date, timedelta
from typing import Any, Optional

import requests

from app.core.database import upsert_runalyze_daily_load
from app.core.logging_conf import get_module_logger

logger = get_module_logger("runalyze_client")

MCP_URL = "https://runalyze.com/mcp"
MCP_PROTOCOL_VERSION = "2025-03-26"

REQUEST_TIMEOUT_SEC = 20  # requests uses seconds (not ms like google-genai)
LIST_TIMEOUT_SEC = 90  # get_activities takes 10-30s on Runalyze's side
RACES_LOOKAHEAD_DAYS = 365


class RunalyzeError(Exception):
    """Any Runalyze API/MCP failure."""


class RunalyzeAuthError(RunalyzeError):
    """Missing/invalid API token — create a new one in Runalyze settings."""


def get_api_token() -> str:
    token = os.environ.get("RUNALYZE_API_TOKEN", "").strip()
    if not token:
        raise RunalyzeAuthError(
            "RUNALYZE_API_TOKEN not set — create one at "
            "https://runalyze.com/settings/personal-api"
        )
    return token


# ── MCP transport ────────────────────────────────────────────────────────────
def _parse_rpc_response(resp: requests.Response, rpc_id: int) -> dict:
    """Extract the JSON-RPC message for rpc_id from a JSON or SSE body."""
    if "text/event-stream" in resp.headers.get("Content-Type", ""):
        for line in resp.text.splitlines():
            if not line.startswith("data:"):
                continue
            msg = json.loads(line[5:].strip())
            if msg.get("id") == rpc_id:
                return msg
        raise RunalyzeError(f"No SSE message for rpc id {rpc_id}")
    return resp.json()


class RunalyzeClient:
    """One MCP session. Create per sync run (see ``get_runalyze_client``)."""

    def __init__(self) -> None:
        self._session_id: Optional[str] = None
        self._next_id = 0

    def _headers(self) -> dict:
        headers = {
            "token": get_api_token(),
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _rpc(
        self,
        method: str,
        params: Optional[dict] = None,
        timeout: int = REQUEST_TIMEOUT_SEC,
    ) -> dict:
        self._next_id += 1
        rpc_id = self._next_id
        resp = requests.post(
            MCP_URL,
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "id": rpc_id,
                "method": method,
                "params": params or {},
            },
            timeout=timeout,
        )
        if resp.status_code == 401:
            raise RunalyzeAuthError(
                "MCP rejected API token (401) — revoked or expired?"
            )
        resp.raise_for_status()
        self._session_id = resp.headers.get("Mcp-Session-Id", self._session_id)
        msg = _parse_rpc_response(resp, rpc_id)
        if "error" in msg:
            raise RunalyzeError(f"MCP {method} error: {msg['error']}")
        return msg.get("result", {})

    def _ensure_session(self) -> None:
        if self._session_id is not None:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "personal-ai-os", "version": "1.0"},
            },
        )
        requests.post(
            MCP_URL,
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=REQUEST_TIMEOUT_SEC,
        )

    def call_tool(
        self,
        name: str,
        arguments: Optional[dict] = None,
        timeout: int = REQUEST_TIMEOUT_SEC,
    ) -> Any:
        """Call an MCP tool and return its decoded JSON payload."""
        self._ensure_session()
        result = self._rpc(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout
        )
        if result.get("isError"):
            raise RunalyzeError(f"Tool {name} failed: {result.get('content')}")
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        text = next(
            (c["text"] for c in result.get("content", []) if c.get("type") == "text"),
            None,
        )
        if text is None:
            raise RunalyzeError(f"Tool {name} returned no text content")
        return json.loads(text)

    # ── sync ────────────────────────────────────────────────────────────────
    def fetch_and_store_daily_load(
        self, user_id: str, target_date: Optional[date] = None
    ) -> bool:
        """Fetch training load + latest health metrics and upsert one daily row.

        Returns True on success, False on failure (logged, never raised).
        """
        target_date = target_date or date.today()
        arguments = {
            "calculations": None,
            "health": None,
            "planned_races": {
                "start_date": target_date.isoformat(),
                "end_date": (
                    target_date + timedelta(days=RACES_LOOKAHEAD_DAYS)
                ).isoformat(),
            },
            "prognosis": None,
        }
        names = {"health": "get_health_metrics"}
        sections = {}
        for section, args in arguments.items():
            try:
                sections[section] = self.call_tool(
                    names.get(section, f"get_{section}"), args
                )
            except RunalyzeAuthError:
                logger.error("[RUNALYZE] Authentication failed")
                break
            except (RunalyzeError, requests.RequestException, ValueError) as exc:
                logger.warning(
                    "[RUNALYZE] Could not sync %s: %s", section, type(exc).__name__
                )
        if not sections:
            return False
        metrics = build_daily_load_metrics(
            sections.get("calculations", {}), sections.get("health", {})
        )
        health_fields = {
            "hrv_last",
            "resting_hr",
            "sleep_duration_min",
            "sleep_quality",
            "weight_kg",
        }
        metrics = {
            key: value
            for key, value in metrics.items()
            if ("health" if key in health_fields else "calculations") in sections
        }
        upsert_runalyze_daily_load(
            user_id, target_date.isoformat(), metrics, sections=sections
        )
        logger.info("[RUNALYZE] Synced sections: %s", ", ".join(sections))
        return len(sections) == len(arguments)


def build_daily_load_metrics(calc: dict, health: dict) -> dict:
    """Flatten Runalyze calculations + health payloads into table columns."""
    hrv_range = calc.get("hrvNormalRange") or [None, None]
    sleep = health.get("sleep") or {}
    hrv = health.get("hrv") or {}
    rhr = health.get("resting_heart_rate") or {}
    weight = health.get("weight") or {}
    return {
        "effective_vo2max": calc.get("effectiveVO2max"),
        "ctl": calc.get("fitness"),
        "atl": calc.get("fatigue"),
        "tsb": calc.get("performance"),
        "acwr": calc.get("acuteChronicWorkloadRatio"),
        "marathon_shape": calc.get("marathonShape"),
        "rest_days": calc.get("restDays"),
        "monotony": calc.get("monotonyValue"),
        "training_strain": calc.get("trainingStrain"),
        "hrv_baseline": calc.get("hrvBaseline"),
        "hrv_range_low": hrv_range[0],
        "hrv_range_high": hrv_range[1],
        "hrv_last": hrv.get("hrv"),
        "resting_hr": rhr.get("heart_rate"),
        "sleep_duration_min": sleep.get("duration"),
        "sleep_quality": sleep.get("quality"),
        "weight_kg": weight.get("weight"),
    }


def get_runalyze_client() -> RunalyzeClient:
    return RunalyzeClient()
