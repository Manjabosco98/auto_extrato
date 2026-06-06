import re
import pandas as pd
from datetime import datetime, timedelta
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class Assas(BankHandler):
    bank = "asaas"
    layout_scan_lines = 4

    def _pick_layout(self, pdf: list[str]):
        head_tail = "\n".join(
            pdf[: self.layout_scan_lines] + pdf[-self.layout_scan_lines:]
        )
        candidates = [
            fn
            for fn in self._layouts()
            if all(sig in head_tail for sig in fn._layout_signatures)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda fn: len(fn._layout_signatures))

    @layout("ASAAS")
    def layout1(self, pdf):
        pdf = pdf[6:]
        pdf = [item for item in pdf if not any(texto in item for texto in ["Data ", "CNPJ: ", "ASAAS "])]

        data_re = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
        valor_re = re.compile(r"R\$\s*-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}")

        inicio_mov_re = re.compile(
            r"^(Transação via Pix|Cobrança recebida|Taxa de boleto|"
            r"Taxa de mensageria|Taxa do Pix|Taxa de notificação)",
            re.I,
        )

        ignorar_re = re.compile(
            r"^data\b|^movimentações$|^valor$|cnpj:|asaas gestão|"
            r"saldo inicial|saldo final|período|extrato gerado|agência:|conta:",
            re.I,
        )

        def limpar(txt: str) -> str:
            return re.sub(r"\s+", " ", txt).strip()

        def montar(data: str, partes: list[str]) -> str:
            texto = limpar(" ".join(partes))
            valores = list(valor_re.finditer(texto))

            if not data or not valores:
                return ""

            valor = valores[-1].group()
            entrada = valor if "R$ -" not in valor else ""
            saida = valor if "R$ -" in valor else ""

            descricao = limpar(valor_re.sub(" ", texto))

            if not descricao:
                return ""

            return limpar(f"{data} | {descricao} | ENTRADA: {entrada} | SAÍDA: {saida}")

        linhas = [limpar(l) for l in pdf if limpar(l)]
        resultado = []
        pendentes = []
        i = 0

        while i < len(linhas):
            linha = linhas[i]

            if ignorar_re.search(linha):
                pendentes = []
                i += 1
                continue

            m_data = data_re.search(linha)

            # Linha sem data: início ou complemento de descrição
            if not m_data:
                if inicio_mov_re.search(linha):
                    pendentes = [linha]
                else:
                    pendentes.append(linha)

                i += 1
                continue

            data = m_data.group()
            resto = limpar(linha[:m_data.start()] + " " + linha[m_data.end():])

            partes = pendentes[:]
            pendentes = []

            if resto:
                partes.append(resto)

            tem_valor = bool(valor_re.search(resto))
            j = i + 1

            # Pega complemento depois da data/valor, sem invadir próxima movimentação
            while j < len(linhas):
                prox = linhas[j]

                if ignorar_re.search(prox):
                    j += 1
                    continue

                if data_re.search(prox):
                    break

                if inicio_mov_re.search(prox):
                    break

                if valor_re.search(prox) and tem_valor:
                    break

                partes.append(prox)

                if valor_re.search(prox):
                    tem_valor = True

                j += 1

            mov = montar(data, partes)

            if mov:
                resultado.append(mov)

            i = j

        dados = resultado

        REGEX_MOVIMENTACAO = re.compile(
            r'^\s*'
            r'(?P<data>\d{2}/\d{2}/\d{4})\s*'
            r'\|\s*'
            r'(?P<descricao>.*?)\s*'
            r'\|\s*ENTRADA:\s*'
            r'(?P<entrada>R\$\s*-?\d{1,3}(?:\.\d{3})*,\d{2})?'
            r'\s*\|\s*SAÍDA:\s*'
            r'(?P<saida>R\$\s*-?\d{1,3}(?:\.\d{3})*,\d{2})?'
            r'\s*$'
        )

        resultado = []

        for item in dados:
            item = item.strip()

            if not item:
                continue

            match = REGEX_MOVIMENTACAO.match(item)

            if not match:
                print("Não capturou:", item)
                continue

            data = match.group("data")
            descricao = match.group("descricao").strip()
            entrada = match.group("entrada")
            saida = match.group("saida")

            if entrada:
                tipo = "C"
            elif saida:
                tipo = "D"
            else:
                tipo = ""

            resultado.append([
                data,
                f"| {descricao} |",
                f"ENTRADA: {entrada} |" if entrada else "ENTRADA: |",
                f"SAÍDA: {saida}" if saida else "SAÍDA:",
                tipo
            ])

        df = pd.DataFrame(resultado, columns=["DATA", "DESCRIÇÃO", "ENTRADA", "SAIDA", "TIPO"])
        df["ENTRADA"] = (
            df["ENTRADA"]
            .fillna("")
            .astype(str)
            .str.replace("ENTRADA:", "", regex=False)
            .str.replace("R$", "", regex=False)
            .str.replace("|", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.replace(" ", "", regex=False)
            .str.strip()
        )
        df["SAIDA"] = (
            df["SAIDA"]
            .fillna("")
            .astype(str)
            .str.replace("SAÍDA:", "", regex=False)
            .str.replace("R$", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.replace(" ", "", regex=False)
            .str.strip()
        )
        df["VALOR"] = df["ENTRADA"].replace("", pd.NA).fillna(df["SAIDA"]).astype(float)
        df["DESCRIÇÃO"] = (df["DESCRIÇÃO"].astype(str).str.replace("|", ""))
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
        df = df[
            ~df["DESCRIÇÃO"]
            .astype(str)
            .str.replace(r"\s+", "", regex=True)
            .str.upper()
            .str.contains("SALDO", na=False)
        ]
        return df
