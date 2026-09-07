#!/usr/bin/env bash
# 为客户 photosIds 增加可空字段；旧任务保持 null，不改动任何历史记录或图片。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate audit-service
export APP_ENV="${APP_ENV:-dgx}"

python - <<'PY'
from sqlalchemy import text

from app.db.session import sync_engine

with sync_engine.begin() as connection:
    connection.execute(text(
        "ALTER TABLE task_photos "
        "ADD COLUMN IF NOT EXISTS external_photo_id VARCHAR(128)"
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_photos_task_external_photo_id "
        "ON task_photos (task_id, external_photo_id) "
        "WHERE external_photo_id IS NOT NULL"
    ))

print("external_photo_id migration complete")
PY
