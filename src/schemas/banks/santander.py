import re
import pandas as pd
from datetime import datetime
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register

@register
class Santander(BankHandler):
    bank = "santander"

    @layout("EXTRATO CONSOLIDADO INTELIGENTE")
    def layout1(self, pdf):
        indice = next((i for i, item in enumerate(pdf) if "“Sesuaempresanãotiverlimitedeconta" in item), None)
        pdf = pdf[58:indice]
        pdf = [item for item in pdf if not any(texto in item for texto in [
            "Data Descrição ",
            "Pagina: ",
            "Extrato_PJ_A4_Inteligente ",
            "BALP_",
            "EXTRATO CONSOLIDADO INTELIGENTE",
            "abril/",
            "Créditos Débitos"
        ])]

        data_re = re.compile(r"^\s*(\d{2}/\d{2})(?:/\d{4})?\b")
        data_sozinha_re = re.compile(r"^\d{2}/\d{2}/\d{4}$")
        valor_re = re.compile(r"(?<!\d)(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}-?(?!\d)")

        inicio_mov_re = re.compile(
            r"^(PIX RECEBIDO|PIX ENVIADO|TARIFA|PAGAMENTO CARTAO|"
            r"PAGAMENTO DE BOLETO|PAGAMENTO DARF|PGTO TRIBUTOS|"
            r"ANTECIPACAO GETNET|APLICACAO CONTAMAX|RESGATE CONTAMAX|"
            r"DEBITO AUT|CHEQUE EMITIDO)",
            re.I,
        )

        ignorar_re = re.compile(
            r"saldo em|data descrição|nº documento|movimentos|créditos|débitos|"
            r"saldo \(r\$\)|pagina:|extrato_pj|balp_|extrato consolidado|"
            r"abril/|se sua empresa|saldos por período",
            re.I,
        )

        def limpar(txt: str) -> str:
            txt = re.sub(r"\s+", " ", txt).strip()
            txt = re.sub(r"\s+-\s+", " ", txt).strip()
            return txt

        def montar(bloco: list[str], data: str) -> str:
            texto = limpar(" ".join(bloco))
            valores = list(valor_re.finditer(texto))

            if not valores:
                return ""

            # Primeiro valor é a movimentação; valor final 0,00 geralmente é saldo.
            valor = valores[0].group()

            if valor.endswith("-"):
                entrada = ""
                saida = valor
            else:
                entrada = valor
                saida = ""

            descricao = limpar(valor_re.sub(" ", texto))

            if not descricao:
                return ""

            return limpar(f"{data} | {descricao} | ENTRADA: {entrada} | SAÍDA: {saida}")

        ano = datetime.now().year
        for linha in pdf:
            m_ano = re.search(r"\b20\d{2}\b", linha)
            if m_ano:
                ano = m_ano.group()
                break

        resultado = []
        data_atual = ""
        bloco = []

        for linha in pdf:
            linha = limpar(linha)

            if not linha or ignorar_re.search(linha):
                continue

            m_data = data_re.search(linha)
            linha_tem_data = bool(m_data)
            resto = linha

            if linha_tem_data:
                data_atual = f"{m_data.group(1)}/{ano}"
                resto = limpar(linha[m_data.end():])

                # Data sozinha tipo 07/04/2026 pode ser complemento da descrição.
                if data_sozinha_re.fullmatch(linha):
                    if bloco:
                        bloco.append(linha)
                    continue

            if not data_atual:
                continue

            novo_inicio = bool(inicio_mov_re.search(resto))

            # Se começa nova movimentação e o bloco anterior já tem valor, fecha o anterior.
            if bloco and novo_inicio and valor_re.search(" ".join(bloco)):
                mov = montar(bloco, data_atual_anterior)
                if mov:
                    resultado.append(mov)
                bloco = []

            if linha_tem_data and resto and bloco and valor_re.search(" ".join(bloco)):
                mov = montar(bloco, data_atual_anterior)
                if mov:
                    resultado.append(mov)
                bloco = []

            if resto:
                bloco.append(resto)

            data_atual_anterior = data_atual

        if bloco:
            mov = montar(bloco, data_atual_anterior)
            if mov:
                resultado.append(mov)
                
        dados = [item.split(" | ") for item in resultado]
        df = pd.DataFrame(dados, columns=["DATA", "DESCRIÇÃO", "ENTRADA", "SAIDA"])
        df["ENTRADA"] = (
            df["ENTRADA"]
            .astype(str)
            .str.extract(r"ENTRADA:\s*([\d.,-]+)", expand=False)
            .fillna("")
        )
        df["SAIDA"] = (
            df["SAIDA"]
            .astype(str)
            .str.extract(r"SAÍDA:\s*([\d.,-]+)", expand=False)
            .fillna("")
        )
        df["ENTRADA"] = df["ENTRADA"].str.replace(".", "").str.replace(",", ".").str.strip()
        df["SAIDA"] = df["SAIDA"].str.replace("-", "").str.replace(".", "").str.replace(",", ".").str.strip()
        df["ENTRADA"] = pd.to_numeric(df["ENTRADA"], errors="coerce")
        df["SAIDA"] = pd.to_numeric(df["SAIDA"], errors="coerce") * -1
        df["VALOR"] = df["ENTRADA"].fillna(df["SAIDA"])
        df["TIPO"] = df.apply(lambda row: "C" if row["VALOR"] > 0 else "D", axis=1)
        df["VALOR"] = df["VALOR"].abs()
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
        return df
    
    @layout("Aplicativo Santander Empresas")
    def layout2(self, pdf):
        pdf = pdf[5:]
        pdf = [item for item in pdf if not any(texto in item for texto in [
                    "Saldo",
                    "Posição ",
                    "Entenda a composição ",
                    "A – ",
                    "B – ",
                    "Ouvidoria - ",
                    "0800 ",
                    "Desbloqueio em ",
                    "C – ",
                    "Juros acumulados ",
                    "IOF Acumulado até ",
                    "D - ",
                    "H – ",
                    "I – ",
                    "Central ",
                    "1/2",
                    "2/2"
                ])]

        data_re = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
        valor_re = re.compile(r"(?<![\w/])-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}(?![\w/])")

        inicio_mov_re = re.compile(
            r"^(mensalidade|pix enviado|pix recebido|tarifa|pagamento darf|"
            r"pagamento|darf|ted|doc|transfer[eê]ncia)",
            re.I,
        )

        ignorar_re = re.compile(
            r"^saldo\b|saldo de contamax|saldo disponível|posição em|entenda a composição|"
            r"saldo bloqueado|provisão|juros acumulados|iof acumulado|limite cheque|"
            r"central de atendimento|ouvidoria|0800|4004|sac|data histórico|"
            r"documento valor|aplicativo santander|agência:|conta:|períodos:",
            re.I,
        )

        def limpar(txt: str) -> str:
            return re.sub(r"\s+", " ", txt).strip()

        def montar(data: str, partes: list[str]) -> str:
            texto = limpar(" ".join(partes))
            valores = list(valor_re.finditer(texto))

            if not valores:
                return ""

            # Primeiro valor = movimentação; segundo valor normalmente = saldo.
            valor_mov = valores[0].group()
            entrada = valor_mov if not valor_mov.startswith("-") else ""
            saida = valor_mov if valor_mov.startswith("-") else ""

            descricao = limpar(valor_re.sub(" ", texto))

            if not descricao:
                return ""

            return limpar(f"{data} | {descricao} | ENTRADA: {entrada} | SAÍDA: {saida}")

        linhas = [limpar(l) for l in pdf if limpar(l)]
        resultado = []
        pendentes = []
        i = 0

        while i < len(linhas):
            linha = linhas[i]

            if ignorar_re.search(linha):
                pendentes = []
                i += 1
                continue

            m_data = data_re.search(linha)

            if not m_data:
                if inicio_mov_re.search(linha):
                    pendentes = [linha]
                else:
                    pendentes.append(linha)
                i += 1
                continue

            data = m_data.group()
            resto = limpar(linha[:m_data.start()] + " " + linha[m_data.end():])

            partes = pendentes[:]
            pendentes = []

            if resto:
                partes.append(resto)

            tem_valor = bool(valor_re.search(resto))
            j = i + 1

            while j < len(linhas):
                prox = limpar(linhas[j])

                if ignorar_re.search(prox):
                    j += 1
                    continue

                if data_re.search(prox):
                    break

                # Se já achou valor e a próxima linha parece nova movimentação, para.
                if tem_valor and inicio_mov_re.search(prox):
                    break

                # Se já achou valor e a próxima linha também tem valor, para.
                if tem_valor and valor_re.search(prox):
                    break

                partes.append(prox)

                if valor_re.search(prox):
                    tem_valor = True

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
            r'(?P<entrada>-?\d{1,3}(?:\.\d{3})*,\d{2})?'
            r'\s*\|\s*SAÍDA:\s*'
            r'(?P<saida>-?\d{1,3}(?:\.\d{3})*,\d{2})?'
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
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.replace(" ", "", regex=False)
        .str.strip()
        )
        df["DESCRIÇÃO"] = (df["DESCRIÇÃO"].astype(str).str.replace("|", ""))
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        df["VALOR"] = df["ENTRADA"].replace("", pd.NA).fillna(df["SAIDA"]).astype(float)
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
        df = df[
            ~df["DESCRIÇÃO"]
            .astype(str)
            .str.replace(r"\s+", "", regex=True)
            .str.upper()
            .str.contains("SALDO", na=False)
        ]
        df["VALOR"] = df["VALOR"].abs()
        return df