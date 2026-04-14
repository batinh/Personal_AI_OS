import os
import logging
from typing import Optional

import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# Initialize logging
logger = logging.getLogger(__name__)
load_dotenv()

# All 11 stream types from Strava API (time, latlng, distance, altitude, velocity_smooth, heartrate, cadence, watts, temp, moving, grade_smooth)
STRAVA_STREAM_KEYS = "time,latlng,distance,altitude,velocity_smooth,heartrate,cadence,watts,temp,moving,grade_smooth"


class StravaClient:
    def __init__(self):
        self.client_id = os.getenv("STRAVA_CLIENT_ID")
        self.client_secret = os.getenv("STRAVA_CLIENT_SECRET")
        self.refresh_token = os.getenv("STRAVA_REFRESH_TOKEN")
        self.auth_url = "https://www.strava.com/oauth/token"
        self.base_url = "https://www.strava.com/api/v3"
        # Token cache: avoids redundant refresh calls within the same activity pipeline
        self._cached_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def get_access_token(self):
        """
        Refresh and retrieve a valid access token.
        Caches the token in memory until expiry to avoid redundant refresh calls
        (e.g., get_activity_data() calls both activity detail AND streams endpoints).
        """
        import time
        # Return cached token if still valid (60-second buffer before actual expiry)
        if self._cached_token and time.time() < (self._token_expires_at - 60):
            return self._cached_token

        payload = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': self.refresh_token,
            'grant_type': 'refresh_token'
        }
        try:
            response = requests.post(self.auth_url, data=payload)
            response.raise_for_status()
            data = response.json()
            self._cached_token = data.get('access_token')
            # Strava returns expires_at (unix timestamp); fall back to 1 hour if missing
            self._token_expires_at = data.get('expires_at', time.time() + 3600)
            return self._cached_token
        except Exception as e:
            logger.error(f"[STRAVA] Failed to refresh token: {e}")
            return None

    def get_activity_streams_raw(self, activity_id: str) -> Optional[dict]:
        """
        Fetch full raw streams from Strava (all 11 keys). Returns key_by_type dict or None.
        Caller should save to file via stream_storage.save_activity_stream_to_file().
        """
        token = self.get_access_token()
        if not token:
            return None
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/activities/{activity_id}/streams?keys={STRAVA_STREAM_KEYS}&key_by_type=true"
        try:
            r = requests.get(url, headers=headers)
            if r.status_code != 200:
                logger.warning(f"[STRAVA] Streams response {r.status_code} for {activity_id}")
                return None
            return r.json()
        except Exception as e:
            logger.error(f"[STRAVA] Error fetching streams for {activity_id}: {e}")
            return None

    def get_activity_data(self, activity_id: str):
        """
        Fetch Full Data: Streams (CSV for LLM), Metadata (Splits, Laps, PRs), and raw streams.
        Returns: (activity_name, csv_data, extended_meta, stream_raw).
        stream_raw is key_by_type dict for saving to file; csv_data is downsampled for Gemini.
        """
        token = self.get_access_token()
        if not token:
            return None, None, None, None

        headers = {"Authorization": f"Bearer {token}"}

        try:
            # 1. Fetch Activity Detail (Contains Laps, Splits, Best Efforts)
            act_url = f"{self.base_url}/activities/{activity_id}"
            act_res = requests.get(act_url, headers=headers)
            if act_res.status_code != 200:
                logger.error(f"[STRAVA] Error fetching activity: {act_res.text}")
                return None, None, None, None

            act_data = act_res.json()
            activity_name = act_data.get("name", "Unknown Run")

            # Check type (Run, VirtualRun, etc.)
            if act_data.get("type") not in ["Run", "VirtualRun", "TrailRun", "Treadmill"]:
                logger.info(f"[STRAVA] Activity {activity_id} is not a run. Skipping.")
                return None, None, None, None

            # 2. Extract Splits & Laps & Metadata
            splits = act_data.get("splits_metric", [])
            splits_summary = [
                {"km": s.get("split"), "pace": s.get("average_speed"), "hr": s.get("average_heartrate", 0)}
                for s in splits
            ]
            laps = act_data.get("laps", [])
            laps_summary = [
                {
                    "lap_name": l.get("name"),
                    "distance": l.get("distance"),
                    "pace": l.get("average_speed"),
                    "hr": l.get("average_heartrate", 0),
                }
                for l in laps
            ]
            extended_meta = {
                "start_date_local": act_data.get("start_date_local"),
                "moving_time": act_data.get("moving_time", 0),
                "average_heartrate": act_data.get("average_heartrate", 0),
                "max_heartrate": act_data.get("max_heartrate", 0),
                "distance": act_data.get("distance", 0),
                "suffer_score": act_data.get("suffer_score"),
                "device_name": act_data.get("device_name"),
                "splits": splits_summary,
                "best_efforts": act_data.get("best_efforts", []),
            }

            # 3. Fetch full raw streams (all keys) for file storage + build CSV from same response
            streams_res = self.get_activity_streams_raw(activity_id)
            if not streams_res:
                logger.warning(f"[STRAVA] No streams for {activity_id}; returning meta only.")
                return activity_name, None, extended_meta, None

            # 4. Build DataFrame from streams for downsampled CSV (Gemini)
            data = {
                "Time_sec": streams_res.get("time", {}).get("data", []),
                "HR_bpm": streams_res.get("heartrate", {}).get("data", []),
                "Velocity_m_s": streams_res.get("velocity_smooth", {}).get("data", []),
                "Cadence_spm": streams_res.get("cadence", {}).get("data", []),
                "Grade_pct": streams_res.get("grade_smooth", {}).get("data", []),
                "Power_watts": streams_res.get("watts", {}).get("data", []),
            }
            df = pd.DataFrame({"Time_sec": data["Time_sec"]})
            for col, values in data.items():
                if col != "Time_sec":
                    s = pd.Series(values)
                    df[col] = s.reindex(df.index)

            df.dropna(subset=["HR_bpm", "Velocity_m_s"], inplace=True)
            df["Stride_m"] = df.apply(
                lambda row: (row["Velocity_m_s"] * 60 / row["Cadence_spm"]) if row["Cadence_spm"] > 0 else 0,
                axis=1,
            )
            if "Power_watts" in df.columns:
                df["Power_watts"] = df["Power_watts"].fillna(0)
            df = df.round({"Velocity_m_s": 2, "Stride_m": 2, "Grade_pct": 1})
            df = df.iloc[::5, :]
            csv_data = df.to_csv(index=False)
            logger.info(f"[STRAVA] Processed CSV + raw streams for {activity_id}")

            return activity_name, csv_data, extended_meta, streams_res

        except Exception as e:
            logger.error(f"[STRAVA] Error processing activity data: {e}")
            return None, None, None, None

    def update_activity_description(self, activity_id: str, description: str):
        """Update the description of a Strava activity."""
        token = self.get_access_token()
        if not token: return False

        url = f"{self.base_url}/activities/{activity_id}"
        headers = {'Authorization': f'Bearer {token}'}
        payload = {'description': description}

        try:
            response = requests.put(url, headers=headers, json=payload)
            if response.status_code == 200:
                logger.info(f"[STRAVA] Description updated for {activity_id}")
                return True
            else:
                logger.error(f"[STRAVA] Failed update: {response.text}")
                return False
        except Exception as e:
            logger.error(f"[STRAVA] Error updating description: {e}")
            return False

    def get_athlete_stats(self, athlete_id):
        """Fetch total running mileage (Week/Month/Year/All-time)"""
        token = self.get_access_token() 
        url = f"https://www.strava.com/api/v3/athletes/{athlete_id}/stats"
        headers = {"Authorization": f"Bearer {token}"}
        
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                return {
                    "recent_run_totals": data["recent_run_totals"]["distance"] / 1000,
                    "ytd_run_totals": data["ytd_run_totals"]["distance"] / 1000,
                    "all_run_totals": data["all_run_totals"]["distance"] / 1000
                }
            logger.error(f"Error fetching stats: {response.status_code}")
        except Exception as e:
            logger.error(f"Stats Exception: {e}")
        return None

    def get_recent_activities(self, limit=10):
        """Fetch the list of most recent activities"""
        token = self.get_access_token()
        url = "https://www.strava.com/api/v3/athlete/activities"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"per_page": limit}

        try:
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Activities Exception: {e}")
        return []

    def get_all_activities_paginated(self, per_page: int = 100) -> list:
        """Fetch every activity on the athlete's account via Strava pagination.
        Stops when an empty page is returned. per_page max is 200 per Strava docs."""
        token = self.get_access_token()
        if not token:
            return []
        url = "https://www.strava.com/api/v3/athlete/activities"
        headers = {"Authorization": f"Bearer {token}"}
        all_activities = []
        page = 1
        while True:
            try:
                resp = requests.get(url, headers=headers, params={"per_page": per_page, "page": page})
                if resp.status_code == 429:
                    logger.warning("[STRAVA] Rate limited during paginated fetch; stopping.")
                    break
                if resp.status_code != 200:
                    logger.error(f"[STRAVA] Paginated fetch page {page} failed: {resp.status_code}")
                    break
                batch = resp.json()
                if not batch:
                    break
                all_activities.extend(batch)
                logger.info(f"[STRAVA] Fetched page {page}: {len(batch)} activities (total so far: {len(all_activities)})")
                page += 1
            except Exception as e:
                logger.error(f"[STRAVA] Paginated fetch exception on page {page}: {e}")
                break
        return all_activities

    def fetch_activity_detail_status(self, activity_id: str) -> dict:
        """
        Verify existence of a Strava activity by ID.
        Returns a dict: {"status": "exists"|"not_found"|"forbidden"|"rate_limited"|"error", "code": int}
        This lets reconciler decide whether it's safe to delete local copies when Strava truly reports 404.
        """
        token = self.get_access_token()
        if not token:
            return {"status": "error", "code": None}
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/activities/{activity_id}"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            code = r.status_code
            if code == 200:
                return {"status": "exists", "code": code}
            if code == 404:
                return {"status": "not_found", "code": code}
            if code in (401, 403):
                return {"status": "forbidden", "code": code}
            if code == 429:
                return {"status": "rate_limited", "code": code}
            # treat other 5xx as error
            if 500 <= code < 600:
                return {"status": "error", "code": code}
            return {"status": "error", "code": code}
        except requests.exceptions.RequestException as e:
            logger.error(f"[STRAVA] Network error verifying activity {activity_id}: {e}")
            return {"status": "error", "code": None}
