# Kiến trúc Bộ nhớ Đa tác tử (Multi-Agent Memory Architecture)
**Dự án:** Personal AI OS  
**Phiên bản:** v3.2.0 (State-Aware Agentic Memory)  
**Trạng thái:** Triển khai lõi (Core Implementation)  
**Mục tiêu:** Xây dựng hệ thống trí nhớ AI có khả năng chia sẻ ngữ cảnh giữa nhiều Agent (Coach, Finance, Work), duy trì hiệu năng cao trong hàng chục năm mà không bị phình to (Memory Bloat), loại bỏ ảo giác lặp từ (Echo Chamber), và hỗ trợ Multi-Tenant.

---

## 1. Triết lý Thiết kế (The 3-Tier Holistic Memory Model)
Hệ thống chuyển đổi từ mô hình "Sổ nhật ký" (Append-Only Log) sang mô hình "Máy trạng thái" (Entity State Machine), phân chia ký ức thành 3 tầng ranh giới rõ rệt:

### Tier 1: Working Memory (Trí nhớ ngắn hạn / Ngữ cảnh làm việc)
* **Lưu trữ:** RAM / Payload truyền vào LLM (từ bảng `chat_history`).
* **Nội dung:** 30 tin nhắn chat gần nhất + Context cấu hình hệ thống hiện tại (Thời gian, ACWR, Weekly Volume).
* **Anti-Bloat Mechanism:** Cơ chế cửa sổ trượt (Sliding Window `limit=30`). Tin nhắn cũ tự động rơi rụng khi kết thúc phiên hoặc vượt giới hạn.

### Tier 2: Core Memory (Máy trạng thái thực thể - Entity State Machine)
* **Lưu trữ:** Relational DB (`os_core.db` -> bảng `core_memory`).
* **Bản chất:** Quản lý trạng thái của VĐV theo nguyên tắc Key-Value:
    * **Fixed Domains (Ngăn tủ):** Giới hạn cứng theo 6-Pillar Filter (`sports`, `health`, `physiological`, `lifestyle`, `nutrition`, `psychology`).
    * **Dynamic Categories (Hồ sơ):** Chuỗi `snake_case` do LLM tự sinh ra để bám sát thực thể (VD: `achilles_injury`, `hm_goal`, `shoe_preference`).
* **Anti-Bloat Mechanism (Retrieval Deduplication):** Bất kể có bao nhiêu thay đổi được insert vào DB, khi Agent truy vấn, hệ thống dùng SQL `INNER JOIN` kết hợp `MAX(timestamp)` và `GROUP BY category`. LLM chỉ nhận được đúng **1 trạng thái mới nhất** của mỗi thực thể.
* **Archiving (Đóng hồ sơ):** Quản lý qua cột `status`. Nếu một chấn thương đã khỏi, status chuyển thành `inactive` và tự động bị loại khỏi Context Injection hàng ngày.

### Tier 3: Long-Term Semantic Memory (Trí nhớ ngữ nghĩa / Vector)
* **Lưu trữ:** Vector DB (`chroma_db`).
* **Bản chất:** Một cuốn sổ cái (Immutable ledger) lưu trữ toàn bộ lịch sử biến thiên của các sự thật (Ví dụ: Hành trình từ đau gót chân đến lúc khỏi bệnh).
* **Anti-Bloat Mechanism:** Dữ liệu này **TUYỆT ĐỐI KHÔNG** được tự động bơm vào Prompt. Nó chỉ được kéo ra thông qua Tool `search_long_term_memory` khi Agent chủ động muốn lục lọi quá khứ xa.

---

## 2. Thiết kế Cơ sở dữ liệu (Database Schema)
**Công nghệ:** SQLite (Zone 1 - 100% English Standard)  
**Bảng:** `core_memory` (Hỗ trợ Multi-Tenant)

| Column Name | Type | Description |
| :--- | :--- | :--- |
| `id` | UUID | Khóa chính, dùng để map 1-1 với Vector ID trong ChromaDB. |
| `user_id` | String | Định danh người dùng (Multi-tenant requirement). |
| `domain` | String | Phân vùng Agent (VD: `sports`, `health`). Cố định. |
| `category` | String | Thực thể theo dõi (VD: `injury`, `goal`). Sinh động bởi AI. |
| `fact` | String | Trạng thái/Sự thật cốt lõi (VD: "Athlete reported zero pain today"). |
| `status` | String | `active` (Bơm vào Prompt) / `inactive` (Archived, ẩn khỏi Prompt). |
| `timestamp` | DateTime | Thời điểm đột biến trạng thái (State mutation time). |
| `last_accessed`| DateTime | Lần cuối cùng Agent query lấy ký ức này. |

---

## 3. Vòng đời Ký ức (The Deduplication Workflow)

### Bước 1: State-Aware Extraction (Trích xuất có nhận thức)
* **Kỹ thuật:** Differential Prompting (Prompt tính toán độ lệch).
* AI được cấp `[EXISTING KNOWLEDGE]` (Danh sách các trạng thái `active` hiện tại) trước khi đọc tin nhắn mới.
* AI chỉ trích xuất thông tin nếu nó là **MỚI** hoặc là một **SỰ THAY ĐỔI** so với trạng thái hiện tại. Nếu trùng lặp, trả về `[]`.

### Bước 2: State Mutation (Đột biến trạng thái)
* Hàm `insert_memory` tạo UUID và `INSERT` bản ghi mới vào `core_memory`.
* Nếu thực thể không còn tồn tại hoặc đã được giải quyết (hủy giải, hết chấn thương), AI gán `"status": "inactive"`.

### Bước 3: Context Injection (Bơm ngữ cảnh)
* Trước mỗi tác vụ (Briefing, Reflection), Agent gọi `get_active_memories(user_id, domain)`.
* Hệ thống lọc SQL (chỉ lấy `status = 'active'` + `GROUP BY category` mới nhất) và bơm vào System Prompt. Ký ức được truyền đi cực kỳ sắc bén và tối ưu Token.

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
