
#### 🏛️ TIER 1: RELATIONAL DATABASE (Dữ liệu Cấu trúc & Tính toán)
[cite_start]*(Các bảng users, run_activities, chat_history, training_plans giữ nguyên như v2.7.1)* [cite: 246, 248, 250, 251]

**5. [cite_start]Table: `user_weekly_targets` (Sổ cái Quản lý Khối lượng Tuần)** [cite: 273]
Đóng vai trò là **"Single Source of Truth" (Nguồn sự thật duy nhất)** để tránh việc AI bị "ảo giác" (hallucinate) khi đọc lại lịch sử chat cũ.
* [cite_start]`user_id` (TEXT) [cite: 274]
* [cite_start]`week_start_date` (TEXT) - *Định dạng YYYY-MM-DD (Luôn là ngày Thứ 2).* [cite: 274]
* [cite_start]`standard_target_km` (REAL) - *Khối lượng gốc HLV giao.* [cite: 274]
* [cite_start]`actual_target_km` (REAL) - *Khối lượng AI hoặc User chốt lại sau khi đàm phán.* [cite: 274]
* [cite_start]`ai_reasoning` (TEXT) - *Lý do AI quyết định điều chỉnh (Lưu lại chuỗi tư duy).* [cite: 274]
* [cite_start]`updated_at` (TIMESTAMP) [cite: 275]

**6. Table: `news_sent_articles` (Dedup Tin Tức)**
Lưu các bài báo đã gửi để tránh gửi lại cùng một bài trong cả buổi sáng lẫn chiều.
* `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
* `user_id` (TEXT NOT NULL) — multi-tenant required
* `article_link` (TEXT NOT NULL) — URL bài báo dùng để dedup
* `session` (TEXT NOT NULL) — `"morning"` hoặc `"afternoon"`
* `sent_at` (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
* Index: `(user_id, article_link)` cho fast dedup lookup

---

#### ⚙️ TIER 3: SYSTEM CONFIGURATION (Trạng thái & Cấu hình App)

**Công nghệ:** JSON File (`data/config.json`)
**Mục đích:** Lưu trữ cấu hình hệ thống và đặc biệt là **Kiến trúc Prompt 4 Trụ cột (4-Pillar Prompt Architecture)** phục vụ xuất bản Đa kênh.

**Cấu trúc JSON Schema:**
* **`task_description`**: Nhiệm vụ tối cao của AI (Domain Logic). [cite_start]Ví dụ: Đánh giá Pace, HR, GCS... [cite: 101]
* **`analysis_requirements`**: Bộ tiêu chí phân tích chuyên sâu (Execution, Mechanics, Physiology). [cite_start]Hướng dẫn AI *cần suy luận những gì*. [cite: 102, 103]
* **`report_structure`**: Khung xương báo cáo (Presentation Logic). [cite_start]Định nghĩa các mục tiêu đề, vị trí điền dữ liệu, Emoji bắt buộc. [cite: 104, 105]
* **`output_format`**: Kỷ luật hiển thị nền tảng (Platform Rules). [cite_start]Ví dụ: Không dùng Markdown, viết hoa từ khóa (Dành cho Strava). [cite: 106, 107]

> 💡 **Architect's Note:** Việc tách biệt `analysis_requirements` và `report_structure` cho phép Admin thay đổi giao diện báo cáo (UI) mà không làm ảnh hưởng đến trí thông minh phân tích (AI Reasoning) của hệ thống.