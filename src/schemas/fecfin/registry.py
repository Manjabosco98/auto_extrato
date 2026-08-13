import logging
from dataclasses import dataclass, field

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


@dataclass
class ResultadoDispatch:
    """Resultado do dispatch com o motivo de nao ter gerado lancamentos.

    Sem o motivo, "nenhum layout casou" e "o layout casou mas nenhuma aba
    gerou lancamentos" chegavam identicos na notificacao, como
    "sem layout reconhecido".
    """

    resultados: list[tuple[str, pd.DataFrame]] = field(default_factory=list)
    layouts_tentados: list[str] = field(default_factory=list)

    @property
    def reconhecido(self) -> bool:
        return bool(self.resultados)

    def motivo(self) -> str:
        if self.resultados:
            return ""
        if not self.layouts_tentados:
            return "nenhum layout reconheceu o arquivo"
        tentados = ", ".join(self.layouts_tentados)
        return f"layout {tentados} reconheceu o arquivo, mas nenhuma aba gerou lancamentos"


def dispatch_fecwin_detalhado(
    xls: pd.ExcelFile,
    file_stem: str,
) -> ResultadoDispatch:
    """Tenta reconhecer o layout de um arquivo FECFIN, guardando o motivo.

    Um handler que reconhece o arquivo mas nao extrai nada nao encerra a
    busca: os handlers seguintes ainda sao tentados.  Sem isso um layout
    generico (ex.: MultiBanco, que casa com qualquer aba cujo nome tenha
    SICOOB/CAIXA) sequestrava arquivos de layouts mais especificos.
    """
    resultado = ResultadoDispatch()

    for handler in _FECFIN_HANDLERS:
        if not handler.matches(xls, file_stem=file_stem):
            continue

        nome_handler = type(handler).__name__
        logger.info("Layout FECFIN reconhecido: %s", nome_handler)
        resultado.layouts_tentados.append(nome_handler)

        resultados = handler.parse(xls, file_stem)
        if resultados:
            resultado.resultados = resultados
            return resultado

        logger.warning(
            "Layout FECFIN %s reconheceu %s mas nao gerou lancamentos; "
            "tentando os proximos layouts",
            nome_handler,
            file_stem,
        )

    if not resultado.layouts_tentados:
        logger.warning("Nenhum layout FECFIN reconhecido para: %s", file_stem)
    return resultado


def dispatch_fecwin(
    xls: pd.ExcelFile,
    file_stem: str,
) -> list[tuple[str, pd.DataFrame]]:
    """Tenta reconhecer o layout de um arquivo FECFIN.

    Retorna uma lista de ``(nome_banco, DataFrame)`` — um par
    por banco encontrado no arquivo.  Se nenhum layout reconhecer,
    retorna lista vazia.
    """
    return dispatch_fecwin_detalhado(xls, file_stem).resultados
