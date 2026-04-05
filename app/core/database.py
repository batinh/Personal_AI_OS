import json
import sqlite3
import os
import uuid
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger("AI_COACH")

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
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            name TEXT,
            max_hr INTEGER DEFAULT 185,
            rest_hr INTEGER DEFAULT 55,
            race_date TEXT,
            current_goal TEXT,
            is_active BOOLEAN DEFAULT 1
        )
    ''')

    # 2. Table: run_activities
    c.execute('''
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
    ''')
    
    # [NEW] Auto-migrate: Add gcs_score column to legacy DB if not exists
    try:
        c.execute("ALTER TABLE run_activities ADD COLUMN gcs_score INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass  # Ignore if column already exists

    # 2b. Table: run_activity_raw (full Strava payload per run for analysis, recall, tools)
    c.execute('''
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
    ''')
    try:
        c.execute("ALTER TABLE run_activity_raw ADD COLUMN stream_file_path TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # 3. Table: chat_history (Upgraded)
    c.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')

    # 4. Table: training_plans (Single Source of Truth)
    # [REFACTOR MULTI-TENANT] Check if legacy table exists and lacks user_id to Migrate
    cursor = c.execute("PRAGMA table_info(training_plans)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if columns and 'user_id' not in columns:
        logger.info("[DATABASE] Migrating training_plans table to Multi-Tenant architecture...")
        c.execute("ALTER TABLE training_plans RENAME TO training_plans_old")
        c.execute('''
            CREATE TABLE training_plans (
                user_id TEXT,
                date TEXT,
                workout_title TEXT,
                description TEXT,
                status TEXT DEFAULT 'Pending',
                PRIMARY KEY (user_id, date)
            )
        ''')
        # Migrate old data (assign temporarily to 'default' user)
        c.execute("INSERT INTO training_plans (user_id, date, workout_title, description, status) SELECT 'default', date, workout_title, description, status FROM training_plans_old")
        c.execute("DROP TABLE training_plans_old")
    else:
        # Create standard Multi-Tenant table
        c.execute('''
            CREATE TABLE IF NOT EXISTS training_plans (
                user_id TEXT,
                date TEXT,
                workout_title TEXT,
                description TEXT,
                status TEXT DEFAULT 'Pending',
                PRIMARY KEY (user_id, date)
            )
        ''')

    # 5. [SPRINT A] Table: user_weekly_targets (Ledger Pattern for Weekly Volume)
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_weekly_targets (
            user_id TEXT,
            week_start_date TEXT,       -- Format YYYY-MM-DD (Always a Monday)
            standard_target_km REAL,    -- Original volume assigned by Coach
            actual_target_km REAL,      -- Negotiated volume set by AI or User
            ai_reasoning TEXT,          -- Reason for AI adjustment
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, week_start_date)
        )
    ''')
# [PHASE 8] Multi-Agent Core Memory Table (Multi-Tenant Ready)
    c.execute('''
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
    ''')
    
    # [DATABASE MIGRATION] Zero-downtime column injection for existing DB
    try:
        cursor_check = c.execute("PRAGMA table_info(core_memory)")
        columns = [col[1] for col in cursor_check.fetchall()]
        if 'user_id' not in columns:
            logger.info("[DATABASE] Migrating core_memory to support Multi-Tenant (adding user_id)...")
            c.execute("ALTER TABLE core_memory ADD COLUMN user_id TEXT DEFAULT 'default_user'")
    except Exception as e:
        logger.error(f"[DATABASE] Migration error on core_memory: {e}")

    # [NEWS AGENT] Table: news_sent_articles (dedup — prevents same article sent morning + afternoon)
    c.execute('''
        CREATE TABLE IF NOT EXISTS news_sent_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            article_link TEXT NOT NULL,
            session TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_news_sent_user_link
        ON news_sent_articles (user_id, article_link)
    ''')

    conn.commit()
    conn.close()
    logger.info("[DATABASE] Relational DB initialized successfully (Multi-Tenant Ready).")

