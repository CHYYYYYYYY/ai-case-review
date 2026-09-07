#!/usr/bin/env bash
# 停止稽核 API + Celery Worker
set -euo pipefail
fuser -k 8000/tcp 2>/dev/null && echo "API stopped" || echo "API not running on :8000"
pkill -f "celery -A app.worker.celery_app worker" 2>/dev/null && echo "Worker stopped" || echo "Worker not running"
