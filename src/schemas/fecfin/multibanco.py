import logging

import pandas as pd

from src.schemas.fecfin.base import FecfinHandler
from src.schemas.fecfin.registry import register


logger = logging.getLogger(__name__)

_BANCOS_CONHECIDOS = {"UNICRED", "SICOOB", "SICREDI", "CAIXA"}


def _identificar_banco(nome_aba: str) -> str | None:
    nome_upper = nome_aba.upper()
    for banco in _BANCOS_CONHECIDOS:
        if banco in nome_upper:
            return banco
    return None


def _processar_unicred(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[5]
    df = df[6:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["OBS"]} {x["TIPO"]} {x["Nº DOC"]} '
        f'{x["HISTÓRICO"]} {x["OBS P/ INTERNAS"]}',
        axis=1,
    )

    df["DESCRIÇÃO"] = (
        df["DESCRIÇÃO"].str.replace("nan", "", regex=False).str.upper()
    )
    df = df[["DATA", "DESCRIÇÃO", "ENTRADA", "SAIDA", "SALDO"]]

    df["ENTRADA"] = pd.to_numeric(df["ENTRADA"], errors="coerce").fillna(0)
    df["SAIDA"] = pd.to_numeric(df["SAIDA"], errors="coerce").fillna(0)

    df["VALOR"] = df["ENTRADA"].where(df["ENTRADA"] != 0, df["SAIDA"] * -1)
    df = df.loc[~df["DESCRIÇÃO"].astype(str).str.upper().str.contains("SALDO", na=False)]

    df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
    df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
    df["VALOR"] = df["VALOR"].abs()
    return df


def _processar_sicoob(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[5]
    df = df[6:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["OBS P/ CONT"]} {x["TIPO"]} {x["Nº DOC"]} '
        f'{x["HISTÓRICO"]} {x["OBS P/ INTERNAS"]}',
        axis=1,
    )

    df["DESCRIÇÃO"] = (
        df["DESCRIÇÃO"].str.replace("nan", "", regex=False).str.upper()
    )
    df = df[["DATA", "DESCRIÇÃO", "ENTRADA", "SAIDA", "SALDO"]]
    df = df.dropna(subset=["DATA"])

    df["ENTRADA"] = pd.to_numeric(df["ENTRADA"], errors="coerce").fillna(0)
    df["SAIDA"] = pd.to_numeric(df["SAIDA"], errors="coerce").fillna(0)

    df["VALOR"] = df["ENTRADA"].where(df["ENTRADA"] != 0, df["SAIDA"] * -1)
    df = df.loc[~df["DESCRIÇÃO"].astype(str).str.upper().str.contains("SALDO", na=False)]

    df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
    df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
    df["VALOR"] = df["VALOR"].abs()
    return df


def _processar_sicredi(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[6]
    df = df[7:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["OBS"]} {x["TIPO"]} {x["Nº DOC"]} {x["HISTÓRICO"]}',
        axis=1,
    )

    df["DESCRIÇÃO"] = (
        df["DESCRIÇÃO"].str.replace("nan", "", regex=False).str.upper()
    )
    df = df[["DATA", "DESCRIÇÃO", "ENTRADA", "SAIDA", "SALDO"]]

    df["ENTRADA"] = pd.to_numeric(df["ENTRADA"], errors="coerce").fillna(0)
    df["SAIDA"] = pd.to_numeric(df["SAIDA"], errors="coerce").fillna(0)

    df["VALOR"] = df["ENTRADA"].where(df["ENTRADA"] != 0, df["SAIDA"] * -1)
    df = df.loc[~df["DESCRIÇÃO"].astype(str).str.upper().str.contains("SALDO", na=False)]

    df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
    df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
    df["VALOR"] = df["VALOR"].abs()
    return df


def _processar_caixa(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[4]
    df = df[5:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["OBS"]} {x["TIPO"]} {x["Nº DOC"]} '
        f'{x["HISTÓRICO"]} {x["DADOS BANCÁRIOS"]}',
        axis=1,
    )

    df["DESCRIÇÃO"] = (
        df["DESCRIÇÃO"].str.replace("nan", "", regex=False).str.upper()
    )
    df = df[["DATA", "DESCRIÇÃO", "ENTRADA", "SAIDA", "SALDO"]]

    df["ENTRADA"] = pd.to_numeric(df["ENTRADA"], errors="coerce").fillna(0)
    df["SAIDA"] = pd.to_numeric(df["SAIDA"], errors="coerce").fillna(0)

    df["VALOR"] = df["ENTRADA"].where(df["ENTRADA"] != 0, df["SAIDA"] * -1)
    df = df.loc[~df["DESCRIÇÃO"].astype(str).str.upper().str.contains("SALDO", na=False)]

    df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
    df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
    df["VALOR"] = df["VALOR"].abs()

    df["DATA"] = pd.to_datetime(df["DATA"], errors="coerce").dt.strftime("%d/%m/%Y")
    return df


_PROCESSADORES = {
    "UNICRED": _processar_unicred,
    "SICOOB": _processar_sicoob,
    "SICREDI": _processar_sicredi,
    "CAIXA": _processar_caixa,
}


@register
class MultiBanco(FecfinHandler):
    """Layout FECFIN — multi-banco (CEMAF).

    Planilha com múltiplas abas, cada aba representando um banco
    (UNICRED, SICOOB, SICREDI, CAIXA).  Cada aba tem uma estrutura
    de colunas diferente.
    """

    bank = "MultiBanco"

    def matches(self, xls: pd.ExcelFile) -> bool:
        abas = [str(aba).upper() for aba in xls.sheet_names]
        for aba in abas:
            if _identificar_banco(aba):
                return True
        return False

    def parse(
        self, xls: pd.ExcelFile, file_stem: str
    ) -> list[tuple[str, pd.DataFrame]]:
        resultado: list[tuple[str, pd.DataFrame]] = []

        for aba in xls.sheet_names:
            banco = _identificar_banco(str(aba))
            if not banco:
                continue

            processador = _PROCESSADORES.get(banco)
            if not processador:
                logger.warning("Banco sem processador: %s (aba: %s)", banco, aba)
                continue

            try:
                df = processador(xls, aba)
                resultado.append((banco, df))
            except Exception:
                logger.exception("Erro ao processar aba %s (banco %s)", aba, banco)

        return resultado
