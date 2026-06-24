from fastapi import APIRouter

from src.api.endpoints.conversao import router as conversao_router
from src.api.endpoints.docs import router as docs_router
from src.api.endpoints.fecfin import router as fecfin_router
from src.api.endpoints.whats import router as whats_router


api_router = APIRouter()


@api_router.get("/health")
async def health_check():
    return {"status": "ok", "service": "autoextrato"}


api_router.include_router(
    conversao_router,
    prefix="/conversao",
    tags=["conversao"],
)
api_router.include_router(
    whats_router,
    prefix="/whats",
    tags=["whats"],
)
api_router.include_router(
    docs_router,
    prefix="/docs",
    tags=["docs"],
)
api_router.include_router(
    fecfin_router,
    prefix="/fecfin",
    tags=["fecfin"],
)
