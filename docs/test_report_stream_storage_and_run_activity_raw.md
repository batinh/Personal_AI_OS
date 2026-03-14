# Báo cáo Test Plan & Kết quả Test

**Phạm vi:** Stream storage (file), run_activity_raw (DB), tool get_run_full_details  
**Ngày:** 2026-03-14  
**Phiên bản tính năng:** Lưu stream raw ra file `data/streams/{user_id}/{activity_id}.json`, metadata + stream_file_path trong DB.

---

## 1. Test Plan (Kế hoạch kiểm thử)

### 1.1 Mục tiêu

- Đảm bảo lưu/đọc stream file đúng cấu trúc thư mục và định dạng JSON.
- Đảm bảo DB lưu và trả về `stream_file_path` đúng, upsert hoạt động đúng.
- Đảm bảo tool `get_run_full_details` hiển thị đường dẫn file stream khi có và không hiển thị khi không có.

### 1.2 Phạm vi (Scope)

| Module | File test | Nội dung kiểm thử |
|--------|-----------|-------------------|
| **Stream storage** | `tests/test_stream_storage.py` | `get_stream_file_path`, `save_activity_stream_to_file`, `load_activity_stream_from_file`, `get_stream_arrays` |
| **Database** | `tests/test_database_run_activity_raw.py` | `save_run_activity_raw` (stream_file_path), `get_run_activity_raw` (trả về stream_file_path), upsert |
| **Tools** | `tests/test_tools_get_run_full_details.py` | `get_run_full_details`: not found, có stream path, không có stream path |

### 1.3 Chi tiết Test Cases

#### A. Stream Storage (`test_stream_storage.py`)

| # | Test case | Mô tả | Kết quả mong đợi |
|---|------------|--------|-------------------|
| 1 | `test_returns_relative_path` | Gọi `get_stream_file_path("user123", "act456")` | `"streams/user123/act456.json"` |
| 2 | `test_handles_string_ids` | Gọi với ID dạng chuỗi số | `"streams/987654321/1234567890.json"` |
| 3 | `test_save_returns_relative_path` | Save stream dict hợp lệ | Trả về `"streams/u1/a1.json"` |
| 4 | `test_save_creates_file_with_expected_structure` | Save rồi đọc file | File tồn tại; JSON có `activity_id`, `user_id`, `fetched_at`, `streams` (time, heartrate) |
| 5 | `test_save_empty_dict_returns_none` | Save `{}` | Trả về `None` |
| 6 | `test_save_none_like_returns_none` | Save `None` | Trả về `None` |
| 7 | `test_load_returns_payload` | Save xong load theo path | Payload trả về đúng activity_id và streams |
| 8 | `test_load_missing_file_returns_none` | Load path file không tồn tại | Trả về `None` |
| 9 | `test_load_empty_path_returns_none` | Load `""` hoặc `"   "` | Trả về `None` |
| 10 | `test_returns_flat_arrays` | `get_stream_arrays(payload)` với payload có streams | Dict `time`, `heartrate` với mảng data |
| 11 | `test_empty_streams_returns_none` | `get_stream_arrays({})`, `{"streams": {}}`, `None` | `None` |
| 12 | `test_missing_streams_key_returns_none` | Payload không có key `streams` | `None` |

#### B. Database run_activity_raw (`test_database_run_activity_raw.py`)

| # | Test case | Mô tả | Kết quả mong đợi |
|---|------------|--------|-------------------|
| 1 | `test_save_and_get_run_activity_raw_with_stream_file_path` | Save metadata + stream_file_path, rồi get | Raw có `activity_name`, `full_meta`, `stream_file_path`, `stream_csv` đúng |
| 2 | `test_get_run_activity_raw_returns_none_for_unknown_activity` | Get activity_id không tồn tại | `None` |
| 3 | `test_save_run_activity_raw_upserts_and_updates_stream_file_path` | Save 2 lần cùng activity_id với path khác | Lần 2 get ra path và name mới (upsert) |

#### C. Tool get_run_full_details (`test_tools_get_run_full_details.py`)

| # | Test case | Mô tả | Kết quả mong đợi |
|---|------------|--------|-------------------|
| 1 | `test_returns_not_found_message_when_no_raw` | Mock `get_run_activity_raw` trả về None | Chuỗi chứa "Không tìm thấy dữ liệu đầy đủ" và activity_id |
| 2 | `test_includes_stream_file_path_when_present` | Mock raw có `stream_file_path` | Chuỗi chứa "data/streams/...", "phân tích chi tiết" |
| 3 | `test_no_stream_path_line_when_path_empty` | Mock raw có `stream_file_path` rỗng | Chuỗi có "Run", không chứa "streams/" |

