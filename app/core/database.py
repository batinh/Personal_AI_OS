import json
import sqlite3
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import pytz

from app.core.logging_conf import get_module_logger

logger = get_module_logger("database")

# --- Absolute path anchored to this file's location ---
# database.py is at: <project_root>/app/core/database.py
# So parent.parent.parent = <project_root>
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = str(_BASE_DIR / "data" / "os_core.db")


def get_db_connection():
    """
    Helper function to get a database connection.
    Uses WAL journal mode for safe concurrent access (multiple threads/BackgroundTasks).
    busy_timeout prevents 'database is locked' errors under write contention.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row  # Return results as dict instead of tuple
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def get_db():
    """
    Context manager for safe database access.
    Guarantees connection is always closed and transaction is rolled back on error.
    Use for new code: `with get_db() as conn:`
    """
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize the relational database schema."""
    os.makedirs("data", exist_ok=True)
    conn = get_db_connection()
    c = conn.cursor()

    # 1. Table: users
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            name TEXT,
            max_hr INTEGER DEFAULT 185,
            rest_hr INTEGER DEFAULT 55,
            race_date TEXT,
            current_goal TEXT,
            is_active BOOLEAN DEFAULT 1
        )
    """)

    # 2. Table: run_activities
    c.execute("""
        CREATE TABLE IF NOT EXISTS run_activities (
            activity_id TEXT PRIMARY KEY,
            user_id TEXT,
            name TEXT,
            start_date DATETIME,
            distance_km REAL,
            moving_time_min REAL,
            avg_hr INTEGER,
            max_hr INTEGER,
            suffer_score INTEGER,
            trimp_score REAL,
            gcs_score INTEGER DEFAULT NULL,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    """)

    # [NEW] Auto-migrate: Add gcs_score column to legacy DB if not exists
    try:
        c.execute(
            "ALTER TABLE run_activities ADD COLUMN gcs_score INTEGER DEFAULT NULL"
        )
    except sqlite3.OperationalError:
        pass  # Ignore if column already exists

    # [PHASE 1] Coach Strava Metrics Upgrade — 25 new computed metric columns
    _metric_migrations = [
        # Group A — Aerobic Base
        "ALTER TABLE run_activities ADD COLUMN aerobic_decoupling_pct REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN cardiac_drift_pct REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN avg_efficiency_factor REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN hr_zone_distribution TEXT DEFAULT NULL",  # JSON
        "ALTER TABLE run_activities ADD COLUMN time_in_hr_zones_sec TEXT DEFAULT NULL",  # JSON
        # Group B — Cadence / Mechanics
        "ALTER TABLE run_activities ADD COLUMN avg_cadence_spm REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN avg_stride_length_m REAL DEFAULT NULL",
        # Group C — Pace / Effort
        "ALTER TABLE run_activities ADD COLUMN avg_pace_min_km REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN pace_variability_cv REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN positive_split_ratio REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN time_in_pace_zones_pct TEXT DEFAULT NULL",  # JSON
        # Group D — Elevation / Grade
        "ALTER TABLE run_activities ADD COLUMN total_elevation_gain_m REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN grade_adjusted_pace_min_km REAL DEFAULT NULL",
        # Group E — Power (Stryd only, all nullable)
        "ALTER TABLE run_activities ADD COLUMN avg_power_watts REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN normalized_power_watts REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN intensity_factor REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN training_stress_score REAL DEFAULT NULL",
        # Group F — Interval / Sprint (auto-detected)
        "ALTER TABLE run_activities ADD COLUMN workout_type_detected TEXT DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN interval_reps_count INTEGER DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN interval_avg_pace_min_km REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN interval_pace_consistency_pct REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN interval_avg_hr_bpm REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN recovery_hr_quality_bpm REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN max_velocity_m_s REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN anaerobic_time_sec REAL DEFAULT NULL",
        "ALTER TABLE run_activities ADD COLUMN z4_z5_time_pct REAL DEFAULT NULL",
    ]
    for _sql in _metric_migrations:
        try:
            c.execute(_sql)
        except sqlite3.OperationalError:
            pass  # Column already exists

    # 2b. Table: run_activity_raw (full Strava payload per run for analysis, recall, tools)
    c.execute("""
        CREATE TABLE IF NOT EXISTS run_activity_raw (
            activity_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            activity_name TEXT,
            full_meta TEXT,
            stream_csv TEXT,
            stream_file_path TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (activity_id) REFERENCES run_activities (activity_id)
        )
    """)
    try:
        c.execute("ALTER TABLE run_activity_raw ADD COLUMN stream_file_path TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # 3. Table: chat_history (Upgraded)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    """)

    # 4. Table: training_plans (Single Source of Truth)
    # [REFACTOR MULTI-TENANT] Check if legacy table exists and lacks user_id to Migrate
    cursor = c.execute("PRAGMA table_info(training_plans)")
    columns = [col[1] for col in cursor.fetchall()]

    if columns and "user_id" not in columns:
        logger.info(
            "[DATABASE] Migrating training_plans table to Multi-Tenant architecture..."
        )
        c.execute("ALTER TABLE training_plans RENAME TO training_plans_old")
        c.execute("""
            CREATE TABLE training_plans (
                user_id TEXT,
                date TEXT,
                workout_title TEXT,
                description TEXT,
                status TEXT DEFAULT 'Pending',
                PRIMARY KEY (user_id, date)
            )
        """)
        # Migrate old data (assign temporarily to 'default' user)
        c.execute(
            "INSERT INTO training_plans (user_id, date, workout_title, description, status) SELECT 'default', date, workout_title, description, status FROM training_plans_old"
        )
        c.execute("DROP TABLE training_plans_old")
    else:
        # Create standard Multi-Tenant table
        c.execute("""
            CREATE TABLE IF NOT EXISTS training_plans (
                user_id TEXT,
                date TEXT,
                workout_title TEXT,
                description TEXT,
                status TEXT DEFAULT 'Pending',
                PRIMARY KEY (user_id, date)
            )
        """)

    # 5. [SPRINT A] Table: user_weekly_targets (Ledger Pattern for Weekly Volume)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_weekly_targets (
            user_id TEXT,
            week_start_date TEXT,       -- Format YYYY-MM-DD (Always a Monday)
            standard_target_km REAL,    -- Original volume assigned by Coach
            actual_target_km REAL,      -- Negotiated volume set by AI or User
            ai_reasoning TEXT,          -- Reason for AI adjustment
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, week_start_date)
        )
    """)
    # [PHASE 8] Multi-Agent Core Memory Table (Multi-Tenant Ready)
    c.execute("""
        CREATE TABLE IF NOT EXISTS core_memory (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'default_user',
            domain TEXT NOT NULL,
            category TEXT NOT NULL,
            fact TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active'
        )
    """)

    # [DATABASE MIGRATION] Zero-downtime column injection for existing DB
    try:
        cursor_check = c.execute("PRAGMA table_info(core_memory)")
        columns = [col[1] for col in cursor_check.fetchall()]
        if "user_id" not in columns:
            logger.info(
                "[DATABASE] Migrating core_memory to support Multi-Tenant (adding user_id)..."
            )
            c.execute(
                "ALTER TABLE core_memory ADD COLUMN user_id TEXT DEFAULT 'default_user'"
            )
    except Exception as e:
        logger.error(f"[DATABASE] Migration error on core_memory: {e}")

    # [NEWS AGENT] Table: news_sent_articles (dedup — prevents same article sent morning + afternoon)
    c.execute("""
        CREATE TABLE IF NOT EXISTS news_sent_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            article_link TEXT NOT NULL,
            session TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_sent_user_link
        ON news_sent_articles (user_id, article_link)
    """)

    # [NEWS AGENT] Table: news_agent_state (persistent key-value memory for news agent)
    c.execute("""
        CREATE TABLE IF NOT EXISTS news_agent_state (
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, key)
        )
    """)

    # [LOG AUDIT] Table: audit_entries (structured log issue tracker)
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            severity TEXT NOT NULL CHECK(severity IN ('error', 'warning', 'info')),
            category TEXT NOT NULL,
            message TEXT NOT NULL,
            raw_line TEXT NOT NULL,
            status TEXT DEFAULT 'open' CHECK(status IN ('open', 'acknowledged', 'resolved')),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, raw_line)
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_user_status
        ON audit_entries(user_id, status)
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_severity
        ON audit_entries(severity, category)
    """)

    # [PHASE 1] Index for user+date range queries on computed metrics
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_run_activities_user_date
        ON run_activities(user_id, start_date)
    """)

    conn.commit()
    conn.close()
    logger.info(
        "[DATABASE] Relational DB initialized successfully (Multi-Tenant Ready)."
    )


# ==========================================
# USERS CRUD
# ==========================================
def upsert_user(
    user_id: str, name: str = "Runner", max_hr: int = 185, rest_hr: int = 55
):
    """Insert a new user or update existing user."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO users (user_id, name, max_hr, rest_hr)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                max_hr=excluded.max_hr,
                rest_hr=excluded.rest_hr
        """,
            (str(user_id), name, max_hr, rest_hr),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to upsert user: {e}")


def get_user(user_id: str) -> Optional[Dict]:
    """Retrieve user physiology profile."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ?", (str(user_id),))
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to get user: {e}")
        return None


