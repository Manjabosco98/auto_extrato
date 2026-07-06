import logging

import pandas as pd

from src.schemas.fecfin.base import FecfinHandler
from src.schemas.fecfin.registry import register


logger = logging.getLogger(__name__)

_BANCOS_CE_PART = {"CORA", "CAIXA"}
_BANCOS_CEMAF_PART = {"UNICRED", "CAIXA"}


def _identificar_tipo_arquivo(file_stem: str) -> str | None:
    stem_upper = file_stem.upper()
    if "CEMAF PART" in stem_upper:
        return "CEMAF_PART"
    if "CE PART" in stem_upper:
        return "CE_PART"
    return None


def _identificar_bancos(xls: pd.ExcelFile, tipo: str) -> dict[str, str]:
    """Retorna dict {nome_banco: nome_aba} para abas conhecidas."""
    bancos_alvo = _BANCOS_CEMAF_PART if tipo == "CEMAF_PART" else _BANCOS_CE_PART
    resultado: dict[str, str] = {}
    for aba in xls.sheet_names:
        aba_upper = str(aba).upper()
        for banco in bancos_alvo:
            if banco in aba_upper and banco not in resultado:
                resultado[banco] = aba
    return resultado


def _processar_cora(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[1]
    df = df[2:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["OBS"]} {x["OBS INTERNA"]} {x["TIPO"]} '
        f'{x["DOC"]} {x["HISTÓRICO"]}',
        axis=1,
    )
    df["DESCRIÇÃO"] = (
        df["DESCRIÇÃO"].str.replace("nan", "", regex=False).str.strip().str.upper()
    )

    df = df[["DATA", "DESCRIÇÃO", "ENTRADA", "SAÍDA"]]
    df["ENTRADA"] = pd.to_numeric(df["ENTRADA"], errors="coerce").fillna(0)
    df["SAÍDA"] = pd.to_numeric(df["SAÍDA"], errors="coerce").fillna(0)

    df["VALOR"] = df["ENTRADA"].where(df["ENTRADA"] != 0, df["SAÍDA"] * -1)
    df = df.loc[~df["DESCRIÇÃO"].astype(str).str.upper().str.contains("SALDO", na=False)]

    df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
    df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
    df["VALOR"] = df["VALOR"].abs()
    df["DATA"] = df["DATA"].apply(
        lambda x: pd.to_datetime(x, errors="coerce").strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


def _processar_caixa_ce_part(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[4]
    df = df[5:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["HISTÓRICO"]} {x["Nº DOC"]} {x["TIPO"]} '
        f'{x["OBS"]} {x["OBS INT"]}',
        axis=1,
    )
    df["DESCRIÇÃO"] = (
        df["DESCRIÇÃO"].str.replace("nan", "", regex=False).str.strip().str.upper()
    )

    df = df[["DATA", "DESCRIÇÃO", "ENTRADA", "SAÍDA"]]
    df["ENTRADA"] = pd.to_numeric(df["ENTRADA"], errors="coerce").fillna(0)
    df["SAÍDA"] = pd.to_numeric(df["SAÍDA"], errors="coerce").fillna(0)

    df["VALOR"] = df["ENTRADA"].where(df["ENTRADA"] != 0, df["SAÍDA"] * -1)
    df = df.loc[~df["DESCRIÇÃO"].astype(str).str.upper().str.contains("SALDO", na=False)]

    df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
    df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
    df["VALOR"] = df["VALOR"].abs()
    df["DATA"] = df["DATA"].apply(
        lambda x: pd.to_datetime(x, errors="coerce").strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


def _processar_unicred_cemaf(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[5]
    df = df[6:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["OBS"]} {x["TIPO"]} {x["Nº DOC"]} {x["HISTÓRICO"]}',
        axis=1,
    )
    df["DESCRIÇÃO"] = (
        df["DESCRIÇÃO"].str.replace("nan", "", regex=False).str.strip().str.upper()
    )

    df = df[["DATA", "DESCRIÇÃO", "ENTRADA", "SAIDA"]]
    df["ENTRADA"] = pd.to_numeric(df["ENTRADA"], errors="coerce").fillna(0)
    df["SAIDA"] = pd.to_numeric(df["SAIDA"], errors="coerce").fillna(0)

    df["VALOR"] = df["ENTRADA"].where(df["ENTRADA"] != 0, df["SAIDA"] * -1)
    df = df.loc[~df["DESCRIÇÃO"].astype(str).str.upper().str.contains("SALDO", na=False)]

    df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
    df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
    df["VALOR"] = df["VALOR"].abs()
    df["DATA"] = df["DATA"].apply(
        lambda x: pd.to_datetime(x, errors="coerce").strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


def _processar_caixa_cemaf(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[4]
    df = df[5:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["OBS"]} {x["TIPO"]} {x["Nº DOC"]} {x["HISTÓRICO"]}',
        axis=1,
    )
    df["DESCRIÇÃO"] = (
        df["DESCRIÇÃO"].str.replace("nan", "", regex=False).str.strip().str.upper()
    )

    df = df[["DATA", "DESCRIÇÃO", "ENTRADA", "SAIDA"]]
    df["ENTRADA"] = pd.to_numeric(df["ENTRADA"], errors="coerce").fillna(0)
    df["SAIDA"] = pd.to_numeric(df["SAIDA"], errors="coerce").fillna(0)

    df["VALOR"] = df["ENTRADA"].where(df["ENTRADA"] != 0, df["SAIDA"] * -1)
    df = df.loc[~df["DESCRIÇÃO"].astype(str).str.upper().str.contains("SALDO", na=False)]

    df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
    df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
    df["VALOR"] = df["VALOR"].abs()
    df["DATA"] = df["DATA"].apply(
        lambda x: pd.to_datetime(x, errors="coerce").strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


_PROCESSADORES_CE_PART = {
    "CORA": _processar_cora,
    "CAIXA": _processar_caixa_ce_part,
}

_PROCESSADORES_CEMAF_PART = {
    "UNICRED": _processar_unicred_cemaf,
    "CAIXA": _processar_caixa_cemaf,
}


@register
class CePart(FecfinHandler):
    """Layout FECFIN — CE PART / CEMAF PART.

    Planilhas multi-banco com abas por instituição.

    CE PART: bancos CORA e CAIXA.
      - CORA: header row 1, colunas OBS/OBS INTERNA/TIPO/DOC/HISTÓRICO
      - CAIXA: header row 4, colunas HISTÓRICO/Nº DOC/TIPO/OBS/OBS INT

    CEMAF PART: bancos UNICRED e CAIXA.
      - UNICRED: header row 5, colunas OBS/TIPO/Nº DOC/HISTÓRICO
      - CAIXA: header row 4, colunas OBS/TIPO/Nº DOC/HISTÓRICO
    """

    bank = "CePart"

    def matches(self, xls: pd.ExcelFile, file_stem: str = "") -> bool:
        tipo = _identificar_tipo_arquivo(file_stem)
        logger.info("CePart.matches file_stem=%r tipo=%s", file_stem, tipo)
        if not tipo:
            return False
        bancos = _identificar_bancos(xls, tipo)
        logger.info("CePart.matches bancos=%s abas=%s", list(bancos.keys()), xls.sheet_names)
        return len(bancos) > 0

    def parse(
        self, xls: pd.ExcelFile, file_stem: str
    ) -> list[tuple[str, pd.DataFrame]]:
        tipo = _identificar_tipo_arquivo(file_stem)
        if not tipo:
            return []

        bancos = _identificar_bancos(xls, tipo)
        processadores = (
            _PROCESSADORES_CEMAF_PART if tipo == "CEMAF_PART" else _PROCESSADORES_CE_PART
        )

        resultado: list[tuple[str, pd.DataFrame]] = []

        for banco, aba in bancos.items():
            processador = processadores.get(banco)
            if not processador:
                logger.warning(
                    "Banco %s sem processador para %s (aba: %s)", banco, tipo, aba
                )
                continue

            try:
                df = processador(xls, aba)
                if not df.empty:
                    resultado.append((banco, df))
            except Exception:
                logger.exception("Erro ao processar aba %s (banco %s)", aba, banco)

        return resultado