# ==========================================
# USERS CRUD
# ==========================================
def upsert_user(user_id: str, name: str = "Runner", max_hr: int = 185, rest_hr: int = 55):
    """Insert a new user or update existing user."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''
            INSERT INTO users (user_id, name, max_hr, rest_hr)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                max_hr=excluded.max_hr,
                rest_hr=excluded.rest_hr
        ''', (str(user_id), name, max_hr, rest_hr))
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
        c.execute('''
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
        ''', (
            str(activity_data['activity_id']),
            str(user_id),
            activity_data.get('name'),
            activity_data.get('start_date'),
            activity_data.get('distance_km'),
            activity_data.get('moving_time_min'),
            activity_data.get('avg_hr'),
            activity_data.get('max_hr'),
            activity_data.get('suffer_score'),
            activity_data.get('trimp_score')
        ))
        
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
        c.execute("INSERT OR IGNORE INTO run_activities (activity_id, user_id) VALUES (?, ?)", (str(activity_id), str(user_id)))
        # Then update the GCS score
        c.execute("UPDATE run_activities SET gcs_score = ? WHERE activity_id = ?", (gcs_score, str(activity_id)))
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
        c.execute("SELECT stream_file_path FROM run_activity_raw WHERE activity_id = ?", (str(activity_id),))
        row = c.fetchone()
        stream_path = row["stream_file_path"] if row and row.get("stream_file_path") else None
        c.execute("DELETE FROM run_activity_raw WHERE activity_id = ?", (str(activity_id),))
        c.execute("DELETE FROM run_activities WHERE activity_id = ?", (str(activity_id),))
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
                logger.warning(f"[DB] Could not delete stream file for {activity_id}: {e}")
        logger.info(f"[DB] Successfully deleted run activity {activity_id} from SQLite.")
    except Exception as e:
        logger.error(f"[DB_ERROR] Error deleting run activity {activity_id}: {e}")


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
        c.execute('''
            INSERT INTO run_activity_raw (activity_id, user_id, activity_name, full_meta, stream_csv, stream_file_path, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(activity_id) DO UPDATE SET
                user_id=excluded.user_id,
                activity_name=excluded.activity_name,
                full_meta=excluded.full_meta,
                stream_csv=excluded.stream_csv,
                stream_file_path=excluded.stream_file_path,
                fetched_at=CURRENT_TIMESTAMP
        ''', (str(activity_id), str(user_id), activity_name or "", meta_json, stream_csv or "", stream_file_path or ""))
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
        out["stream_csv"] = row["stream_csv"] if row["stream_csv"] else ""  # legacy; prefer loading from file
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
        c.execute("INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)", 
                  (str(user_id), role, text))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[DB_ERROR] Save Message Error: {e}")

def load_history_for_gemini(user_id: str, limit: int = 20) -> List[Dict]:
    """Load conversation history for the AI Agent."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY timestamp DESC, rowid DESC LIMIT ?", 
                  (str(user_id), limit))
        rows = c.fetchall()
        conn.close()
        
        history = []
        for row in reversed(rows):
            history.append({"role": row['role'], "parts": [row['content']]})
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
        cursor.execute('''
            SELECT trimp_score, distance_km, start_date 
            FROM run_activities 
            WHERE user_id = ? AND start_date >= date('now', '-28 days')
        ''', (str(user_id),))
        rows = cursor.fetchall()
        conn.close()

        acute_trimp = 0.0
        chronic_trimp = 0.0
        total_dist_28d = 0.0 # [NEW]
        
        from datetime import datetime, timedelta
        now = datetime.now()
        seven_days_ago = (now - timedelta(days=7)).isoformat()

        for row in rows:
            trimp = row['trimp_score'] or 0
            dist = row['distance_km'] or 0
            s_date = row['start_date']
            
            # Calculate TRIMP
            chronic_trimp += trimp
            if s_date >= seven_days_ago:
                acute_trimp += trimp
            
            # Calculate Mileage [NEW]
            total_dist_28d += dist

        return {
            "acute_load_7d": round(acute_trimp, 1),
            "chronic_load_28d": round(chronic_trimp, 1), # 28-day total
            "avg_weekly_mileage": round(total_dist_28d / 4.0, 1) # [NEW] Average km per week
        }
    except Exception as e:
        logger.error(f"[DB_ERROR] get_training_loads Error: {e}")
        return {"acute_load_7d": 0, "chronic_load_28d": 0, "avg_weekly_mileage": 0}

