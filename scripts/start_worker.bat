@echo off
REM Windows 本地启动 Celery Worker
REM 需另开终端运行此脚本

setlocal
cd /d %~dp0..
call .venv\Scripts\activate
celery -A app.worker.celery_app worker -c 2 -Q audit --loglevel=info -P solo
REM 注意: Windows 下 Celery 需加 -P solo, 否则报错
