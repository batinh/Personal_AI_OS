# Kiến trúc Bộ nhớ Đa tác tử (Multi-Agent Memory Architecture)
**Dự án:** Personal AI OS
**Trạng thái:** Đề xuất & Thiết kế lõi
**Mục tiêu:** Xây dựng hệ thống trí nhớ AI có khả năng chia sẻ ngữ cảnh giữa nhiều Agent (Coach, Finance, Work), duy trì hiệu năng cao trong hàng chục năm mà không bị phình to (bloat), và có khả năng truy xuất chính xác sự kiện trong quá khứ xa.



## 1. Triết lý Thiết kế (The 4-Tier Memory System)
Hệ thống mô phỏng bộ não người, phân chia ký ức thành 4 tầng ranh giới rõ rệt nhằm tối ưu hóa Context Window và hạn chế Hallucination:

1. **Working Memory (Tầng 1 - Chat Session):** Context hiện tại đang trò chuyện. Rớt não ngay khi kết thúc phiên. Tốc độ cực nhanh.
2. **Active Structured Memory (Tầng 2 - Sự thật ưu tiên):** Những thói quen, chấn thương, mục tiêu *hiện tại*. Luôn được tiêm ngầm (inject) vào System Prompt của Agent tương ứng.
3. **Archived Structured Memory (Tầng 3 - Kho lưu trữ lạnh):** Các sự thật/sự kiện đã diễn ra trong quá khứ (ví dụ: đau gối năm 2024). Bị chặn không cho tiêm vào Prompt hàng ngày. Chỉ được gọi lên thông qua Tool Use (Function Calling).
4. **Episodic Memory (Tầng 4 - Vector RAG):** ChromaDB lưu trữ toàn bộ văn bản thô (Raw text) của các cuộc hội thoại và báo cáo tuần. Dùng để lấy lại "cảm xúc" và bối cảnh chi tiết.

## 2. Thiết kế Cơ sở dữ liệu (Database Schema)
**Công nghệ:** SQLite (Zone 1 - 100% English Standard)
**Bảng:** `core_memory`

| Column Name | Type | Description |
| :--- | :--- | :--- |
| `id` | UUID | Khóa chính. |
| `domain` | String | Lĩnh vực của Agent (`sports`, `health`, `work`, `finance`, `general`). Dùng để cách ly bối cảnh. |
| `category` | String | Phân loại ký ức (`injury`, `preference`, `goal`, `relationship`). |
| `fact` | String | Nội dung cốt lõi trích xuất được (VD: "Has right knee pain"). |
| `confidence` | Float | Độ tự tin của AI khi trích xuất (0.0 - 1.0). |
| `created_at` | DateTime | Thời điểm trích xuất ký ức. |
| `last_accessed`| DateTime | Lần cuối cùng Agent query lấy ký ức này. |
| `status` | String | `active` (Tiêm vào Prompt) / `archived` (Chỉ lấy khi Tool gọi) / `conflicted`. |

## 3. Vòng đời Ký ức (Memory Lifecycle)

### A. Ingestion & Extraction (Nạp ngầm)
- Chạy ngầm định kỳ (VD: Tối Chủ Nhật).
- Đọc lịch sử chat, AI trả về chuẩn JSON (`Structured Output`).
- Nếu có xung đột (Conflict) với fact cũ, Memory Manager tự động chuyển status fact cũ thành `archived`, nạp fact mới thành `active`.

### B. Daily Injection (Bơm ngữ cảnh hàng ngày)
- Agent nào hoạt động thì chỉ query domain của Agent đó.
- `SELECT fact FROM core_memory WHERE domain IN ('sports', 'health') AND status = 'active'`

### C. Decay & Forgetting (Sự lãng quên chủ động)
- **Tuyệt đối không dùng lệnh DELETE.**
- Cronjob hàng tháng quét bảng `core_memory`. Các records có `last_accessed` > 60 ngày (trừ category `goal`) sẽ bị update `status = 'archived'`. Giúp Prompt nhẹ nhàng, tiết kiệm Token.

## 4. Cơ chế Truy xuất Quá khứ Xa (Long-term Retrieval Scenario)
**Kịch bản:** Vài năm sau (2028), VĐV hỏi: *"Hồi 2024 tôi xử lý cái gối bị đau thế nào nhỉ?"*

