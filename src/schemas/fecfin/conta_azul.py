import logging

import pandas as pd

from src.schemas.fecfin.base import FecfinHandler
from src.schemas.fecfin.registry import register


logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = {"Data movimento", "Descrição", "Nome do fornecedor/cliente", "Valor (R$)", "Conta bancária"}


@register
class ContaAzul(FecfinHandler):
    """Layout FECFIN — Conta Azul.

    Planilha de uma única aba com colunas:
    Data movimento, Descrição, Nome do fornecedor/cliente,
    Valor (R$), Conta bancária.

    A coluna ``Conta bancária`` é usada para separar por banco.
    """

    bank = "ContaAzul"

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

        df["DESCRIÇÃO"] = df.apply(
            lambda x: f"{x['Descrição']} {x['Nome do fornecedor/cliente']}",
            axis=1,
        )

        df = df[["Data movimento", "DESCRIÇÃO", "Valor (R$)", "Conta bancária"]]

        df["DESCRIÇÃO"] = (
            df["DESCRIÇÃO"]
            .astype(str)
            .str.strip()
            .str.replace("nan", "", regex=False)
            .str.upper()
        )

        df["VALOR"] = pd.to_numeric(df["Valor (R$)"], errors="coerce").fillna(0)
        df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
        df["VALOR"] = df["VALOR"].abs()

        df = df.rename(columns={"Data movimento": "DATA", "Conta bancária": "BANCO"})

        resultado: list[tuple[str, pd.DataFrame]] = []

        for banco, grupo in df.groupby("BANCO"):
            df_banco = grupo[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]].copy()
            df_banco = df_banco.reset_index(drop=True)
            resultado.append((str(banco).strip(), df_banco))

        return resultado
