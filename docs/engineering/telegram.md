# Telegram Notifications — Engineering Guide

Covers HTML chunking behavior, attachment fallback, telemetry, and environment configuration.

---

## Message delivery pipeline

`send_telegram_msg(chat_id, text)` in `app/core/notification.py` handles all cases:

| Message length | Behavior |
|---------------|----------|
| ≤ `TELEGRAM_LIMIT` (default 4000 chars) | Send as single HTML message; fallback to plain text if parse fails |
| > `TELEGRAM_LIMIT` | HTML-balanced chunking: splits at tag boundaries, reopens tags at next chunk |
| > `TELEGRAM_ATTACHMENT_THRESHOLD` (default 100000 chars) | Send as `.txt` file via `sendDocument` |

### HTML-balanced chunking

The chunker (`split_html_preserving_tags`) splits sanitized HTML into segments that close open tags at chunk boundaries and reopen them at the start of the next chunk. This preserves `<b>`, `<i>`, `<code>` formatting across multi-part messages.

Intentionally conservative: if malformed/complex nested tags are detected, falls back to plain-text chunking.

### Attachment fallback

For very long AI reports (e.g., full weekly reflection):
- Sends via `sendDocument` as `report.txt`
- Avoids Telegram's hard chat truncation at extreme lengths
- Increments `state.chunked_send_count` telemetry counter

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_LIMIT` | `4000` | Max chars before chunking activates |
| `TELEGRAM_ATTACHMENT_THRESHOLD` | `100000` | Max chars before attachment mode activates |

Set in `.env` or system environment:

```env
TELEGRAM_LIMIT=4000
TELEGRAM_ATTACHMENT_THRESHOLD=100000
```

Lowering `TELEGRAM_LIMIT` forces more chunking. Raising `TELEGRAM_ATTACHMENT_THRESHOLD` delays attachment mode.

---

## Telemetry

`state.chunked_send_count` (in `app/core/state.py`) increments each time chunked delivery is used. Currently in-memory only; survives within a container lifecycle. Consider exporting to persistent metrics if needed.

---

## Files changed during implementation

- `app/core/notification.py` — HTML-balanced chunking, attachment fallback, telemetry
- `app/core/state.py` — in-memory telemetry counter
- `tests/test_notification_document.py` — integration-style tests (network calls mocked)
- `tests/test_telegram_chunking.py` — unit tests for `split_html_preserving_tags`

## Test gate

Always run after any change to `notification.py`:

```bash
python -m pytest tests/test_telegram_chunking.py tests/test_notification_document.py -v
```
