# Plan: `/sync` — đối chiếu Strava và gỡ activity đã xóa trên Strava

## 1. Mục tiêu

Sau khi đồng bộ từ Strava, **dọn DB cục bộ** cho các bài chạy mà VĐV đã **xóa trên Strava** (webhook delete có thể lỡ, hoặc xóa trước khi có webhook).

**Không** mục tiêu: “làm DB giống hệt mọi activity từng có trên Strava trong đời” (cần phân trang lớn + chi phí API).

---

## 2. Rủi ro xóa nhầm (phải tránh)

| Nguyên nhân | Vì sao nguy hiểm |
|-------------|------------------|
| Chỉ so với **danh sách N bài gần nhất** | Strava trả tối đa `per_page` bài; bài cũ hơn trong DB **không** xuất hiện trong list → có thể bị coi là “mất” và xóa nhầm. |
| So set `DB − list_strava` không verify | List không đầy đủ theo thời gian → false orphan. |
| Lỗi mạng / 401 / 429 | Coi như “không tồn tại” → xóa nhầm. |

---

## 3. Nguyên tắc an toàn (bắt buộc)

### 3.1 Chỉ xóa khi Strava xác nhận “resource gone”

- Gọi **`GET /api/v3/activities/{id}`** (hoặc endpoint tương đương đang dùng trong `StravaClient`).
- **Chỉ khi HTTP 404** (activity không còn / không truy cập được với token hiện tại theo doc Strava) → được phép gọi `delete_run_activity` + `rag_db.forget` + (đã có) xóa file stream.
- **200**: giữ nguyên DB (dù không có trong batch list — list có thể thiếu).
- **401 / 403 / 429 / 5xx / timeout**: **không xóa**; log + có thể dừng reconcile trong lần sync này.

→ Thuật toán **không** được phép: “không thấy trong list → xóa”.

### 3.2 Giới hạn phạm vi thời gian (reconciliation window)

Chỉ xét các dòng trong `run_activities` của `user_id` hiện tại thỏa **cửa sổ thời gian** trùng với ý nghĩa lệnh `/sync`:

| Lệnh | Đề xuất cửa sổ |
|------|----------------|
| `/sync` (limit N, không `days_back`) | `start_date` từ **min** đến **max** `start_date_local` của **N activity run** vừa fetch từ Strava trong lần sync đó. Bài trong DB **cũ hơn min** → **không** đưa vào candidate (chưa có bằng chứng từ batch hiện tại). |
| `/sync month` (`days_back=30`) | `start_date >= today - 30d` (theo TZ app). Có thể **bổ sung** paginate Strava trong 30 ngày để build set ID đầy đủ hơn; **vẫn** chỉ xóa sau 404. |

→ Tránh quét toàn bộ lịch sử DB mỗi lần sync ngắn.

### 3.3 Giới hạn số lần gọi API xác minh

- Mỗi lần `/sync`: cap ví dụ **10–20** activity tối đa được gọi GET verify (ưu tiên candidate “orphan” mới nhất theo `start_date`).
- Giữa các GET: `sleep` ngắn (đã có pattern trong sync) để giảm 429.

### 3.4 Loại activity

Chỉ reconcile bản ghi **run** trong DB (cùng rule type như pipeline hiện tại: Run / TrailRun / VirtualRun nếu có lưu type; nếu DB không có cột type thì mọi row `run_activities` của user — mặc định đều là run đã ingest).

---

## 4. Thuật toán đề xuất (từng bước)

1. **Ingest như hiện tại**: fetch list Strava → `build_activity_record` → `save_run_activity` / stream / RAG như code `execute_manual_sync`.
2. **Build `strava_ids_in_batch`**: `set` id các activity **run** trong `target_activities` (sau filter type).
3. **Tính `[window_start, window_end]`** theo mục 3.2 (từ batch hoặc từ `days_back`).
4. **Query DB**: `list_run_activity_ids_in_window(user_id, window_start, window_end)` → `db_ids`.
5. **Candidates**: `db_ids - strava_ids_in_batch` (chỉ trong window).
6. **Verify từng candidate** (theo thứ tự `start_date` DESC, tối đa K lần):
   - `GET /activities/{id}` → **404** → `delete_run_activity(id)` + `rag_db.forget(id)` + log `[SYNC-RECONCILE] removed stale activity {id}`.
   - **200** → log debug “still on Strava, list mismatch only”.
   - **Lỗi** → không xóa; nếu 429 có thể `break`.
7. **Telegram** (tùy chọn): một dòng trong tin nhắn kết thúc sync: “Đã gỡ N bài không còn trên Strava (đã xác minh API).”

---

## 5. Thay đổi code dự kiến

| Thành phần | Việc làm |
|------------|----------|
| `app/core/database.py` | Thêm `list_run_activity_ids_in_date_range(user_id, start_date_str, end_date_str) -> list[str]` (hoặc ISO date inclusive). |
| `app/agents/coach/strava_client.py` | Thêm `fetch_activity_detail_status(activity_id) -> int` hoặc enum `exists / not_found / error` bọc status code. |
| `app/agents/coach/harvest.py` | Cuối `execute_manual_sync`, gọi bước reconcile có guard 404 + cap + window. |
| `tests/test_harvest.py` | Mock: candidate + 404 → `delete_run_activity` được gọi; 200 → không gọi delete; lỗi mạng → không delete. |

---

## 6. Góc cạnh & tương lai

- **Privacy / quyền truy cập**: Activity chuyển private với app khác token có thể trả 404 vs 403 — cần đọc doc Strava; nếu 403, **không xóa** (an toàn hơn).
- **Paginate đầy đủ trong 30 ngày**: Giảm số candidate “giả”; vẫn giữ 404 làm bước cuối.
- **Webhook delete**: Đã có `handle_deleted_activity`; reconcile là lớp bù **best-effort**, không thay thế webhook.

---

## 7. Tiêu chí xong (Definition of Done)

- [ ] Không có nhánh code nào xóa DB chỉ vì “không có trong list”.
- [ ] Unit test cover 404 / 200 / error.
- [ ] Log rõ `[SYNC-RECONCILE]` để audit.
- [ ] (Optional) Ghi vào `docs/ISSUES.md` + link feature doc nếu triển khai ≥2 file.

---

*Tài liệu plan — triển khai sau khi review phạm vi cửa sổ thời gian và cap API.*
