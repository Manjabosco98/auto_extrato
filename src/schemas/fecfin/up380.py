import logging
import re

import pandas as pd

from src.schemas.fecfin.base import FecfinHandler
from src.schemas.fecfin.registry import register


logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = {"Unnamed: 0", "Classificação", "Descrição", "Valor (R$)"}

_FILTROS = ["Saldo inicial", "Saldo final", "linhas"]


def _converter_valor(valor) -> float:
    """Converte valor brasileiro (1.234,56 ou -050) para float."""
    if pd.isna(valor):
        return 0.0
    texto = str(valor).strip()
    if not texto:
        return 0.0
    # Remover pontos de milhar e trocar virgula por ponto
    texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def _extrair_banco_do_stem(stem: str) -> str:
    """Extrai o nome do banco do stem do arquivo.

    Formato esperado: MMAA_FECFIN_NOME_BANCO ou similar.
    Ex: 0526_FECFIN_KAMINO_UP380 -> KAMINO
    """
    partes = stem.split("_")
    if len(partes) >= 3:
        return partes[2].strip().upper()
    return "GERAL"


@register
class Up380(FecfinHandler):
    """Layout FECFIN — UP380 / Kamino.

    Planilha com colunas:
    Unnamed: 0 (DATA), Classificação, Descrição, Valor (R$), Unnamed: 4, Unnamed: 5.

    A coluna ``Unnamed: 0`` precisa de forward-fill para preencher datas.
    O valor vem em formato brasileiro (1.234,56 ou -050).
    O banco é extraído do nome do arquivo.
    """

    bank = "Up380"

    def matches(self, xls: pd.ExcelFile) -> bool:
        try:
            df = pd.read_excel(xls, sheet_name=0, nrows=0)
            colunas = set(str(c).strip() for c in df.columns)
            return _REQUIRED_COLUMNS.issubset(colunas)
        except Exception:
            return False

    def parse(
        self, xls: pd.ExcelFile, file_stem: str
    ) -> list[tuple[str, pd.DataFrame]]:
        df = pd.read_excel(xls, sheet_name=0)

        # Renomear colunas
        df = df.rename(columns={
            "Unnamed: 0": "DATA",
            "Valor (R$)": "VALOR",
        })

        # Forward-fill na coluna DATA
        df["DATA"] = df["DATA"].ffill()

        # Filtrar linhas indesejadas
        filtro_classificacao = "|".join(_FILTROS)
        df = df.loc[
            ~df["Classificação"].astype(str).str.contains(filtro_classificacao, na=False)
        ]
        df = df.dropna(subset=["Classificação"])

        # Montar DESCRIÇÃO
        df["DESCRIÇÃO"] = df.apply(
            lambda x: f"{x['Classificação']} {x['Descrição']}", axis=1
        )
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()

        # Converter VALOR
        df["VALOR"] = df["VALOR"].apply(_converter_valor)

        # TIPO
        df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
        df["VALOR"] = df["VALOR"].abs()

        # Selecionar colunas finais
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]

        # Extrair banco do nome do arquivo
        banco = _extrair_banco_do_stem(file_stem)

        return [(banco, df)]
