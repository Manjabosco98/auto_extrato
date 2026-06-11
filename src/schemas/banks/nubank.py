import re
import pandas as pd
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register

@register
class Nubank(BankHandler):
    bank = "Nubank"
    layout_head_lines = 5
    layout_tail_lines = 30

    def _pick_layout(self, pdf: list[str]):
        head_tail = "\n".join(
            pdf[: self.layout_head_lines] + pdf[-self.layout_tail_lines:]
        )
        candidates = [
            fn
            for fn in self._layouts()
            if any(sig in head_tail for sig in fn._layout_signatures)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda fn: len(fn._layout_signatures))

    @layout("Nu Pagamentos S.A. ", "Nu Financeira S.A. ", "NU PAGAMENTOS ")
    def layout1(self, pdf):
        pdf = pdf.copy()
        empresa = pdf[0]
        conta = pdf[2]
        cpf = pdf[1]
        datas = pdf[3]
        pdf = pdf[11:]
        pdf = [item for item in pdf if not any(texto in item for texto in [empresa, cpf, conta, datas, "CNPJ", "Tem alguma dúvida?", "metropolitanas) ", "Caso a solução ", "disponíveis em nubank.com.br", "Extrato gerado ", "O saldo líquido ", "Não nos responsabilizamos ", "Asseguramos a autenticidade ", "Nu Financeira S.A.", "e Investimento", "Nu Pagamentos S.A. - Instituição de Pagamento", "Investimento Pagamento"])]

        padrao_data = re.compile(
            r"\b\d{1,2}\s+(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+\d{4}\b"
            r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
            re.I
        )

        padrao_valor = re.compile(
            r"(?:R\$\s*)?[+-]?\s*\d{1,3}(?:\.\d{3})*,\d{2}[CD]?"
            r"|(?:R\$\s*)?[+-]?\s*\d+,\d{2}[CD]?"
            r"|R\$\s*[+-]?\s*\d+(?:,\d{2})?[CD]?",
            re.I
        )

        padrao_inicio = re.compile(
            r"^(Transfer[eê]ncia|Pagamento|Reembolso|Aplic[aç][aã]o|Resgate|Valor adicionado)",
            re.I
        )

        padrao_ignorar = re.compile(
            r"(CPF|CNPJ|Ag[eê]ncia 0001 Conta|VALORES EM R\$|Saldo final|Saldo inicial|"
            r"Rendimento líquido|Movimenta[cç][oõ]es|Tem alguma dúvida|Caso a solução|"
            r"Extrato gerado|Nu Financeira|Nu Pagamentos|Ouvidoria|Atendimento|"
            r"Não nos responsabilizamos|Asseguramos a autenticidade|disponíveis em nubank)",
            re.I
        )

        padrao_total = re.compile(r"^Total de (entradas|sa[ií]das)\b", re.I)

        resultado = []
        data_atual = ""
        bloco = []

        def fechar_bloco():
            if not bloco or not data_atual:
                return

            texto = " ".join(bloco)
            texto = re.sub(r"\s+", " ", texto).strip()

            valores = list(padrao_valor.finditer(texto))
            if not valores:
                return

            valor = valores[-1].group().strip()
            texto_sem_valor = (texto[:valores[-1].start()] + " " + texto[valores[-1].end():]).strip()
            texto_sem_valor = re.sub(r"\s+", " ", texto_sem_valor).strip(" -")

            resultado.append(f"{data_atual} {texto_sem_valor} {valor}")

        for item in pdf:
            linha = re.sub(r"\s+", " ", str(item)).strip()
            if not linha or padrao_ignorar.search(linha):
                continue

            data = padrao_data.search(linha)
            if data:
                fechar_bloco()
                bloco = []
                data_atual = data.group()
                linha = (linha[:data.start()] + " " + linha[data.end():]).strip()

                if not linha or padrao_total.search(linha):
                    continue

            if padrao_total.search(linha):
                fechar_bloco()
                bloco = []
                continue

            if padrao_inicio.search(linha):
                fechar_bloco()
                bloco = [linha]
            elif bloco:
                bloco.append(linha)

        fechar_bloco()

        padrao = re.compile(
            r"""
            ^(?P<data>\d{2}\s+[A-Z]{3}\s+\d{4})\s+
            (?P<descricao>.*?)
            \s+
            (?P<valor>-?\s?\d{1,3}(?:\.\d{3})*,\d{2})
            $
            """,
            re.VERBOSE
        )

        dados = []

        for item in resultado:
            match = padrao.match(item)

            if match:
                data = match.group("data")
                descricao = match.group("descricao").strip()
                valor_bruto = match.group("valor").strip()

                # Tipo baseado no sinal do valor
                tipo = "D" if valor_bruto.startswith("-") else "C"

                # Normaliza valor para float
                valor = (
                    valor_bruto
                    .replace("-", "")
                    .replace(" ", "")
                    .replace(".", "")
                    .replace(",", ".")
                )

                valor = float(valor)

                dados.append({
                    "DATA": data,
                    "DESCRIÇÃO": descricao,
                    "VALOR": valor,
                    "TIPO": tipo
                })
            else:
                print("Não capturou:", item)

        df = pd.json_normalize(dados)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
        df = df.rename(columns={"VALOR_BRUTO": "VALOR"})
        df["DATA"] = (
            df["DATA"]
            .astype(str)
            .str.strip()
            .str.upper()
            .str.replace("JAN", "01", regex=False)
            .str.replace("FEV", "02", regex=False)
            .str.replace("MAR", "03", regex=False)
            .str.replace("ABR", "04", regex=False)
            .str.replace("MAI", "05", regex=False)
            .str.replace("JUN", "06", regex=False)
            .str.replace("JUL", "07", regex=False)
            .str.replace("AGO", "08", regex=False)
            .str.replace("SET", "09", regex=False)
            .str.replace("OUT", "10", regex=False)
            .str.replace("NOV", "11", regex=False)
            .str.replace("DEZ", "12", regex=False)
            .str.replace(" ", "/", regex=False)
        )
        df["DATA"] = pd.to_datetime(df["DATA"], format="%d/%m/%Y", errors="coerce").dt.strftime("%d/%m/%Y")
        df["VALOR"] = df["VALOR"].abs()
        return df