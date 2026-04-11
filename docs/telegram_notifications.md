Telegram Notifications: HTML chunking, attachments, telemetry

Summary
-------
This document describes the Telegram notification improvements implemented:

- Preserve HTML formatting for short messages (<= 4000 chars) and fallback to plain text if Telegram parse fails.
- For medium-length messages (>4000 chars) the system performs HTML-balanced chunking: it splits sanitized HTML into chunks that close open tags at chunk boundaries and reopen them at the next chunk, preserving formatting across messages.
- For extremely long messages (>100000 chars) the system sends the report as a .txt attachment via sendDocument to avoid chat truncation and bad UX.
- A telemetry counter (state.chunked_send_count) increments when chunking is used.

Files changed
-------------
- app/core/notification.py — added HTML-balanced chunking, attachment fallback, and telemetry increment.
- app/core/state.py — added an in-memory telemetry counter and increment method.
- tests/test_notification_document.py — new integration-style test that mocks requests.post for document uploads.

How to run tests
----------------
Run the notification tests or the full test suite locally (network calls are mocked):

python -m pytest tests/test_notification_document.py -q
python -m pytest tests/ -q

Notes and next steps
--------------------
- Telemetry currently lives in-memory (state.chunked_send_count). Consider exporting to Prometheus or persistent metrics if required.
- The HTML-balanced chunking attempts to keep tags balanced but is intentionally conservative; if complex/malformed tags are produced by the LLM, the notifier falls back to plain-text chunking.
- If you prefer very large reports to be sent as files at a lower threshold, update ATTACHMENT_THRESHOLD in app/core/notification.py.
