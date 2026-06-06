import re
import pandas as pd

from src.schemas.base import BankHandler, layout
from src.schemas.registry import register

@register
class BancoBrasil(BankHandler):
    
    bank = "Banco do Brasil"
    
    @layout("BB RENDE FÁCIL")
    def layout1(self, pdf):
        dados = pdf
        indice = next((i for i, item in enumerate(dados) if " início " in item), None)
        dados = dados[17:indice]
        
        padrao = re.compile(
            r"^(?P<Data>\d{2}/\d{2}/\d{4})\s+"
            r"(?P<Histórico>.*?)\s+"
            r"R\$\s*(?P<Capital>-?\d{1,3}(?:\.\d{3})*,\d{2})\s+"
            r"R\$\s*(?P<Rendimento>-?\d{1,3}(?:\.\d{3})*,\d{2})\s+"
            r"R\$\s*(?P<IR>-?\d{1,3}(?:\.\d{3})*,\d{2})\s+"
            r"R\$\s*(?P<IOF>-?\d{1,3}(?:\.\d{3})*,\d{2})\s+"
            r"R\$\s*(?P<Valor_Liquido>-?\d{1,3}(?:\.\d{3})*,\d{2})$"
        )

        registros = []

        for linha in dados:
            match = padrao.match(linha)

            if match:
                registros.append(match.groupdict())

        df = pd.DataFrame(registros)

        colunas_valores = [
            "Capital",
            "Rendimento",
            "IR",
            "IOF",
            "Valor_Liquido"
        ]

        for coluna in colunas_valores:
            df[coluna] = (
                df[coluna]
                .str.replace(".", "", regex=False)
                .str.replace(",", ".", regex=False)
                .astype(float)
            )

        df["Data"] = pd.to_datetime(df["Data"], format="%d/%m/%Y")
        # df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        return df
    
    @layout("Consultas - Extrato de conta corrente")
    def layout2(self, pdf):
        indice = next((i for i, item in enumerate(pdf) if "------------------------------------------------" in item), None)
        pdf = pdf[9:indice]
        pdf = [item for item in pdf if not any(texto in item for texto in ["Saldo Anterior", " S A L D O "])]
        
        padrao_linha_principal = re.compile(
            r"^(?P<DATA>\d{2}/\d{2}/\d{4})\s+"
            r"(?P<AGENCIA>\d{4})\s+"
            r"(?P<CODIGO>\d{8})\s+"
            r"(?P<DESCRIÇÃO>.*?)\s+"
            r"(?P<DOCUMENTO>\d[\d\.]*)\s+"
            r"(?P<VALOR>-?\d{1,3}(?:\.\d{3})*,\d{2})\s+"
            r"(?P<TIPO>[CD])"
        )

        registros = []
        registro_atual = None

        for linha in pdf:
            linha = linha.strip()

            if not linha:
                continue

            match = padrao_linha_principal.match(linha)

            if match:
                if registro_atual:
                    registros.append(registro_atual)

                registro_atual = match.groupdict()
                registro_atual["COMPLEMENTO"] = ""
                continue

            if registro_atual:
                if registro_atual["COMPLEMENTO"]:
                    registro_atual["COMPLEMENTO"] += " " + linha
                else:
                    registro_atual["COMPLEMENTO"] = linha

        if registro_atual:
            registros.append(registro_atual)
            for registro in registros:
                complemento = registro.get("COMPLEMENTO", "")
                documento = registro.get("DOCUMENTO", "")
                registro["DESCRIÇÃO"] = (f"{registro['AGENCIA']} | " f"{registro['CODIGO']} | " f"{registro['DESCRIÇÃO']} {complemento} | " f"{documento}").strip()

        df = pd.DataFrame(registros)
        df["VALOR"] = df["VALOR"].str.replace(".", "").str.replace(",", ".").astype(float)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
        return df
    
    @layout("Extrato de conta corrente - Autorizável")
    def layout3(self, pdf):
        indice = next((i for i, item in enumerate(pdf) if "Lançamentos futuros" in item), None)
        pdf = pdf[9:indice]
        
        regex_transacao = re.compile(
            r"^(?P<DATA>\d{2}/\d{2}/\d{4})\s+"
            r"(?:(?P<DT_LANCAMENTO>\d{2}/\d{2}/\d{4})\s+)?"
            r"(?P<AGENCIA>\d{4})\s+"
            r"(?P<CODIGO>\d{5})"
            r"(?P<DESCRICÃO>.*?)\s+"
            r"(?P<DOCUMENTO>[\d\.]+)\s+"
            r"(?P<VALOR>\d{1,3}(?:\.\d{3})*,\d{2})\s+"
            r"(?P<TIPO>[CD])"
            r"(?:\s+(?P<SALDO>\d{1,3}(?:\.\d{3})*,\d{2})\s+(?P<TIPO_SALDO>[CD]))?"
            r"$"
        )

        regex_saldo = re.compile(
            r"^(?P<DATA>\d{2}/\d{2}/\d{4})\s+"
            r"(?P<AGENCIA>\d{4})\s+"
            r"(?P<CODIGO>\d{5})"
            r"(?P<DESCRICÃO>Saldo Anterior|S A L D O)\s+"
            r"(?P<VALOR>\d{1,3}(?:\.\d{3})*,\d{2})\s+"
            r"(?P<TIPO>[CD])"
            r"(?:\s+(?P<SALDO>\d{1,3}(?:\.\d{3})*,\d{2})\s+(?P<TIPO_SALDO>[CD]))?"
            r"$"
        )

        transacoes = []
        transacao_atual = None

        for linha in pdf:

            linha = linha.strip()

            if not linha:
                continue

            match = regex_transacao.match(linha)

            if match:

                if transacao_atual:
                    transacoes.append(transacao_atual)

                transacao_atual = match.groupdict()
                transacao_atual["COMPLEMENTO"] = ""

                continue

            match_saldo = regex_saldo.match(linha)

            match_saldo = regex_saldo.match(linha)

            if match_saldo:
                if transacao_atual:
                    transacoes.append(transacao_atual)

                transacao_atual = match_saldo.groupdict()
                transacao_atual["DOCUMENTO"] = None
                transacao_atual["DT_LANCAMENTO"] = None
                transacao_atual["COMPLEMENTO"] = ""

                continue

            if transacao_atual:

                if transacao_atual["COMPLEMENTO"]:
                    transacao_atual["COMPLEMENTO"] += " " + linha
                else:
                    transacao_atual["COMPLEMENTO"] = linha

        # adiciona última
        if transacao_atual:
            transacoes.append(transacao_atual)
            
            for transacao in transacoes:
                partes = [
                    transacao.get("AGENCIA", ""),
                    transacao.get("CODIGO", ""),
                    transacao.get("DESCRICÃO", ""),
                    transacao.get("COMPLEMENTO", ""),
                    transacao.get("DOCUMENTO") or "",
                ]

                transacao["DESCRICÃO"] = " | ".join(
                    str(parte).strip()
                    for parte in partes
                    if parte and str(parte).strip() not in ["nan", "None"]
                )
            
        df = pd.DataFrame(transacoes)
        df = df[["DATA", "DT_LANCAMENTO", "DESCRICÃO", "VALOR", "TIPO"]]
        df["DATA"] = pd.to_datetime(df["DATA"], format="%d/%m/%Y", errors="coerce")
        df["DT_LANCAMENTO"] = pd.to_datetime(df["DT_LANCAMENTO"], format="%d/%m/%Y", errors="coerce")
        df["VALOR"] = df["VALOR"].str.replace(".", "", regex=False).str.replace(",", ".", regex=False).astype(float)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        return df
    
    @layout("Extrato de Conta Corrente")
    def layout4(self, pdf):
        pdf = pdf[5:]
        pdf = [item for item in pdf if not any(texto in item for texto in [
            "Informações Adicionais", 
            "- Limite Ouro Empresarial ",
            "Taxa Cheque Especial ao Mês",
            "Taxa Cheque Especial ao Ano",
            "Tributos (IOF) Diário ",
            "Tributos (IOF) Adicional ",
            "Custo Efetivo Total ao Mês ",
            "Custo Efetivo Total ao Ano ",
            "Data Venc. Ch. Especial ",
            "Extrato de Conta Corrente",
            "Cliente ",
            "Agência: ",
            "Informações Complementares - CET (*)",
            "Valor Total Devido ",
            "Valor Liberado ",
            "Despesas-(IOF) ",
            "(*) Simulação para utilização única e integral do limite por 30 dias.",
            "Total Aplicações Financeiras ",
            "* Saldos por dia Base",
            "Sujeitos a confirmação no momento da contratação",
        ])]

        data_re = re.compile(r"^\d{2}/\d{2}/\d{4}\b")
        data_zero_re = re.compile(r"^00/00/0000\b")
        valor_re = re.compile(r"(?:R\$\s*)?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}\s*\([+-]\)")

        ignorar_re = re.compile(
            r"saldo anterior|saldo do dia|s a l d o|informações adicionais|"
            r"extrato de conta corrente|cliente |agência:|total aplicações|"
            r"taxa cheque especial ao mês|taxa cheque especial ao ano|"
            r"custo efetivo|valor total devido|valor liberado|despesas|"
            r"sujeitos a confirmação|data venc\. ch\. especial",
            re.I,
        )

        def limpar(txt: str) -> str:
            return re.sub(r"\s+", " ", txt).strip()

        def proxima_data(pos: int) -> str:
            for k in range(pos + 1, min(len(linhas_limpas), pos + 8)):
                if data_re.search(linhas_limpas[k]) and not data_zero_re.search(linhas_limpas[k]):
                    return data_re.search(linhas_limpas[k]).group()
            return ""

        def montar(data: str, partes: list[str]) -> str:
            texto = limpar(" ".join(partes))
            valores = list(valor_re.finditer(texto))

            if not data or not valores:
                return ""

            valor = valores[-1].group()
            entrada = valor if "(+)" in valor else ""
            saida = valor if "(-)" in valor else ""

            descricao = limpar(valor_re.sub(" ", texto))

            if not descricao:
                return ""

            return limpar(f"{data} | {descricao} | ENTRADA: {entrada} | SAÍDA: {saida}")

        linhas_limpas = [limpar(l) for l in pdf if limpar(l)]
        resultado = []
        pendentes = []
        data_atual = ""
        i = 0

        while i < len(linhas_limpas):
            linha = linhas_limpas[i]

            if data_zero_re.search(linha) or ignorar_re.search(linha):
                pendentes = []
                i += 1
                continue

            m_data = data_re.search(linha)
            tem_valor = valor_re.search(linha)

            if not m_data and not tem_valor:
                pendentes.append(linha)
                i += 1
                continue

            partes = []

            if m_data:
                data_atual = m_data.group()
                resto = limpar(linha[m_data.end():])

                if pendentes:
                    partes.extend(pendentes)
                    pendentes = []

                if resto:
                    partes.append(resto)

            else:
                data_mov = proxima_data(i) or data_atual

                if not data_mov:
                    i += 1
                    continue

                data_atual = data_mov

                if pendentes:
                    partes.extend(pendentes)
                    pendentes = []

                partes.append(linha)

            j = i + 1
            achou_valor = bool(tem_valor)

            while j < len(linhas_limpas):
                prox = linhas_limpas[j]

                if data_zero_re.search(prox) or ignorar_re.search(prox):
                    break

                if data_re.search(prox):
                    break

                if valor_re.search(prox) and achou_valor:
                    break

                partes.append(prox)

                if valor_re.search(prox):
                    achou_valor = True

                j += 1

            mov = montar(data_atual, partes)

            if mov:
                resultado.append(mov)

            i = j

        dados = resultado

        REGEX_MOVIMENTACAO = re.compile(
            r'^\s*'
            r'(?P<data>\d{2}/\d{2}/\d{4})\s*'
            r'\|\s*'
            r'(?P<descricao>.*?)\s*'
            r'\|\s*'
            r'ENTRADA:\s*'
            r'(?P<entrada>\d{1,3}(?:\.\d{3})*,\d{2}\s*\(\+\))?'
            r'\s*\|\s*'
            r'SAÍDA:\s*'
            r'(?P<saida>\d{1,3}(?:\.\d{3})*,\d{2}\s*\(-\))?'
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
                valor = entrada.strip()
                tipo = "C"
            elif saida:
                valor = saida.strip()
                tipo = "D"
            else:
                valor = ""
                tipo = ""

            resultado.append([
                data,
                f"| {descricao} |",
                f"ENTRADA: {entrada.strip()}" if entrada else "ENTRADA:",
                f"SAÍDA: {saida.strip()}" if saida else "SAÍDA:",
                tipo
            ])
        df = pd.DataFrame(resultado, columns=["DATA", "DESCRIÇÃO", "ENTRADA", "SAIDA", "TIPO"])
        df["ENTRADA"] = (
            df["ENTRADA"]
            .fillna("")
            .astype(str)
            .str.replace("ENTRADA:", "", regex=False)
            .str.replace("(+)", "", regex=False)
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
            .str.replace("(-)", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.replace(" ", "", regex=False)
            .str.strip()
        )
        df["VALOR"] = df["ENTRADA"].replace("", pd.NA).fillna(df["SAIDA"]).astype(float)
        df["DESCRIÇÃO"] = (df["DESCRIÇÃO"].astype(str).str.replace("|", ""))
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
        return df
    
    @layout("Agência ", "Cliente - ")
    def layout5(self, pdf):
        indice = next((i for i, item in enumerate(pdf) if "------------------------------------------------" in item), None)
        pdf = pdf[8:indice]

        data_re = re.compile(r"^\d{2}/\d{2}/\d{4}\b")
        data_hora_re = re.compile(r"^\d{2}/\d{2}\s+\d{2}:\d{2}\s*")
        valor_re = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}\s*[CD]\b")

        ignorar_re = re.compile(
            r"saldo anterior|dt\. balancete|dt\. movimento|ag\. origem|"
            r"lote hist[oó]rico|documento|valor r\$|saldo$|cliente - conta atual|"
            r"agência|conta corrente|período do extrato|lançamentos|"
            r"g\d{10,}|------------------------------------------------",
            re.I,
        )

        def limpar(txt: str) -> str:
            txt = re.sub(r"\s+", " ", txt).strip()

            # Corrige documento grudado em valor com milhar:
            # 40.80415.000,00 D -> 40.804 15.000,00 D
            # 100.261.20714.500,00 C -> 100.261.207 14.500,00 C
            txt = re.sub(
                r"(\b\d{2,3}(?:\.\d{3})+)(\d{1,3}\.\d{3},\d{2}\s*[CD]\b)",
                r"\1 \2",
                txt,
            )

            # Corrige documento simples grudado em valor sem milhar:
            # 40.804235,00 D -> 40.804 235,00 D
            txt = re.sub(
                r"(\b\d{2}\.\d{3})(\d{1,3},\d{2}\s*[CD]\b)",
                r"\1 \2",
                txt,
            )

            return txt

        def montar(data: str, partes: list[str]) -> str:
            texto = limpar(" ".join(partes))
            valores = list(valor_re.finditer(texto))

            if not valores:
                return ""

            valor = limpar(valores[0].group())
            entrada = valor if valor.endswith("C") else ""
            saida = valor if valor.endswith("D") else ""

            descricao = limpar(texto[:valores[0].start()] + " " + texto[valores[0].end():])
            descricao = limpar(valor_re.sub(" ", descricao))

            if not descricao:
                return ""

            return limpar(f"{data} | {descricao} | ENTRADA: {entrada} | SAÍDA: {saida}")

        linhas = [limpar(l) for l in pdf if limpar(l)]
        resultado = []
        i = 0

        while i < len(linhas):
            linha = linhas[i]

            if ignorar_re.search(linha):
                i += 1
                continue

            m_data = data_re.search(linha)

            if not m_data:
                i += 1
                continue

            data = m_data.group()
            resto = limpar(linha[m_data.end():])

            if not valor_re.search(resto):
                i += 1
                continue

            partes = [resto]
            j = i + 1

            while j < len(linhas):
                prox = limpar(linhas[j])

                if ignorar_re.search(prox):
                    j += 1
                    continue

                if data_re.search(prox):
                    break

                if valor_re.search(prox):
                    break

                prox = limpar(data_hora_re.sub("", prox))

                if prox:
                    partes.append(prox)

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
            r'(?P<entrada>\d{1,3}(?:\.\d{3})*,\d{2}\s*C)?'
            r'\s*\|\s*SAÍDA:\s*'
            r'(?P<saida>\d{1,3}(?:\.\d{3})*,\d{2}\s*D)?'
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
            .str.replace("|", "", regex=False)
            .str.replace("C", "", regex=False)
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
            .str.replace("D", "", regex=False)
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