### 1.4 Môi trường chạy test

- **Chạy từ:** thư mục gốc project (`Personal_AI_OS`).
- **Lệnh:** `python -m unittest discover -s tests -v` hoặc `pytest tests/ -v`.
- **Phụ thuộc:** 
  - Toàn bộ test: cần cài `pip install -r requirements.txt` (pytz, sqlite3, app modules).
  - Chỉ stream storage: không cần DB/pytz (chỉ `app.services.stream_storage`).

---

## 2. Kết quả Test chi tiết

### 2.1 Chạy trong môi trường có đủ dependencies (venv / Docker)

Khi đã cài `requirements.txt`, kỳ vọng:

```
Ran 18 tests in ...s
OK
```

- **test_stream_storage**: 12 passed  
- **test_database_run_activity_raw**: 3 passed  
- **test_tools_get_run_full_details**: 3 passed  

### 2.2 Chạy trong môi trường thiếu dependencies (ví dụ: system Python chưa cài pytz)

**Lệnh:** `python -m unittest discover -s tests -v`

**Kết quả thực tế:**

| Kết quả | Số lượng | Chi tiết |
|---------|----------|----------|
| **OK** | 12 | Toàn bộ `tests.test_stream_storage` |
| **ERROR** | 2 | ImportError khi load module test (thiếu `pytz`) |

**Output chi tiết:**

```
test_database_run_activity_raw ... ERROR (ImportError: No module named 'pytz')
test_tools_get_run_full_details ... ERROR (ImportError: No module named 'pytz')
test_empty_streams_returns_none ... ok
test_missing_streams_key_returns_none ... ok
test_returns_flat_arrays ... ok
test_handles_string_ids ... ok
test_returns_relative_path ... ok
test_load_empty_path_returns_none ... ok
test_load_missing_file_returns_none ... ok
test_load_returns_payload ... ok
test_save_creates_file_with_expected_structure ... ok
test_save_empty_dict_returns_none ... ok
test_save_none_like_returns_none ... ok
test_save_returns_relative_path ... ok

Ran 14 tests in 0.004s
FAILED (errors=2)
```

**Giải thích:** Hai module test `test_database_run_activity_raw` và `test_tools_get_run_full_details` import `app.core.database` / `app.agents.coach.tools`, các module này dùng `pytz`. Khi chưa cài `pytz`, Python báo lỗi ngay khi import, nên 2 “test” bị báo ERROR (không phải test fail logic).

### 2.3 Chạy chỉ Stream Storage (không cần pytz)

**Lệnh:** `python -m unittest tests.test_stream_storage -v`

**Kết quả:**

```
test_empty_streams_returns_none ... ok
test_missing_streams_key_returns_none ... ok
test_returns_flat_arrays ... ok
test_handles_string_ids ... ok
test_returns_relative_path ... ok
test_load_empty_path_returns_none ... ok
test_load_missing_file_returns_none ... ok
test_load_returns_payload ... ok
test_save_creates_file_with_expected_structure ... ok
test_save_empty_dict_returns_none ... ok
test_save_none_like_returns_none ... ok
test_save_returns_relative_path ... ok

----------------------------------------------------------------------
Ran 12 tests in ~0.004s
OK
```

---

## 3. Tóm tắt

| Hạng mục | Kết quả |
|----------|---------|
| **Test plan** | Đã định nghĩa 18 test cases cho stream storage, DB run_activity_raw, tool get_run_full_details. |
| **Stream storage (12 tests)** | Pass khi chạy (kể cả môi trường thiếu pytz). |
| **Database (3 tests)** | Thiết kế đúng; cần môi trường có `pytz` (và dependencies app) để chạy thành công. |
| **Tools (3 tests)** | Thiết kế đúng; cần môi trường có `pytz` (và dependencies app) để chạy thành công. |

**Khuyến nghị:**

- Chạy toàn bộ test trong venv hoặc Docker đã cài `pip install -r requirements.txt` (và nếu dùng pytest: `pip install -r requirements-dev.txt`).
- CI/CD: cài dependencies trước khi chạy `python -m unittest discover -s tests -v` (hoặc `pytest tests/ -v`) để đủ 18 tests và báo OK.
