import logging
import re
import unicodedata

import pandas as pd

from src.schemas.fecfin.base import FecfinHandler
from src.schemas.fecfin.registry import register


logger = logging.getLogger(__name__)

_BANCOS_CE_PART = {"CORA", "CAIXA"}
_BANCOS_CEMAF_PART = {"UNICRED", "CAIXA"}
_BANCOS_ALOHA = {"INTER", "CAIXA"}
_BANCOS_ADP_PART = {"INTER", "ITAU", "CAIXA"}
_BANCOS_ACR = {"SICOOB", "CAIXA"}
_BANCOS_CEMAF_60 = {"SICOOB", "CAIXA"}
_BANCOS_AD_52 = {"SICOOB", "INTER", "CAIXA"}


def _normalizar_identificador(valor: str) -> str:
    """Normaliza nomes vindos do Excel/Drive para comparacao de layout.

    Arquivos copiados ou renomeados no Drive podem conter NBSP e outros
    separadores visualmente identicos a espacos. A selecao do layout nao deve
    depender dessa diferenca invisivel.
    """
    normalizado = unicodedata.normalize("NFKC", str(valor)).upper()
    return re.sub(r"[^A-Z0-9]+", " ", normalizado).strip()


def _identificar_tipo_arquivo(file_stem: str) -> str | None:
    stem_normalizado = _normalizar_identificador(file_stem)
    if "CEMAF PART" in stem_normalizado:
        return "CEMAF_PART"
    if "CEMAF 60" in stem_normalizado:
        return "CEMAF_60"
    if "ADP PART" in stem_normalizado:
        return "ADP_PART"
    if "CE PART" in stem_normalizado:
        return "CE_PART"
    if "ALOHA" in stem_normalizado:
        return "ALOHA"
    if "ACR" in stem_normalizado:
        return "ACR"
    if "AD 52" in stem_normalizado:
        return "AD_52"
    return None


def _identificar_bancos(xls: pd.ExcelFile, tipo: str) -> dict[str, str]:
    """Retorna dict {nome_banco: nome_aba} para abas conhecidas."""
    bancos_map = {
        "CEMAF_PART": _BANCOS_CEMAF_PART,
        "CEMAF_60": _BANCOS_CEMAF_60,
        "ALOHA": _BANCOS_ALOHA,
        "ADP_PART": _BANCOS_ADP_PART,
        "ACR": _BANCOS_ACR,
        "AD_52": _BANCOS_AD_52,
    }
    bancos_alvo = bancos_map.get(tipo, _BANCOS_CE_PART)
    resultado: dict[str, str] = {}
    for aba in xls.sheet_names:
        aba_normalizada = _normalizar_identificador(aba)
        for banco in bancos_alvo:
            if banco in aba_normalizada and banco not in resultado:
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
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
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
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
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
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
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
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


