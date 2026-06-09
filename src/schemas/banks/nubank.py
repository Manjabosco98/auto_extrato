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
        pdf = pdf[12:]
        pdf = [item for item in pdf if not any(texto in item for texto in [empresa, "CNPJ", "Tem alguma dúvida?", "metropolitanas) ", "Caso a solução ", "disponíveis em nubank.com.br", "Extrato gerado ", "O saldo líquido ", "Não nos responsabilizamos ", "Asseguramos a autenticidade ", "Nu Financeira S.A.", "e Investimento", "Nu Pagamentos S.A. - Instituição de Pagamento"])]

        rx_data = re.compile(
            r"^(?P<data>(?:\d{2}/\d{2}(?:/\d{2,4})?|\d{2}\s+"
            r"(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+\d{4}))\b",
            re.I
        )

        rx_valor = re.compile(
            r"(?P<valor>(?:R\$\s*)?[+-]?\s*(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}[CD]?)$",
            re.I
        )

        rx_inicio_mov = re.compile(
            r"^(?:Transferência enviada pelo Pix|Transferência Recebida|Transferência recebida pelo pix"
            r"Crédito em conta|Pagamento de boleto efetuado)\b",
            re.I
        )

        rx_total_entrada = re.compile(r"^Total de entradas\b", re.I)
        rx_total_saida = re.compile(r"^Total de saídas\b", re.I)
        rx_separador = re.compile(r"^(?:Saldo do dia|Saldo final|Saldo inicial)\b", re.I)

        rx_lixo_linha = re.compile(
            r"^(?:Movimentações|CNPJ\b|Agência\b|Conta\b|VALORES EM R\$|"
            r"Tem alguma dúvida|Extrato gerado|O saldo líquido|"
            r"Não nos responsabilizamos|Asseguramos|Nu Financeira|"
            r"Nu Pagamentos S\.A)",
            re.I
        )

        rx_lixo_meio = re.compile(
            r"\b(?:ADVOCACIA\s+364970674-3\s+)?"
            r"(?:\d{2}\s+DE\s+[A-ZÇ]+\s+DE\s+\d{4}\s+a\s+"
            r"\d{2}\s+DE\s+[A-ZÇ]+\s+DE\s+\d{4}\s+VALORES\s+EM\s+R\$)"
            r"|\bADVOCACIA\s+364970674-3\b",
            re.I
        )

        def limpar(texto: str) -> str:
            texto = rx_lixo_meio.sub(" ", texto)
            texto = re.sub(r"\s+", " ", texto)
            return texto.strip()

        def tipo_pela_descricao(descricao: str, tipo_bloco: str) -> str:
            if re.search(r"^(?:Transferência Recebida|Transferência recebida pelo pix|Crédito em conta)\b", descricao, re.I):
                return "entrada"

            if re.search(r"^(?:Transferência enviada pelo Pix|Transferência recebida pelo pix|Pagamento de boleto efetuado)\b", descricao, re.I):
                return "saida"

            return tipo_bloco

        def ajustar_sinal(valor: str, descricao: str, tipo_bloco: str) -> str:
            tipo = tipo_pela_descricao(descricao, tipo_bloco)
            valor_sem_sinal = re.sub(r"^[+-]\s*", "", valor.strip())

            if tipo == "saida":
                return "-" + valor_sem_sinal

            if tipo == "entrada":
                return valor_sem_sinal

            return valor.strip()

        def montar(data: str, partes: list[str], valor: str, tipo_bloco: str) -> str:
            descricao = limpar(" ".join(partes))
            valor = ajustar_sinal(valor, descricao, tipo_bloco)
            return limpar(f"{data} {descricao} {valor}")

        resultado = []
        data_atual = ""
        partes = []
        valor = ""
        tipo_bloco = ""

        for item in pdf:
            linha = limpar(item)

            if not linha:
                continue

            m_data = rx_data.match(linha)
            if m_data:
                if data_atual and partes and valor:
                    resultado.append(montar(data_atual, partes, valor, tipo_bloco))

                data_atual = m_data.group("data")
                partes = []
                valor = ""
                linha = limpar(linha[m_data.end():])

                if not linha:
                    continue

            if rx_total_entrada.match(linha):
                if data_atual and partes and valor:
                    resultado.append(montar(data_atual, partes, valor, tipo_bloco))
                tipo_bloco = "entrada"
                partes = []
                valor = ""
                continue

            if rx_total_saida.match(linha):
                if data_atual and partes and valor:
                    resultado.append(montar(data_atual, partes, valor, tipo_bloco))
                tipo_bloco = "saida"
                partes = []
                valor = ""
                continue

            if rx_separador.match(linha):
                if data_atual and partes and valor:
                    resultado.append(montar(data_atual, partes, valor, tipo_bloco))
                partes = []
                valor = ""
                tipo_bloco = ""
                continue

            if rx_lixo_linha.search(linha):
                continue

            if rx_inicio_mov.match(linha):
                if data_atual and partes and valor:
                    resultado.append(montar(data_atual, partes, valor, tipo_bloco))
                partes = []
                valor = ""

            if not partes and not rx_inicio_mov.match(linha):
                continue

            m_valor = rx_valor.search(linha)

            if m_valor:
                valor = m_valor.group("valor").strip()
                texto_sem_valor = limpar(linha[:m_valor.start()])
                if texto_sem_valor:
                    partes.append(texto_sem_valor)
            else:
                partes.append(linha)

        if data_atual and partes and valor:
            resultado.append(montar(data_atual, partes, valor, tipo_bloco))

        padrao = r"^(\d{2}\s+[A-Z]{3}\s+\d{4})\s+(.+?)\s+(-?\s?\d{1,3}(?:\.\d{3})*,\d{2})$"

        dados = []

        for item in resultado:
            match = re.match(padrao, item)

            if match:
                data = match.group(1)
                descricao = match.group(2).strip()
                valor = match.group(3).strip()

                dados.append({
                    "DATA": data,
                    "DESCRIÇÃO": descricao,
                    "VALOR": valor
                })
            else:
                dados.append({
                    "DATA": None,
                    "DESCRIÇÃO": item,
                    "VALOR": None
                })

        df = pd.json_normalize(dados)
        df["VALOR"] = (
            df["VALOR"]
            .str.strip()
            .str.replace(" ", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .astype(float)
        )
        df["TIPO"] = df.apply(lambda x: "C" if x["VALOR"] > 0 else "D", axis=1)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
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
    
    # @layout("CNPJ ")
    # def layout2(self, pdf):
    #     empresa = pdf[0]
    #     pdf = pdf[11:]
    #     pdf = [item for item in pdf if not any(texto in item for texto in [empresa, "CNPJ", "Tem alguma dúvida?", "metropolitanas) ", "Caso a solução ", "disponíveis em nubank.com.br", "Extrato gerado ", "O saldo líquido ", "Não nos responsabilizamos ", "Asseguramos a autenticidade ", "Nu Financeira S.A.", "e Investimento", "Nu Pagamentos S.A. - Instituição de Pagamento"])]

    #     re_data = re.compile(
    #         r"\b\d{2}\s+(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+\d{4}\b",
    #         re.I
    #     )

    #     re_valor = re.compile(
    #         r"(?<![\d/])(?:R\$\s*)?[+-]?\d{1,3}(?:\.\d{3})*,\d{2}[CD]?\b|"
    #         r"(?<![\d/])(?:R\$\s*)?[+-]?\d+,\d{2}[CD]?\b",
    #         re.I
    #     )

    #     re_inicio_mov = re.compile(
    #         r"^(Transferência|Pagamento|Reembolso|Aplicação|Resgate|Valor adicionado)",
    #         re.I
    #     )

    #     re_ignorar = re.compile(
    #         r"^(João Vitor|CPF\b|CNPJ\b|Agência\s+\d|"
    #         r"\d{2}\s+DE\s+\w+\s+DE\s+\d{4}|"
    #         r"Saldo|Movimentações|VALORES EM R\$|"
    #         r"Tem alguma dúvida|Caso a solução|Extrato gerado|"
    #         r"Nu Financeira|Nu Pagamentos|O saldo líquido|"
    #         r"Não nos responsabilizamos|Asseguramos)",
    #         re.I
    #     )

    #     resultado = []
    #     data_atual = ""
    #     tipo_bloco = ""
    #     bloco = []
    #     tipo_mov = ""

    #     for linha in pdf:
    #         linha = re.sub(r"\s+", " ", str(linha)).strip()
    #         if not linha:
    #             continue

    #         m_data = re_data.search(linha)

    #         if m_data:
    #             if bloco and data_atual:
    #                 texto = " ".join(bloco)
    #                 texto = re.sub(r"\s+", " ", texto).strip()

    #                 valores = list(re_valor.finditer(texto))
    #                 if valores:
    #                     valor = valores[-1].group(0).strip()
    #                     descricao = (texto[:valores[-1].start()] + texto[valores[-1].end():]).strip()
    #                     descricao = re.sub(r"\s+", " ", descricao).strip(" -")

    #                     valor_sem_sinal = re.sub(r"^(?:R\$\s*)?[+-]\s*", "", valor).strip()
    #                     prefixo = "R$ " if valor.upper().startswith("R$") else ""

    #                     if tipo_mov == "saida":
    #                         valor = prefixo + "-" + valor_sem_sinal
    #                     elif tipo_mov == "entrada":
    #                         valor = prefixo + "+" + valor_sem_sinal

    #                     if descricao:
    #                         resultado.append(f"{data_atual} {descricao} {valor}")

    #             bloco = []
    #             tipo_mov = ""
    #             data_atual = m_data.group(0).upper()

    #             resto = linha[m_data.end():].strip()

    #             if re.search(r"Total de entradas", resto, re.I):
    #                 tipo_bloco = "entrada"
    #             elif re.search(r"Total de saídas", resto, re.I):
    #                 tipo_bloco = "saida"

    #             if resto and not re_ignorar.search(resto):
    #                 if re_inicio_mov.search(resto):
    #                     bloco = [resto]
    #                     tipo_mov = tipo_bloco

    #             continue

    #         if re.search(r"Total de entradas", linha, re.I):
    #             if bloco and data_atual:
    #                 texto = " ".join(bloco)
    #                 texto = re.sub(r"\s+", " ", texto).strip()

    #                 valores = list(re_valor.finditer(texto))
    #                 if valores:
    #                     valor = valores[-1].group(0).strip()
    #                     descricao = (texto[:valores[-1].start()] + texto[valores[-1].end():]).strip()
    #                     descricao = re.sub(r"\s+", " ", descricao).strip(" -")

    #                     valor_sem_sinal = re.sub(r"^(?:R\$\s*)?[+-]\s*", "", valor).strip()
    #                     prefixo = "R$ " if valor.upper().startswith("R$") else ""

    #                     if tipo_mov == "saida":
    #                         valor = prefixo + "-" + valor_sem_sinal
    #                     elif tipo_mov == "entrada":
    #                         valor = prefixo + "+" + valor_sem_sinal

    #                     if descricao:
    #                         resultado.append(f"{data_atual} {descricao} {valor}")

    #             bloco = []
    #             tipo_mov = ""
    #             tipo_bloco = "entrada"
    #             continue

    #         if re.search(r"Total de saídas", linha, re.I):
    #             if bloco and data_atual:
    #                 texto = " ".join(bloco)
    #                 texto = re.sub(r"\s+", " ", texto).strip()

    #                 valores = list(re_valor.finditer(texto))
    #                 if valores:
    #                     valor = valores[-1].group(0).strip()
    #                     descricao = (texto[:valores[-1].start()] + texto[valores[-1].end():]).strip()
    #                     descricao = re.sub(r"\s+", " ", descricao).strip(" -")

    #                     valor_sem_sinal = re.sub(r"^(?:R\$\s*)?[+-]\s*", "", valor).strip()
    #                     prefixo = "R$ " if valor.upper().startswith("R$") else ""

    #                     if tipo_mov == "saida":
    #                         valor = prefixo + "-" + valor_sem_sinal
    #                     elif tipo_mov == "entrada":
    #                         valor = prefixo + "+" + valor_sem_sinal

    #                     if descricao:
    #                         resultado.append(f"{data_atual} {descricao} {valor}")

    #             bloco = []
    #             tipo_mov = ""
    #             tipo_bloco = "saida"
    #             continue

    #         if re_ignorar.search(linha):
    #             continue

    #         if re_inicio_mov.search(linha):
    #             if bloco and data_atual:
    #                 texto = " ".join(bloco)
    #                 texto = re.sub(r"\s+", " ", texto).strip()

    #                 valores = list(re_valor.finditer(texto))
    #                 if valores:
    #                     valor = valores[-1].group(0).strip()
    #                     descricao = (texto[:valores[-1].start()] + texto[valores[-1].end():]).strip()
    #                     descricao = re.sub(r"\s+", " ", descricao).strip(" -")

    #                     valor_sem_sinal = re.sub(r"^(?:R\$\s*)?[+-]\s*", "", valor).strip()
    #                     prefixo = "R$ " if valor.upper().startswith("R$") else ""

    #                     if tipo_mov == "saida":
    #                         valor = prefixo + "-" + valor_sem_sinal
    #                     elif tipo_mov == "entrada":
    #                         valor = prefixo + "+" + valor_sem_sinal

    #                     if descricao:
    #                         resultado.append(f"{data_atual} {descricao} {valor}")

    #             bloco = [linha]
    #             tipo_mov = tipo_bloco

    #         elif bloco:
    #             bloco.append(linha)

    #     if bloco and data_atual:
    #         texto = " ".join(bloco)
    #         texto = re.sub(r"\s+", " ", texto).strip()

    #         valores = list(re_valor.finditer(texto))
    #         if valores:
    #             valor = valores[-1].group(0).strip()
    #             descricao = (texto[:valores[-1].start()] + texto[valores[-1].end():]).strip()
    #             descricao = re.sub(r"\s+", " ", descricao).strip(" -")

    #             valor_sem_sinal = re.sub(r"^(?:R\$\s*)?[+-]\s*", "", valor).strip()
    #             prefixo = "R$ " if valor.upper().startswith("R$") else ""

    #             if tipo_mov == "saida":
    #                 valor = prefixo + "-" + valor_sem_sinal
    #             elif tipo_mov == "entrada":
    #                 valor = prefixo + "+" + valor_sem_sinal

    #             if descricao:
    #                 resultado.append(f"{data_atual} {descricao} {valor}")

    #     dados = resultado
    #     padrao = (
    #         r"(?P<data>\d{2}\s+[A-Z]{3}\s+\d{4})\s+"
    #         r"(?P<descricao>.*?)"
    #         r"(?P<valor>(?:R\$\s*)?[+-]\s*\d{1,3}(?:\.\d{3})*,\d{2})$"
    #     )

    #     resultado = []

    #     for linha in dados:
    #         match = re.search(padrao, linha)

    #         if match:
    #             data = match.group("data").strip()
    #             descricao = match.group("descricao").strip()
    #             valor = match.group("valor").strip()

    #             tipo = "C" if valor.startswith("+") else "D"

    #             resultado.append([
    #                 data,
    #                 descricao,
    #                 valor,
    #                 tipo
    #             ])

    #     df = pd.DataFrame(resultado, columns=["DATA", "DESCRIÇÃO", "VALOR", "TIPO"])
    #     df["DATA"] = (
    #         df["DATA"]
    #         .astype(str)
    #         .str.strip()
    #         .str.upper()
    #         .str.replace("JAN", "01", regex=False)
    #         .str.replace("FEV", "02", regex=False)
    #         .str.replace("MAR", "03", regex=False)
    #         .str.replace("ABR", "04", regex=False)
    #         .str.replace("MAI", "05", regex=False)
    #         .str.replace("JUN", "06", regex=False)
    #         .str.replace("JUL", "07", regex=False)
    #         .str.replace("AGO", "08", regex=False)
    #         .str.replace("SET", "09", regex=False)
    #         .str.replace("OUT", "10", regex=False)
    #         .str.replace("NOV", "11", regex=False)
    #         .str.replace("DEZ", "12", regex=False)
    #         .str.replace(" ", "/", regex=False)
    #     )
    #     df["DATA"] = pd.to_datetime(df["DATA"], format="%d/%m/%Y", errors="coerce").dt.strftime("%d/%m/%Y")
    #     df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
    #     df["VALOR"] = (
    #         df["VALOR"]
    #         .str.replace("-", "", regex=False)
    #         .str.replace("+", "", regex=False)
    #         .str.replace("R$", "", regex=False)
    #         .str.replace(".", "", regex=False)
    #         .str.replace(",", ".", regex=False)
    #         .str.strip()
    #         .astype(float)
    #     )
    #     return df
