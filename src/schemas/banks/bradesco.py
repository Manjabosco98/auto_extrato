import re
import pandas as pd
from datetime import datetime, timedelta
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class Bradesco(BankHandler):
    bank = "Bradesco"

    @layout("Agência | Conta Total Disponível (R$) Total (R$)")
    def layout1(self, pdf):
        indice = next((i for i, item in enumerate(pdf) if "Saldos Invest Fácil / Plus" in item), None)
        pdf = pdf[4:indice]

        pdf = [item for item in pdf if not any(texto in item for texto in ["Total ", "Os dados acima têm como base ", "Últimos Lançamento", "Data Lançamento Dcto. Crédito (R$) Débito (R$) Saldo (R$)"])]


        padrao_movimento = re.compile(
            r"^(?:(?P<DATA>\d{2}/\d{2}/\d{4})\s+)?"
            r"(?:(?P<HISTORICO_LINHA>.*?)\s+)?"
            r"(?P<DOCUMENTO>\d+)\s+"
            r"(?P<VALOR>-?\d{1,3}(?:\.\d{3})*,\d{2})\s+"
            r"(?P<SALDO>-?\d{1,3}(?:\.\d{3})*,\d{2})$"
        )

        padrao_saldo_anterior = re.compile(
            r"^(?P<DATA>\d{2}/\d{2}/\d{4})\s+"
            r"(?P<HISTORICO>SALDO ANTERIOR)\s+"
            r"(?P<SALDO>-?\d{1,3}(?:\.\d{3})*,\d{2})$"
        )

        historicos_validos = (
            "ENCARGOS LIMITE DE CRED",
            "IOF S/ UTILIZACAO LIMITE",
            "TRANSFERENCIA PIX",
            "PAGTO ELETRON COBRANCA",
            "CARTAO CREDITO ANUIDADE",
            "TARIFA BANCARIA",
            "TRANSF PGTO PIX",
            "PAGTO ELETRON TRIBUTO",
            "PIX QR CODE DINAMICO",
            "PIX RECEBIDO",
            "PIX ENVIADO",
        )

        def eh_historico(linha):
            return any(linha.startswith(h) for h in historicos_validos)

        def moeda_para_float(valor):
            return float(valor.replace(".", "").replace(",", "."))

        def adicionar_descricao(registros, indice, texto):
            if indice is None:
                return

            if registros[indice]["DESCRIÇÃO"]:
                registros[indice]["DESCRIÇÃO"] += f" {texto}"
            else:
                registros[indice]["DESCRIÇÃO"] = texto

        registros = []
        historico_pendente = None
        descricao_pendente = []
        ultima_data = None
        ultimo_indice = None

        for linha in pdf:
            linha = linha.strip()

            if not linha:
                continue

            match_saldo = padrao_saldo_anterior.match(linha)

            if match_saldo:
                dados = match_saldo.groupdict()

                registros.append({
                    "DATA": dados["DATA"],
                    "HISTORICO": dados["HISTORICO"],
                    "DOCUMENTO": None,
                    "DESCRIÇÃO": None,
                    "CREDITO": None,
                    "DEBITO": None,
                    "SALDO": dados["SALDO"]
                })

                ultima_data = dados["DATA"]
                historico_pendente = None
                descricao_pendente = []
                ultimo_indice = len(registros) - 1
                continue

            match = padrao_movimento.match(linha)

            if match:
                dados = match.groupdict()

                valor_float = moeda_para_float(dados["VALOR"])

                historico = dados["HISTORICO_LINHA"] or historico_pendente
                descricao = " ".join(descricao_pendente) if descricao_pendente else None

                registros.append({
                    "DATA": dados["DATA"] or ultima_data,
                    "HISTORICO": historico,
                    "DOCUMENTO": dados["DOCUMENTO"],
                    "DESCRIÇÃO": descricao,
                    "CREDITO": dados["VALOR"] if valor_float > 0 else None,
                    "DEBITO": dados["VALOR"] if valor_float < 0 else None,
                    "SALDO": dados["SALDO"]
                })

                if dados["DATA"]:
                    ultima_data = dados["DATA"]

                ultimo_indice = len(registros) - 1
                historico_pendente = None
                descricao_pendente = []
                continue

            if eh_historico(linha):
                historico_pendente = linha
                descricao_pendente = []
            else:
                if historico_pendente:
                    descricao_pendente.append(linha)
                else:
                    adicionar_descricao(registros, ultimo_indice, linha)

        registros = [registro for registro in registros if "SALDO" not in (registro["HISTORICO"] or "")]
        df = pd.DataFrame(registros)
        df["DESCRIÇÃO"] = (
            df["HISTORICO"].fillna("") + " " +
            df["DESCRIÇÃO"].fillna("") + " " +
            df["DOCUMENTO"].fillna("").astype(str)
        ).str.strip()
        df["TIPO"] = df.apply(lambda row: "C" if pd.notna(row["CREDITO"]) else "D", axis=1)
        df["VALOR"] = df.apply(lambda row: row["CREDITO"] if row["TIPO"] == "C" else row["DEBITO"], axis=1)
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
