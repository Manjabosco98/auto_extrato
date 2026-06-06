from fastapi import APIRouter

from src.api.endpoints.conversao import router as conversao_router


api_router = APIRouter()
api_router.include_router(
    conversao_router,
    prefix="/conversao",
    tags=["conversao"],
)
