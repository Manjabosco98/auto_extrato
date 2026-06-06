import re
import pandas as pd
from datetime import datetime, timedelta
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class C6(BankHandler):
    bank = "C6"

    @layout("Extrato exportado ")
    def layout1(self, pdf):
        indice = next((i for i, item in enumerate(pdf) if "Informações sujeitas a" in item), None)
        pdf = pdf[9:indice]
        pdf = [item for item in pdf if not any(texto in item for texto in ["Data Data", "Tipo Descrição Valor", "lançamento contábil", "Saldo do dia "])]

        PADRAO_C6 = re.compile(
            r"""
            ^
            (?P<data_lancamento>\d{2}/\d{2})\s+
            (?P<data_contabil>\d{2}/\d{2})\s+
            (?P<tipo_original>Entrada\s+PIX|Saída\s+PIX|Débito\s+de\s+Cartão|Outros\s+gastos|Pagamento)\s+
            (?P<descricao>.*?)
            \s+
            (?P<valor_original>-?R\$\s?[\d\.]+,\d{2})
            $
            """,
            re.VERBOSE
        )

        registros = []

        for linha in pdf:
            linha = " ".join(str(linha).split())

            match = PADRAO_C6.match(linha)

            if not match:
                continue

            dados = match.groupdict()

            valor_original = dados["valor_original"]

            tipo = "Saída" if valor_original.startswith("-") else "Entrada"

            registros.append({
                "data_lancamento": dados["data_lancamento"],
                "data_contabil": dados["data_contabil"],
                "tipo": tipo,
                "tipo_original": dados["tipo_original"],
                "descricao": dados["descricao"].strip(),
                "valor_original": valor_original,
            })

        df = pd.DataFrame(registros)
        df["descricao"] = df.apply(lambda row: f"{row['descricao']} | {row['tipo_original']}", axis=1)
        df["data_contabil"] = df.apply(lambda row: f"{row["data_contabil"]}/{datetime.now().year}", axis=1)
        df = df.rename(columns={"data_contabil": "DATA", "tipo": "TIPO", "descricao": "DESCRIÇÃO", "valor_original": "VALOR"})
        df["VALOR"] = (
            df["VALOR"]
            .str.replace("-", "", regex=False)
            .str.replace("+", "", regex=False)
            .str.replace("R$", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.strip()
            .astype(float)
        )
        df["TIPO"] = df["TIPO"].replace({
            "Entrada": "C",
            "Saída": "D"
        })
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
        df["DESCRIÇÃO"] = (
            df["DESCRIÇÃO"]
            .fillna("")
            .astype(str)
            .str.upper()
            .str.strip()
        )
        df["DESCRIÇÃO"] = (
            df["DESCRIÇÃO"]
            .str.split("|")
            .apply(lambda x: " | ".join([parte.strip() for parte in x[::-1]]) if isinstance(x, list) else x)
        )
        return df