# ==========================================
# RUN ACTIVITIES CRUD
# ==========================================
def save_run_activity(user_id: str, activity_data: dict):
    """Save run activity to SQLite. Use UPSERT to ensure Data Integrity between Webhook and Harvest pipelines."""
    try:
        conn = get_db_connection()
        c = conn.cursor()

        # [ARCHITECTURE UPDATE] Use ON CONFLICT DO UPDATE (UPSERT)
        # If a Placeholder exists (created by Webhook), it will populate NULL columns WITHOUT losing gcs_score
        c.execute(
            """
            INSERT INTO run_activities 
            (activity_id, user_id, name, start_date, distance_km, moving_time_min, avg_hr, max_hr, suffer_score, trimp_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(activity_id) DO UPDATE SET
                name=excluded.name,
                start_date=excluded.start_date,
                distance_km=excluded.distance_km,
                moving_time_min=excluded.moving_time_min,
                avg_hr=excluded.avg_hr,
                max_hr=excluded.max_hr,
                suffer_score=excluded.suffer_score,
                trimp_score=excluded.trimp_score
        """,
            (
                str(activity_data["activity_id"]),
                str(user_id),
                activity_data.get("name"),
                activity_data.get("start_date"),
                activity_data.get("distance_km"),
                activity_data.get("moving_time_min"),
                activity_data.get("avg_hr"),
                activity_data.get("max_hr"),
                activity_data.get("suffer_score"),
                activity_data.get("trimp_score"),
            ),
        )

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to save/upsert run activity: {e}")


