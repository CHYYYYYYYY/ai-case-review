#!/usr/bin/env bash
# 启动 Celery Worker (Linux/DGX)
set -euo pipefail
cd "$(dirname "$0")/.."
export APP_ENV="${APP_ENV:-dgx}"
exec celery -A app.worker.celery_app worker -c 2 -Q audit --loglevel=info