**Luồng xử lý (On-Demand Retrieval):**
1. System Prompt hiện tại KHÔNG chứa ký ức đau gối 2024 (do status là `archived`).
2. Gemini phân tích câu hỏi, nhận diện Intent "tìm kiếm quá khứ".
3. Tự động kích hoạt Tool: `search_historical_memory(topic="knee injury", year="2024")`.
4. SQL chạy ngầm bỏ qua điều kiện status: `SELECT * FROM core_memory WHERE fact LIKE '%knee%'`.
5. AI nhận Data SQL + gọi thêm Vector RAG (ChromaDB) để lấy bối cảnh chi tiết.
6. AI tổng hợp kết quả và trả lời người dùng.

---
### 🏛️ BỨC TRANH TỔNG THỂ (THE BIG PICTURE)

Kiến trúc của dự án giờ đây không còn là nguyên khối (Monolithic) mà được thiết kế theo mô hình **Event-Driven & Modular (Hướng sự kiện và Mô-đun hóa)**. Hệ thống được chia thành 5 phân lớp (Layers) tách biệt hoàn toàn về trách nhiệm (Separation of Concerns).

#### 🌐 Layer 1: Giao tiếp Ngoại vi & Kích hoạt (The Edge & Triggers)

Nơi tiếp nhận tín hiệu từ thế giới thực. Không chứa logic AI.

* **Telegram Webhook:** Giao diện người dùng duy nhất (User-Facing). Nhận lệnh chat, hiển thị thông báo.
* **Strava Webhook:** Lắng nghe tín hiệu khi VĐV hoàn thành bài chạy.
* **Scheduler (Cronjobs):** Kích hoạt theo thời gian thực (06:00 Morning Briefing, 20:00 Chủ Nhật Weekly Reflection, Background Memory Extraction).
* **OpenWeather API / Strava API:** Các cổng kết nối lấy dữ liệu ngoại cảnh và thể thao.

#### 🧠 Layer 2: Lõi Nhận thức Đa tác tử (The Cognitive Multi-Agent Core)

Nơi "Bộ não" đưa ra quyết định. Tuân thủ Zone 3 (Logic English, Template Vietnamese).

* **Router/Orchestrator:** Phân luồng tín hiệu (Webhook vào thì gọi Strava Agent, Chat vào thì gọi Coach Agent).
* **Coach Agent (Dyno):** Chuyên gia Thể thao. Xử lý Giáo án, Phân tích bài chạy, Động viên.
* **Memory Manager Agent (Background):** Chuyên gia Tâm lý/Dữ liệu. Chạy ngầm để đọc lịch sử chat, trích xuất (Extract) và dọn dẹp (Decay) ký ức.
* *(Tương lai)* **Work/Finance Agent:** Các tác tử chạy song song xử lý lịch làm việc và tài chính.
* **Prompt Engine (Lego Framework):** Lắp ghép bối cảnh động, chèn luật sinh lý học (Cardiac Drift) và format thẻ HTML.

#### 🗄️ Layer 3: Hệ thống Bộ nhớ 4 Tầng (The 4-Tier Universal Memory)

Đây là trái tim của dự án, đảm bảo AI không bao giờ quên, nhưng cũng không bị "ảo giác" (Hallucination).

* **Tầng 1 - Working Memory:** Context Window tức thời của Gemini (1M Tokens) cho phiên chat hiện tại.
* **Tầng 2 - Active Structured Memory (SQLite `core_memory`):** Chứa các Facts (Sự thật) *đang diễn ra* (VD: "Đang đau gối"). Được tiêm vào mọi Prompt.
* **Tầng 3 - Archived Structured Memory (SQLite `core_memory`):** Kho lạnh chứa ký ức *đã qua* (VD: "Đau gối năm 2024"). Chỉ được lôi ra bằng Tool Use khi có truy vấn quá khứ.
* **Tầng 4 - Episodic Memory (ChromaDB):** Trí nhớ Vector lưu toàn bộ nhật ký (Raw logs), Weekly Reflections để AI tìm lại cảm xúc và bối cảnh cụ thể.

#### 🔬 Layer 4: Dịch vụ Chuyên biệt (Domain Services)

Đảm nhận nguyên lý: **"Python làm Toán, AI làm Thơ"**. 100% Code Tiếng Anh (Zone 1).

* **Sports Science Module:** Thư viện Pure Python tự build. Tính toán HR Zones, Aerobic Decoupling, Pace Drop.
* **Weather Context Service:** Bóc tách `average_temp` từ thiết bị Garmin/Strava và lấy Dự báo thời tiết.
* **Plan & Load Calculator:** Tính toán TRIMP, ACWR (Acute:Chronic Workload Ratio), Target Volume tuần.