def update_run_gcs_score(activity_id: str, user_id: str, gcs_score: int):
    """Update GCS score, automatically creating a placeholder if the run hasn't been Harvested yet."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        # [FIX BUG] Create a placeholder row first so GCS score is not lost
        c.execute(
            "INSERT OR IGNORE INTO run_activities (activity_id, user_id) VALUES (?, ?)",
            (str(activity_id), str(user_id)),
        )
        # Then update the GCS score
        c.execute(
            "UPDATE run_activities SET gcs_score = ? WHERE activity_id = ?",
            (gcs_score, str(activity_id)),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to update GCS: {e}")


def delete_run_activity(activity_id: str):
    """Delete a run activity from the database when receiving a Delete signal from Strava.
    Also removes the stream file if it exists (path stored in run_activity_raw).
    """
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "SELECT stream_file_path FROM run_activity_raw WHERE activity_id = ?",
            (str(activity_id),),
        )
        row = c.fetchone()
        stream_path = (
            row["stream_file_path"] if row and row["stream_file_path"] else None
        )
        c.execute(
            "DELETE FROM run_activity_raw WHERE activity_id = ?", (str(activity_id),)
        )
        c.execute(
            "DELETE FROM run_activities WHERE activity_id = ?", (str(activity_id),)
        )
        conn.commit()
        conn.close()
        if stream_path:
            try:
                from pathlib import Path
                from app.services.stream_storage import DATA_DIR

                full = Path(DATA_DIR) / stream_path.lstrip("/").replace("data/", "")
                if full.is_file():
                    full.unlink()
                    logger.info(f"[DB] Deleted stream file {full}")
            except Exception as e:
                logger.warning(
                    f"[DB] Could not delete stream file for {activity_id}: {e}"
                )
        logger.info(
            f"[DB] Successfully deleted run activity {activity_id} from SQLite."
        )
    except Exception as e:
        logger.error(f"[DB_ERROR] Error deleting run activity {activity_id}: {e}")


def list_run_activity_ids_in_date_range(
    user_id: str, start_date_str: str, end_date_str: str
) -> List[Dict]:
    """
    Return list of run activity ids in the inclusive date range [start_date_str, end_date_str].
    Each item is a dict: {"activity_id": str, "start_date": str} ordered by start_date DESC.
    Dates must be YYYY-MM-DD strings.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT activity_id, start_date FROM run_activities
            WHERE user_id = ? AND date(start_date) >= ? AND date(start_date) <= ?
            ORDER BY start_date DESC
        """,
            (str(user_id), start_date_str, end_date_str),
        )
        rows = cursor.fetchall()
        conn.close()
        return [{"activity_id": r[0], "start_date": r[1]} for r in rows]
    except Exception as e:
        logger.error(f"[DB_ERROR] list_run_activity_ids_in_date_range: {e}")
        return []


def save_run_activity_raw(
    user_id: str,
    activity_id: str,
    activity_name: str,
    full_meta: dict,
    stream_csv: Optional[str] = None,
    stream_file_path: Optional[str] = None,
):
    """
    Persist full run data: metadata in DB; stream content in file (path stored here).
    full_meta: extended_meta from Strava (splits, laps, best_efforts, device_name, etc.).
    stream_file_path: relative path from data/ to JSON file (e.g. streams/user_id/activity_id.json).
    """
    try:
        conn = get_db_connection()
        c = conn.cursor()
        meta_json = json.dumps(full_meta, ensure_ascii=False, default=str)
        c.execute(
            """
            INSERT INTO run_activity_raw (activity_id, user_id, activity_name, full_meta, stream_csv, stream_file_path, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(activity_id) DO UPDATE SET
                user_id=excluded.user_id,
                activity_name=excluded.activity_name,
                full_meta=excluded.full_meta,
                stream_csv=excluded.stream_csv,
                stream_file_path=excluded.stream_file_path,
                fetched_at=CURRENT_TIMESTAMP
        """,
            (
                str(activity_id),
                str(user_id),
                activity_name or "",
                meta_json,
                stream_csv or "",
                stream_file_path or "",
            ),
        )
        conn.commit()
        conn.close()
        logger.info(f"[DB] Saved full run data for activity {activity_id}.")
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to save run_activity_raw {activity_id}: {e}")


def get_run_activity_raw(activity_id: str) -> Optional[Dict]:
    """
    Load full run data (metadata + stream file path) for analysis, recall, or tools.
    Returns dict with keys: activity_name, full_meta (dict), stream_file_path (str), fetched_at; or None.
    Stream content: load from file via stream_storage.load_activity_stream_from_file(stream_file_path).
    """
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "SELECT activity_name, full_meta, stream_csv, stream_file_path, fetched_at FROM run_activity_raw WHERE activity_id = ?",
            (str(activity_id),),
        )
        row = c.fetchone()
        conn.close()
        if not row:
            return None
        meta = json.loads(row["full_meta"]) if row["full_meta"] else {}
        out = {
            "activity_name": row["activity_name"],
            "full_meta": meta,
            "fetched_at": row["fetched_at"],
        }
        if "stream_file_path" in row.keys():
            out["stream_file_path"] = row["stream_file_path"] or ""
        else:
            out["stream_file_path"] = ""
        out["stream_csv"] = (
            row["stream_csv"] if row["stream_csv"] else ""
        )  # legacy; prefer loading from file
        return out
    except Exception as e:
        logger.error(f"[DB_ERROR] get_run_activity_raw {activity_id}: {e}")
        return None


# ==========================================
# CHAT HISTORY CRUD
# ==========================================
def save_message(user_id: str, role: str, text: str):
    """Save conversation message to chat history."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)",
            (str(user_id), role, text),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[DB_ERROR] Save Message Error: {e}")


def load_history_for_gemini(user_id: str, limit: int = 20) -> List[Dict]:
    """Load conversation history for the AI Agent."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY timestamp DESC, rowid DESC LIMIT ?",
            (str(user_id), limit),
        )
        rows = c.fetchall()
        conn.close()

        history = []
        for row in reversed(rows):
            history.append({"role": row["role"], "parts": [row["content"]]})
        return history
    except Exception as e:
        logger.error(f"[DB_ERROR] Load History Error: {e}")
        return []


def clear_history(user_id: str):
    """Clear conversation history for a specific user."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM chat_history WHERE user_id = ?", (str(user_id),))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[DB_ERROR] Clear History Error: {e}")


