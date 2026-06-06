import re
import pandas as pd

from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class PagSeguro(BankHandler):
    bank = "PagSeguro"

    @layout("290 - PagSeguro Internet S/A")
    def layout1(self, pdf):
        pdf = pdf[9:]
        pdf = [item for item in pdf if not any(texto in item for texto in [" Saldo do dia ", "Data "])]

        texto_linhas = "\n".join(pdf)
        padrao = r"(\d{2}/\d{2}/\d{4})\s+(.*?)\s+(-?R\$\s*[\d\.]+,\d{2})"
        registros = re.findall(padrao, texto_linhas)

        df = pd.DataFrame(registros, columns=["DATA", "DESCRIÇÃO", "VALOR"])
        df["VALOR"] = (
            df["VALOR"]
            .str.replace("R$", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.replace(" ", "", regex=False)
            .astype(float)
        )
        df["TIPO"] = df.apply(lambda x: "C" if x["VALOR"] > 0 else "D", axis=1)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        df["VALOR"] = df["VALOR"].abs()
        return df
