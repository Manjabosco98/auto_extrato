import re
import pandas as pd
from datetime import datetime, timedelta
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class Caixa(BankHandler):
    bank = "Caixa"

    @layout("CNPJ: ")
    def layout1(self, pdf):
        pdf = pdf[8:]
        pdf = [item for item in pdf if not any(texto in item for texto in ["SAC ", "0800", "Pessoas com ", "Documento ", "Data Efetiva", "Data"])]

        data_re = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
        data_efetiva_re = re.compile(r"\b\d{2}/\d{2}\s+\d{2}:\d{2}\b")

        valor_re = re.compile(
            r"(?:-\s*)?R\$\s*(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}(?:\s*[CD])?"
        )

        historico_re = re.compile(
            r"^(PIX ENVIADO|DEB PIX CHAVE|PAG BOLETO IBC|PAG ORGAOS GOV IBC|"
            r"PAGAMENTO AGUA IBC|DEB AUT|PIX RECEBIDO|AGUA|DEB PAG CARNES IBC|"
            r"PAGAMENTO TELEFONE IBC|RESGATE AUTOMAT - CLIENTE|"
            r"ENVIO TRANSF INTERNET TEV|CREDITO TRANSF INTERNET|RESG CDB)",
            re.I,
        )

        ignorar_re = re.compile(
            r"^(data|data efetiva|documento|hist[oó]rico|valor|saldo)$|"
            r"saldo dia|saldo anterior|extrato no per[ií]odo|cnpj:|ag[eê]ncia|"
            r"sac caixa|0800|ouvidoria|al[oô] caixa|pessoas com deficiência",
            re.I,
        )

        def limpar(txt: str) -> str:
            return re.sub(r"\s+", " ", txt).strip()

        linhas = [limpar(l) for l in pdf if limpar(l)]
        resultado = []
        i = 0

        while i < len(linhas):
            linha = linhas[i]

            if ignorar_re.search(linha):
                i += 1
                continue

            titulo = ""

            # Histórico antes da data: PIX ENVIADO, DEB PIX CHAVE, etc.
            if historico_re.search(linha) and i + 1 < len(linhas) and data_re.search(linhas[i + 1]):
                titulo = linha
                i += 1
                linha = linhas[i]

            m_data = data_re.search(linha)

            if not m_data:
                i += 1
                continue

            data = m_data.group()
            resto_data = limpar(linha[:m_data.start()] + " " + linha[m_data.end():])

            if ignorar_re.search(linha):
                i += 1
                continue

            bloco = []

            if titulo:
                bloco.append(titulo)

            if resto_data:
                bloco.append(resto_data)

            i += 1

            # Junta somente as linhas da mesma movimentação
            while i < len(linhas):
                prox = linhas[i]

                if data_re.search(prox):
                    break

                if historico_re.search(prox) and i + 1 < len(linhas) and data_re.search(linhas[i + 1]):
                    break

                if ignorar_re.search(prox):
                    i += 1
                    continue

                prox = limpar(data_efetiva_re.sub(" ", prox))

                if prox:
                    bloco.append(prox)

                i += 1

            texto_bloco = limpar(" ".join(bloco))
            valores = list(valor_re.finditer(texto_bloco))

            if not valores:
                continue

            # Na Caixa, o primeiro valor é a movimentação; o último geralmente é o saldo.
            valor_mov = limpar(valores[0].group())
            descricao = limpar(valor_re.sub(" ", texto_bloco))
            descricao = limpar(data_efetiva_re.sub(" ", descricao))

            if descricao:
                resultado.append(limpar(f"{data} {descricao} {valor_mov}"))

            padrao = re.compile(
                r'^\s*'
                r'(?P<data>\d{2}/\d{2}/\d{4})\s+'
                r'(?P<descricao>.*?)\s+'
                r'(?P<sinal>-)?\s*R\$\s*'
                r'(?P<valor>\d{1,3}(?:\.\d{3})*,\d{2})'
                r'\s*$'
            )

            movimentacoes_normalizadas = []

            for item in resultado:
                match = padrao.match(item)

                if not match:
                    print("Não capturou:", item)
                    continue

                data = match.group("data")
                descricao = match.group("descricao").strip()
                sinal = match.group("sinal")
                valor = match.group("valor")

                tipo = "D" if sinal == "-" else "C"

                valor_formatado = f"- R$ {valor}" if tipo == "D" else f"R$ {valor}"

                movimentacoes_normalizadas.append({
                    "data": data,
                    "descricao": descricao,
                    "valor": valor_formatado,
                    "tipo": tipo
                })
            df = pd.DataFrame(movimentacoes_normalizadas)
            df["valor"] = (
                df["valor"]
                .str.replace("-", "", regex=False)
                .str.replace("+", "", regex=False)
                .str.replace("R$", "", regex=False)
                .str.replace(".", "", regex=False)
                .str.replace(",", ".", regex=False)
                .str.strip()
                .astype(float)
            )
            df["descricao"] = df["descricao"].str.upper().str.strip()
            df = df.rename(columns={"data": "DATA", "descricao": "DESCRIÇÃO", "valor": "VALOR", "tipo": "TIPO"})
        return df
    
    @layout("Extrato por período", "Cliente: ")
    def layout2(self, pdf):
        pdf = pdf[9:]
        pdf = [item for item in pdf if not any(texto in item for texto in ["* 661 - ", "SAC CAIXA: ", "Pessoas com ", "Ouvidoria: ", "Alô CAIXA: "])]

        REGEX_MOVIMENTACAO = re.compile(
            r'^\s*'
            r'(?P<data>\d{2}/\d{2}/\d{2,4})\s+'
            r'(?P<descricao>.*?)\s+'
            r'(?P<valor>\d{1,3}(?:\.\d{3})*,\d{2})\s+'
            r'(?P<tipo>[CD])\s+'
            r'(?P<saldo>\d{1,3}(?:\.\d{3})*,\d{2}\s+[CD])'
            r'\s*$'
        )

        resultado = []

        for item in pdf:
            item = item.strip()

            if not item:
                continue

            match = REGEX_MOVIMENTACAO.match(item)

            if not match:
                print("Não capturou:", item)
                continue

            data = match.group("data")
            descricao = match.group("descricao").strip()
            valor = match.group("valor")
            tipo = match.group("tipo")
            saldo = match.group("saldo")

            resultado.append([
                data,
                descricao,
                valor,
                tipo,
                saldo
            ])

        df = pd.DataFrame(resultado, columns=["DATA", "DESCRIÇÃO", "VALOR", "TIPO", "SALDO"])
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
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
        df = df[~df["DESCRIÇÃO"].str.contains(" SALDO |TOTAL ", case=False, na=False)]
        return df