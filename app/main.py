"""FastAPI 应用入口."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import audits, embed, health, session_auth
from app.api.middleware import APIKeyMiddleware
from app.core.config import get_config
from app.core.logging import get_logger, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期: 启动时初始化."""
    setup_logging()
    log = get_logger(__name__)
    cfg = get_config()
    log.info("app_startup", env=cfg.env, model=cfg.llm.default_model)
    # 开发/DGX 期自动建表(生产用 alembic)
    if cfg.env in ("development", "dgx", "production"):
        try:
            from app.db.session import init_db
            init_db()
            log.info("db_initialized")
        except Exception as e:
            log.warning("db_init_failed", error=str(e))
    yield
    log.info("app_shutdown")


def create_app() -> FastAPI:
    cfg = get_config()
    app = FastAPI(
        title="集装箱修箱清单 AI 稽核系统",
        description="提供修箱清单 OCR + 照片视觉匹配 + 损伤核验的异步 API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.include_router(health.router)
    app.include_router(audits.router)
    app.include_router(embed.router)
    app.include_router(session_auth.router)
    # CORS 必须加在 APIKeyMiddleware 之前，这样浏览器预检请求能直接过
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(APIKeyMiddleware)

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        """Swagger UI — 使用国内 CDN, 避免 jsdelivr 加载失败白屏."""
        return get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - Swagger UI",
            swagger_js_url="https://cdn.bootcdn.net/ajax/libs/swagger-ui/5.11.0/swagger-ui-bundle.js",
            swagger_css_url="https://cdn.bootcdn.net/ajax/libs/swagger-ui/5.11.0/swagger-ui.css",
        )

    @app.get("/redoc", include_in_schema=False)
    async def redoc_ui() -> HTMLResponse:
        return get_redoc_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - ReDoc",
            redoc_js_url="https://cdn.bootcdn.net/ajax/libs/redoc/2.1.3/redoc.standalone.js",
        )

    # 静态文件: 提供本地提交页面(生产环境可关闭)
    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        async def index_page():
            """根路径返回提交页面, 比 Swagger UI 友好."""
            return FileResponse(str(static_dir / "index.html"))

    return app


app = create_app()
