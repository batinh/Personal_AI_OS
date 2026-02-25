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