def get_recent_runs_log(user_id: str, limit: int = 10) -> str:
    """Get a formatted string of recent runs for the AI prompt."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''
            SELECT start_date, name, distance_km, trimp_score, gcs_score 
            FROM run_activities 
            WHERE user_id = ? ORDER BY start_date DESC LIMIT ?
        ''', (str(user_id), limit))
        rows = c.fetchall()
        conn.close()
        
        if not rows: return "No recent runs found in database."
        log_lines = []
        for r in rows:
            date_str = r['start_date'][:10] if r['start_date'] else "N/A"
            gcs_text = f" | GCS: {r['gcs_score']}%" if r['gcs_score'] is not None else ""
            log_lines.append(f"- {date_str}: {r['name']} | {r['distance_km']}km | TRIMP Load: {r['trimp_score']}{gcs_text}")
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
        cursor.execute(query, (str(user_id), f'-{days} days'))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return f"Không có bài chạy nào trong {days} ngày qua."
        log_lines = []
        for r in rows:
            date_str = r['start_date'][:10]
            
            # Calculate Pace (Min/km) from time and distance
            dist = r['distance_km']
            mins = r['moving_time_min']
            
            if dist > 0:
                pace_min = int(mins // dist)
                pace_sec = int(((mins / dist) % 1) * 60)
                pace_str = f"{pace_min}:{pace_sec:02d}"
            else:
                pace_str = "0:00"
                
            log_lines.append(f"- {date_str}: {r['name']} ({r['distance_km']}km, Pace {pace_str}, HR {r['avg_hr']}, TRIMP {r['trimp_score']})")
            
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
        c.execute('''
            SELECT start_date, trimp_score 
            FROM run_activities 
            WHERE user_id = ? AND start_date >= date('now', '-60 days')
        ''', (str(user_id),))
        rows = c.fetchall()
        conn.close()

        # Group TRIMP by day
        daily_trimp = {}
        for r in rows:
            date_str = r['start_date'][:10]
            daily_trimp[date_str] = daily_trimp.get(date_str, 0) + (r['trimp_score'] or 0)

        history = {"dates": [], "acute": [], "chronic": [], "optimal_min": [], "optimal_max": []}
        base_date = datetime.now().date()

        # Iterate backward from N days ago to today
        for i in range(days - 1, -1, -1):
            target_date = base_date - timedelta(days=i)
            
            # Calculate Acute (Sum of previous 7 days)
            acute = sum(daily_trimp.get((target_date - timedelta(days=j)).strftime('%Y-%m-%d'), 0) for j in range(7))
            
            # Calculate TOTAL Chronic (28 days)
            chronic_total = sum(daily_trimp.get((target_date - timedelta(days=j)).strftime('%Y-%m-%d'), 0) for j in range(28))
            
            # [FIX BUG] SCALE CHRONIC TO MATCH ACUTE METRICS (AVERAGE OF 1 WEEK)
            chronic_scaled = chronic_total / 4 if chronic_total > 0 else 0

            history["dates"].append(target_date.strftime('%m-%d'))
            history["acute"].append(round(acute, 2))
            
            # Expose Chronic_Scaled for the chart so it aligns with Acute
            history["chronic"].append(round(chronic_scaled, 2)) 
            
            # Gray cloud optimal range centered on Chronic Scaled (ACWR 0.8 - 1.3)
            history["optimal_min"].append(round(chronic_scaled * 0.8, 2))
            history["optimal_max"].append(round(chronic_scaled * 1.3, 2))

        return history
    except Exception as e:
        logger.error(f"[DB_ERROR] get_historical_training_loads Error: {e}")
        return {"dates": [], "acute": [], "chronic": [], "optimal_min": [], "optimal_max": []}

# ==========================================
# 📅 TRAINING PLAN MANAGEMENT (STATEFUL PLANNING)
# ==========================================

def update_daily_plan(user_id: str, target_date: str, workout_title: str, description: str, status: str = "Pending") -> str:
    """[TOOL] Update or insert a new training plan for a specific date."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO training_plans (user_id, date, workout_title, description, status)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                workout_title=excluded.workout_title,
                description=excluded.description,
                status=excluded.status
        ''', (str(user_id), target_date, workout_title, description, status))
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
        cursor.execute('''
            SELECT date, workout_title, description, status 
            FROM training_plans 
            WHERE user_id = ? AND date >= date('now', 'localtime') 
            ORDER BY date ASC 
            LIMIT ?
        ''', (str(user_id), limit_days))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows: return "Chưa có giáo án nào được lên lịch cho những ngày tới."
        plan_text = [f"- Ngày {r['date']} [{r['status']}]: {r['workout_title']} - {r['description']}" for r in rows]
        return "\n".join(plan_text)
    except Exception as e:
        logger.error(f"[DB_ERROR] get_upcoming_plans Error: {e}")
        return "Lỗi đọc giáo án."

def get_plan_for_date(user_id: str, target_date: str) -> dict:
    """Retrieve detailed training plan for an EXACT specific date."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT workout_title, description, status FROM training_plans WHERE user_id = ? AND date = ?', (str(user_id), target_date))
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
        cursor.execute('UPDATE training_plans SET status = ? WHERE user_id = ? AND date = ?', (status, str(user_id), target_date))
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
    start_str = monday.strftime('%Y-%m-%d')
    end_str = sunday.strftime('%Y-%m-%d')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # [FIX] Dùng date(start_date) để SQLite tự chuẩn hóa chữ 'T' của Strava
        cursor.execute('''
            SELECT SUM(distance_km) FROM run_activities 
            WHERE user_id = ? AND date(start_date) >= ? AND date(start_date) <= ?
        ''', (str(user_id), start_str, end_str))
        
        result = cursor.fetchone()[0]
        return round(result, 2) if result else 0.0
    except Exception as e:
        logger.error(f"[DB_ERROR] Error calculating weekly volume for {target_date.strftime('%Y-%m-%d')}: {e}")
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
        cursor.execute('''
            SELECT standard_target_km, actual_target_km, ai_reasoning 
            FROM user_weekly_targets 
            WHERE user_id = ? AND week_start_date = ?
        ''', (str(user_id), week_start_date))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "standard_target_km": row["standard_target_km"],
                "actual_target_km": row["actual_target_km"],
                "ai_reasoning": row["ai_reasoning"]
            }
        return None
    except Exception as e:
        logger.error(f"[DB_ERROR] get_weekly_target Error: {e}")
        return None

def upsert_weekly_target(user_id: str, week_start_date: str, standard_target_km: float, actual_target_km: float, ai_reasoning: str) -> bool:
    """
    Update or create a new volume target for a week.
    Allows the AI to autonomously document its adjustment reasoning.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO user_weekly_targets (user_id, week_start_date, standard_target_km, actual_target_km, ai_reasoning, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, week_start_date) DO UPDATE SET
                standard_target_km = excluded.standard_target_km,
                actual_target_km = excluded.actual_target_km,
                ai_reasoning = excluded.ai_reasoning,
                updated_at = CURRENT_TIMESTAMP
        ''', (str(user_id), week_start_date, standard_target_km, actual_target_km, ai_reasoning))
        conn.commit()
        conn.close()
        logger.info(f"[DB] Updated weekly target for week {week_start_date} for user {user_id}: Std={standard_target_km}, Actual={actual_target_km}")
        return True
    except Exception as e:
        logger.error(f"[DB_ERROR] upsert_weekly_target Error: {e}")
        return False
        
