import re
import pandas as pd
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register

@register
class Prudention(BankHandler):
    bank = "Prudention"

    @layout("Nome ", "CNPJ ")
    def layout1(self, pdf):
        pdf = pdf[6:]
        pdf = [item for item in pdf if not any(texto in item for texto in ["O extrato é baseado ", "Extrato gerado ", "cancelamentos e informações gerais, ", "localidades: "])]

        re_data_ext = re.compile(
            r'^(\d{2})\s+(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+(\d{4})\b'
        )
        re_valor = re.compile(
            r'((?:[+-]?\s*R\$\s*-?\s*)?\d{1,3}(?:\.\d{3})*,\d{2}[CD]?|'
            r'[+-]?\d+,\d{2}[CD]?)\s*$'
        )
        re_ignorar = re.compile(
            r'\b(SALDO\s+AO\s+FINAL\s+DO\s+DIA|SALDO\s+FINAL|SALDO\s+ANTERIOR|'
            r'EXTRATO|VALORES\s+EM\s+R\$|AG[EÊ]NCIA|CONTA|CPF|CNPJ|'
            r'P[AÁ]GINA|PAGINA|BANCO|OUVIDORIA|SAC)\b',
            re.I
        )

        meses = {
            'JAN': '01', 'FEV': '02', 'MAR': '03', 'ABR': '04',
            'MAI': '05', 'JUN': '06', 'JUL': '07', 'AGO': '08',
            'SET': '09', 'OUT': '10', 'NOV': '11', 'DEZ': '12'
        }

        linhas_limpas = []
        for linha in pdf:
            linha = re.sub(r'\s+', ' ', linha).strip()
            if linha:
                linhas_limpas.append(linha)

        resultado = []
        data_atual = None
        i = 0

        while i < len(linhas_limpas):
            linha = linhas_limpas[i]

            if re_ignorar.search(linha):
                i += 1
                continue

            m_data = re_data_ext.search(linha)
            m_valor = re_valor.search(linha)

            if m_data:
                dia, mes, ano = m_data.groups()
                data_atual = f'{dia}/{meses[mes]}/{ano}'
                texto = linha[m_data.end():].strip()
            else:
                texto = linha

            if not data_atual or not m_valor:
                i += 1
                continue

            valor = m_valor.group(1).strip()
            descricao = texto[:m_valor.start()].strip()
            descricao = re.sub(r'\s+', ' ', descricao)

            partes = [descricao] if descricao else []

            j = i + 1
            while j < len(linhas_limpas):
                prox = linhas_limpas[j]

                if re_ignorar.search(prox):
                    break

                if re_data_ext.search(prox):
                    break

                if re_valor.search(prox):
                    break

                partes.append(prox)
                j += 1

            descricao_final = ' '.join(partes)
            descricao_final = re.sub(r'\s+', ' ', descricao_final).strip()

            if descricao_final:
                resultado.append(f'{data_atual} {descricao_final} {valor}'.strip())

            i = j

        dados = resultado

        padrao = re.compile(
            r"^(\d{2}/\d{2}/\d{4})\s+(.+)\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})$"
        )

        resultado = []

        for linha in dados:
            match = padrao.search(linha)

            if match:
                data = match.group(1)
                descricao = match.group(2).strip()
                valor = match.group(3)

                # Regra de débito/crédito
                if valor.startswith("-"):
                    tipo = "D"
                elif "enviado" in descricao.lower() or "enviada" in descricao.lower():
                    tipo = "D"
                elif "pagamento de boleto" in descricao.lower() or "pago à" in descricao.lower():
                    tipo = "D"
                else:
                    tipo = "C"

                resultado.append((data, descricao, valor, tipo))
        df = pd.DataFrame(resultado, columns=["DATA", "DESCRIÇÃO", "VALOR", "TIPO"])
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
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
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].astype(str)
        for i, row in df.iterrows():
            valor = row["VALOR"]

            # converte o valor para padrão brasileiro: 1133.47 -> 1.133,47
            valor_formatado = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

            # cria variações possíveis do valor dentro da descrição
            padroes = [
                valor_formatado,              # 1.133,47
                "-" + valor_formatado,        # -1.133,47
                valor_formatado.replace(".", ""),       # 1133,47
                "-" + valor_formatado.replace(".", "")  # -1133,47
            ]

            descricao = row["DESCRIÇÃO"]

            for padrao in padroes:
                descricao = descricao.replace(padrao, "")

            # remove espaços duplicados
            descricao = re.sub(r"\s+", " ", descricao).strip()

            df.at[i, "DESCRIÇÃO"] = descricao
        return df