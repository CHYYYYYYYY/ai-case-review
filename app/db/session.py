"""数据库 session 工厂.

API 层用 async session, Celery Worker 用 sync session.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_config
from app.db.models import Base

_config = get_config()


def _sync_dsn() -> str:
    """同步驱动 DSN(alembic / worker 用)."""
    dsn = _config.database.dsn()
    return dsn.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


# 异步引擎(API 层)
async_engine = create_async_engine(
    _config.database.dsn(),
    pool_size=_config.database.pool_size,
    pool_pre_ping=True,
    echo=False,
)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

# 同步引擎(Worker / alembic 用)
sync_engine = create_engine(_sync_dsn(), pool_size=_config.database.pool_size, pool_pre_ping=True, echo=False)
SyncSessionLocal = sessionmaker(sync_engine, expire_on_commit=False, class_=Session)


async def get_async_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用."""
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def async_session_scope() -> AsyncIterator[AsyncSession]:
    """业务代码显式开启事务用."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_sync_session() -> Session:
    return SyncSessionLocal()


def init_db() -> None:
    """开发期直接建表(生产用 alembic 迁移)."""
    Base.metadata.create_all(sync_engine)
