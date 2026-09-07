#!/usr/bin/env bash
# 启动 API 服务 (Linux/DGX)
set -euo pipefail
cd "$(dirname "$0")/.."
export APP_ENV="${APP_ENV:-dgx}"
exec uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
