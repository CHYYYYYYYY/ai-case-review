"""健康检查接口."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["系统"])
async def health() -> dict:
    """健康检查."""
    return {"status": "ok"}
