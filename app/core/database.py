import sqlite3
import os
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger("AI_COACH")
DB_PATH = "data/os_core.db"  # Đổi tên file để đánh dấu kỷ nguyên mới (Multi-Tenant)

def get_db_connection():
    """Helper function to get a database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Trả về kết quả dưới dạng dict thay vì tuple
    return conn

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
    
    # [NEW] Auto-migrate: Thêm cột gcs_score cho DB cũ nếu chưa có
    try:
        c.execute("ALTER TABLE run_activities ADD COLUMN gcs_score INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass # Bỏ qua nếu cột đã tồn tại

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
# [NEW] Bảng lưu trữ Giáo án Tập luyện (Single Source of Truth)
# [REFACTOR MULTI-TENANT] Bảng lưu trữ Giáo án Tập luyện (Single Source of Truth)
    # Kiểm tra xem bảng cũ có tồn tại và thiếu user_id không để Migrate
    cursor = c.execute("PRAGMA table_info(training_plans)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if columns and 'user_id' not in columns:
        logger.info("[DATABASE] Migrating training_plans table to Multi-Tenant...")
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
        # Chuyển dữ liệu cũ sang (gán tạm cho user mặc định hoặc bỏ trống)
        c.execute("INSERT INTO training_plans (user_id, date, workout_title, description, status) SELECT 'default', date, workout_title, description, status FROM training_plans_old")
        c.execute("DROP TABLE training_plans_old")
    else:
        # Tạo mới chuẩn Multi-Tenant
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
    # [SPRINT A] Thêm bảng Sổ cái Quản lý Khối lượng Tuần (Ledger Pattern)
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_weekly_targets (
            user_id TEXT,
            week_start_date TEXT,       -- Định dạng YYYY-MM-DD (Luôn là ngày Thứ 2)
            standard_target_km REAL,    -- Khối lượng gốc HLV giao
            actual_target_km REAL,      -- Khối lượng AI hoặc User chốt lại
            ai_reasoning TEXT,          -- Lý do AI điều chỉnh
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, week_start_date)
        )
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
    """Lưu bài chạy vào SQLite. Sử dụng UPSERT để đảm bảo Data Integrity giữa luồng Webhook và Harvest."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # [CẬP NHẬT KIẾN TRÚC] Sử dụng ON CONFLICT DO UPDATE (UPSERT)
        # Nếu đã có Placeholder (do Webhook tạo), nó sẽ điền nốt các cột NULL mà KHÔNG làm mất gcs_score
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
    """Cập nhật điểm GCS, tự động tạo placeholder nếu bài chạy chưa được Harvest."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        # [FIX BUG] Tạo trước một dòng giữ chỗ để GCS không bị rơi mất
        c.execute("INSERT OR IGNORE INTO run_activities (activity_id, user_id) VALUES (?, ?)", (str(activity_id), str(user_id)))
        # Sau đó mới cập nhật điểm GCS
        c.execute("UPDATE run_activities SET gcs_score = ? WHERE activity_id = ?", (gcs_score, str(activity_id)))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to update GCS: {e}")

def delete_run_activity(activity_id: str):
    """Xóa bài chạy khỏi Database khi nhận tín hiệu Delete từ Strava."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM run_activities WHERE activity_id = ?", (str(activity_id),))
        conn.commit()
        conn.close()
        logger.info(f"[DB] Đã xóa thành công bài chạy {activity_id} khỏi SQLite.")
    except Exception as e:
        logger.error(f"[DB_ERROR] Lỗi khi xóa bài chạy {activity_id}: {e}")

