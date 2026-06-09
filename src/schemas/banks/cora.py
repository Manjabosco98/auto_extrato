import re
import pandas as pd

from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class Cora(BankHandler):
    bank = "Cora"
    layout_head_lines = 5
    layout_tail_lines = 5

    def _pick_layout(self, pdf: list[str]):
        head_tail = "\n".join(
            pdf[: self.layout_head_lines] + pdf[-self.layout_tail_lines:]
        )
        candidates = [
            fn
            for fn in self._layouts()
            if all(sig in head_tail for sig in fn._layout_signatures)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda fn: len(fn._layout_signatures))

    @layout("Cora SCFI ")
    def layout1(self, pdf):
        empresa = pdf[0]
        pdf = pdf[9:]
        pdf = [item for item in pdf if not any(texto in item for texto in [
            empresa,
            "CNPJ ",
            "Agência: ",
            "Cora SCFI - CNPJ 37.880.206/0001-63",
            "Ouvidoria:",
            "Extrato gerado no dia ",
        ])]

        rx_espacos = re.compile(r"\s+")
        rx_data = re.compile(r"\b\d{2}/\d{2}(?:/\d{2,4})?\b")
        rx_saldo_dia = re.compile(r"\bSaldo do dia\b", re.I)

        rx_valor = re.compile(
            r"([+-]\s*)?"
            r"(?:R\$\s*)?"
            r"-?"
            r"\d{1,3}(?:\.\d{3})*,\d{2}"
            r"[CD]?"
            r"$"
        )

        # Inícios confiáveis de movimentação neste extrato
        rx_inicio_mov = re.compile(
            r"^(?:"
            r"Transf Pix enviada|Transf Pix recebida|Transferência recebida|"
            r"Pagamento recebido|Pgto QR Code Pix|"
            r"Boleto pago|Empr[eé]stimo Cora"
            r")\b",
            re.I
        )

        rx_ignorar = re.compile(
            r"^(?:"
            r"COMERCIAL GOIANIA DE BATERIAS|CNPJ|Ag[êe]ncia|Conta|"
            r"Cora SCFI - CNPJ|Ouvidoria|Extrato gerado|"
            r"p[aá]g\s+\d+\s+de\s+\d+|Saldo do dia|"
            r"SALDO ANTERIOR|SALDO FINAL|SALDO DISPON[IÍ]VEL|"
            r"DATA HIST[ÓO]RICO VALOR|DATA DESCRI[ÇC][ÃA]O VALOR"
            r")",
            re.I
        )

        resultado = []
        data_atual = None
        pendente = []

        for linha in pdf:
            linha = rx_espacos.sub(" ", str(linha).strip())

            if not linha:
                continue

            data_encontrada = rx_data.search(linha)

            # A data do grupo vem da linha "Saldo do dia"
            if data_encontrada and rx_saldo_dia.search(linha):
                data_atual = data_encontrada.group()
                pendente = []
                continue

            # Linha só com data, comum em quebra visual do PDF
            if data_encontrada and not rx_valor.search(linha):
                data_atual = data_encontrada.group()
                pendente = []
                continue

            # Linha com data + movimentação na mesma string
            if data_encontrada:
                data_atual = data_encontrada.group()
                linha = rx_data.sub("", linha, count=1).strip()

            if not data_atual:
                continue

            if rx_ignorar.search(linha):
                continue

            # Se começou nova movimentação antes de fechar a anterior, descarta pendente incompleta
            if rx_inicio_mov.search(linha) and pendente and not rx_valor.search(" ".join(pendente)):
                pendente = []

            pendente.append(linha)
            texto = rx_espacos.sub(" ", " ".join(pendente)).strip()

            valor_encontrado = rx_valor.search(texto)

            if valor_encontrado:
                valor = valor_encontrado.group().strip()
                descricao = texto[:valor_encontrado.start()].strip()

                if descricao and rx_inicio_mov.search(descricao):
                    movimento = f"{data_atual} {descricao} {valor}"
                    resultado.append(rx_espacos.sub(" ", movimento).strip())

                pendente = []

        dados = resultado

        padrao = re.compile(
            r"^(?P<data>\d{2}/\d{2}/\d{4})\s+"
            r"(?P<descricao>.+?)\s+"
            r"(?P<sinal>[+-])\s*R\$\s*"
            r"(?P<valor>\d{1,3}(?:\.\d{3})*,\d{2})$"
        )

        movimentacoes_tratadas = []

        for linha in dados:
            match = padrao.match(linha.strip())

            if not match:
                print(f"LINHA NÃO CAPTURADA: {linha}")
                continue

            dados = match.groupdict()

            sinal = dados["sinal"]
            valor = dados["valor"]

            movimentacoes_tratadas.append({
                "DATA": dados["data"],
                "DESCRIÇÃO": dados["descricao"].strip(),
                "VALOR": f"{sinal} R$ {valor}",
                "TIPO": "C" if sinal == "+" else "D"
            })

        df = pd.json_normalize(movimentacoes_tratadas)
        df["VALOR"] = (
            df["VALOR"]
            .str.strip()
            .str.replace("-", "", regex=False)
            .str.replace("+", "", regex=False)
            .str.replace("R$", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .astype(float)
        )
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
        return df
        
