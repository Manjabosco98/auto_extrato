import re
import pandas as pd

from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class Inter(BankHandler):
    bank = "Inter"

    @layout("Solicitado em: ")
    def layout1(self, pdf):
        pdf = pdf[7:]
        pdf = [
            item for item in pdf
            if not item.startswith(("Fale com a gente", "SAC: "))
        ]

        re_data_num = re.compile(r'\b\d{2}/\d{2}(?:/\d{2,4})?\b')
        re_data_ext = re.compile(
            r'\b(\d{1,2})\s+de\s+'
            r'(janeiro|fevereiro|março|marco|abril|maio|junho|julho|'
            r'agosto|setembro|outubro|novembro|dezembro)\s+de\s+(\d{4})\b',
            re.I
        )
        re_valor = re.compile(
            r'[+-]?\s*R\$\s*-?\s*\d{1,3}(?:\.\d{3})*,\d{2}[CD]?|'
            r'[+-]?\s*-?\d{1,3}(?:\.\d{3})*,\d{2}[CD]?'
        )
        re_ignorar = re.compile(
            r'\b(SALDO\s+DO\s+DIA|VALOR\s+SALDO\s+POR\s+TRANSA[CÇ][AÃ]O|'
            r'SALDO\s+FINAL|SALDO\s+ANTERIOR|SALDO\s+DISPON[IÍ]VEL|'
            r'AG[EÊ]NCIA|CONTA|CPF|CNPJ|EXTRATO|P[AÁ]GINA|PAGINA|BANCO|'
            r'OUVIDORIA|SAC|CENTRAL\s+DE\s+ATENDIMENTO|PER[IÍ]ODO|PERIODO)\b',
            re.I
        )

        meses = {
            'janeiro': '01', 'fevereiro': '02', 'março': '03', 'marco': '03',
            'abril': '04', 'maio': '05', 'junho': '06', 'julho': '07',
            'agosto': '08', 'setembro': '09', 'outubro': '10',
            'novembro': '11', 'dezembro': '12'
        }

        resultado = []
        data_atual = None

        for linha in pdf:
            linha = re.sub(r'\s+', ' ', linha).strip()
            if not linha:
                continue

            m_data_num = re_data_num.search(linha)
            m_data_ext = re_data_ext.search(linha)

            if m_data_ext:
                dia, mes, ano = m_data_ext.groups()
                data_atual = f"{int(dia):02d}/{meses[mes.lower()]}/{ano}"
                continue

            if m_data_num:
                data_atual = m_data_num.group(0)
                resto = (linha[:m_data_num.start()] + linha[m_data_num.end():]).strip()
                if re_ignorar.search(resto):
                    continue

            if not data_atual or re_ignorar.fullmatch(linha):
                continue

            valores = list(re_valor.finditer(linha))
            if not valores:
                continue

            valor = valores[0].group(0).strip()
            descricao = linha[:valores[0].start()].strip()
            descricao = re.sub(r'\s+', ' ', descricao)

            if descricao:
                resultado.append(f"{data_atual} {descricao} {valor}".strip())

        dados = resultado

        padrao = re.compile(
            r'^(\d{2}/\d{2}/\d{4})\s+(Pix\s+(?:enviado|recebido):)\s+"([^"]+)"\s+(-?R\$\s*[\d\.]+,\d{2})$'
        )

        resultado = []

        for linha in dados:
            match = padrao.search(linha)

            if match:
                data = match.group(1)
                operacao = match.group(2)
                descricao = match.group(3)
                valor = match.group(4)

                descricao_final = f"{operacao} {descricao}"

                tipo = "D" if valor.startswith("-") else "C"

                resultado.append((data, descricao_final, valor, tipo))

        df = pd.DataFrame(resultado, columns=["DATA", "DESCRIÇÃO", "VALOR", "TIPO"])
        df["VALOR"] = df["VALOR"].str.replace("-", "").str.replace("R$", "").str.replace(".", "").str.replace(",", ".").astype(float)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        return df