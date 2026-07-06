import logging

import pandas as pd

from src.schemas.fecfin.base import FecfinHandler


logger = logging.getLogger(__name__)

_FECFIN_HANDLERS: list[FecfinHandler] = []


class FecfinLayoutNotRecognized(Exception):
    pass


def register(cls):
    """Registra um handler FECFIN.

    Remove duplicatas pelo nome da classe e adiciona à lista
    de handlers candidatos.
    """
    _FECFIN_HANDLERS[:] = [
        h
        for h in _FECFIN_HANDLERS
        if not (
            type(h).__module__ == cls.__module__
            and type(h).__name__ == cls.__name__
        )
    ]
    _FECFIN_HANDLERS.append(cls())
    return cls


def dispatch_fecwin(
    xls: pd.ExcelFile,
    file_stem: str,
) -> list[tuple[str, pd.DataFrame]]:
    """Tenta reconhecer o layout de um arquivo FECFIN.

    Retorna uma lista de ``(nome_banco, DataFrame)`` — um par
    por banco encontrado no arquivo.  Se nenhum layout reconhecer,
    retorna lista vazia.
    """
    for handler in _FECFIN_HANDLERS:
        if handler.matches(xls, file_stem=file_stem):
            logger.info("Layout FECFIN reconhecido: %s", type(handler).__name__)
            return handler.parse(xls, file_stem)

    logger.warning("Nenhum layout FECFIN reconhecido para: %s", file_stem)
    return []
