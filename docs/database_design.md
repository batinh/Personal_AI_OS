### 🗄️ DATABASE ARCHITECTURE DESIGN (v2.7.0 - Multi-Tenant Ready)

**Triết lý thiết kế (Design Philosophy):**

* [cite_start]**Zero-Heavy:** Sử dụng SQLite và file-based DB, không yêu cầu cài đặt Docker container DB riêng biệt [cite: 232-233].
* [cite_start]**Multi-Tenant:** Tất cả các bảng và bản ghi (records) đều phải có `user_id` để cô lập dữ liệu giữa các Runner[cite: 233].
* [cite_start]**Separation of Concerns (Phân tách trách nhiệm):** Phân chia rõ ràng giữa Dữ liệu cấu trúc (Toán học/Logic), Dữ liệu phi cấu trúc (Ngữ nghĩa/AI) và Cấu hình hệ thống[cite: 234].

---

#### 🏛️ TIER 1: RELATIONAL DATABASE (Dữ liệu Cấu trúc & Tính toán)

**Công nghệ:** SQLite (`data/os_core.db`)
[cite_start]**Mục đích:** Lưu trữ hồ sơ người dùng, các chỉ số toán học chính xác (TRIMP, ACWR), lịch sử hoạt động và Giáo án tập luyện (Stateful Planning)[cite: 235].

**1. Table: `users` (Hồ sơ Vận động viên)**
[cite_start]Lưu trữ hồ sơ cá nhân hóa để mỗi Runner có một chỉ số sinh lý riêng[cite: 236].
* `user_id` (TEXT, Primary Key) - *Telegram Chat ID.*
* `name` (TEXT)
* `max_hr` (INTEGER)
* `rest_hr` (INTEGER)
* `race_date` (TEXT) - *Ngày thi đấu mục tiêu (YYYY-MM-DD).*
* `current_goal` (TEXT)
* `is_active` (BOOLEAN) - *Trạng thái hoạt động.*

**2. Table: `run_activities` (Lịch sử Strava)**
* `activity_id` (TEXT, Primary Key) - *ID bài chạy từ Strava.*
* `user_id` (TEXT, Foreign Key -> `users.user_id`)
* `name` (TEXT)
* `start_date` (DATETIME)
* `distance_km` (REAL)
* `moving_time_min` (REAL)
* `avg_hr` (INTEGER)
* `max_hr` (INTEGER)
* `suffer_score` (INTEGER)
* `trimp_score` (REAL)
* `gcs_score` (INTEGER DEFAULT NULL) - *Điểm tự tin hoàn thành mục tiêu. [cite_start]Sử dụng cơ chế Placeholder để tránh Race Condition khi Webhook và Cronjob chạy song song.* [cite: 251-252]

**3. Table: `chat_history` (Lịch sử giao tiếp)**
* `id` (INTEGER, Primary Key, Auto Increment)
* `user_id` (TEXT, Foreign Key -> `users.user_id`)
* `role` (TEXT) - *'user' hoặc 'model'.*
* `content` (TEXT)
* `timestamp` (DATETIME)

**4. Table: `training_plans` (Quản lý Kế hoạch - Single Source of Truth)** [NEW]
[cite_start]Nơi AI tự chủ quyết định và lưu trữ giáo án tập luyện, tránh hiện tượng "nhớ nhầm" qua RAG [cite: 253-254].
* `date` (TEXT, Primary Key) - *Định dạng YYYY-MM-DD.*
* `workout_title` (TEXT) - *Tên bài tập ngắn gọn.*
* `description` (TEXT) - *Chi tiết bài tập hoặc lời dặn dò.*
* `status` (TEXT) - *Mặc định 'Pending'. Chuyển thành 'Completed' khi VĐV chạy xong bài ngày hôm đó.*

---

#### 🧠 TIER 2: VECTOR DATABASE (Trí nhớ Dài hạn & Ngữ nghĩa)

**Công nghệ:** ChromaDB (`data/chroma_db`)
[cite_start]**Mục đích:** Lưu trữ Embeddings để AI tìm kiếm ngữ cảnh, so sánh chéo các bài chạy và nhớ lại lời khuyên cũ[cite: 239].

**Collection: `os_local_memory`**
[cite_start]Sử dụng mô hình nhúng (Embedding) cục bộ chạy hoàn toàn bằng CPU của máy chủ, không phụ thuộc vào API bên ngoài [cite: 308-309].

* [cite_start]**`id`**: Unique ID (Ví dụ: `run_12345` hoặc `chat_9876`)[cite: 241].
* **`document`**: Semantic Text (Văn bản chứa ngữ nghĩa).
* **`metadata`**:
```json
{
    "user_id": "telegram_id_cua_tinh",  // Bắt buộc để phân tách Multi-Tenant
    "domain": "coach",                  // Phân loại: coach, finance, life
    "type": "run_analysis",             // Phân loại chi tiết: run_analysis, chat_advice, daily_standup
    "date": "2026-02-20"
}

```

---

#### ⚙️ TIER 3: SYSTEM CONFIGURATION (Trạng thái & Cấu hình App)

**Công nghệ:** JSON File (`config.json` & `.env`)


**Mục đích:** Chỉ lưu trữ các cấu hình mang tính chất **hệ thống (System-wide)**, không phụ thuộc vào cá nhân VĐV nào .

* 
**`scheduler`**: Khung giờ chạy auto-sync, gửi morning briefing, sao lưu dữ liệu.


* 
**`email_config`**: SMTP server, Port, Enable/Disable.


* 
**`system_instruction` & `model_name**`: Quản lý phiên bản AI đang được sử dụng trực tiếp qua giao diện Admin Web.
