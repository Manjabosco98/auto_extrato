import re
import numpy as np
import pandas as pd
from datetime import datetime

class Pagcorp():
    bank = "PAGCORP"

    def __init__(self, df):
        self.df = df

    def layout1(self):
        df = self.df
        df = df[["Data/Hora", "Apelido", "Identificação","Tipo Trans.", "Histórico", "Crédito", "Débito"]]
        df["Débito"] = df["Débito"] * -1
        df["VALOR"] = np.where(
            df["Débito"] != 0,
            df["Débito"],
            df["Crédito"]
        )
        df["TIPO"] = df.apply(lambda row: "C" if row["VALOR"] > 0 else "D", axis=1)
        df = df[df["Apelido"] != "TOTAL"]
        df["DESCRIÇÃO"] = df.apply(lambda row: f"{row["Tipo Trans."]} {row["Apelido"]} {row["Identificação"]} {row["Histórico"]}", axis=1)
        df = df[["Data/Hora", "DESCRIÇÃO", "VALOR", "TIPO"]]
        df["DESCRIÇÃO"] = (
            df["DESCRIÇÃO"]
            .str.strip()
            .str.replace("nan", "", regex=False)
            .str.replace("  ", " ", regex=False)
        )
        df["Data/Hora"] = pd.to_datetime(df["Data/Hora"], format="%d/%m/%Y %H:%M", errors="coerce").dt.strftime("%d/%m/%Y")
        df = df.rename(columns={"Data/Hora": "DATA"})
        df["VALOR"] = df["VALOR"].abs()
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
        return df

    