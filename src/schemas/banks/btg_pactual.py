import re
import pandas as pd
from datetime import datetime, timedelta
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class BTG_Pactual(BankHandler):
    bank = "BTG Pactual"
    layout_head_lines = 5
    layout_tail_lines = 15

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

    @layout("Razão social CNPJ Banco Agência Conta")
    def layout1(self, pdf):
        pdf = pdf[18:]
        pdf = [item for item in pdf if not any(texto in item for texto in [
                    "Fale com ",
                    "Ligue para: ",
                    "Atendimento 24 ",
                    "Ouvidoria: ",
                    "Das 9h às 18h ",
                    "© 2026 BTG ",
                    "Data lançamento ",
                    "Saldo "
                ])]

        data_re = re.compile(r"\b\d{2}/\d{2}(?:/\d{2,4})?\b")

        # Valor precisa ter vírgula decimal. Evita pegar pedaços de chave Pix.
        valor_re = re.compile(
            r"(?<![\w/])(?:R\$\s*)?-?\d{1,3}(?:\.\d{3})*,\d{2}[CD]?(?!\w)"
        )

        ignorar_re = re.compile(
            r"^(data lançamento|descrição do lançamento|entradas|saídas|saldo|"
            r"saldo de abertura|saldo de fechamento|saldo final|saldo do dia|"
            r"saldo disponível|agência|conta|cpf|cnpj|extrato emitido|"
            r"página|pagina|ouvidoria|sac|central de atendimento|"
            r"período|periodo)$",
            re.I
        )

        inicio_mov_re = re.compile(
            r"^(pix\s+(?:enviado|recebido)|ted|doc|transfer[êe]ncia|"
            r"pagamento|compra|d[eé]bito|cr[eé]dito|boleto|tarifa)\b",
            re.I
        )

        def limpar(txt: str) -> str:
            return re.sub(r"\s+", " ", txt).strip()

        linhas_limpas = []
        for linha in pdf:
            linha = limpar(linha)
            if linha and not ignorar_re.search(linha):
                linhas_limpas.append(linha)

        resultado = []
        pendentes = []
        i = 0

        while i < len(linhas_limpas):
            linha = linhas_limpas[i]
            data_m = data_re.search(linha)

            if not data_m:
                pendentes.append(linha)
                i += 1
                continue

            data = data_m.group()
            antes_data = linha[:data_m.start()].strip()
            depois_data = linha[data_m.end():].strip()

            texto_base = limpar(" ".join(pendentes + [antes_data, depois_data]))

            valores = valor_re.findall(texto_base)

            if not valores:
                pendentes = []
                i += 1
                continue

            # Quando há valor + saldo, o valor da movimentação é o primeiro.
            valor_mov = valores[0]

            descricao = valor_re.sub(" ", texto_base)
            descricao = limpar(descricao)

            j = i + 1
            complementos = []

            while j < len(linhas_limpas):
                prox = linhas_limpas[j]

                if data_re.search(prox):
                    break

                if inicio_mov_re.search(prox):
                    break

                complementos.append(prox)
                j += 1

            if complementos:
                descricao = limpar(descricao + " " + " ".join(complementos))

            if descricao and not re.search(r"saldo de abertura|saldo de fechamento", descricao, re.I):
                resultado.append(limpar(f"{data} {descricao} {valor_mov}"))

            pendentes = []
            i = j

        dados = resultado

        padrao = r"^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})$"

        resultado = []

        for linha in dados:
            match = re.match(padrao, linha)

            if match:
                data = match.group(1)
                descricao = match.group(2)
                valor = match.group(3)

                resultado.append({
                    "DATA": data,
                    "DESCRIÇÃO": descricao,
                    "VALOR": valor
                })
            else:
                print("Linha não reconhecida:", linha)

        df = pd.json_normalize(resultado)
        df["VALOR"] = (df["VALOR"].str.replace(".", "").str.replace(",", ".").astype(float))
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        df["TIPO"] = df.apply(lambda row: "C" if row["VALOR"] > 0 else "D", axis=1)
        df["VALOR"] = df["VALOR"].abs()
        return df
