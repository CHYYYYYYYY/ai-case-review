#!/usr/bin/env bash
# 使用 conda 环境启动 Celery Worker (DGX 宿主机)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate audit-service
export APP_ENV=dgx
exec celery -A app.worker.celery_app worker -c 2 -Q audit --loglevel=info "$@"
