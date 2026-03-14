# Stream Data Storage Design

## 1. Mục tiêu

- **Streams**: Lấy đủ tối đa dữ liệu stream từ Strava (11 loại), lưu **file** trên thư mục `data/` với cấu trúc thư mục rõ ràng, dễ lấy lại khi cần (phân tích chi tiết đoạn, re-analyze).
- **Metadata detail**: Giữ trong database như hiện tại (`run_activity_raw.full_meta`). Không lưu nội dung stream (CSV/text) trong DB, chỉ lưu **đường dẫn file** stream.

## 2. Cấu trúc thư mục

```
data/
  streams/
    {user_id}/
      {activity_id}.json
```

Ví dụ: `data/streams/123456789/9876543210.json`

- Một file per activity, dễ backup, xóa theo user hoặc theo activity.
- Có thể mở rộng sau: `data/streams/{user_id}/{year}/{month}/{activity_id}.json` nếu cần tổ chức theo thời gian.

## 3. Định dạng file stream (JSON)

Lưu nguyên cấu trúc Strava (key_by_type) để giữ đầy đủ thông tin (original_size, resolution, series_type). Thêm envelope để trace.

```json
{
  "activity_id": "9876543210",
  "user_id": "123456789",
  "fetched_at": "2026-03-14T10:00:00Z",
  "streams": {
    "time": { "data": [0,1,2,...], "original_size": 3600, "resolution": "low", "series_type": "time" },
    "heartrate": { "data": [120,121,...], ... },
    "velocity_smooth": { "data": [...], ... },
    "latlng": { "data": [[lat,lng],...], ... },
    "distance": { "data": [...], ... },
    "altitude": { "data": [...], ... },
    "cadence": { "data": [...], ... },
    "watts": { "data": [...], ... },
    "temp": { "data": [...], ... },
    "moving": { "data": [...], ... },
    "grade_smooth": { "data": [...], ... }
  }
}
```

Chỉ những key Strava trả về mới có trong `streams` (activity có thể không có HR, power, temp...).

## 4. Strava stream keys (full)

Theo [Strava Streams API](https://strava.github.io/api/v3/streams/):  
`time`, `latlng`, `distance`, `altitude`, `velocity_smooth`, `heartrate`, `cadence`, `watts`, `temp`, `moving`, `grade_smooth`.

Request một lần với đủ keys; response thiếu key nào thì activity không có dữ liệu đó.

## 5. Database (run_activity_raw)

- **Giữ**: `activity_id`, `user_id`, `activity_name`, `full_meta` (JSON), `fetched_at`.
- **Bỏ**: `stream_csv` (không lưu nội dung stream trong DB).
- **Thêm**: `stream_file_path` (TEXT) — đường dẫn tương đối từ thư mục `data/`, ví dụ `streams/123456789/9876543210.json`.

Khi cần stream (phân tích chi tiết đoạn, re-analyze): đọc từ `data/{stream_file_path}`.

## 6. Luồng dữ liệu

| Bước | Hành động |
|------|------------|
| Webhook / Sync | Gọi `get_activity_data(activity_id)` → (name, csv_data, extended_meta, stream_raw). Nếu có stream_raw: `save_activity_stream_to_file(user_id, activity_id, stream_raw)` → path; `save_run_activity_raw(..., stream_file_path=path)`. |
| Phân tích thường | Dùng `csv_data` (downsampled) từ return của `get_activity_data` như hiện tại (Gemini). |
| Phân tích chi tiết / đoạn | Load stream từ file: `load_activity_stream(stream_file_path)` → dict; extract đoạn (theo time/distance index); đưa vào pipeline phân tích hoặc build CSV con cho LLM. |
| Tool / Recall | `get_run_full_details(activity_id)` đọc metadata từ DB; nếu có `stream_file_path` thì báo "Đã có file stream, có thể dùng để phân tích chi tiết hoặc re-analyze". |

## 7. Re-analyze / phân tích theo đoạn

Khi cần phân tích lại hoặc chỉ một đoạn trong bài chạy:

1. Lấy path: `raw = get_run_activity_raw(activity_id)` → `stream_file_path = raw["stream_file_path"]`.
2. Load file: `payload = load_activity_stream_from_file(stream_file_path)`.
3. Lấy mảng: `arrays = get_stream_arrays(payload)` → `{"time": [...], "heartrate": [...], ...}`.
4. Cắt đoạn theo index (time hoặc distance), build CSV hoặc dict nhỏ rồi đưa vào pipeline phân tích (Gemini hoặc pure Python).

Helper trong `app/services/stream_storage.py`: `load_activity_stream_from_file`, `get_stream_arrays`.

## 8. Backward compatibility

- Code cũ đọc `stream_csv` từ `get_run_activity_raw`: sau migration trả về `stream_csv` rỗng; tool dùng `stream_file_path` và load từ file khi cần.
- `get_activity_data` trả về `(name, csv_data, extended_meta, stream_raw)`; caller lưu stream ra file rồi ghi `stream_file_path` vào DB.
