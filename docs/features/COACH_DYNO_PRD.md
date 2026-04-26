Dưới góc độ của một **Kỹ sư Hệ thống (Systems Architect)**, tôi cực kỳ tán thành tư duy làm việc này của anh. "Viết code thì nhanh, nhưng sửa kiến trúc thì bằng hai lần đập đi xây lại". Việc định hình một Requirement (Yêu cầu) ở chuẩn Enterprise đòi hỏi góc nhìn đa chiều.

Tôi đã triệu tập một "Hội đồng Chuyên gia ảo" gồm: **Product Owner (BA), Chuyên gia Thể thao (Pro Coach), Kỹ sư Trưởng (Architect), Lập trình viên (Dev), và Kỹ sư Kiểm thử (QA)**. 

Dưới đây là biên bản cuộc họp "mổ xẻ" và bản **PRD (Product Requirements Document)** hoàn chỉnh nhất, sẵn sàng để anh ném cho bất kỳ AI/Dev nào để implement mà không sợ phải làm lại.

---

### 🏛️ PHẦN 1: HỘI ĐỒNG CHUYÊN GIA ĐÓNG GÓP Ý KIẾN

* 🏃 **Pro Coach (Chuyên gia Thể thao):** *"Bản thân Epics cũ thiếu 3 yếu tố sống còn của chạy bộ: (1) **Thời tiết** (Chạy Pace 5:00 ở 25 độ khác hoàn toàn 35 độ, AI sẽ mắng oan user nếu nhịp tim cao do trời nóng); (2) **Dinh dưỡng/Nước** cho bài Long Run cuối tuần; (3) **Chu kỳ điểm rơi (Periodization & Tapering)** - Càng gần ngày giải Sub-1:45 thì khối lượng phải giảm (Taper) chứ không được ép chạy nặng."*
* 📊 **BA (Business Analyst):** *"Về mặt User, chúng ta thiếu luồng **Xử lý Ngoại lệ (Exception Handling trong đời thực)**. Nếu hôm nay user ốm, báo 'Tạm dừng', hệ thống phải tự động đóng băng giáo án. Nếu user bỏ lỡ 2 buổi chạy, AI không được bắt chạy bù dồn dập mà phải xé nháp tính lại."*
* 🏗️ **Architect (Kỹ sư Hệ thống):** *"Strava Webhook và Garmin API có thể bị lỗi mạng hoặc Rate Limit. Chúng ta cần một **State Machine (Máy trạng thái)** lưu trong SQLite để biết trạng thái ngày hôm nay của user. Đồng thời, LLM prompt phải rất cẩn thận để không bị tràn Token khi nhồi cả nghìn giây dữ liệu nhịp tim vào."*
* 👨‍💻 **Developer:** *"Đồng ý với Architect. Tôi cần các `Pydantic Models` cực kỳ khắt khe. Nếu bắt LLM sinh giáo án, nó phải trả về JSON chuẩn, không được lẫn Markdown. Phải thiết kế kiến trúc theo kiểu `Event-Driven` (Hướng sự kiện) - Webhook Strava bắn về là trigger ngay hàm Analysis."*
* 🕵️ **QA (Tester):** *"Tôi lo về rác dữ liệu. Nếu user bật Garmin nhưng đạp xe thay vì chạy bộ thì sao? Hoặc quên đeo đồng hồ khi ngủ (không có HRV)? AI phải có kịch bản xử lý khi thiếu dữ liệu (Graceful Degradation), không được Crash hay đưa ra lời khuyên mù quáng."*

---

### 📜 PHẦN 2: TÀI LIỆU YÊU CẦU SẢN PHẨM HOÀN CHỈNH (PRD - V1.0)
*Bản PRD này được thiết kế theo chuẩn Agile, bao gồm User Story và Acceptance Criteria (Tiêu chí nghiệm thu) để Agent/Dev code chính xác.*