# ==========================================
# ADVANCED ANALYTICS (AI QUERIES)
# ==========================================
def get_training_loads(user_id: str) -> dict:
    """
    [UPGRADED] Calculate both TRIMP Loads and Weekly Mileage in a single DB scan.
    Returns data used for ACWR and the 15% Volume Rule.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Fetch data from the last 28 days
        cursor.execute(
            """
            SELECT trimp_score, distance_km, start_date 
            FROM run_activities 
            WHERE user_id = ? AND start_date >= date('now', '-28 days')
        """,
            (str(user_id),),
        )
        rows = cursor.fetchall()
        conn.close()

        acute_trimp = 0.0
        chronic_trimp = 0.0
        total_dist_28d = 0.0  # [NEW]

        from datetime import datetime, timedelta

        now = datetime.now()
        seven_days_ago = (now - timedelta(days=7)).isoformat()

        for row in rows:
            trimp = row["trimp_score"] or 0
            dist = row["distance_km"] or 0
            s_date = row["start_date"]

            # Calculate TRIMP
            chronic_trimp += trimp
            if s_date >= seven_days_ago:
                acute_trimp += trimp

            # Calculate Mileage [NEW]
            total_dist_28d += dist

        return {
            "acute_load_7d": round(acute_trimp, 1),
            "chronic_load_28d": round(chronic_trimp, 1),  # 28-day total
            "avg_weekly_mileage": round(
                total_dist_28d / 4.0, 1
            ),  # [NEW] Average km per week
        }
    except Exception as e:
        logger.error(f"[DB_ERROR] get_training_loads Error: {e}")
        return {"acute_load_7d": 0, "chronic_load_28d": 0, "avg_weekly_mileage": 0}


def get_recent_runs_log(user_id: str, limit: int = 10) -> str:
    """Get a formatted string of recent runs for the AI prompt."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            """
            SELECT start_date, name, distance_km, trimp_score, gcs_score 
            FROM run_activities 
            WHERE user_id = ? ORDER BY start_date DESC LIMIT ?
        """,
            (str(user_id), limit),
        )
        rows = c.fetchall()
        conn.close()

        if not rows:
            return "No recent runs found in database."
        log_lines = []
        for r in rows:
            date_str = r["start_date"][:10] if r["start_date"] else "N/A"
            gcs_text = (
                f" | GCS: {r['gcs_score']}%" if r["gcs_score"] is not None else ""
            )
            log_lines.append(
                f"- {date_str}: {r['name']} | {r['distance_km']}km | TRIMP Load: {r['trimp_score']}{gcs_text}"
            )
        return "\n".join(log_lines)
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to get recent runs: {e}")
        return "Error loading recent runs."


def get_runs_in_last_days(user_id: str, days: int = 7) -> str:
    """Fetch accurately bounded running logs from the last N days."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Use SQLite date() for relative date filtering
        query = """
            SELECT start_date, name, distance_km, moving_time_min, avg_hr, trimp_score 
            FROM run_activities 
            WHERE user_id = ? AND start_date >= date('now', ?)
            ORDER BY start_date DESC
        """
        cursor.execute(query, (str(user_id), f"-{days} days"))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return f"Không có bài chạy nào trong {days} ngày qua."
        log_lines = []
        for r in rows:
            date_str = r["start_date"][:10]

            # Calculate Pace (Min/km) from time and distance
            dist = r["distance_km"]
            mins = r["moving_time_min"]

            if dist > 0:
                pace_min = int(mins // dist)
                pace_sec = int(((mins / dist) % 1) * 60)
                pace_str = f"{pace_min}:{pace_sec:02d}"
            else:
                pace_str = "0:00"

            log_lines.append(
                f"- {date_str}: {r['name']} ({r['distance_km']}km, Pace {pace_str}, HR {r['avg_hr']}, TRIMP {r['trimp_score']})"
            )

        return "\n".join(log_lines)
    except Exception as e:
        logger.error(f"[DB_ERROR] Error fetching log for last {days} days: {e}")
        return "Lỗi đọc dữ liệu."


def get_historical_training_loads(user_id: str, days: int = 30) -> dict:
    """Calculate the time series of Acute, Chronic, and Optimal Range for charting."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        # Fetch 60 days of data to provide enough baseline for calculating the 28-day Chronic load over the last 30 days
        c.execute(
            """
            SELECT start_date, trimp_score 
            FROM run_activities 
            WHERE user_id = ? AND start_date >= date('now', '-60 days')
        """,
            (str(user_id),),
        )
        rows = c.fetchall()
        conn.close()

        # Group TRIMP by day
        daily_trimp = {}
        for r in rows:
            date_str = r["start_date"][:10]
            daily_trimp[date_str] = daily_trimp.get(date_str, 0) + (
                r["trimp_score"] or 0
            )

        history = {
            "dates": [],
            "acute": [],
            "chronic": [],
            "optimal_min": [],
            "optimal_max": [],
        }
        base_date = datetime.now().date()

        # Iterate backward from N days ago to today
        for i in range(days - 1, -1, -1):
            target_date = base_date - timedelta(days=i)

            # Calculate Acute (Sum of previous 7 days)
            acute = sum(
                daily_trimp.get(
                    (target_date - timedelta(days=j)).strftime("%Y-%m-%d"), 0
                )
                for j in range(7)
            )

            # Calculate TOTAL Chronic (28 days)
            chronic_total = sum(
                daily_trimp.get(
                    (target_date - timedelta(days=j)).strftime("%Y-%m-%d"), 0
                )
                for j in range(28)
            )

            # [FIX BUG] SCALE CHRONIC TO MATCH ACUTE METRICS (AVERAGE OF 1 WEEK)
            chronic_scaled = chronic_total / 4 if chronic_total > 0 else 0

            history["dates"].append(target_date.strftime("%m-%d"))
            history["acute"].append(round(acute, 2))

            # Expose Chronic_Scaled for the chart so it aligns with Acute
            history["chronic"].append(round(chronic_scaled, 2))

            # Gray cloud optimal range centered on Chronic Scaled (ACWR 0.8 - 1.3)
            history["optimal_min"].append(round(chronic_scaled * 0.8, 2))
            history["optimal_max"].append(round(chronic_scaled * 1.3, 2))

        return history
    except Exception as e:
        logger.error(f"[DB_ERROR] get_historical_training_loads Error: {e}")
        return {
            "dates": [],
            "acute": [],
            "chronic": [],
            "optimal_min": [],
            "optimal_max": [],
        }


# ==========================================
# 📅 TRAINING PLAN MANAGEMENT (STATEFUL PLANNING)
# ==========================================


def update_daily_plan(
    user_id: str,
    target_date: str,
    workout_title: str,
    description: str,
    status: str = "Pending",
) -> str:
    """[TOOL] Update or insert a new training plan for a specific date."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO training_plans (user_id, date, workout_title, description, status)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                workout_title=excluded.workout_title,
                description=excluded.description,
                status=excluded.status
        """,
            (str(user_id), target_date, workout_title, description, status),
        )
        conn.commit()
        conn.close()
        return f"✅ Đã cập nhật giáo án ngày {target_date}: {workout_title} ({status})"
    except Exception as e:
        logger.error(f"[DB_ERROR] update_daily_plan Error: {e}")
        return f"❌ Lỗi cập nhật giáo án: {e}"


def get_upcoming_plans(user_id: str, limit_days: int = 7) -> str:
    """Get an overview of training plans from today up to N days into the future."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT date, workout_title, description, status 
            FROM training_plans 
            WHERE user_id = ? AND date >= date('now', 'localtime') 
            ORDER BY date ASC 
            LIMIT ?
        """,
            (str(user_id), limit_days),
        )
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "Chưa có giáo án nào được lên lịch cho những ngày tới."
        plan_text = [
            f"- Ngày {r['date']} [{r['status']}]: {r['workout_title']} - {r['description']}"
            for r in rows
        ]
        return "\n".join(plan_text)
    except Exception as e:
        logger.error(f"[DB_ERROR] get_upcoming_plans Error: {e}")
        return "Lỗi đọc giáo án."


def get_plan_for_date(user_id: str, target_date: str) -> dict:
    """Retrieve detailed training plan for an EXACT specific date."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT workout_title, description, status FROM training_plans WHERE user_id = ? AND date = ?",
            (str(user_id), target_date),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB_ERROR] get_plan_for_date Error: {e}")
        return None


