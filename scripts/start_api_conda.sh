#!/usr/bin/env bash
# 使用 conda 环境启动 API (DGX 宿主机)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate audit-service
export APP_ENV=dgx
# 运行密钥只保存在未纳入版本控制的 .env 中。
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 "$@"
