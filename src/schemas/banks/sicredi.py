import re
import pandas as pd
from datetime import datetime, timedelta
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class Sicredi(BankHandler):
    bank = "Sicredi"

    @layout("Associado: ", "Cooperativa: ")
    def layout1(self, pdf):
        pdf = pdf[6:]
        pdf = [item for item in pdf if not any(texto in item for texto in ["Lançamentos Futuros ", "Data ", "Valores das operações ", "Sicredi Fone ", "0800 ", "SAC ", "Ouvidoria "])]

        REGEX_MOVIMENTACAO = re.compile(
            r'^\s*'
            r'(?P<data>\d{2}/\d{2}/\d{4})\s+'
            r'(?P<descricao>.*?)\s+'
            r'(?P<valor>-?\d{1,3}(?:\.\d{3})*,\d{2})\s+'
            r'(?P<saldo>-?\d{1,3}(?:\.\d{3})*,\d{2})'
            r'\s*$'
        )

        resultado = []

        for item in pdf:
            item = item.strip()

            if not item:
                continue

            match = REGEX_MOVIMENTACAO.match(item)

            if not match:
                print("Não capturou:", item)
                continue

            data = match.group("data")
            descricao = match.group("descricao").strip()
            valor = match.group("valor")
            saldo = match.group("saldo")

            tipo = "D" if valor.startswith("-") else "C"

            resultado.append([
                data,
                descricao,
                valor,
                saldo,
                tipo
            ])
        df = pd.DataFrame(resultado, columns=["DATA", "DESCRIÇÃO", "VALOR", "SALDO", "TIPO"])
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
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
        return df
    
    @layout("Associado:", "Cooperativa:")
    def layout2(self, pdf):
        pdf = pdf[6:]
        pdf = [item for item in pdf if not any(texto in item for texto in ["SALDO", " of ", "Firefox ", "Lançamentos Futuros ", "Data ", "Valores das operações ", "Sicredi Fone ", "0800 ", "SAC ", "Ouvidoria "])]

        REGEX_MOVIMENTACAO = re.compile(
            r'^\s*'
            r'(?P<data>\d{2}/\d{2}/\d{4})\s+'
            r'(?P<descricao>.*?)\s+'
            r'(?P<valor>-?\d{1,3}(?:\.\d{3})*,\d{2})\s+'
            r'(?P<saldo>-?\d{1,3}(?:\.\d{3})*,\d{2})'
            r'\s*$'
        )

        resultado = []

        for item in pdf:
            item = item.strip()

            if not item:
                continue

            match = REGEX_MOVIMENTACAO.match(item)

            if not match:
                print("Não capturou:", item)
                continue

            data = match.group("data")
            descricao = match.group("descricao").strip()
            valor = match.group("valor")
            saldo = match.group("saldo")

            tipo = "D" if valor.startswith("-") else "C"

            resultado.append([
                data,
                descricao,
                valor,
                saldo,
                tipo
            ])
        df = pd.DataFrame(resultado, columns=["DATA", "DESCRIÇÃO", "VALOR", "SALDO", "TIPO"])
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
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
        return df