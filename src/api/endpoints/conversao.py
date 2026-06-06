import logging
from threading import Lock

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from src.services.conversao import executar_conversao
from src.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
router = APIRouter()
_conversion_lock = Lock()


def _executar_conversao_com_lock():
    setup_logging()

    try:
        logger.info("Execucao da conversao iniciada pela API")
        executar_conversao()
    except Exception:
        logger.exception("Erro durante execucao da conversao pela API")
    finally:
        _conversion_lock.release()
        logger.info("Bloqueio de execucao da conversao liberado")


@router.post("/executar", status_code=202)
def executar(background_tasks: BackgroundTasks):
    if not _conversion_lock.acquire(blocking=False):
        return JSONResponse(
            status_code=409,
            content={"message": "Conversão já está em execução"},
        )

    background_tasks.add_task(_executar_conversao_com_lock)
    return {"message": "Conversão iniciada"}
