import logging

import pandas as pd

from src.schemas.excel.base import ExcelBankHandler


logger = logging.getLogger(__name__)

_EXCEL_HANDLERS: list[ExcelBankHandler] = []


class ExcelLayoutNotRecognized(Exception):
    pass


def register(cls):
    """Registra um handler de extrato bancário em Excel.

    Remove duplicatas pelo nome da classe e adiciona à lista
    de handlers candidatos.
    """
    _EXCEL_HANDLERS[:] = [
        h
        for h in _EXCEL_HANDLERS
        if not (
            type(h).__module__ == cls.__module__
            and type(h).__name__ == cls.__name__
        )
    ]
    _EXCEL_HANDLERS.append(cls())
    return cls


def dispatch_excel(xls: pd.ExcelFile, file_stem: str = "") -> pd.DataFrame:
    """Reconhece o layout de um extrato em Excel e o converte.

    Levanta ``ExcelLayoutNotRecognized`` quando nenhum handler reconhece
    a planilha.
    """
    for handler in _EXCEL_HANDLERS:
        if not handler.matches(xls, file_stem=file_stem):
            continue

        logger.info("Layout Excel reconhecido: %s", type(handler).__name__)
        return handler.parse(xls, file_stem)

    raise ExcelLayoutNotRecognized(
        f"Nenhum layout Excel reconhecido. Abas: {list(xls.sheet_names)}"
    )