# ==========================================
# 🧠 MULTI-AGENT MEMORY MANAGEMENT (PHASE 8 - MULTI-TENANT)
# ==========================================

def insert_memory(user_id: str, domain: str, category: str, fact: str, status: str = 'active'):
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
            '''
            SELECT id FROM core_memory
            WHERE user_id = ? AND domain = ? AND category = ? AND fact = ? AND status = ?
            ORDER BY rowid DESC
            LIMIT 1
            ''',
            (str(user_id), domain, category, fact, status),
        )
        existing = c.fetchone()

        if existing:
            c.execute(
                '''
                UPDATE core_memory
                SET last_accessed = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                ''',
                (existing["id"], str(user_id)),
            )
            conn.commit()
            conn.close()
            logger.info(f"[DATABASE] Memory deduplicated: [{category}] {fact[:30]}... (Status: {status})")
            return

        mem_id = str(uuid.uuid4())
        c.execute('''
            INSERT INTO core_memory (id, user_id, domain, category, fact, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (mem_id, str(user_id), domain, category, fact, status))
        conn.commit()
        conn.close()
        logger.info(f"[DATABASE] Memory committed: [{category}] {fact[:30]}... (Status: {status})")
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
        cursor.execute('''
            SELECT m1.id, m1.domain, m1.category, m1.fact 
            FROM core_memory m1
            INNER JOIN (
                SELECT category, MAX(rowid) as max_rowid
                FROM core_memory
                WHERE user_id = ?
                GROUP BY category
            ) m2 ON m1.rowid = m2.max_rowid
            WHERE m1.status = 'active'
        ''', (str(user_id),))
        
        rows = cursor.fetchall()
        
        memory_ids = []
        for row in rows:
            memories.append({
                "id": row["id"],
                "category": row["category"],
                "fact": row["fact"]
            })
            memory_ids.append(row["id"])
            
        # Update last_accessed to prevent decay
        if memory_ids:
            placeholders = ','.join('?' * len(memory_ids))
            cursor.execute(f'''
                UPDATE core_memory SET last_accessed = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders}) AND user_id = ?
            ''', (*memory_ids, str(user_id)))
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
        cursor.execute('''
            UPDATE core_memory SET status = 'archived' WHERE id = ? AND user_id = ?
        ''', (memory_id, str(user_id)))
        conn.commit()
        logger.info(f"[DB] Archived memory ID: {memory_id} for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"[DB] Error archiving memory: {e}")
        return False
    finally:
        conn.close()


# ==========================================
# NEWS AGENT — DEDUPLICATION
# ==========================================

def save_sent_articles(user_id: str, links: list, session: str) -> None:
    """
    Record article links that were sent in a news briefing session.
    Used to prevent the same article appearing in both morning and afternoon briefings.
    """
    if not links:
        return
    with get_db() as conn:
        conn.executemany(
            "INSERT INTO news_sent_articles (user_id, article_link, session) VALUES (?, ?, ?)",
            [(str(user_id), link, session) for link in links]
        )
    logger.info(f"[DB] Saved {len(links)} sent article links for user {user_id} ({session})")


def get_recent_sent_links(user_id: str, hours: int = 24) -> set:
    """
    Return set of article links already sent to this user in the last N hours.
    Used for deduplication between morning and afternoon briefings.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT article_link FROM news_sent_articles
            WHERE user_id = ?
              AND sent_at >= datetime('now', ? || ' hours')
            """,
            (str(user_id), f"-{hours}")
        ).fetchall()
    return {row["article_link"] for row in rows}
