from fastapi import APIRouter

from src.api.endpoints.conversao import router as conversao_router
from src.api.endpoints.docs import router as docs_router
from src.api.endpoints.whats import router as whats_router


api_router = APIRouter()
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
