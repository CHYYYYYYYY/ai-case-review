#!/usr/bin/env bash
# 为已有 task_photos 表添加 v3.8-GG 新列
set -euo pipefail
source "${HOME}/miniconda3/etc/profile.d/conda.sh" 2>/dev/null || true
conda activate audit-service 2>/dev/null || true

export PGPASSWORD="${POSTGRES_PASSWORD:-audit_dev_pwd}"
PSQL="psql -h ${POSTGRES_HOST:-localhost} -U ${POSTGRES_USER:-audit} -d ${POSTGRES_DB:-audit}"

echo "Adding v3.8-GG columns to task_photos..."

$PSQL <<'SQL'
ALTER TABLE task_photos ADD COLUMN IF NOT EXISTS is_interior BOOLEAN;
ALTER TABLE task_photos ADD COLUMN IF NOT EXISTS likely_location VARCHAR(16);
ALTER TABLE task_photos ADD COLUMN IF NOT EXISTS has_damage BOOLEAN;
ALTER TABLE task_photos ADD COLUMN IF NOT EXISTS is_plate_info BOOLEAN;
ALTER TABLE task_photos ADD COLUMN IF NOT EXISTS handwritten_location VARCHAR(16);
ALTER TABLE task_photos ADD COLUMN IF NOT EXISTS chinese_direction VARCHAR(8);
ALTER TABLE task_photos ADD COLUMN IF NOT EXISTS original_filename VARCHAR(255);
SQL

echo "Done."