def update_plan_status(user_id: str, target_date: str, status: str):
    """Update the completion status of a training plan."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE training_plans SET status = ? WHERE user_id = ? AND date = ?",
            (status, str(user_id), target_date),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[DB_ERROR] update_plan_status Error: {e}")


def get_weekly_volume(user_id: str, target_date: datetime = None) -> float:
    """
    [GENERIC FUNCTION] Calculate total kilometers run for any given week.
    - user_id: Athlete ID.
    - target_date: Any date falling within the target week. If None, defaults to today.
    """
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))

    if target_date is None:
        target_date = datetime.now(tz)
    elif target_date.tzinfo is None:
        target_date = tz.localize(target_date)

    monday = target_date - timedelta(days=target_date.weekday())
    sunday = monday + timedelta(days=6)

    # [FIX] Chỉ lấy chuỗi ngày (YYYY-MM-DD), bỏ qua giờ phút giây
    start_str = monday.strftime("%Y-%m-%d")
    end_str = sunday.strftime("%Y-%m-%d")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # [FIX] Dùng date(start_date) để SQLite tự chuẩn hóa chữ 'T' của Strava
        cursor.execute(
            """
            SELECT SUM(distance_km) FROM run_activities 
            WHERE user_id = ? AND date(start_date) >= ? AND date(start_date) <= ?
        """,
            (str(user_id), start_str, end_str),
        )

        result = cursor.fetchone()[0]
        return round(result, 2) if result else 0.0
    except Exception as e:
        logger.error(
            f"[DB_ERROR] Error calculating weekly volume for {target_date.strftime('%Y-%m-%d')}: {e}"
        )
        return 0.0
    finally:
        conn.close()


# =====================================================================
# SPRINT A: WEEKLY VOLUME INTELLIGENCE (Weekly Volume Ledger)
# =====================================================================


def get_weekly_target(user_id: str, week_start_date: str) -> dict:
    """
    Retrieve volume target information for a specific week.
    week_start_date MUST be in YYYY-MM-DD format representing a Monday.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT standard_target_km, actual_target_km, ai_reasoning 
            FROM user_weekly_targets 
            WHERE user_id = ? AND week_start_date = ?
        """,
            (str(user_id), week_start_date),
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                "standard_target_km": row["standard_target_km"],
                "actual_target_km": row["actual_target_km"],
                "ai_reasoning": row["ai_reasoning"],
            }
        return None
    except Exception as e:
        logger.error(f"[DB_ERROR] get_weekly_target Error: {e}")
        return None


def upsert_weekly_target(
    user_id: str,
    week_start_date: str,
    standard_target_km: float,
    actual_target_km: float,
    ai_reasoning: str,
) -> bool:
    """
    Update or create a new volume target for a week.
    Allows the AI to autonomously document its adjustment reasoning.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO user_weekly_targets (user_id, week_start_date, standard_target_km, actual_target_km, ai_reasoning, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, week_start_date) DO UPDATE SET
                standard_target_km = excluded.standard_target_km,
                actual_target_km = excluded.actual_target_km,
                ai_reasoning = excluded.ai_reasoning,
                updated_at = CURRENT_TIMESTAMP
        """,
            (
                str(user_id),
                week_start_date,
                standard_target_km,
                actual_target_km,
                ai_reasoning,
            ),
        )
        conn.commit()
        conn.close()
        logger.info(
            f"[DB] Updated weekly target for week {week_start_date} for user {user_id}: Std={standard_target_km}, Actual={actual_target_km}"
        )
        return True
    except Exception as e:
        logger.error(f"[DB_ERROR] upsert_weekly_target Error: {e}")
        return False


# =====================================================================
# PHASE 1: COMPUTED METRICS CRUD (Coach Strava Metrics Upgrade)
# =====================================================================

_COMPUTED_METRIC_COLUMNS = [
    "aerobic_decoupling_pct",
    "cardiac_drift_pct",
    "avg_efficiency_factor",
    "hr_zone_distribution",
    "time_in_hr_zones_sec",
    "avg_cadence_spm",
    "avg_stride_length_m",
    "avg_pace_min_km",
    "pace_variability_cv",
    "positive_split_ratio",
    "time_in_pace_zones_pct",
    "total_elevation_gain_m",
    "grade_adjusted_pace_min_km",
    "avg_power_watts",
    "normalized_power_watts",
    "intensity_factor",
    "training_stress_score",
    "workout_type_detected",
    "interval_reps_count",
    "interval_avg_pace_min_km",
    "interval_pace_consistency_pct",
    "interval_avg_hr_bpm",
    "recovery_hr_quality_bpm",
    "max_velocity_m_s",
    "anaerobic_time_sec",
    "z4_z5_time_pct",
]


def upsert_run_computed_metrics(activity_id: str, user_id: str, metrics: dict) -> bool:
    """
    Upsert pre-computed stream metrics for a run. Creates a placeholder row first
    so metrics are never lost when Harvest hasn't run yet.
    Only updates columns present in metrics dict; other columns are untouched.
    """
    if not metrics:
        return False
    # Filter to only known metric columns to avoid injection
    safe = {k: v for k, v in metrics.items() if k in _COMPUTED_METRIC_COLUMNS}
    if not safe:
        return False
    try:
        with get_db() as conn:
            c = conn.cursor()
            # Ensure placeholder row exists
            c.execute(
                "INSERT OR IGNORE INTO run_activities (activity_id, user_id) VALUES (?, ?)",
                (str(activity_id), str(user_id)),
            )
            set_clause = ", ".join(f"{col} = ?" for col in safe)
            values = list(safe.values()) + [str(activity_id), str(user_id)]
            c.execute(
                f"UPDATE run_activities SET {set_clause} WHERE activity_id = ? AND user_id = ?",  # nosec B608
                values,
            )
        logger.info(
            f"[DB] Upserted computed metrics for activity {activity_id} ({len(safe)} fields)"
        )
        return True
    except Exception as e:
        logger.error(f"[DB_ERROR] upsert_run_computed_metrics: {e}")
        return False


def get_run_metrics_from_db(activity_id: str, user_id: str) -> dict:
    """
    Retrieve computed metrics for a single run.
    Returns dict with only the metric columns (not the base activity columns).
    Returns empty dict if no row found.
    """
    cols = ", ".join(_COMPUTED_METRIC_COLUMNS)
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                f"SELECT {cols} FROM run_activities WHERE activity_id = ? AND user_id = ?",  # nosec B608
                (str(activity_id), str(user_id)),
            )
            row = c.fetchone()
        if not row:
            return {}
        return {col: row[col] for col in _COMPUTED_METRIC_COLUMNS}
    except Exception as e:
        logger.error(f"[DB_ERROR] get_run_metrics_from_db: {e}")
        return {}


def get_metric_trend_data(user_id: str, metric_name: str, days: int = 28) -> List[Dict]:
    """
    Retrieve trend data for a specific computed metric over the last N days.
    Returns list of {start_date, activity_id, name, <metric_name>} dicts, newest first.
    Only returns rows where the metric is not NULL.
    """
    if metric_name not in _COMPUTED_METRIC_COLUMNS:
        logger.warning(f"[DB] Unknown metric for trend: {metric_name}")
        return []
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                f"""
                SELECT activity_id, name, start_date, {metric_name}
                FROM run_activities
                WHERE user_id = ?
                  AND {metric_name} IS NOT NULL
                  AND start_date >= date('now', ?)
                ORDER BY start_date DESC
                """,  # nosec B608
                (str(user_id), f"-{days} days"),
            )
            rows = c.fetchall()
        return [
            {
                "activity_id": r["activity_id"],
                "name": r["name"],
                "start_date": r["start_date"],
                metric_name: r[metric_name],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"[DB_ERROR] get_metric_trend_data({metric_name}): {e}")
        return []


def get_monthly_volume(user_id: str, year: int, month: int) -> dict:
    """
    Return total km, run count, avg pace, and longest run for a calendar month.
    month: 1-12.
    """
    try:
        start = f"{year:04d}-{month:02d}-01"
        # Last day: first day of next month minus 1
        if month == 12:
            end = f"{year + 1:04d}-01-01"
        else:
            end = f"{year:04d}-{month + 1:02d}-01"
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT
                    COUNT(*) AS run_count,
                    ROUND(SUM(distance_km), 2) AS total_km,
                    ROUND(AVG(avg_pace_min_km), 2) AS avg_pace_min_km,
                    ROUND(MAX(distance_km), 2) AS longest_km
                FROM run_activities
                WHERE user_id = ?
                  AND date(start_date) >= ?
                  AND date(start_date) < ?
                """,
                (str(user_id), start, end),
            )
            row = c.fetchone()
        return {
            "year": year,
            "month": month,
            "run_count": row["run_count"] or 0,
            "total_km": row["total_km"] or 0.0,
            "avg_pace_min_km": row["avg_pace_min_km"],
            "longest_km": row["longest_km"] or 0.0,
        }
    except Exception as e:
        logger.error(f"[DB_ERROR] get_monthly_volume({year}-{month}): {e}")
        return {
            "year": year,
            "month": month,
            "run_count": 0,
            "total_km": 0.0,
            "avg_pace_min_km": None,
            "longest_km": 0.0,
        }