#### 📂 Layer 5: Data Lake & Hạ tầng (Infrastructure)

* **SQLite (Relational DB):** Lưu trữ Cấu trúc (Runs, Plans, Facts).
* **Local JSON Data Lake (`data/streams/`):** Lưu Raw Time-series (Từng giây của bài chạy) từ Strava để dùng cho Data Science sau này mà không làm phình Database.
* **Nginx & DuckDNS:** Xử lý SSL/TLS bảo mật.

---

### 🔄 VÍ DỤ: LUỒNG DỮ LIỆU KẾT HỢP (DATA FLOW EXAMPLE)

Để thấy rõ sự phối hợp của 5 Layer này, hãy nhìn vào Kịch bản **"Hoàn thành bài chạy dưới trời nắng nóng" (Task 6.3)**:

1. **Layer 1 (Trigger):** Garmin đồng bộ lên Strava -> Strava bắn Webhook về FastAPI router.
2. **Layer 4 (Services):**
* Bóc tách Data cơ bản (Pace, HR).
* Bóc tách `average_temp` (Nhiệt độ) ngay trên đường chạy.
* Lưu Full Time-series xuống ổ cứng **Layer 5 (Data Lake)** thành file JSON.
* Gọi *Sports Science Module* tính ra: *"Tỉ lệ trượt tim (Decoupling) là 8% ở nửa sau"*.


3. **Layer 3 (Memory):** Query SQLite xem VĐV có đang bị "Đau gối" (Active Fact) không để chuẩn bị dặn dò.
4. **Layer 2 (Cognitive):** Nhồi toàn bộ (Nhiệt độ, Trượt tim, Đau gối) vào `build_universal_run_analysis_prompt`.
5. **Layer 1 (Output):** AI tạo ra phản hồi chuẩn Coach chuyên nghiệp: *"Anh chạy rớt Pace cuối bài do trượt tim 8%, nhưng dưới trời nóng 35°C thì điều này rất bình thường. Chú ý cái gối đang đau nhé, về chườm đá ngay!"* -> Gửi qua Telegram.

---

### ⚖️ ĐÁNH GIÁ TỪ KIẾN TRÚC SƯ

Kiến trúc này thỏa mãn 100% các tiêu chí khắt khe nhất của chúng ta:

* **No Side-effect:** Việc thêm Memory hay Weather không hề đụng chạm đến logic tính ACWR cũ.
* **Zero-Heavy Processing:** Tự code Pure Python (Sports Science) và lưu JSON (Data Lake) giúp loại bỏ thư viện Pandas nặng nề, giữ Docker Image siêu nhẹ.
* **Enterprise-grade:** Chia tách rõ ràng giữa "Người điều phối" (Scheduler/Router) và "Bộ não tư duy" (Agent).

---
## 5. Lộ trình Triển khai (Implementation Roadmap)

*Tiếp nối Roadmap v3.1.0 hiện tại:*

### 🎯 Phase 1: The Memory Foundation (Xây móng dữ liệu)
- [ ] Cập nhật `app/core/database.py`: Thêm bảng `core_memory` SQLite.
- [ ] Viết hàm CRUD: `insert_memory`, `get_active_memories(domain)`, `archive_memory`.
- [ ] Viết Agent Prompt (Zone 3): `MEMORY_EXTRACTION_PROMPT` bắt buộc trả về JSON.

### 🎯 Phase 2: Autonomous Manager (Quản lý Tự trị)
- [ ] Tích hợp luồng Extraction vào Cronjob tối Chủ Nhật (Chạy sau Weekly Reflection).
- [ ] Sửa đổi `build_standup_prompt` trong Coach Dyno để query và tiêm các `active` facts thay vì đọc config tĩnh.
- [ ] Xây dựng Cronjob `memory_decay_job` chạy hàng tháng để đánh dấu `archived`.

### 🎯 Phase 3: The Multi-Agent Ecosystem (Hệ sinh thái Đa tác tử)
- [ ] Xây dựng Tool `search_historical_memory` cung cấp cho Agent quyền moi móc ký ức `archived`.
- [ ] Khởi tạo Agent mới (VD: Work Agent) và cấu hình query Domain tương ứng từ cùng một Database `core_memory`.