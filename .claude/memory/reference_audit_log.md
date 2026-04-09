---
name: Check Audit Log
description: How to run and read the log audit system — must run inside Docker container on T440
type: reference
---

Audit log phải chạy **bên trong Docker container** (log file owned by root, user tinhn không đọc được trực tiếp).

## Chạy audit + xem kết quả

```bash
docker exec airunningcoach python3 -c "
from app.core.logging_conf import setup_logging
setup_logging()
from app.core.database import init_db, get_audit_entries, get_audit_stats
from app.services.log_auditor import run_audit
from app.core.user_context import get_primary_user_id
init_db()
user_id = str(get_primary_user_id())
count = run_audit(user_id)
stats = get_audit_stats(user_id)
entries = get_audit_entries(user_id, limit=50)
print('NEW_ENTRIES:', count)
print('STATS:', stats)
for e in entries:
    print(e['severity'].upper(), '|', e['category'], '|', e['status'])
    print(' ', e['raw_line'][:150])
"
```

## Web UI

http://localhost:8000/audit (Basic Auth)

## Scheduler

`task_log_audit()` chạy tự động mỗi 6 giờ. Dedup bằng UNIQUE(user_id, raw_line) — chạy lại bao nhiêu lần cũng safe.

## Key files

- `app/services/log_auditor.py` — pattern matching engine
- `app/routers/audit.py` — API + HTML endpoints
- `templates/audit.html` — dashboard UI
- `app/core/database.py` — audit_entries table + 4 DB functions