# ==========================================
# CHAT HISTORY CRUD
# ==========================================
def save_message(user_id: str, role: str, text: str):
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
        c.execute("SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?", 
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
    [UPGRADED] Tính toán cả TRIMP Loads và Weekly Mileage trong 1 lần quét DB.
    Trả về dữ liệu phục vụ ACWR và Luật 15% Volume.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Lấy dữ liệu 28 ngày gần nhất
        cursor.execute('''
            SELECT trimp_score, distance_km, start_date 
            FROM run_activities 
            WHERE user_id = ? AND start_date >= date('now', '-28 days')
        ''', (str(user_id),))
        rows = cursor.fetchall()
        conn.close()

        acute_trimp = 0.0
        chronic_trimp = 0.0
        total_dist_28d = 0.0 # [MỚI]
        
        from datetime import datetime, timedelta
        now = datetime.now()
        seven_days_ago = (now - timedelta(days=7)).isoformat()

        for row in rows:
            trimp = row['trimp_score'] or 0
            dist = row['distance_km'] or 0
            s_date = row['start_date']
            
            # Tính TRIMP
            chronic_trimp += trimp
            if s_date >= seven_days_ago:
                acute_trimp += trimp
            
            # Tính Mileage [MỚI]
            total_dist_28d += dist

        return {
            "acute_load_7d": round(acute_trimp, 1),
            "chronic_load_28d": round(chronic_trimp, 1), # Trung bình tuần
            "avg_weekly_mileage": round(total_dist_28d / 4.0, 1) # [MỚI] Trung bình km/tuần
        }
    except Exception as e:
        logger.error(f"[DB] Lỗi get_training_loads: {e}")
        return {"acute_load_7d": 0, "chronic_load_28d": 0, "avg_weekly_mileage": 0}

def get_recent_runs_log(user_id: str, limit: int = 5) -> str:
    """Get a formatted string of recent runs for the AI prompt."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''
            SELECT start_date, name, distance_km, trimp_score, gcs_score 
            FROM run_activities 
            WHERE user_id = ? 
            ORDER BY start_date DESC LIMIT ?
        ''', (str(user_id), limit))
        rows = c.fetchall()
        conn.close()
        
        if not rows: return "No recent runs found in database."
        
        log_lines = []
        for r in rows:
            date_str = r['start_date'][:10]
            gcs_text = f" | GCS: {r['gcs_score']}%" if r['gcs_score'] is not None else ""
            log_lines.append(f"- {date_str}: {r['name']} | {r['distance_km']}km | TRIMP Load: {r['trimp_score']}{gcs_text}")
        return "\n".join(log_lines)
    except Exception as e:
        logger.error(f"[DB_ERROR] Failed to get recent runs: {e}")
        return "Error loading recent runs."
def get_runs_in_last_days(user_id: str, days: int = 7) -> str:
    """Lấy log chạy bộ giới hạn chuẩn xác trong N ngày qua."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Dùng SQLite date() để trừ lùi ngày
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
            
            # Tính toán Pace (Phút/km) từ thời gian và quãng đường
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
        logger.error(f"[DB] Lỗi lấy log {days} ngày: {e}")
        return "Lỗi đọc dữ liệu."

def get_historical_training_loads(user_id: str, days: int = 30) -> dict:
    """Tính toán chuỗi thời gian Acute, Chronic và Vùng tối ưu (Optimal Range) cho biểu đồ."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        # Lấy data 60 ngày để đủ cơ sở tính Chronic 28 ngày cho 30 ngày qua
        c.execute('''
            SELECT start_date, trimp_score 
            FROM run_activities 
            WHERE user_id = ? AND start_date >= date('now', '-60 days')
        ''', (str(user_id),))
        rows = c.fetchall()
        conn.close()

        # Gom nhóm TRIMP theo từng ngày
        daily_trimp = {}
        for r in rows:
            date_str = r['start_date'][:10]
            daily_trimp[date_str] = daily_trimp.get(date_str, 0) + (r['trimp_score'] or 0)

        history = {"dates": [], "acute": [], "chronic": [], "optimal_min": [], "optimal_max": []}
        base_date = datetime.now().date()

        # Lặp ngược từ 30 ngày trước đến hôm nay
        for i in range(days - 1, -1, -1):
            target_date = base_date - timedelta(days=i)
            
            # Tính Acute (Tổng 7 ngày lùi lại)
            acute = sum(daily_trimp.get((target_date - timedelta(days=j)).strftime('%Y-%m-%d'), 0) for j in range(7))
            
            # Tính Chronic TOÀN BỘ (28 ngày)
            chronic_total = sum(daily_trimp.get((target_date - timedelta(days=j)).strftime('%Y-%m-%d'), 0) for j in range(28))
            
            # [FIX BUG] ÉP CHRONIC VỀ CÙNG HỆ QUY CHIẾU VỚI ACUTE (TRUNG BÌNH 1 TUẦN)
            chronic_scaled = chronic_total / 4 if chronic_total > 0 else 0

            history["dates"].append(target_date.strftime('%m-%d'))
            history["acute"].append(round(acute, 2))
            
            # Đẩy Chronic_Scaled ra biểu đồ để nó nằm ngang hàng với Acute
            history["chronic"].append(round(chronic_scaled, 2)) 
            
            # Đám mây xám lấy Chronic Scaled làm tâm (ACWR 0.8 - 1.3)
            history["optimal_min"].append(round(chronic_scaled * 0.8, 2))
            history["optimal_max"].append(round(chronic_scaled * 1.3, 2))

        return history
    except Exception as e:
        logger.error(f"[DB] Lỗi get_historical_training_loads: {e}")
        return {"dates": [], "acute": [], "chronic": [], "optimal_min": [], "optimal_max": []}

# ==========================================
# 📅 QUẢN LÝ GIÁO ÁN TẬP LUYỆN (STATEFUL PLANNING)
# ==========================================

def update_daily_plan(user_id: str, target_date: str, workout_title: str, description: str, status: str = "Pending") -> str:
    """[TOOL] Cập nhật hoặc thêm mới giáo án cho một ngày cụ thể."""
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
        logger.error(f"[DB] Lỗi update_daily_plan: {e}")
        return f"❌ Lỗi cập nhật giáo án: {e}"

def get_upcoming_plans(user_id: str, limit_days: int = 7) -> str:
    """Lấy tổng quan giáo án từ hôm nay đến N ngày tới."""
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
        logger.error(f"[DB] Lỗi get_upcoming_plans: {e}")
        return "Lỗi đọc giáo án."

def get_plan_for_date(user_id: str, target_date: str) -> dict:
    """Lấy chi tiết giáo án của ĐÚNG một ngày cụ thể."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT workout_title, description, status FROM training_plans WHERE user_id = ? AND date = ?', (str(user_id), target_date))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Lỗi get_plan_for_date: {e}")
        return None

