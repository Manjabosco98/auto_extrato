import logging

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from src.services.whats import (
    executar_fluxo_whatsapp,
    processar_webhook_whats,
    should_process_webhook,
)
from src.utils.logging_config import setup_logging


logger = logging.getLogger(__name__)
router = APIRouter()


def _processar_webhook_em_background(payload: dict):
    setup_logging()

    try:
        processar_webhook_whats(payload)
    except Exception:
        logger.exception("Erro durante processamento do webhook WhatsApp")


def _executar_fluxo_whatsapp_em_background():
    setup_logging()

    try:
        executar_fluxo_whatsapp()
    except Exception:
        logger.exception("Erro durante execucao manual do fluxo WhatsApp")


def _receber_webhook(payload: dict, background_tasks: BackgroundTasks):
    should_process, reason, _ = should_process_webhook(payload)

    if not should_process:
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "message": reason},
        )

    background_tasks.add_task(_processar_webhook_em_background, payload)
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "message": "Webhook WhatsApp recebido"},
    )


@router.post("/webhook", status_code=202)
def receber_webhook(payload: dict, background_tasks: BackgroundTasks):
    return _receber_webhook(payload, background_tasks)


@router.post("/webhook/messages-upsert", status_code=202)
def receber_messages_upsert(payload: dict, background_tasks: BackgroundTasks):
    return _receber_webhook(payload, background_tasks)


@router.post("/executar", status_code=202)
def executar(background_tasks: BackgroundTasks):
    background_tasks.add_task(_executar_fluxo_whatsapp_em_background)
    return {"message": "Fluxo WhatsApp iniciado"}
