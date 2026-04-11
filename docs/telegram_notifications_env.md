Telegram Notification Environment Configuration

This file documents environment variables that control Telegram notification behavior.

Variables
---------
- TELEGRAM_LIMIT (default: 4000)
  - Maximum message length to attempt sending as a single HTML message. Values above this will trigger chunking or attachment behavior.
- TELEGRAM_ATTACHMENT_THRESHOLD (default: 100000)
  - If a sanitized message exceeds this many characters, the notifier will send the content as a .txt document attachment via Telegram sendDocument.

How to set
----------
Add to your .env file or system environment variables. Example:

TELEGRAM_LIMIT=4000
TELEGRAM_ATTACHMENT_THRESHOLD=100000

Notes
-----
- These variables accept integer values. Adjust according to your needs and chat client behaviour.
- Lower TELEGRAM_LIMIT forces more chunking; raising the attachment threshold delays using attachments.