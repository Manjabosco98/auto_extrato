import re
import pandas as pd
from datetime import datetime, timedelta
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class Unicred(BankHandler):
    bank = "UNICRED"
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
    
    @layout("CENTRAL DE RELACIONAMENTO: Capitais e regiões metropolitanas: ")
    def layout1(self, pdf):
        pdf = pdf[10:]
        pdf = [item for item in pdf if not any(texto in item for texto in [
                "Data Lançamentos Valor (R$) ",
                "Lançamentos futuros ",
                "CENTRAL DE RELACIONAMENTO: ",
                "0800 200 7302 - No exterior: ",
                "Saldo no final ",
                "Saldo ",
                "Total disponível ",
                "Limite de cheque especial ",
                "IOF ",
                "Juros cheque especial ",
                "Juros adiant. ",
                "Tarifas pendentes ",
                "Data "
            ])]

        rx_data = re.compile(r"\b\d{2}/\d{2}(?:/\d{2,4})?\b")
        rx_valor = re.compile(
            r"(?:-\s*)?(?:R\$\s*)?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}[CD]?"
            r"|(?:-\s*)?R\$\s*\d+(?:,\d{2})?[CD]?"
        )

        rx_ignorar = re.compile(
            r"^(?:"
            r"DATA\s+LANÇAMENTOS|DATA\s+HIST[ÓO]RICO|DATA\s+DESCRIÇÃO|"
            r"LANÇAMENTOS FUTUROS|CENTRAL DE RELACIONAMENTO|0800|"
            r"SALDO\s|SALDO NO FINAL|SALDO ATUAL|TOTAL DISPON[IÍ]VEL|"
            r"LIMITE DE CHEQUE ESPECIAL|IOF|JUROS|TARIFAS PENDENTES|"
            r"EXTRATO|PER[IÍ]ODO|P[ÁA]G\.?|BANCO|OUVIDORIA|SAC"
            r")",
            re.IGNORECASE
        )

        limpas = []
        for linha in pdf:
            linha = re.sub(r"\s+", " ", str(linha)).strip()
            if linha and not rx_ignorar.search(linha):
                limpas.append(linha)

        resultado = []
        pendentes = []
        i = 0

        while i < len(limpas):
            linha = limpas[i]
            m_data = rx_data.search(linha)

            if not m_data:
                pendentes.append(linha)
                pendentes = pendentes[-4:]
                i += 1
                continue

            data = m_data.group(0)
            resto = linha[m_data.end():].strip()
            valores = list(rx_valor.finditer(resto))

            if not valores:
                pendentes = []
                i += 1
                continue

            valor = valores[0].group(0).strip()
            desc_linha = resto[:valores[0].start()].strip()

            partes_antes = pendentes[:]
            partes_depois = []
            pendentes = []

            j = i + 1

            while j < len(limpas):
                prox = limpas[j]

                if rx_data.search(prox):
                    break

                texto_atual = " ".join(partes_antes + partes_depois + [desc_linha])
                parenteses_abertos = texto_atual.count("(") > texto_atual.count(")")

                existe_data_logo = any(
                    rx_data.search(limpas[k])
                    for k in range(j + 1, min(j + 3, len(limpas)))
                )

                if parenteses_abertos or not existe_data_logo:
                    partes_depois.append(prox)
                    j += 1
                else:
                    break

            partes_desc = partes_antes + partes_depois
            if desc_linha:
                partes_desc.append(desc_linha)

            descricao = re.sub(r"\s+", " ", " ".join(partes_desc)).strip()
            movimento = re.sub(r"\s+", " ", f"{data} {descricao} {valor}").strip()
            resultado.append(movimento)

            i = j

        dados = resultado

        padrao = re.compile(
            r"^"
            r"(?P<data>\d{2}/\d{2}/\d{4})"
            r"\s+"
            r"(?P<descricao>.+?)"
            r"\s+"
            r"(?P<valor>-?\s*R\$\s*\d{1,3}(?:\.\d{3})*,\d{2})"
            r"$"
        )

        movimentacoes = []

        for linha in dados:
            match = padrao.match(linha)

            if match:
                data = match.group("data")
                descricao = match.group("descricao").strip()
                valor = re.sub(r"\s+", " ", match.group("valor").strip())

                movimentacoes.append({
                    "DATA": data,
                    "DESCRIÇÃO": descricao,
                    "VALOR": valor
                })
            else:
                print("Linha não reconhecida:", linha)

        df = pd.json_normalize(movimentacoes)
        df["VALOR"] = (
            df["VALOR"]
            .str.strip()
            .str.replace("R$", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.replace(" ", "", regex=False)
            .astype(float)
        )
        df["TIPO"] = df.apply(lambda row: "C" if row["VALOR"] > 0 else "D", axis=1)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
        return df