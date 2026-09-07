@echo off
REM Windows 本地启动 API 服务
REM 前置: 1) 已 cp .env.example .env 并填好 QWEN_API_KEY
REM       2) docker-compose up -d 起好 PG/Redis/MinIO
REM       3) 已 pip install -e .

setlocal
cd /d %~dp0..

REM 创建虚拟环境(首次)
if not exist .venv (
    python -m venv .venv
    call .venv\Scripts\activate
    pip install --upgrade pip
    pip install -e .
) else (
    call .venv\Scripts\activate
)

REM 建表(开发期)
python -c "from app.db.session import init_db; init_db()"

REM 启动 API
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
