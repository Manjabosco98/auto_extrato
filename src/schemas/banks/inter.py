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

        REGEX_DIA = re.compile(
            r'^(?P<data>\d{1,2}\s+de\s+[A-Za-zçÇãÃéÉíÍóÓúÚ]+\s+de\s+\d{4})'
            r'\s+Saldo do dia:\s+'
            r'(?P<saldo_dia>-?R\$\s*[\d\.]+,\d{2})'
            r'(?:\s+Valor\s+Saldo por transação)?$',
            re.IGNORECASE
        )


        REGEX_MOVIMENTACAO = re.compile(
            r'^(?P<descricao>.+?)\s+'
            r'(?P<valor>-?R\$\s*[\d\.]+,\d{2})\s+'
            r'(?P<saldo_transacao>-?R\$\s*[\d\.]+,\d{2})$'
        )


        def brl_para_float(valor: str) -> float:
            """
            Converte valores no formato brasileiro:
            R$ 1.500,00 -> 1500.00
            -R$ 5.399,00 -> -5399.00
            """
            valor = valor.strip()
            valor = valor.replace("R$", "").replace(" ", "")
            valor = valor.replace(".", "").replace(",", ".")

            return float(valor)


        def extrair_movimentacoes_inter(linhas: list[str]) -> pd.DataFrame:
            registros = []

            data_atual = None
            saldo_dia_atual = None

            for linha in linhas:
                linha = linha.strip()

                if not linha:
                    continue

                # Ignora cabeçalhos/rodapés que não são movimentações
                if linha.startswith(("Solicitado em:", "Fale com a gente", "SAC:", "Ouvidoria:")):
                    continue

                match_dia = REGEX_DIA.match(linha)

                if match_dia:
                    data_atual = match_dia.group("data")
                    saldo_dia_atual = match_dia.group("saldo_dia")
                    continue

                match_mov = REGEX_MOVIMENTACAO.match(linha)

                if match_mov and data_atual:
                    descricao = match_mov.group("descricao")
                    valor = match_mov.group("valor")
                    saldo_transacao = match_mov.group("saldo_transacao")

                    registros.append({
                        "DATA": data_atual,
                        "SALDO_DIA": saldo_dia_atual,
                        "DESCRICAO": descricao,
                        "VALOR_ORIGINAL": valor,
                        "VALOR": brl_para_float(valor),
                        "SALDO_TRANSACAO_ORIGINAL": saldo_transacao,
                        "SALDO_TRANSACAO": brl_para_float(saldo_transacao),
                        "TIPO": "C" if not valor.strip().startswith("-") else "D"
                    })
            return registros

        regitros = extrair_movimentacoes_inter(pdf)
        df = pd.json_normalize(regitros)
        df = df[["DATA", "DESCRICAO", "VALOR", "TIPO"]]
        meses = {
            "Janeiro": "01",
            "Fevereiro": "02",
            "Março": "03",
            "Marco": "03",
            "Abril": "04",
            "Maio": "05",
            "Junho": "06",
            "Julho": "07",
            "Agosto": "08",
            "Setembro": "09",
            "Outubro": "10",
            "Novembro": "11",
            "Dezembro": "12",
        }
        def converter_data_ptbr(data):
            if pd.isna(data):
                return None

            data = str(data).strip()

            # Exemplo: "1 de Maio de 2026"
            partes = data.split(" de ")

            if len(partes) != 3:
                return None

            dia = partes[0].zfill(2)
            mes = meses.get(partes[1])
            ano = partes[2]

            if not mes:
                return None

            return f"{dia}/{mes}/{ano}"
        df["DATA"] = df["DATA"].apply(converter_data_ptbr)
        df["DESCRICAO"] = df["DESCRICAO"].str.upper()
        df["VALOR"] = df["VALOR"].abs()
        df = df.rename(columns={"DESCRICAO": "DESCRIÇÃO"})
        return df