def get_yearly_volume(user_id: str, year: int) -> dict:
    """
    Return total km, run count, avg pace, longest run, and monthly breakdown for a year.
    """
    try:
        start = f"{year:04d}-01-01"
        end = f"{year + 1:04d}-01-01"
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT
                    COUNT(*) AS run_count,
                    ROUND(SUM(distance_km), 2) AS total_km,
                    ROUND(AVG(avg_pace_min_km), 2) AS avg_pace_min_km,
                    ROUND(MAX(distance_km), 2) AS longest_km
                FROM run_activities
                WHERE user_id = ?
                  AND date(start_date) >= ?
                  AND date(start_date) < ?
                """,
                (str(user_id), start, end),
            )
            row = c.fetchone()
            # Monthly breakdown
            c.execute(
                """
                SELECT
                    strftime('%m', start_date) AS month,
                    COUNT(*) AS run_count,
                    ROUND(SUM(distance_km), 2) AS total_km
                FROM run_activities
                WHERE user_id = ?
                  AND date(start_date) >= ?
                  AND date(start_date) < ?
                GROUP BY strftime('%m', start_date)
                ORDER BY month
                """,
                (str(user_id), start, end),
            )
            monthly_rows = c.fetchall()
        monthly = [
            {
                "month": int(r["month"]),
                "run_count": r["run_count"],
                "total_km": r["total_km"],
            }
            for r in monthly_rows
        ]
        return {
            "year": year,
            "run_count": row["run_count"] or 0,
            "total_km": row["total_km"] or 0.0,
            "avg_pace_min_km": row["avg_pace_min_km"],
            "longest_km": row["longest_km"] or 0.0,
            "monthly_breakdown": monthly,
        }
    except Exception as e:
        logger.error(f"[DB_ERROR] get_yearly_volume({year}): {e}")
        return {
            "year": year,
            "run_count": 0,
            "total_km": 0.0,
            "avg_pace_min_km": None,
            "longest_km": 0.0,
            "monthly_breakdown": [],
        }


# ==========================================
# 🧠 MULTI-AGENT MEMORY MANAGEMENT (PHASE 8 - MULTI-TENANT)
# ==========================================


def insert_memory(
    user_id: str, domain: str, category: str, fact: str, status: str = "active"
):
    """
    [DATABASE] Inserts or mutates a state in 'core_memory'.
    Allows AI to archive obsolete facts by setting status='inactive'.
    Idempotent behavior: if the exact same state already exists, refresh last_accessed
    instead of inserting a duplicate row.
    """
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            """
            SELECT id FROM core_memory
            WHERE user_id = ? AND domain = ? AND category = ? AND fact = ? AND status = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (str(user_id), domain, category, fact, status),
        )
        existing = c.fetchone()

        if existing:
            c.execute(
                """
                UPDATE core_memory
                SET last_accessed = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                """,
                (existing["id"], str(user_id)),
            )
            conn.commit()
            conn.close()
            logger.info(
                f"[DATABASE] Memory deduplicated: [{category}] {fact[:30]}... (Status: {status})"
            )
            return

        mem_id = str(uuid.uuid4())
        c.execute(
            """
            INSERT INTO core_memory (id, user_id, domain, category, fact, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (mem_id, str(user_id), domain, category, fact, status),
        )
        conn.commit()
        conn.close()
        logger.info(
            f"[DATABASE] Memory committed: [{category}] {fact[:30]}... (Status: {status})"
        )
    except Exception as e:
        logger.error(f"[DATABASE] Error inserting memory: {e}")


def get_all_active_memories(user_id: str) -> list:
    """
    [DATABASE] Fetch the absolute latest active state for each category.
    Uses MAX(rowid) to guarantee exactly 1 unique record per category globally,
    eliminating Timestamp Collisions and Domain Drift.
    """
    conn = get_db_connection()
    memories = []
    try:
        cursor = conn.cursor()

        # [ZONE 1] Global Deduplication Subquery
        cursor.execute(
            """
            SELECT m1.id, m1.domain, m1.category, m1.fact 
            FROM core_memory m1
            INNER JOIN (
                SELECT category, MAX(rowid) as max_rowid
                FROM core_memory
                WHERE user_id = ?
                GROUP BY category
            ) m2 ON m1.rowid = m2.max_rowid
            WHERE m1.status = 'active'
        """,
            (str(user_id),),
        )

        rows = cursor.fetchall()

        memory_ids = []
        for row in rows:
            memories.append(
                {"id": row["id"], "category": row["category"], "fact": row["fact"]}
            )
            memory_ids.append(row["id"])

        # Update last_accessed to prevent decay
        if memory_ids:
            placeholders = ",".join("?" * len(memory_ids))
            cursor.execute(
                f"""
                UPDATE core_memory SET last_accessed = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders}) AND user_id = ?
            """,  # nosec B608
                (*memory_ids, str(user_id)),
            )
            conn.commit()

        return memories
    except Exception as e:
        logger.error(f"[DATABASE] Error global memory fetch: {e}")
        return []
    finally:
        conn.close()


def archive_memory(user_id: str, memory_id: str) -> bool:
    """
    [BRAIN] Mark a memory as 'archived'.
    Secured by user_id.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE core_memory SET status = 'archived' WHERE id = ? AND user_id = ?
        """,
            (memory_id, str(user_id)),
        )
        conn.commit()
        logger.info(f"[DB] Archived memory ID: {memory_id} for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"[DB] Error archiving memory: {e}")
        return False
    finally:
        conn.close()


# ==========================================
# NEWS AGENT — PERSISTENT STATE / MEMORY
# ==========================================


def get_news_state(user_id: str, key: str) -> Optional[str]:
    """Return value for a news agent state key, or None if not set."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM news_agent_state WHERE user_id = ? AND key = ?",
                (str(user_id), key),
            ).fetchone()
        return row["value"] if row else None
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to get news state {key}: {e}")
        return None


