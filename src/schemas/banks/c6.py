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
        periodo = pdf[3].split(" • ")[-1].split(" ")[-1]
        indice = next((i for i, item in enumerate(pdf) if "Informações sujeitas a" in item), None)
        pdf = pdf[9:indice]
        pdf = [item for item in pdf if not any(texto in item for texto in ["Data Data", "Tipo Descrição Valor", "lançamento contábil", "Saldo do dia "])]

        padrao = r"^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+(-?R\$\s?\d{1,3}(?:\.\d{3})*,\d{2})$"
        dados = []
        for item in pdf:
            match = re.match(padrao, item)

            if match:
                data_1 = match.group(1)
                data_2 = match.group(2)
                descricao = match.group(3)
                valor = match.group(4)

                dados.append({
                    "DATA_LANCAMENTO": data_1,
                    "DATA_CONTABIL": data_2,
                    "DESCRIÇÃO": descricao,
                    "VALOR": valor
                })
            else:
                print("Não capturou:", item)
        df = pd.json_normalize(dados)
        df["VALOR"] = (
            df["VALOR"]
            .str.strip()
            .str.replace("R$", "", regex=False)
            .str.replace(" ", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .astype(float)
        )
        df["TIPO"] = df.apply(lambda x: "C" if x["VALOR"] > 0 else "D", axis=1)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
        df["DATA_CONTABIL"] = df.apply(lambda x: f"{x["DATA_CONTABIL"]}/{periodo}", axis=1)
        df["VALOR"] = df["VALOR"].astype(str).str.replace("-", "", regex=False)
        df = df[["DATA_CONTABIL", "DESCRIÇÃO", "VALOR", "TIPO"]]
        df = df.rename(columns={"DATA_CONTABIL": "DATA"})
        return df