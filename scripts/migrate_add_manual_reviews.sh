#!/usr/bin/env bash
# 新增人工复核表；不修改原始 AI 报告和历史照片。
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
    connection.execute(text("""
        CREATE TABLE IF NOT EXISTS manual_reviews (
            id BIGSERIAL PRIMARY KEY,
            task_id VARCHAR(32) NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            item_no INTEGER NOT NULL,
            ai_verification_status VARCHAR(32) NOT NULL,
            is_ai_correct BOOLEAN NOT NULL,
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_manual_reviews_task_item UNIQUE (task_id, item_no)
        )
    """))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_manual_reviews_task_id "
        "ON manual_reviews (task_id)"
    ))

print("manual_reviews migration complete")
PY
