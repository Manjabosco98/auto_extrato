import pandas as pd

from src.schemas.base import BankHandler


class LayoutNotRecognized(Exception):
    pass


_BANKS: list[BankHandler] = []


def register(cls):
    _BANKS[:] = [
        bank
        for bank in _BANKS
        if not (
            type(bank).__module__ == cls.__module__
            and type(bank).__name__ == cls.__name__
        )
    ]
    _BANKS.append(cls())
    return cls


def dispatch(pdf: list[str]) -> pd.DataFrame:
    for bank in _BANKS:
        if bank.matches(pdf):
            return bank.parse(pdf)

    cabecalho = "\n".join(pdf[:5])
    raise LayoutNotRecognized(
        f"Nenhum layout reconhecido. Primeiras linhas do PDF:\n{cabecalho}"
    )