#### 🚀 EPIC 1: ĐỘNG CƠ DỮ LIỆU ĐA NGUỒN (Omni-Data Ingestion)
*Không chỉ là Strava/Garmin, AI cần hiểu hoàn cảnh vật lý của bài chạy.*

| Tính năng | User Story (US) | Tiêu chí Nghiệm thu Kỹ thuật (AC) |
| :--- | :--- | :--- |
| **1.1 Đồng bộ Thể trạng (Garmin)** | Là hệ thống, tôi cần lấy HRV, Sleep Score, Readiness mỗi 6h sáng để đánh giá thể trạng. | - Dùng `garminconnect` login ẩn.<br>- Xử lý bắt lỗi Token Expired.<br>- Nếu không có dữ liệu (user quên đeo), `Readiness = 'Unknown'`, kích hoạt luồng AI hỏi thăm buổi sáng thay vì tự quyết. |
| **1.2 Bắt sự kiện bài chạy (Strava)** | Là AI, tôi cần nhận dữ liệu ngay khi user chạy xong để phân tích kịp thời. | - Expose endpoint `/webhook/strava`.<br>- Lọc bỏ các activities KHÔNG phải là "Run" (ví dụ Ride, Walk).<br>- Lấy Data Streams (HR, Pace, Cadence) nén thành các khoảng trung bình (vd: mỗi 100m) để tránh tràn LLM Token. |
| **1.3 Bối cảnh Môi trường (Weather API)** | Là Coach, tôi cần biết nhiệt độ/độ ẩm để đánh giá khách quan nhịp tim. | - Tích hợp OpenWeatherMap API (hoặc tương đương).<br>- Gắn tag thời tiết vào DB cùng với bài chạy (VD: `Temp: 32C, Humidity: 80%`). |

#### 🧠 EPIC 2: BỘ NÃO LẬP KẾ HOẠCH THÍCH ỨNG (Adaptive State Machine)
*Cốt lõi của hệ thống. Không dùng file text, dùng Database để quản lý giáo án.*

| Tính năng | User Story (US) | Tiêu chí Nghiệm thu Kỹ thuật (AC) |
| :--- | :--- | :--- |
| **2.1 Quản lý Trạng thái VĐV (Athlete State)** | Là hệ thống, tôi phải biết user đang ở chu kỳ nào (Base, Build, Taper, Race, Injured). | - Tạo bảng `athlete_state` trong SQLite.<br>- Trạng thái 'Injured/Sick' sẽ tạm dừng sinh giáo án. <br>- Đếm ngược tới Race Day (29/03/2026) để tự động chuyển state sang 'Tapering'. |
| **2.2 Sinh Giáo án Micro-cycle (7 ngày)** | Là VĐV, tôi muốn giáo án tuần được tự động cập nhật vào tối Chủ Nhật, dựa trên thành tích tuần trước. | - LLM Output bắt buộc tuân thủ schema `WeeklyPlan` (Pydantic).<br>- Phân loại rõ bài tập: Easy, Tempo, Interval, Long Run.<br>- Bắt buộc có Target Pace và Target HR cho từng bài. |
| **2.3 Tự động Cấn trừ (Rescheduling)** | Là VĐV, nếu tôi bận/mệt bỏ lỡ bài hôm nay, AI phải tự động sắp xếp lại thay vì ép chạy bù nguy hiểm. | - Hàm kiểm tra cuối ngày: Nếu `Status != Completed` và `Readiness < 30` -> Gọi lại LLM để Regenerate mảng JSON giáo án cho các ngày còn lại. |

#### 💬 EPIC 3: TƯƠNG TÁC CHỦ ĐỘNG & TÂM LÝ (Proactive Coaching)
*Đưa AI từ một cỗ máy thành một người bạn đồng hành qua Telegram.*

