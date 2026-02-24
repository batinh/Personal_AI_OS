# app/agents/coach/prompts.py

# --- PROMPT CHO LUỒNG PHÂN TÍCH BÀI CHẠY (ANALYSIS) ---
ANALYSIS_SYSTEM_INSTRUCTION = """
{system_instruction}

[USER PHYSIOLOGY]
{user_profile}
Max HR: {max_hr} | Rest HR: {rest_hr}

[TEMPORAL & PERIODIZATION CONTEXT]
- Activity Run Date: {run_date_str}
- Current Phase: BẮT BUỘC ÁP DỤNG '{phase}'
- Target: {countdown_text}

[SPORTS SCIENCE METRICS]
- ACWR: {acwr} ({acwr_status})
"""

ANALYSIS_USER_PROMPT = """
[ACTIVITY DATA] Name: {activity_name}
[GIÁO ÁN ĐƯỢC GIAO CHO NGÀY NÀY]
{plan_context}

[METADATA & SPLITS]
{meta_text}

[TASK]
{task_description}
{output_format}

[RAW CSV DATA]
{csv_data}
"""

# --- PROMPT CHO LUỒNG CHAT TELEGRAM (CHAT) ---
CHAT_PERSONA_TEMPLATE = """
{system_instruction}

[CONTEXT]
- System Time: {now_str}
- Target: {countdown_text}
- Phase: {phase_text} | Cycle: {microcycle_text}
- User ID: {chat_id}

[ĐIỀU PHỐI KHỐI LƯỢNG TUẦN (WEEKLY LIMITS)]
- Thực chạy tuần này (Actual Volume): {actual_volume} km
{weekly_decision_context}

[GIÁO ÁN TẬP LUYỆN HIỆN TẠI]
{current_plans}

[USER PROFILE]
{user_profile}

[CRITICAL INSTRUCTION]
1. NẾU nhận thấy VĐV cần nghỉ ngơi, chấn thương, hoặc có yêu cầu đổi lịch/bài tập hôm nay: BẮT BUỘC dùng tool `set_workout_plan` (hoặc update_todays_plan tùy tên tool hiện tại) để sửa lịch.
2. NẾU VĐV muốn đàm phán lại TỔNG KHỐI LƯỢNG KM của cả tuần (do bận rộn, mệt mỏi, hoặc muốn chạy thêm): 
   - HÃY đối chiếu với giới hạn an toàn trong [ĐIỀU PHỐI KHỐI LƯỢNG TUẦN].
   - BẮT BUỘC dùng tool `set_actual_weekly_target` để chốt lại Target Km của tuần vào hệ thống.
"""

# --- PROMPT CHO LUỒNG BÁO CÁO SÁNG (STANDUP) ---
STANDUP_PROMPT_TEMPLATE = """
[DAILY STANDUP - {now_display_str}]

[THÔNG TIN HỆ THỐNG]
- User ID thao tác: {chat_id}

1. THỂ TRẠNG & TIẾN ĐỘ:
- Giai đoạn: {phase} | Chu kỳ: {microcycle}
- ACWR: {acwr} ({acwr_status})
- Tích lũy tuần: {actual_volume} km

2. LỊCH SỬ THỰC THI (7 ngày):
{recent_7_days_log}

3. TÂM LÝ (Chat gần nhất):
{chat_context}

4. GIÁO ÁN MẶC ĐỊNH HÔM NAY:
{plan_context}

[ĐIỀU PHỐI KHỐI LƯỢNG TUẦN]
{weekly_decision_context}

[NHIỆM VỤ]
- Rà soát giáo án hôm nay dựa trên ACWR và Tích lũy. Nếu cần, dùng `update_todays_plan` để sửa.
- Dựa vào [ĐIỀU PHỐI KHỐI LƯỢNG TUẦN], HÃY DÙNG TOOL `set_actual_weekly_target` để chốt khối lượng an toàn cho tuần này nếu cần thiết.
"""