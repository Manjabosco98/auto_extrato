import re
import pandas as pd

from src.schemas.base import BankHandler, layout
from src.schemas.registry import register

@register
class Stone(BankHandler):
    bank = "Stone"
    
    @layout("Stone Instituição de Pagamento S.A.")
    def layout1(self, pdf):
        indice = next((i for i, item in enumerate(pdf) if "nformações do Comprovante" in item), None)
        pdf = pdf[10:indice]
        pdf = [item for item in pdf if not any(texto in item for texto in ["DATA TIPO DESCRIÇÃO VALOR SALDO CONTRAPARTE", "Período: ", "Página ", "Emitido ", "Extrato de conta corrente"])]
        
        padrao_movimento = re.compile(
            r"^(?P<DATA>\d{2}/\d{2}/\d{2})\s+"
            r"(?P<TIPO>Entrada|Saída)\s+"
            r"(?P<DESCRICAO_INTERNA>.*?)\s*"
            r"(?P<VALOR>-?\s*R\$\s*[\d\.]+,\d{2})\s+"
            r"(?P<SALDO>R\$\s*[\d\.]+,\d{2})"
            r"(?:\s+(?P<DESCRICAO_FINAL>.*))?$"
        )

        registros = []

        for i, linha in enumerate(pdf):
            linha = linha.strip()

            match = padrao_movimento.match(linha)

            if not match:
                continue

            dados = match.groupdict()

            anterior = pdf[i - 1].strip() if i > 0 else ""
            proxima = pdf[i + 1].strip() if i + 1 < len(pdf) else ""

            # Se a linha anterior não começa com data, ela provavelmente é parte da descrição
            if not re.match(r"^\d{2}/\d{2}/\d{2}", anterior):
                descricao_anterior = anterior
            else:
                descricao_anterior = ""

            # Se a próxima linha não começa com data, ela provavelmente é categoria/complemento
            if not re.match(r"^\d{2}/\d{2}/\d{2}", proxima):
                complemento = proxima
            else:
                complemento = ""

            descricao = " ".join(
                parte for parte in [
                    descricao_anterior,
                    dados["DESCRICAO_INTERNA"],
                    dados["DESCRICAO_FINAL"] or "",
                ]
                if parte
            ).strip()

            registros.append({
                "DATA": dados["DATA"],
                "DESCRIÇÃO": f"{dados["TIPO"]} | {descricao} | {complemento}",
                "TIPO": "D" if "-" in dados["VALOR"] else "C",
                "VALOR": float(dados["VALOR"].replace("R$", "").replace(".", "").replace(",", ".").replace("-", "").strip()),
                "SALDO": float(dados["SALDO"].replace("R$", "").replace(".", "").replace(",", ".").replace("-", "").strip()),
            })

        df = pd.DataFrame(registros)
        # df["DATA"] = pd.to_datetime(df["DATA"], format="%d/%m/%y", errors="coerce")
        df["VALOR"] = df.apply(lambda row: -row["VALOR"] if row["TIPO"] == "D" else row["VALOR"], axis=1)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
        df["DESCRIÇÃO"] = (
            df["DESCRIÇÃO"]
            .astype(str)
            .str.replace(r"^(SAÍDA|ENTRADA)\s*\|\s*", "", regex=True)
            .str.strip()
        )
        df["DATA"] = pd.to_datetime(df["DATA"], format="%d/%m/%y", errors="coerce").dt.strftime("%d/%m/%Y").astype(str)
        df["VALOR"] = df["VALOR"].abs()
        return df