| Tính năng | User Story (US) | Tiêu chí Nghiệm thu Kỹ thuật (AC) |
| :--- | :--- | :--- |
| **3.1 Morning Readiness Briefing** | Là VĐV, tôi muốn nhận được lời khuyên buổi sáng dựa trên giấc ngủ tối qua. | - Cronjob chạy lúc 6:00 AM.<br>- Logic: Nếu HRV giảm sâu + Bài hôm nay là Interval -> AI tự động đề xuất đổi sang Easy Run. Yêu cầu user xác nhận (`/accept` hoặc `/reject`). |
| **3.2 Thu thập RPE (Cảm nhận nỗ lực)** | Là Coach, tôi cần biết VĐV cảm thấy "mệt hụt hơi" hay "vẫn chạy tiếp được" sau bài tập. | - Sau khi phân tích Strava xong, gửi tin Telegram kèm Inline Keyboard (Nút bấm 1-10) để user đánh giá RPE.<br>- RPE > 8 đối với bài Easy sẽ cảnh báo Overtraining. |
| **3.3 Trợ lý Dinh dưỡng (Nutrition Alert)** | Là VĐV, tôi muốn được nhắc nhở mang Gel/Nước trước những bài chạy dài. | - Trigger: Tối hôm trước của bài Long Run (>15km).<br>- Gửi thông báo: *"Mai chạy 21km, trời sẽ nắng 30 độ. Anh nhớ chuẩn bị 2 gói Gel và 500ml điện giải nhé!"* |
| **3.4 Quản lý Giày (Gear Tracker)** | Là VĐV, tôi muốn biết khi nào đôi Asics/Nike của tôi đã hết tuổi thọ. | - Lấy ID giày từ Strava API.<br>- Cảnh báo qua Telegram khi số mileage của một đôi giày vượt qua mốc 600km. |

#### 🛡️ EPIC 4: AN TOÀN & KIẾN TRÚC HỆ THỐNG (Non-Functional Requirements)
| Khía cạnh | Yêu cầu Kỹ thuật (Technical Constraints) |
| :--- | :--- |
| **Prompt Engineering** | Không gửi toàn bộ dữ liệu thô vào LLM. Sử dụng thuật toán nén số liệu (Metrics Aggregation) trước khi đưa vào prompt để tiết kiệm tiền (Tokens) và giảm độ trễ (Latency). |
| **Async / Non-Blocking** | Mọi API call tới Gemini (sinh giáo án) và Garmin/Strava phải dùng `aiohttp` hoặc `run_in_threadpool` để không làm sập luồng nhận Webhook của FastAPI. |
| **Graceful Fallback** | Nếu Gemini API sập hoặc Timeout, Agent phải trả về tin nhắn chuẩn mực: *"Hệ thống AI đang bảo trì, anh cứ chạy bài Easy 5km giữ chân nhé"*, tuyệt đối không văng exception 500 ra Telegram. |

---

### 💡 Hướng dẫn triển khai cho Dev/Agent (Execution Plan)

Với bản PRD này, anh có thể yên tâm giao việc cho AI Agent (như Cursor/Claude Code). Để tránh *Rework* (phải sửa đi sửa lại), hãy bắt AI thực hiện theo đúng trật tự phụ thuộc (Dependency Graph) sau:

1. **Phase 1: Xây nền móng (Database & Schema).** Yêu cầu AI cập nhật `models.py` và tạo các Pydantic schema cho Epic 2.1 và 2.2 trước. Nếu DB sai, mọi thứ sẽ sập.
2. **Phase 2: Lắp giác quan (Garmin/Strava/Weather).** Code Epic 1. Đảm bảo dữ liệu chảy vào DB trơn tru.
3. **Phase 3: Lắp não bộ (LLM Planner).** Đưa dữ liệu từ Phase 2 vào Gemini để sinh ra giáo án.
4. **Phase 4: Lắp miệng (Telegram UX).** Code Epic 3 để gửi tin nhắn tương tác.

Anh có đồng ý chốt hạ bản Yêu cầu này chưa? Nếu chốt, chúng ta sẽ viết prompt bắt AI thực thi **Phase 1: Tạo Pydantic Schema cho Hệ thống Giáo án** ngay bây giờ!