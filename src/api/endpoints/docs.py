import logging
from threading import Lock

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from src.services.docs import executar_docs
from src.utils.logging_config import setup_logging


logger = logging.getLogger(__name__)
router = APIRouter()
_docs_lock = Lock()


def _executar_docs_com_lock():
    setup_logging()

    try:
        logger.info("Execucao do fluxo DOCS iniciada pela API")
        executar_docs()
    except Exception:
        logger.exception("Erro durante execucao do fluxo DOCS pela API")
    finally:
        _docs_lock.release()
        logger.info("Bloqueio de execucao do fluxo DOCS liberado")


@router.post("/executar", status_code=202)
def executar(background_tasks: BackgroundTasks):
    if not _docs_lock.acquire(blocking=False):
        return JSONResponse(
            status_code=409,
            content={"message": "Fluxo DOCS ja esta em execucao"},
        )

    background_tasks.add_task(_executar_docs_com_lock)
    return {"message": "Fluxo DOCS iniciado"}
