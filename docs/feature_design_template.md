### 🏛️ 1. Thẩm định Giá trị & Tầm nhìn (YAGNI / KISS)

*Trước khi viết dòng code đầu tiên, hãy tự hỏi:*

* [ ] **Tính năng này có thực sự cần thiết lúc này không?** (Nó giải quyết nỗi đau gì cho người dùng, hay chỉ là "nice to have"?).
* [ ] **Có cách nào làm đơn giản hơn không?** (Áp dụng KISS: Thay vì viết 1 Tool phức tạp cho AI, liệu Backend Python có thể tính toán và "bơm" thẳng dữ liệu vào Prompt không?).
* [ ] **Nó nằm ở đâu trên Master Roadmap?** (Là một Phase tiếp theo hay chỉ là một Hotfix?).

### 🧩 2. Kiến trúc Hệ thống & Luồng dữ liệu (System Architecture)

*Xác định vị trí đặt code để đảm bảo Separation of Concerns (SoC).*

* [ ] **Điểm kích hoạt (Trigger) nằm ở đâu?** - Là Webhook Strava tự động đẩy về?
* Là Cronjob chạy ngầm (như Weekly Reflection)?
* Hay là Lệnh chủ động từ Telegram (`/command`)?


* [ ] **Nó thuộc Lớp 1 (Gateway) hay Lớp 2 (AI Brain)?**
* Nếu chỉ là lệnh cơ học (như `/sync`, `/toggle`): Đặt ở `routers/webhooks.py` hoặc `admin.py`.
* Nếu cần AI suy luận (Cognitive): BẮT BUỘC đẩy vào `agents/coach/agent.py`.


* [ ] **Tác động chéo (Side-effect Analysis):** Luồng mới này có làm chậm luồng cũ không? (Có cần bọc trong `BackgroundTasks` để không block API Telegram không?).

### 🗄️ 3. Kiến trúc Dữ liệu & Trí nhớ (Database & RAG Architect)

* [ ] **Schema Database (SQLite):** Có cần thêm bảng (Table) hay cột (Column) mới không? Nếu có, script migration/update DB là gì?
* [ ] **Trí nhớ dài hạn (ChromaDB):** Tính năng này có cần sinh ra "Ký ức" không? (VD: Nhớ thời tiết hôm nay, nhớ bản kiểm điểm tuần). Nếu có, ID của document là gì để tránh bị trùng lặp (Duplicate)?
* [ ] **Single Source of Truth (SSOT):** Dữ liệu tính năng này lấy từ đâu? Đảm bảo AI phải đọc từ DB thay vì tự bịa (Hallucinate) ra số liệu.

### 🤖 4. Thiết kế Agentic & Prompt (AI / Prompt Expert)

* [ ] **Mô hình cung cấp dữ liệu:** Dùng **Tool Calling** (AI tự gọi hàm để lấy data) hay **Data Injection** (Python lấy sẵn data nhét vào Prompt)? *Ưu tiên Data Injection cho Cronjobs.*
* [ ] **Cấu trúc Prompt (Lego Architecture):** Prompt mới phải được lắp ghép từ các biến hằng số: `[BỐI CẢNH] + [NHIỆM VỤ] + [RÀNG BUỘC] + [FORMAT BÁO CÁO]`.
* [ ] **Chi phí Token:** Việc nhét thêm context này có làm phình to Token limit không? Có cần giới hạn số lượng record query từ DB lên không? (VD: Chỉ lấy 7 ngày thay vì 30 ngày).

### 🛡️ 5. Độ bền bỉ & Ngoại lệ (Resilience & Security)

* [ ] **External API Dependencies:** Tính năng này có gọi ra ngoài (Strava, OpenWeather, Gemini) không?
* [ ] **Fallback Mechanism:** Đã áp dụng `send_message_with_retry` (Exponential Backoff) để chống sập mạng/Rate Limit (429/503) chưa?
* [ ] **Timeout Handling:** Nếu API thời tiết chết, AI có bị treo theo không, hay sẽ bỏ qua và dùng context mặc định?

### 🌐 6. Tiêu chuẩn Mã nguồn & Ngôn ngữ (Language Demarcation Line)

* [ ] **Zone 1 (100% Tiếng Anh):** Tên file, hàm, biến, class, Docstrings, Log hệ thống (`logger.info`) BẮT BUỘC là tiếng Anh.
* [ ] **Zone 2/3 (Tiếng Việt):** Các f-string text gửi cho người dùng, Telegram Message, Prompt AI Persona BẮT BUỘC là tiếng Việt.
* [ ] **Update Documentation (Docs-as-code):** Đã cập nhật `README.md` hoặc thiết kế lại `architecture.md` cho luồng mới này chưa?

### 🧪 7. Kế hoạch Kiểm thử & Triển khai (Testing & Rollout)

* [ ] **Secret Command:** Có tạo lệnh ẩn (VD: `/test_weather`, `/reflect`) để Admin test nóng luồng mới mà không cần chờ Cronjob/Webhook chạy thật không?
* [ ] **Backward Compatibility:** Nếu code mới bị lỗi và phải `git revert`, dữ liệu cũ có bị hỏng không?
