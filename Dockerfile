FROM python:3.11-slim

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖(利用缓存)
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# 拷贝项目代码
COPY app ./app
COPY prompts ./prompts
COPY config ./config

# 默认启动 API, 由 docker-compose 覆盖命令以启动 worker/beat
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
