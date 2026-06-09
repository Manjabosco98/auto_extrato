import re
import pandas as pd

from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class PagSeguro(BankHandler):
    bank = "PagSeguro"

    @layout("290 - PagSeguro Internet S/A")
    def layout1(self, pdf):
        pdf = pdf[9:]
        rx_data = re.compile(r'\b\d{2}/\d{2}(?:/\d{2,4})?\b')
        rx_valor = re.compile(r'-?\s*R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}[CD]?|-?\d{1,3}(?:\.\d{3})*,\d{2}[CD]?')
        rx_ignorar = re.compile(
            r'^(EXTRATO|EMITIDO|PER[IÍ]ODO|CNPJ|AG[ÊE]NCIA|CONTA|'
            r'DATA\s+DESCRI[CÇ][AÃ]O\s+VALOR|PAGSEGURO|BANCO)',
            re.I
        )

        # Inícios reais de movimentação neste PDF
        rx_inicio_desc = re.compile(
            r'\b(Vendas\s*-\s*Disponivel|Saldo do dia|Pix enviado\s*-|'
            r'Mensalidade Seguro Conta\s*-)\b',
            re.I
        )

        def limpar(txt: str) -> str:
            return re.sub(r'\s+', ' ', txt).strip()

        def separar_linha(linha: str, prefixo: str = '') -> tuple[str, str]:
            data = rx_data.search(linha).group(0)
            texto = limpar(rx_data.sub(' ', linha, count=1))

            valores = list(rx_valor.finditer(texto))
            if not valores:
                return '', prefixo + ' ' + texto

            valor_match = valores[-1]
            valor = limpar(valor_match.group(0))
            antes_valor = limpar(texto[:valor_match.start()])
            depois_valor = limpar(texto[valor_match.end():])

            if prefixo:
                descricao = limpar(prefixo + ' ' + antes_valor + ' ' + depois_valor)
                return limpar(f'{data} {descricao} {valor}'), ''

            inicios = list(rx_inicio_desc.finditer(antes_valor))

            # Caso crítico: duas descrições grudadas antes do valor.
            # Ex.: Pix enviado ... Mensalidade Seguro Conta ... -R$ 1.068,98
            if len(inicios) >= 2:
                corte = inicios[1].start()
                desc_atual = limpar(antes_valor[:corte])
                prefixo_proxima = limpar(antes_valor[corte:] + ' ' + depois_valor)
                return limpar(f'{data} {desc_atual} {valor}'), prefixo_proxima

            descricao = limpar(antes_valor + ' ' + depois_valor)
            return limpar(f'{data} {descricao} {valor}'), ''

        resultado = []
        prefixo_proxima = ''
        soltas = []

        for linha in pdf:
            linha = limpar(str(linha))
            if not linha or rx_ignorar.search(linha):
                continue

            tem_data = bool(rx_data.search(linha))

            if not tem_data:
                soltas.append(linha)
                continue

            if soltas:
                prefixo_proxima = limpar(prefixo_proxima + ' ' + ' '.join(soltas))
                soltas = []

            movimento, prefixo_proxima = separar_linha(linha, prefixo_proxima)

            if movimento:
                resultado.append(movimento)

        padrao = r"^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(-?\s?R\$\s?\d{1,3}(?:\.\d{3})*,\d{2})$"

        dados = []

        for item in resultado:
            match = re.search(padrao, item)

            if match:
                data = match.group(1)
                descricao = match.group(2).strip()
                valor = match.group(3).replace(" ", "")

                dados.append({
                    "DATA": data,
                    "DESCRIÇÃO": descricao,
                    "VALOR": valor
                })

        df = pd.json_normalize(dados)
        df["VALOR"] = (
            df["VALOR"]
            .str.replace("R$", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.replace(" ", "", regex=False)
            .astype(float)
        )
        df["TIPO"] = df.apply(lambda x: "C" if x["VALOR"] > 0 else "D", axis=1)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        df["VALOR"] = df["VALOR"].abs()
        df = df[~df["DESCRIÇÃO"].astype(str).str.upper().str.contains("SALDO", na=False)]
        return df
