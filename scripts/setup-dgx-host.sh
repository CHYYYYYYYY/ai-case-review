#!/usr/bin/env bash
# DGX 宿主机部署 — conda 环境 + 系统 Redis/PostgreSQL，不用 Docker
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONDA_BASE="${CONDA_BASE:-$HOME/miniconda3}"

echo "==> 1. Conda 环境 (Python 3.11)"
if [ ! -x "$CONDA_BASE/bin/conda" ]; then
  echo "未找到 conda，请先安装 Miniforge/Miniconda 到 $CONDA_BASE"
  exit 1
fi
# shellcheck disable=SC1091
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main 2>/dev/null || true
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r 2>/dev/null || true
if conda env list | awk '{print $1}' | grep -qx audit-service; then
  echo "环境 audit-service 已存在，更新依赖..."
else
  conda create -n audit-service python=3.11 -y
fi
conda activate audit-service
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[dev]"

echo "==> 2. 系统依赖 (需 sudo，仅首次)"
echo "    sudo apt install -y redis-server postgresql postgresql-contrib libpq-dev"
echo "    sudo systemctl enable --now redis-server postgresql"

echo "==> 3. PostgreSQL 初始化 (仅首次)"
echo "    sudo -u postgres createuser -s audit 2>/dev/null || true"
echo "    sudo -u postgres psql -c \"ALTER USER audit PASSWORD 'audit_dev_pwd';\" 2>/dev/null || true"
echo "    sudo -u postgres createdb -O audit audit 2>/dev/null || true"

echo "==> 4. 本地存储目录"
mkdir -p "$ROOT/data/uploads"

echo "==> 5. 环境变量"
if [ ! -f .env ]; then
  cp .env.example .env
fi

echo ""
echo "完成。启动方式（两个终端）："
echo "  bash scripts/start_api_conda.sh      # API :8000"
echo "  bash scripts/start_worker_conda.sh   # Celery Worker"
echo ""
echo "或手动："
echo "  source ~/miniconda3/etc/profile.d/conda.sh"
echo "  conda activate audit-service"
echo "  export APP_ENV=dgx"
echo "LLM 使用宿主机 vLLM: http://127.0.0.1:8080/v1 (qwen3-vl)"
