---
name: T440 Deploy Process
description: How to deploy this project on T440 — T440 is now the primary dev/deploy machine
type: reference
---

Deploy script: `scripts/deploy-t440.sh`

T440 is the primary dev and deploy machine. Run the script locally (no SSH needed):

- Full deploy (pull + rebuild): `bash scripts/deploy-t440.sh`
- Rebuild only (skip pull): `bash scripts/deploy-t440.sh --skip-pull`

Health endpoint: `http://localhost:8000/health`

The script: git pull → docker compose up --build -d → waits for /health → runs 6 E2E smoke tests.
All 6 must pass before deploy is considered successful.
