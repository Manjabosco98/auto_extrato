import logging

import pandas as pd

from src.schemas.fecfin.base import FecfinHandler
from src.schemas.fecfin.registry import register


logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = {"Destinado à", "CPF/CNPJ", "Descrição", "Data", "Situação", "Valor"}


@register
class Midia(FecfinHandler):
    """Layout FECFIN — colunas fixas (Mídia, etc.).

    Estrutura do Excel:
      - Linha 0: título/cabeçalho vazio
      - Linha 1: colunas reais (Destinado à, CPF/CNPJ, Descrição, Data, Situação, Valor)
      - Linha 2+: dados

    A detecção é puramente por colunas — qualquer Excel com essa
    estrutura será reconhecido, independentemente do nome do arquivo.
    """

    bank = "Midia"

    def matches(self, xls: pd.ExcelFile, file_stem: str = "") -> bool:
        try:
            df = pd.read_excel(xls, sheet_name=0, header=None, nrows=2)
            if len(df) < 2:
                return False
            # Verifica a linha 1 (índice 1) — colunas reais
            colunas = set(
                str(c).strip() for c in df.iloc[1] if pd.notna(c)
            )
            return _REQUIRED_COLUMNS.issubset(colunas)
        except Exception:
            return False

    def parse(
        self, xls: pd.ExcelFile, file_stem: str
    ) -> list[tuple[str, pd.DataFrame]]:
        df = pd.read_excel(xls, sheet_name=0)
        df.columns = df.iloc[0]
        df = df[1:].reset_index(drop=True)

        df["Data"] = pd.to_datetime(df["Data"], format="%d/%m/%Y", errors="coerce")
        df = df.dropna(subset=["Data"])

        df["TIPO"] = df.apply(
            lambda x: "D" if "-" in str(x["Valor"]) else "C", axis=1
        )

        df["Valor"] = (
            df["Valor"]
            .astype(str)
            .str.strip()
            .str.replace("-", "", regex=False)
            .str.replace("+", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.strip()
            .astype(float)
        )

        df["Data"] = df["Data"].dt.strftime("%d/%m/%Y")

        df["DESCRIÇÃO"] = df.apply(
            lambda x: f"{x['Descrição']} {x['Destinado à']} {x['CPF/CNPJ']}",
            axis=1,
        )
        df["DESCRIÇÃO"] = (
            df["DESCRIÇÃO"]
            .str.strip()
            .str.replace("nan", "", regex=False)
            .str.strip()
            .str.upper()
        )

        df = df[["Data", "DESCRIÇÃO", "Valor", "TIPO"]]
        df = df.rename(columns={"Data": "DATA", "Valor": "VALOR"})

        partes = file_stem.split("_")
        banco = partes[-1] if len(partes) >= 3 else "GERAL"

        return [(banco, df)]
