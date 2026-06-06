from fastapi import FastAPI

from src.api.router import api_router


app = FastAPI(title="AutoExtrato API")
app.include_router(api_router, prefix="/api")