def set_news_state(user_id: str, key: str, value: str) -> None:
    """Upsert a news agent state key-value pair."""
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO news_agent_state (user_id, key, value, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(user_id), key, value),
            )
        logger.debug(f"[DB] Set news state {key} for user {user_id}")
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to set news state {key}: {e}")


# ==========================================
# LOG AUDIT CRUD
# ==========================================


def insert_audit_entry(
    user_id: str, severity: str, category: str, message: str, raw_line: str
) -> bool:
    """
    Insert a new audit entry. Returns True if inserted, False if duplicate (UNIQUE constraint).
    raw_line acts as the dedup key per user.
    """
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO audit_entries (user_id, severity, category, message, raw_line)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, severity, category, message, raw_line),
            )
            return conn.execute("SELECT changes()").fetchone()[0] > 0
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to insert audit entry: {e}")
        return False


def get_audit_entries(
    user_id: str,
    status: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 100,
) -> List[Dict]:
    """Fetch audit entries filtered by status/category/severity, ordered newest-first."""
    try:
        with get_db() as conn:
            clauses = ["user_id = ?"]
            params: list = [user_id]
            if status:
                clauses.append("status = ?")
                params.append(status)
            if category:
                clauses.append("category = ?")
                params.append(category)
            if severity:
                clauses.append("severity = ?")
                params.append(severity)
            where = " AND ".join(clauses)
            rows = conn.execute(
                f"SELECT * FROM audit_entries WHERE {where} ORDER BY created_at DESC LIMIT ?",  # nosec B608
                params + [limit],
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to get audit entries: {e}")
        return []


def update_audit_status(entry_id: int, new_status: str) -> bool:
    """Update the status of an audit entry. Returns True on success."""
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE audit_entries SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_status, entry_id),
            )
            return conn.execute("SELECT changes()").fetchone()[0] > 0
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to update audit status: {e}")
        return False


# ==========================================
# ⌚ GARMIN DAILY METRICS
# ==========================================
def upsert_garmin_daily_metrics(user_id: str, date_str: str, metrics: dict) -> None:
    """Upsert Garmin daily wellness metrics for a given date."""
    try:
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS garmin_daily_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    training_readiness_score INTEGER,
                    hrv_status TEXT,
                    sleep_duration_sec INTEGER,
                    training_status TEXT,
                    daily_steps INTEGER,
                    avg_stress_level INTEGER,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, date)
                )
                """)
            conn.execute(
                """
                INSERT INTO garmin_daily_metrics
                    (user_id, date, training_readiness_score, hrv_status, sleep_duration_sec,
                     training_status, daily_steps, avg_stress_level, raw_json, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    training_readiness_score=excluded.training_readiness_score,
                    hrv_status=excluded.hrv_status,
                    sleep_duration_sec=excluded.sleep_duration_sec,
                    training_status=excluded.training_status,
                    daily_steps=excluded.daily_steps,
                    avg_stress_level=excluded.avg_stress_level,
                    raw_json=excluded.raw_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    date_str,
                    metrics.get("training_readiness_score"),
                    metrics.get("hrv_status"),
                    metrics.get("sleep_duration_sec"),
                    metrics.get("training_status"),
                    metrics.get("daily_steps"),
                    metrics.get("avg_stress_level"),
                    str(metrics),
                ),
            )
    except Exception as e:
        logger.error(f"[DB_ERROR] upsert_garmin_daily_metrics failed: {e}")


