# ai-case-review｜集装箱修箱清单 AI 稽核系统

泛亚箱体险 AI 审案项目。系统通过维修清单 OCR、修箱照片识别、位置/方向匹配和损伤核验，输出结构化稽核报告。

## 功能

- 提交「维修清单图 + 修箱照片」异步稽核任务
- 多阶段流水线：清单 OCR → 照片索引/手写位置 → 规则预处理 → 部件/方向匹配 → 损伤核验 → 报告
- 输出 VERIFIED / PARTIAL / MISSING 稽核结论
- 异步任务状态查询、取消、Webhook 回调
- 客户 `photosIds` 与系统 `photo_id` 双 ID 映射
- 每个维修项目支持人工复核 AI 结果，错误原因可持久保存并在报告中回显
- API Key 保护业务接口，管理网页通过短时 HttpOnly 会话访问
- 只读 iframe 结果页与合作方 JSON 渲染模板

## 技术栈

- Python 3.11 + FastAPI + Celery
- PostgreSQL + Redis + MinIO（Docker Compose 起基础设施）
- OpenAI 兼容的多模态 LLM 接口（DGX 当前使用本地 Qwen3-VL）

## DGX 宿主机部署（推荐，不用 Docker）

```bash
cd ai-case-review
bash scripts/setup-dgx-host.sh   # 首次：conda 环境 + 提示装 redis/postgresql

# 终端 1 — API
bash scripts/start_api_conda.sh

# 终端 2 — Worker
bash scripts/start_worker_conda.sh
```

- **Python**：conda 环境 `audit-service`（`~/miniconda3`）
- **LLM**：宿主机 vLLM `http://127.0.0.1:8080/v1`，模型 `qwen3-vl`
- **图片**：本地目录 `data/uploads/`（无需 MinIO）
- **队列**：系统 Redis（`apt install redis-server`）
- **数据库**：系统 PostgreSQL（`apt install postgresql`）

> Docker 仅用于 Windows 同学生态；DGX 宿主机直接跑即可。

## 快速开始（Windows 本地开发 + Docker 基础设施）

### 1. 准备环境

```powershell
# 进入项目目录
cd ai-case-review

# 复制环境变量模板并填写真实 Key
copy .env.example .env
# 用编辑器打开 .env, 填入 QWEN_API_KEY=sk-xxxx
```

### 2. 启动基础设施（PG / Redis / MinIO）

```powershell
docker-compose up -d
# 检查: docker-compose ps  三个服务都应是 healthy
# MinIO 控制台: http://localhost:9001  账号 audit_dev / audit_dev_secret
```

### 3. 创建虚拟环境并装依赖

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

### 4. 启动 API 服务（终端 1）

```powershell
scripts\start_api.bat
# 或手动:
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

访问 Swagger：http://localhost:8000/docs

### 5. 启动 Celery Worker（终端 2，另开）

```powershell
scripts\start_worker.bat
# 或手动:
celery -A app.worker.celery_app worker -c 2 -Q audit --loglevel=info -P solo
```

> Windows 必须 `-P solo`，否则 Celery 报错。

### 6. 跑测试

```powershell
pytest
```

### 7. 联调冒烟测试

```powershell
# 准备一张清单图 + 几张修箱照片放到 tests/fixtures/
python scripts\smoke_test.py tests\fixtures\manifest.jpg tests\fixtures\p1.jpg tests\fixtures\p2.jpg
```

## 接口速览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/audits` | 提交任务，返回 task_id 和照片 ID 映射 |
| GET | `/api/v1/audits/{task_id}` | 查询任务状态 |
| GET | `/api/v1/audits/{task_id}/report` | 获取报告 |
| POST | `/api/v1/audits/{task_id}/cancel` | 取消任务 |
| GET | `/health` | 健康检查 |
| GET | `/docs` | Swagger 文档 |

合作方自建前端的参考文件位于 [`partner_frontend/`](partner_frontend/)。

详见 http://localhost:8000/docs

## 项目结构

```
ai-case-review/
├── app/
│   ├── api/            FastAPI 路由
│   ├── core/           配置、日志
│   ├── db/             ORM 模型、session
│   ├── llm/            LLM 客户端、限流、JSON 兜底、Prompt 加载
│   ├── pipeline/       流水线编排
│   │   ├── stages/     P1~P6 各阶段实现
│   │   ├── orchestrator.py
│   │   └── schemas.py
│   ├── rules/          IICL 代码表、P3 纠错、MCO 硬规则
│   ├── storage/        MinIO 封装
│   ├── worker/         Celery 任务
│   └── main.py         FastAPI 入口
├── prompts/            7 套 Prompt 模板(独立文件, 便于调优)
├── config/             development.yaml / production.yaml
├── scripts/            启动脚本、冒烟测试
├── tests/              单测 + 黄金集
├── docker-compose.yml       本地开发(仅基础设施)
├── docker-compose.prod.yml  生产(全套容器化)
├── Dockerfile
├── pyproject.toml
└── .env.example
```

## 部署到生产

### 1. 打镜像

```powershell
docker build -t audit-api:latest .
```

### 2. 准备生产配置

```powershell
copy .env.example .env.production
# 编辑 .env.production, 填入生产 QWEN_API_KEY、DB 密码等
```

### 3. 推到服务器后启动

```bash
docker-compose -f docker-compose.prod.yml up -d
```

### 4. 建表（首次）

```bash
docker-compose -f docker-compose.prod.yml exec api python -c "from app.db.session import init_db; init_db()"
```

## 成本控制

调试代码逻辑时（不验证 Prompt 准确率），可在 `config/development.yaml` 设 `llm.mock: true`，跳过真实 LLM 调用。准确率验证再切回 `false`。

## 相关文档

- [API 接口使用说明](docs/API接口使用说明.md)
- [外部系统接入指南](docs/外部接入示例.md)
- [iframe 嵌入鉴定结果接口](docs/iframe嵌入鉴定结果接口.md)
- [合作方前端接入说明](partner_frontend/README.md)