def _processar_inter_adp(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[1]
    df = df[2:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["OBS CONTABILIDADE"]} {x["TIPO"]} '
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
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


def _processar_itau_adp(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[1]
    df = df[2:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["OBS CONTABILIDADE"]} {x["OBS INTERNA"]} {x["TIPO"]} '
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
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


def _processar_caixa_adp(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[4]
    df = df[5:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["HISTÓRICO"]} {x["Nº DOC"]} {x["TIPO"]} {x["OBS"]}',
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
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


def _processar_sicoob_acr(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[1]
    df = df[2:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["OBS / CONT"]} {x["OBS / INT"]} {x["TIPO"]} '
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
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


def _processar_sicoob_cemaf60(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[1]
    df = df[2:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["OBS"]} {x["OBS / INT"]} {x["TIPO"]} '
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
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


def _processar_caixa_acr(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[4]
    df = df[5:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["HISTÓRICO"]} {x["Nº DOC"]} {x["TIPO"]} {x["OBS"]}',
        axis=1,
    )
    df["DESCRIÇÃO"] = (
        df["DESCRIÇÃO"]
        .str.replace("nan", "", regex=False)
        .str.replace("None", "", regex=False)
        .str.strip()
        .str.upper()
    )
    df["DESCRIÇÃO"] = df["DESCRIÇÃO"].replace("", pd.NA)
    df = df.dropna(subset=["DESCRIÇÃO"])

    df = df[["DATA", "DESCRIÇÃO", "ENTRADA", "SAÍDA"]]
    df["ENTRADA"] = pd.to_numeric(df["ENTRADA"], errors="coerce").fillna(0)
    df["SAÍDA"] = pd.to_numeric(df["SAÍDA"], errors="coerce").fillna(0)

    df["VALOR"] = df["ENTRADA"].where(df["ENTRADA"] != 0, df["SAÍDA"] * -1)
    df = df.loc[~df["DESCRIÇÃO"].astype(str).str.upper().str.contains("SALDO", na=False)]

    df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
    df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
    df["VALOR"] = df["VALOR"].abs()
    df["DATA"] = df["DATA"].apply(
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


def _processar_sicoob_ad52(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[2]
    df = df[3:].reset_index(drop=True)

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
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


def _processar_inter_ad52(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[4]
    df = df[5:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["OBS"]} {x["TIPO"]} {x["DOC"]} {x["HISTÓRICO"]}',
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
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
        if pd.notnull(x)
        else ""
    )
    return df


def _processar_caixa_ad52(xls: pd.ExcelFile, aba: str) -> pd.DataFrame:
    df = pd.read_excel(xls, sheet_name=aba)
    df.columns = df.iloc[3]
    df = df[4:].reset_index(drop=True)

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f'{x["HISTÓRICO"]} {x["Nº DOC"]} {x["TIPO"]} {x["OBS"]}',
        axis=1,
    )
    df["DESCRIÇÃO"] = (
        df["DESCRIÇÃO"]
        .str.replace("nan", "", regex=False)
        .str.replace("None", "", regex=False)
        .str.strip()
        .str.upper()
    )
    df["DESCRIÇÃO"] = df["DESCRIÇÃO"].replace("", pd.NA)
    df = df.dropna(subset=["DESCRIÇÃO"])

    df = df[["DATA", "DESCRIÇÃO", "ENTRADA", "SAÍDA"]]
    df["ENTRADA"] = pd.to_numeric(df["ENTRADA"], errors="coerce").fillna(0)
    df["SAÍDA"] = pd.to_numeric(df["SAÍDA"], errors="coerce").fillna(0)

    df["VALOR"] = df["ENTRADA"].where(df["ENTRADA"] != 0, df["SAÍDA"] * -1)
    df = df.loc[~df["DESCRIÇÃO"].astype(str).str.upper().str.contains("SALDO", na=False)]

    df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
    df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
    df["VALOR"] = df["VALOR"].abs()
    df["DATA"] = df["DATA"].apply(
        lambda x: pd.to_datetime(x, errors="coerce", dayfirst=True).strftime("%d/%m/%Y")
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

_PROCESSADORES_ALOHA = {
    "INTER": _processar_cora,
    "CAIXA": _processar_caixa_ce_part,
}

_PROCESSADORES_ADP_PART = {
    "INTER": _processar_inter_adp,
    "ITAU": _processar_itau_adp,
    "CAIXA": _processar_caixa_adp,
}

_PROCESSADORES_ACR = {
    "SICOOB": _processar_sicoob_acr,
    "CAIXA": _processar_caixa_acr,
}

_PROCESSADORES_CEMAF_60 = {
    "SICOOB": _processar_sicoob_cemaf60,
    "CAIXA": _processar_caixa_acr,
}

_PROCESSADORES_AD_52 = {
    "SICOOB": _processar_sicoob_ad52,
    "INTER": _processar_inter_ad52,
    "CAIXA": _processar_caixa_ad52,
}


@register
class CePart(FecfinHandler):
    """Layout FECFIN — CE PART / CEMAF PART / ALOHA / ADP PART / ACR / CEMAF 60 / AD 52.

    Planilhas multi-banco com abas por instituição.

    CE PART: bancos CORA e CAIXA.
      - CORA: header row 1, colunas OBS/OBS INTERNA/TIPO/DOC/HISTÓRICO
      - CAIXA: header row 4, colunas HISTÓRICO/Nº DOC/TIPO/OBS/OBS INT

    CEMAF PART: bancos UNICRED e CAIXA.
      - UNICRED: header row 5, colunas OBS/TIPO/Nº DOC/HISTÓRICO
      - CAIXA: header row 4, colunas OBS/TIPO/Nº DOC/HISTÓRICO

    ALOHA: bancos INTER e CAIXA.
      - INTER: header row 1, colunas OBS/OBS INTERNA/TIPO/DOC/HISTÓRICO
      - CAIXA: header row 4, colunas HISTÓRICO/Nº DOC/TIPO/OBS/OBS INT

    ADP PART: bancos INTER, ITAU e CAIXA.
      - INTER: header row 1, colunas OBS CONTABILIDADE/TIPO/DOC/HISTÓRICO
      - ITAU: header row 1, colunas OBS CONTABILIDADE/OBS INTERNA/TIPO/DOC/HISTÓRICO
      - CAIXA: header row 4, colunas HISTÓRICO/Nº DOC/TIPO/OBS

    ACR: bancos SICOOB e CAIXA.
      - SICOOB: header row 1, colunas OBS / CONT/OBS / INT/TIPO/DOC/HISTÓRICO
      - CAIXA: header row 4, colunas HISTÓRICO/Nº DOC/TIPO/OBS

    CEMAF 60: bancos SICOOB e CAIXA.
      - SICOOB: header row 1, colunas OBS/OBS / INT/TIPO/DOC/HISTÓRICO
      - CAIXA: header row 4, colunas HISTÓRICO/Nº DOC/TIPO/OBS

    AD 52: bancos SICOOB, INTER e CAIXA.
      - SICOOB: header row 2, colunas OBS/OBS INTERNA/TIPO/DOC/HISTÓRICO
      - INTER: header row 4, colunas OBS/TIPO/DOC/HISTÓRICO
      - CAIXA: header row 3, colunas HISTÓRICO/Nº DOC/TIPO/OBS
    """

    bank = "CePart"

    def matches(self, xls: pd.ExcelFile, file_stem: str = "") -> bool:
        tipo = _identificar_tipo_arquivo(file_stem)
        if not tipo:
            return False
        bancos = _identificar_bancos(xls, tipo)
        if not bancos:
            logger.warning(
                "Layout FECFIN %s identificado pelo nome, mas nenhuma aba bancaria "
                "foi reconhecida. Abas encontradas: %s",
                tipo,
                xls.sheet_names,
            )
        return len(bancos) > 0

    def parse(
        self, xls: pd.ExcelFile, file_stem: str
    ) -> list[tuple[str, pd.DataFrame]]:
        tipo = _identificar_tipo_arquivo(file_stem)
        if not tipo:
            return []

        bancos = _identificar_bancos(xls, tipo)
        processadores = {
            "CEMAF_PART": _PROCESSADORES_CEMAF_PART,
            "CEMAF_60": _PROCESSADORES_CEMAF_60,
            "ALOHA": _PROCESSADORES_ALOHA,
            "ADP_PART": _PROCESSADORES_ADP_PART,
            "ACR": _PROCESSADORES_ACR,
            "AD_52": _PROCESSADORES_AD_52,
        }.get(tipo, _PROCESSADORES_CE_PART)

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
