import re
import pandas as pd
from datetime import datetime
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class Sicoob(BankHandler):
    bank = "Sicoob"

    @layout("SICOOB", "SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL", " EXTRATO CONTA CORRENTE ")
    def layout1(self, pdf):
        periodo = pdf[6].split("/")[-1]
        indice = next((i for i, item in enumerate(pdf) if "RESUMO" in item), None)
        pdf = pdf[9:indice]
        pdf = [item for item in pdf if not any(texto in item for texto in ["DATA "])]

        re_data = re.compile(r"^(\d{2}/\d{2}(?:/\d{2,4})?)\b")
        re_valor = re.compile(
            r"(?:R\$\s*-?\s*(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{2})?[CD]?|"
            r"-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}[CD]?)"
        )
        re_valor_linha = re.compile(rf"^{re_valor.pattern}$")
        re_sufixo = re.compile(r"^[CD]$")
        re_ignorar = re.compile(
            r"SALDO|RESUMO|AGÊNCIA|AGENCIA|CONTA:|CPF|CNPJ|EXTRATO|"
            r"PÁGINA|PAGINA|BANCO|OUVIDORIA|SAC|CENTRAL|"
            r"DATA\s+HISTÓRICO\s+VALOR|DATA\s+DESCRIÇÃO\s+VALOR|PERÍODO|PERIODO",
            re.I
        )

        itens = []
        for linha in pdf:
            linha = re.sub(r"\s+", " ", str(linha)).strip()
            if not linha:
                continue
            if re.search(r"\bRESUMO\b", linha, re.I):
                break
            itens.append(linha)

        resultado = []
        n = len(itens)

        for i, linha in enumerate(itens):
            m_data = re_data.match(linha)
            if not m_data:
                continue

            if re_ignorar.search(linha):
                continue

            data = m_data.group(1)
            corpo = linha[m_data.end():].strip()

            valor = ""
            m_valores = list(re_valor.finditer(corpo))
            if m_valores:
                ultimo = m_valores[-1]
                valor = ultimo.group(0)
                descricao_base = (corpo[:ultimo.start()] + " " + corpo[ultimo.end():]).strip()
            else:
                descricao_base = corpo
                if i > 0 and re_valor_linha.match(itens[i - 1]):
                    valor = itens[i - 1]

            fim = n
            for j in range(i + 1, n):
                if re_data.match(itens[j]):
                    fim = j
                    break

            partes_desc = []
            if descricao_base:
                partes_desc.append(descricao_base)

            for j in range(i + 1, fim):
                item = itens[j]

                if re_sufixo.match(item):
                    if valor and not valor.endswith(("C", "D")):
                        valor += item
                    continue

                if re_valor_linha.match(item):
                    continue

                if re_ignorar.search(item):
                    continue

                partes_desc.append(item)

            if not valor:
                continue

            descricao = " ".join(partes_desc)
            descricao = re.sub(r"\s+", " ", descricao).strip()

            movimentacao = f"{data} {descricao} {valor}"
            movimentacao = re.sub(r"\s+", " ", movimentacao).strip()
            resultado.append(movimentacao)

        dados = []
        padrao = re.compile(r"^(\d{2}/\d{2})\s+(.+?)\s+(\d{1,3}(?:\.\d{3})*,\d{2})([CD])$")
        for linha in resultado:
            match = padrao.match(linha)

            if match:
                data, descricao, valor, tipo = match.groups()
                dados += [[data, descricao, valor, tipo]]
            else:
                print(f"Linha não capturada: {linha}")

        df = pd.DataFrame(dados, columns=["DATA", "DESCRIÇÃO", "VALOR", "TIPO"])
        df["VALOR"] = (
            df["VALOR"]
            .str.strip()
            .str.replace(".", "")
            .str.replace(",", ".")
            .str.replace("R$", "")
            .astype(float)
        )
        df["DATA"] = df.apply(lambda row: f"{row["DATA"]}/{periodo}", axis=1)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
        return df

    @layout("SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL", "EXTRATO DE CONTA CORRENTE ")
    def layout2(self, pdf):
        periodo = pdf[6].split("/")[-1]
        indice = next((i for i, item in enumerate(pdf) if "RESUMO" in item), None)
        pdf = pdf[9:indice]
        pdf = [item for item in pdf if not any(texto in item for texto in ["DATA "])]

        re_data = re.compile(r"^(\d{2}/\d{2}(?:/\d{2,4})?)\b")
        re_valor = re.compile(
            r"(?:R\$\s*-?\s*(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{2})?[CD]?|"
            r"-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}[CD]?)"
        )
        re_valor_linha = re.compile(rf"^{re_valor.pattern}$")
        re_sufixo = re.compile(r"^[CD]$")
        re_ignorar = re.compile(
            r"SALDO|RESUMO|AGÊNCIA|AGENCIA|CONTA:|CPF|CNPJ|EXTRATO|"
            r"PÁGINA|PAGINA|BANCO|OUVIDORIA|SAC|CENTRAL|"
            r"DATA\s+HISTÓRICO\s+VALOR|DATA\s+DESCRIÇÃO\s+VALOR|PERÍODO|PERIODO",
            re.I
        )

        itens = []
        for linha in pdf:
            linha = re.sub(r"\s+", " ", str(linha)).strip()
            if not linha:
                continue
            if re.search(r"\bRESUMO\b", linha, re.I):
                break
            itens.append(linha)

        resultado = []
        n = len(itens)

        for i, linha in enumerate(itens):
            m_data = re_data.match(linha)
            if not m_data:
                continue

            if re_ignorar.search(linha):
                continue

            data = m_data.group(1)
            corpo = linha[m_data.end():].strip()

            valor = ""
            m_valores = list(re_valor.finditer(corpo))
            if m_valores:
                ultimo = m_valores[-1]
                valor = ultimo.group(0)
                descricao_base = (corpo[:ultimo.start()] + " " + corpo[ultimo.end():]).strip()
            else:
                descricao_base = corpo
                if i > 0 and re_valor_linha.match(itens[i - 1]):
                    valor = itens[i - 1]

            fim = n
            for j in range(i + 1, n):
                if re_data.match(itens[j]):
                    fim = j
                    break

            partes_desc = []
            if descricao_base:
                partes_desc.append(descricao_base)

            for j in range(i + 1, fim):
                item = itens[j]

                if re_sufixo.match(item):
                    if valor and not valor.endswith(("C", "D")):
                        valor += item
                    continue

                if re_valor_linha.match(item):
                    continue

                if re_ignorar.search(item):
                    continue

                partes_desc.append(item)

            if not valor:
                continue

            descricao = " ".join(partes_desc)
            descricao = re.sub(r"\s+", " ", descricao).strip()

            movimentacao = f"{data} {descricao} {valor}"
            movimentacao = re.sub(r"\s+", " ", movimentacao).strip()
            resultado.append(movimentacao)

        dados = []
        padrao = re.compile(r"^(\d{2}/\d{2})\s+(.+?)\s+(\d{1,3}(?:\.\d{3})*,\d{2})([CD])$")
        for linha in resultado:
            match = padrao.match(linha)

            if match:
                data, descricao, valor, tipo = match.groups()
                dados += [[data, descricao, valor, tipo]]
            else:
                print(f"Linha não capturada: {linha}")

        df = pd.DataFrame(dados, columns=["DATA", "DESCRIÇÃO", "VALOR", "TIPO"])
        df["VALOR"] = (
            df["VALOR"]
            .str.strip()
            .str.replace(".", "")
            .str.replace(",", ".")
            .str.replace("R$", "")
            .astype(float)
        )
        df["DATA"] = df.apply(lambda row: f"{row["DATA"]}/{periodo}", axis=1)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
        return df
    
    @layout("PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB – SISBR", " EXTRATO DE CARTÃO DE CRÉDITO ")
    def layout3(self, pdf):
        indice = next((i for i, item in enumerate(pdf) if "DEMONSTRATIVO DE PAGAMENTO EM R$" in item), None)
        pdf = pdf[9:indice]
        pdf = [
            item for item in pdf
            if not item.startswith(("TOTAL"))
        ]
        pdf = [
            item for item in pdf
            if re.match(r"^\d{2}/\d{2}", item.strip())
        ]

        padrao = re.compile(
            r"^(\d{2}/\d{2})\s+(.+?)\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})$"
        )
        resultado = []

        for linha in pdf:
            match = padrao.search(linha)

            if match:
                data = match.group(1)
                descricao = match.group(2).strip()
                valor = match.group(3)

                tipo = "D" if valor.startswith("-") else "C"

                resultado.append((data, descricao, valor, tipo))
        df = pd.DataFrame(resultado, columns=["DATA", "DESCRIÇÃO", "VALOR", "TIPO"])
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
        df.loc[
            df["DATA"].astype(str).str.match(r"^\d{2}/\d{2}$", na=False),
            "DATA"
        ] = ""
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        return df