#!/usr/bin/env bash
# 使用 conda 环境启动 API (DGX 宿主机)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate audit-service
export APP_ENV=dgx
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 "$@"
