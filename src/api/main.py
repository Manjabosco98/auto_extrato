import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.router import api_router
from src.services.chat_notifications import (
    OUTBOX_WORKER_INTERVAL_SECONDS,
    reprocessar_notificacoes_pendentes,
)


logger = logging.getLogger(__name__)


async def _notification_outbox_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            resultado = await asyncio.to_thread(reprocessar_notificacoes_pendentes)
            if resultado["pendentes"]:
                logger.info("Reprocessamento da fila de notificacoes: %s", resultado)
        except Exception:
            logger.exception("Erro inesperado no worker de notificacoes")

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=OUTBOX_WORKER_INTERVAL_SECONDS,
            )
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    worker = asyncio.create_task(_notification_outbox_worker(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await worker


app = FastAPI(title="AutoExtrato API", lifespan=lifespan)
app.include_router(api_router, prefix="/api")