def get_garmin_daily_metrics(user_id: str, date_str: str) -> Optional[dict]:
    """Retrieve Garmin daily metrics for a given date. Returns None if not found."""
    try:
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT training_readiness_score, hrv_status, sleep_duration_sec,
                       training_status, daily_steps, avg_stress_level
                FROM garmin_daily_metrics
                WHERE user_id=? AND date=?
                """,
                (user_id, date_str),
            ).fetchone()
            if row:
                return dict(row)
            return None
    except Exception as e:
        logger.error(f"[DB_ERROR] get_garmin_daily_metrics failed: {e}")
        return None


# ==========================================
# 🏃 ATHLETE STATE (sick / healthy / injured)
# ==========================================
def get_athlete_state(user_id: str) -> str:
    """Return the latest athlete state for user_id. Defaults to 'healthy' if no record."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT state FROM athlete_state WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            return row["state"] if row else "healthy"
    except Exception as e:
        logger.error(f"[DB_ERROR] get_athlete_state failed: {e}")
        return "healthy"


def set_athlete_state(
    user_id: str, state: str, note: str = "", updated_by: str = "user"
) -> None:
    """Append a new athlete state row (append-only, latest via ORDER BY id DESC)."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO athlete_state (user_id, state, note, updated_at, updated_by) VALUES (?,?,?,CURRENT_TIMESTAMP,?)",
                (user_id, state, note, updated_by),
            )
    except Exception as e:
        logger.error(f"[DB_ERROR] set_athlete_state failed: {e}")


# ==========================================
# 📋 WEEKLY PLANS
# ==========================================
def upsert_weekly_plan(user_id: str, week_start_date: str, ai_output: str) -> None:
    """Insert or replace a weekly plan (status='pending')."""
    try:
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS weekly_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    week_start_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    ai_output TEXT,
                    rejected_reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, week_start_date)
                )
                """)
            conn.execute(
                """
                INSERT INTO weekly_plans (user_id, week_start_date, status, ai_output, created_at, updated_at)
                VALUES (?,?,'pending',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, week_start_date) DO UPDATE SET
                    status='pending', ai_output=excluded.ai_output, updated_at=CURRENT_TIMESTAMP
                """,
                (user_id, week_start_date, ai_output),
            )
    except Exception as e:
        logger.error(f"[DB_ERROR] upsert_weekly_plan failed: {e}")


def get_pending_weekly_plan(user_id: str, week_start_date: str) -> Optional[dict]:
    """Return the pending weekly plan row for the given week. None if not found."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, user_id, week_start_date, status, ai_output FROM weekly_plans WHERE user_id=? AND week_start_date=? AND status='pending'",
                (user_id, week_start_date),
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB_ERROR] get_pending_weekly_plan failed: {e}")
        return None


def update_weekly_plan_status(
    user_id: str, week_start_date: str, status: str, rejected_reason: str = ""
) -> None:
    """Update the status of the weekly plan (pending → accepted/rejected/expired)."""
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE weekly_plans SET status=?, rejected_reason=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND week_start_date=?",
                (status, rejected_reason, user_id, week_start_date),
            )
    except Exception as e:
        logger.error(f"[DB_ERROR] update_weekly_plan_status failed: {e}")


def has_active_plan_this_week(user_id: str, week_start_date: str = "") -> bool:
    """Return True if there is an accepted plan for the given week (defaults to current week Monday)."""
    if not week_start_date:
        from datetime import date, timedelta

        today = date.today()
        week_start_date = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT 1 FROM weekly_plans WHERE user_id=? AND week_start_date=? AND status='accepted' LIMIT 1",
                (user_id, week_start_date),
            ).fetchone()
            return row is not None
    except Exception as e:
        logger.error(f"[DB_ERROR] has_active_plan_this_week failed: {e}")
        return False


# ==========================================
# ⚙️ SETUP SESSIONS (onboarding FSM)
# ==========================================
def get_setup_session(user_id: str) -> Optional[dict]:
    """Return the active setup session row for user_id. None if no session."""
    try:
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS setup_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL UNIQUE,
                    step INTEGER NOT NULL DEFAULT 1,
                    data TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """)
            row = conn.execute(
                "SELECT id, user_id, step, data, status, updated_at FROM setup_sessions WHERE user_id=? AND status='active'",
                (user_id,),
            ).fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB_ERROR] get_setup_session failed: {e}")
        return None


def upsert_setup_session(
    user_id: str, step: int, data: dict, status: str = "active"
) -> None:
    """Create or update the setup session for user_id."""
    import json as _json

    try:
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS setup_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL UNIQUE,
                    step INTEGER NOT NULL DEFAULT 1,
                    data TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """)
            conn.execute(
                """
                INSERT INTO setup_sessions (user_id, step, data, status, created_at, updated_at)
                VALUES (?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    step=excluded.step, data=excluded.data, status=excluded.status,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (user_id, step, _json.dumps(data), status),
            )
    except Exception as e:
        logger.error(f"[DB_ERROR] upsert_setup_session failed: {e}")


def complete_setup_session(user_id: str) -> None:
    """Mark setup session as completed."""
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE setup_sessions SET status='completed', updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                (user_id,),
            )
    except Exception as e:
        logger.error(f"[DB_ERROR] complete_setup_session failed: {e}")


def abandon_stale_setup_sessions(timeout_hours: int = 24) -> int:
    """Mark active sessions with no activity for > timeout_hours as abandoned. Returns count."""
    try:
        with get_db() as conn:
            result = conn.execute(
                """
                UPDATE setup_sessions SET status='abandoned', updated_at=CURRENT_TIMESTAMP
                WHERE status='active'
                  AND updated_at < datetime('now', ? || ' hours')
                """,
                (f"-{timeout_hours}",),
            )
            return result.rowcount
    except Exception as e:
        logger.error(f"[DB_ERROR] abandon_stale_setup_sessions failed: {e}")
        return 0


def get_audit_stats(user_id: str) -> Dict:
    """Return total count and breakdowns by severity and status for a user's audit entries."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT severity, status, COUNT(*) as cnt
                FROM audit_entries
                WHERE user_id = ?
                GROUP BY severity, status
                """,
                (user_id,),
            ).fetchall()
            by_severity: Dict = {}
            by_status: Dict = {}
            total = 0
            for row in rows:
                sev, st, cnt = row["severity"], row["status"], row["cnt"]
                by_severity[sev] = by_severity.get(sev, 0) + cnt
                by_status[st] = by_status.get(st, 0) + cnt
                total += cnt
            return {"total": total, "by_severity": by_severity, "by_status": by_status}
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to get audit stats: {e}")
        return {"total": 0, "by_severity": {}, "by_status": {}}
