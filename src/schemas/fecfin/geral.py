import logging

import pandas as pd

from src.schemas.fecfin.base import FecfinHandler
from src.schemas.fecfin.registry import register
from src.utils.helpers import normalizar_parc


logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = {
    "Data Competência",
    "Cliente / Fornecedor / Funcionario",
    "Conta",
    "Descrição",
    "Banco",
    "Entrada | Saida",
    "Valor",
    "Data Pagamento | Recebimento",
}


@register
class Geral(FecfinHandler):
    """Layout FECFIN — Geral (Caixa / contador).

    Planilha com header na linha 4 e colunas:
    Data Competência, Cliente / Fornecedor / Funcionario, Conta,
    Descrição, Banco, Forma de Pagamento, Parc, Valor,
    Data Pagamento | Recebimento, NF, Pedido, CONC, Entrada | Saida.

    A coluna ``Banco`` é usada para separar por banco.
    """

    bank = "Geral"

    def matches(self, xls: pd.ExcelFile, file_stem: str = "") -> bool:
        try:
            df = pd.read_excel(xls, sheet_name=0, header=4, nrows=0)
            colunas = set(str(c).strip().rstrip("\n") for c in df.columns)
            required_clean = {c.strip().rstrip("\n") for c in _REQUIRED_COLUMNS}
            return required_clean.issubset(colunas)
        except Exception:
            return False

    def parse(
        self, xls: pd.ExcelFile, file_stem: str
    ) -> list[tuple[str, pd.DataFrame]]:
        df = pd.read_excel(xls, sheet_name=0, header=4,
                           converters={"Parc": normalizar_parc})

        # Limpar nomes das colunas (remover \n)
        df.columns = [str(c).strip().rstrip("\n") for c in df.columns]

        # Montar DESCRIÇÃO
        def _montar_descricao(row):
            partes = []
            nf = row.get("NF")
            if pd.notna(nf) and str(nf).strip():
                nf_str = str(nf).replace(".0", "").strip()
                partes.append(f"NF {nf_str}")
            desc = str(row.get("Descrição", "")).strip()
            if desc and desc != "nan":
                partes.append(desc)
            cliente = str(row.get("Cliente / Fornecedor / Funcionario", "")).strip()
            if cliente and cliente != "nan":
                partes.append(cliente)
            parc = str(row.get("Parc", "")).strip()
            if parc and parc != "nan":
                partes.append(parc)
            conta = str(row.get("Conta", "")).strip()
            if conta and conta != "nan":
                partes.append(conta)
            return " ".join(partes)

        df["DESCRIÇÃO"] = df.apply(_montar_descricao, axis=1)

        # TIPO
        df["TIPO"] = df["Entrada | Saida"].apply(
            lambda x: "C" if str(x).strip() == "Entrada" else "D"
        )

        # Renomear colunas
        df = df.rename(columns={
            "Data Pagamento | Recebimento": "DATA",
            "Valor": "VALOR",
        })

        # Formatar DATA
        df["DATA"] = pd.to_datetime(df["DATA"], errors="coerce").dt.strftime("%d/%m/%Y")

        # VALOR absoluto
        df["VALOR"] = pd.to_numeric(df["VALOR"], errors="coerce").fillna(0).abs()

        # Limpar DESCRIÇÃO
        df["DESCRIÇÃO"] = (
            df["DESCRIÇÃO"]
            .str.replace("nan", "", regex=False)
            .str.strip()
            .str.upper()
        )

        # Agrupar por Banco
        resultado: list[tuple[str, pd.DataFrame]] = []

        for banco, grupo in df.groupby("Banco"):
            banco_str = str(banco).strip()
            df_banco = grupo[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]].copy()
            df_banco = df_banco.reset_index(drop=True)
            resultado.append((banco_str, df_banco))

        return resultado
