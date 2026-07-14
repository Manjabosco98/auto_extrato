import re
import pandas as pd
from datetime import datetime
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


def _ano_periodo_sicoob(linhas) -> str | None:
    """Extrai o ano (4 dígitos) do cabeçalho do extrato SICOOB.

    Busca uma data completa (DD/MM/AAAA) nas linhas do cabeçalho em vez de
    depender de uma posição fixa, que varia conforme o layout/cliente.
    """
    for linha in linhas:
        m = re.search(r"\d{2}/\d{2}/(\d{4})", str(linha))
        if m:
            return m.group(1)
    return None


@register
class Sicoob(BankHandler):
    bank = "Sicoob"

    @layout("SICOOB", "SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL", " EXTRATO CONTA CORRENTE ")
    def layout1(self, pdf):
        periodo = _ano_periodo_sicoob(pdf[:9])
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
        df["DATA"] = df.apply(
            lambda row: f"{row["DATA"]}/{periodo}" if periodo else row["DATA"],
            axis=1,
        )
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
        return df

    @layout("SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL", "EXTRATO DE CONTA CORRENTE ")
    def layout2(self, pdf):
        periodo = _ano_periodo_sicoob(pdf[:8])
        indice = next((i for i, item in enumerate(pdf) if "RESUMO" in item), None)
        pdf = pdf[8:indice]

        REG_DATA = re.compile(r"^\d{2}/\d{2}\b")

        REG_VALOR = re.compile(
            r"R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}[CD\*]?"
        )

        REG_CABECALHO = re.compile(
            r"^(Data\s+Documento\s+Histórico\s+Valor|Data\s+Documento\s+Histórico|Valor)$",
            re.IGNORECASE
        )


        def limpar_linha(texto: str) -> str:
            return re.sub(r"\s+", " ", str(texto).strip())


        def separar_documento_historico(texto: str) -> tuple[str | None, str]:
            """
            Separa Documento e Histórico sem inventar informação.
            Só considera documento quando o padrão aparece claramente no extrato.
            """

            texto = limpar_linha(texto)

            # Documento numérico: 1425010162, 7890891, 1786772...
            match = re.match(r"^(?P<doc>\d+)\s+(?P<historico>.+)$", texto)
            if match:
                return match.group("doc"), limpar_linha(match.group("historico"))

            # Documento IOF/4-5
            match = re.match(r"^(?P<doc>IOF/\d+-\d+)\s+(?P<historico>.+)$", texto)
            if match:
                return match.group("doc"), limpar_linha(match.group("historico"))

            # Documento Pix
            match = re.match(r"^(?P<doc>Pix)\s+(?P<historico>.+)$", texto, re.IGNORECASE)
            if match:
                return match.group("doc"), limpar_linha(match.group("historico"))

            # Documento BORDERO
            match = re.match(r"^(?P<doc>BORDERO)\s+(?P<historico>.+)$", texto, re.IGNORECASE)
            if match:
                return match.group("doc"), limpar_linha(match.group("historico"))

            # Documento SICOOB SEG
            match = re.match(r"^(?P<doc>SICOOB SEG)\s+(?P<historico>.+)$", texto, re.IGNORECASE)
            if match:
                return match.group("doc"), limpar_linha(match.group("historico"))

            # Documento MASTERCARD / MASTERCA
            match = re.match(r"^(?P<doc>MASTERCARD|MASTERCA)\s+(?P<historico>.+)$", texto, re.IGNORECASE)
            if match:
                return match.group("doc"), limpar_linha(match.group("historico"))

            # Se não tiver documento claro, tudo fica como histórico
            return None, texto


        def extrair_movimentacao_sicoob(bloco: list[str], ano: str | None = None) -> dict:
            texto = limpar_linha(" ".join(bloco))

            data_match = REG_DATA.match(texto)

            if not data_match:
                return {
                    "DATA": None,
                    "DOCUMENTO": None,
                    "HISTORICO": texto,
                    "VALOR": None,
                    "TIPO": None,
                    "STATUS": "SEM_DATA"
                }

            data = data_match.group()

            texto_sem_data = limpar_linha(texto[data_match.end():])

            valor_match = REG_VALOR.search(texto_sem_data)

            if not valor_match:
                documento, historico = separar_documento_historico(texto_sem_data)

                return {
                    "DATA": f"{data}/{ano}" if ano else data,
                    "DOCUMENTO": documento,
                    "HISTORICO": historico,
                    "VALOR": None,
                    "TIPO": None,
                    "STATUS": "SEM_VALOR"
                }

            valor = valor_match.group()

            if valor.endswith("C"):
                tipo = "C"
            elif valor.endswith("D"):
                tipo = "D"
            else:
                tipo = None

            # Remove o valor do meio/fim da descrição.
            texto_sem_valor = limpar_linha(
                texto_sem_data[:valor_match.start()] + " " + texto_sem_data[valor_match.end():]
            )

            documento, historico = separar_documento_historico(texto_sem_valor)

            return {
                "DATA": f"{data}/{ano}" if ano else data,
                "DOCUMENTO": documento,
                "HISTORICO": historico,
                "VALOR": valor,
                "TIPO": tipo,
                "STATUS": "OK"
            }


        def normalizar_sicoob(pdf_movimentacoes: list[str], ano: str | None = None) -> pd.DataFrame:
            registros = []
            bloco = []

            for linha in pdf_movimentacoes:
                linha = limpar_linha(linha)

                if not linha:
                    continue

                if REG_CABECALHO.match(linha):
                    continue

                nova_data = bool(REG_DATA.match(linha))

                # Quando aparece uma nova data, fecha a movimentação anterior.
                if nova_data and bloco:
                    registros.append(extrair_movimentacao_sicoob(bloco, ano=ano))
                    bloco = []

                bloco.append(linha)

            if bloco:
                registros.append(extrair_movimentacao_sicoob(bloco, ano=ano))

            return pd.DataFrame(registros)
        df = normalizar_sicoob(pdf, ano=periodo)
        df = df[~df["HISTORICO"].str.contains("SALDO", case=False, na=False)].reset_index(drop=True)
        df["HISTORICO"] = df.apply(lambda x: f"{x["HISTORICO"]} {x["DOCUMENTO"]}", axis=1)
        df["HISTORICO"] = df["HISTORICO"].str.strip().str.replace("Pix", "", regex=False).str.upper()
        df["VALOR"] = (
            df["VALOR"]
            .str.strip()
            .str.replace("R$", "", regex=False)
            .str.replace("C", "", regex=False)
            .str.replace("D", "",regex=False)
            .str.replace("*", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .astype(float)
        )
        df = df[["DATA", "HISTORICO", "VALOR", "TIPO"]]
        df = df.rename(columns={"HISTORICO": "DESCRIÇÃO"})
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