# 🚀 TỔNG HỢP PRAGMATIC REVIEW CHECKLIST

*(Dùng trước mỗi lần Commit Code hoặc Release Phiên bản mới)*

### 🏗️ 1. Kiến trúc & Hệ thống (Software / System Architect)

* [ ] **Tách biệt Logic (SoC):** Thay đổi này có tách bạch rõ ràng giữa xử lý logic (Data/AI) và hiển thị (Format Telegram/Strava) không?
* [ ] **Kiểm soát Tác động chéo (Side-effect Check):** Việc update code ở module này có vô tình làm gãy luồng của Webhook Strava, Cronjob, hay Giao diện Admin không?
* [ ] **Bắt lỗi & Dự phòng (Fallback):** Đã bọc `try-except` cho các external call (Gemini API, Telegram API) chưa? Hệ thống có sống sót nếu API trả về 429 hoặc 500 không?

### 💻 2. Chất lượng Code & Bảo mật (Clean Code Standards)

* [ ] **Tuân thủ Ngôn ngữ (English Only):** Tên hàm, biến, class và docstrings đã được viết 100% bằng Tiếng Anh chưa? (Tuân thủ `Rules.txt`).
* [ ] **Quản lý Secret:** Tuyệt đối không hardcode API Key, Token, Password vào source code. Tất cả đã nằm trong `.env` chưa?
* [ ] **Tinh gọn (KISS/YAGNI):** Code có đang bị phức tạp hóa quá mức cần thiết không? (Chỉ code những gì phục vụ hiện tại, không over-engineering cho 2 năm tới).

### 🤖 3. AI & Prompt Engineering (AI / Prompt Expert)

* [ ] **Kích hoạt Ý định (Intent-Driven):** Prompt đã có "mệnh lệnh chốt hạ" rõ ràng để ép AI gọi Tool chưa? (Tránh AI chỉ tóm tắt mà không hành động).
* [ ] **Kiến trúc 4 Trụ cột:** Prompt mới có tuân thủ cấu trúc Lego (Task, Analysis, Structure, Format) không?
* [ ] **Màng lọc Hiển thị (Sanitizer):** LLM Output đã được đẩy qua hàm `sanitize_md_to_tg_html` để thoát lỗi Markdown crash Telegram chưa?

### 🗄️ 4. Dữ liệu & Trí nhớ (Database Architect)

* [ ] **An toàn Đa người dùng (Multi-tenant):** Câu query SQL đã có mệnh đề `WHERE user_id = ?` để ngăn rò rỉ dữ liệu chưa?
* [ ] **Nguồn Sự thật Duy nhất (SSOT):** AI có được ép đọc số liệu Target/Plan từ Database thay vì nghe theo lịch sử Chat không?
* [ ] **Chống ghi đè lỗi (Idempotent):** Các lệnh ghi DB (update plan) đã dùng `UPSERT` để đảm bảo nếu cronjob chạy 2 lần thì data không bị duplicate chưa?

### 🏃‍♂️ 5. Nghiệp vụ Domain (Pro Running Coach)

* [ ] **An toàn Sinh lý:** Các rule kiểm soát an toàn (ACWR > 1.3, Cadence < 170) có bị bypass trong đợt update này không?
* [ ] **Tính cách AI:** Lời phản hồi có giữ đúng tone giọng "Nghiêm khắc, thẳng thắn nhưng khích lệ" của Coach Dyno không?

### 📖 6. Tài liệu hóa (Docs-as-Code Rule)

* [ ] **Cập nhật Đồng bộ:** Update code này đã đi kèm việc update Docstrings hoặc các file Markdown trong thư mục `docs/` chưa? (BẮT BUỘC cùng 1 commit).
