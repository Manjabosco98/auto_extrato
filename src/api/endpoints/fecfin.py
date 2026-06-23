import logging
from threading import Lock

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from src.services.fecfin import executar_fecfin
from src.utils.logging_config import setup_logging


logger = logging.getLogger(__name__)
router = APIRouter()
_fecfin_lock = Lock()


def _executar_fecfin_com_lock():
    setup_logging()

    try:
        logger.info("Execucao do fluxo FECFIN iniciada pela API")
        executar_fecfin()
    except Exception:
        logger.exception("Erro durante execucao do fluxo FECFIN pela API")
    finally:
        _fecfin_lock.release()
        logger.info("Bloqueio de execucao do fluxo FECFIN liberado")


@router.post("/executar", status_code=202)
def executar(background_tasks: BackgroundTasks):
    if not _fecfin_lock.acquire(blocking=False):
        return JSONResponse(
            status_code=409,
            content={"message": "Fluxo FECFIN ja esta em execucao"},
        )

    background_tasks.add_task(_executar_fecfin_com_lock)
    return {"message": "Fluxo FECFIN iniciado"}