def update_plan_status(user_id: str, target_date: str, status: str):
    """Cập nhật trạng thái hoàn thành."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE training_plans SET status = ? WHERE user_id = ? AND date = ?', (status, str(user_id), target_date))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[DB] Lỗi update_plan_status: {e}")

def get_weekly_volume(user_id: str, target_date: datetime = None) -> float:
    """
    [GENERIC FUNCTION] Tính tổng km đã chạy của một tuần bất kỳ.
    - user_id: ID của VĐV.
    - target_date: Một ngày bất kỳ trong tuần cần tính. Nếu None, mặc định là ngày hôm nay.
    """
    tz = pytz.timezone(os.getenv("TZ", "Asia/Ho_Chi_Minh"))
    
    if target_date is None:
        target_date = datetime.now(tz)
    elif target_date.tzinfo is None: # Đảm bảo có múi giờ
        target_date = tz.localize(target_date)
        
    # Xác định Thứ 2 (Start) và Chủ Nhật (End) của tuần chứa target_date
    monday = target_date - timedelta(days=target_date.weekday())
    sunday = monday + timedelta(days=6)
    
    # Ép kiểu string để query SQLite (từ 00:00:00 Thứ 2 đến 23:59:59 Chủ Nhật)
    start_str = monday.strftime('%Y-%m-%d 00:00:00')
    end_str = sunday.strftime('%Y-%m-%d 23:59:59')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT SUM(distance_km) FROM run_activities 
            WHERE user_id = ? AND start_date >= ? AND start_date <= ?
        ''', (str(user_id), start_str, end_str))
        
        result = cursor.fetchone()[0]
        return round(result, 2) if result else 0.0
    except Exception as e:
        logger.error(f"[DB] Lỗi tính volume tuần cho {target_date.strftime('%Y-%m-%d')}: {e}")
        return 0.0
    finally:
        conn.close()
# =====================================================================
# SPRINT A: WEEKLY VOLUME INTELLIGENCE (Quản lý Khối lượng Tuần)
# =====================================================================

def get_weekly_target(user_id: str, week_start_date: str) -> dict:
    """
    Lấy thông tin mục tiêu khối lượng của một tuần cụ thể.
    week_start_date phải là định dạng YYYY-MM-DD của ngày Thứ 2.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
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
                "standard_target_km": row[0],
                "actual_target_km": row[1],
                "ai_reasoning": row[2]
            }
        return None
    except Exception as e:
        logger.error(f"[DB] Lỗi get_weekly_target: {e}")
        return None

def upsert_weekly_target(user_id: str, week_start_date: str, standard_target_km: float, actual_target_km: float, ai_reasoning: str) -> bool:
    """
    Cập nhật hoặc tạo mới mục tiêu khối lượng của một tuần.
    Hỗ trợ AI tự động lưu lại quyết định điều chỉnh của nó.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
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
        logger.info(f"[DB] Đã cập nhật mục tiêu tuần {week_start_date} cho {user_id}: Std={standard_target_km}, Actual={actual_target_km}")
        return True
    except Exception as e:
        logger.error(f"[DB] Lỗi upsert_weekly_target: {e}")
        return False