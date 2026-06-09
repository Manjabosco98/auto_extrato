import re
import pandas as pd
from datetime import datetime
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register

@register
class Itau(BankHandler):    
    bank = "Itau"
    layout_head_lines = 5
    layout_tail_lines = 5

    def _pick_layout(self, pdf: list[str]):
        head_tail = "\n".join(
            pdf[: self.layout_head_lines] + pdf[-self.layout_tail_lines:]
        )
        candidates = [
            fn
            for fn in self._layouts()
            if all(sig in head_tail for sig in fn._layout_signatures)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda fn: len(fn._layout_signatures))
    
    @layout(" www.itau.com.br/empresas. ", "Saldo total Limite da conta Utilizado Disponível")
    def layout2(self, pdf):
        pdf = pdf[5:]
        pdf = [item for item in pdf if not any(texto in item for texto in [
                    "aviso: ",
                    "novos lançamentos",
                    "atualizado em ",
                    "Em caso de dúvidas, ",
                    " 24 horas por dia "
                ])]

        rx_data = re.compile(r'\b\d{2}/\d{2}(?:/\d{2,4})?\b')
        rx_valor = re.compile(r'(?:R\$\s*)?-?\d{1,3}(?:\.\d{3})*,\d{2}[CD]?|(?:R\$\s*)?-?\d+[CD]?\b')
        rx_ignorar = re.compile(
            r'^(DATA\b|LANÇAMENTOS\b|RAZÃO SOCIAL\b|CNPJ/CPF\b|VALOR\b|SALDO\b|'
            r'SALDO ANTERIOR\b|SALDO TOTAL DISPON[IÍ]VEL DIA\b|SALDO FINAL\b|'
            r'SALDO DO DIA\b|AG[ÊE]NCIA\b|CONTA\b|CPF\b|CNPJ\b|EXTRATO\b|'
            r'P[ÁA]GINA\b|BANCO\b|OUVIDORIA\b|SAC\b|CENTRAL\b|PER[IÍ]ODO\b|AVISO:)',
            re.I
        )
        rx_inicio_lanc = re.compile(
            r'\b(PIX|PAGAMENTOS|BOLETO|BOLETOS|TAR/|TAR\b|JUROS|IOF|DEP\b|'
            r'DESC\b|EST\b|TIT\b|FIN\b|JR\b|DA\b|DEB\b|BUSINESS\b|'
            r'RENDIMENTOS|RECEBIMENTOS|TRANSFER[ÊE]NCIA|RENEGOCIACAO)\b',
            re.I
        )
        rx_pede_prefixo = re.compile(
            r'\b(PIX RECEBIDO|PIX ENVIADO|PAGAMENTOS PIX QR-CODE|'
            r'PAGAMENTOS TRANSF CC ITAU|BOLETO PAGO)\b',
            re.I
        )

        def limpar(txt: str) -> str:
            return re.sub(r'\s+', ' ', txt).strip()

        def fechar(bloco: list[str]) -> str:
            texto = limpar(' '.join(bloco))
            data = rx_data.search(texto).group(0)
            valores = list(rx_valor.finditer(texto))
            valor = valores[-1].group(0)

            ini, fim = valores[-1].span()
            texto_sem_data = rx_data.sub(' ', texto, count=1)
            # remove só a última ocorrência exata do valor
            pos = texto_sem_data.rfind(valor)
            if pos >= 0:
                texto_sem_data = texto_sem_data[:pos] + ' ' + texto_sem_data[pos + len(valor):]

            desc = limpar(texto_sem_data).strip(' -')
            return limpar(f'{data} {desc} {valor}')

        def vai_para_proxima(buffer: list[str], proxima_linha: str, bloco_atual: list[str]) -> bool:
            texto_buffer = limpar(' '.join(buffer))
            texto_atual = limpar(' '.join(bloco_atual))
            prox = limpar(proxima_linha)

            # Se a próxima linha é lançamento que costuma ter razão social quebrada,
            # o buffer provavelmente é prefixo da próxima movimentação.
            if rx_pede_prefixo.search(prox):
                if not re.search(r'\b(RECEBIDA|RECEBIDO|LTDA\.?|AUT MAIS|PAGAMENTO LTDA|JUNIOR|AUTO)\b$', texto_buffer, re.I):
                    return True

            # Se o bloco atual ainda parece incompleto, o buffer fica nele.
            if rx_pede_prefixo.search(texto_atual) and re.search(r'\b(LTDA\.?|SANTOS|SILVA|OLIVEIRA|JUNIOR|AUTO|AUT MAIS|RECEBIDA)\b', texto_buffer, re.I):
                return False

            # Linhas curtas depois de um lançamento normalmente são complemento dele.
            if len(texto_buffer.split()) <= 4:
                return False

            return False

        saida = []
        atual = []
        buffer = []

        for original in pdf:
            linha = limpar(str(original))
            if not linha:
                continue

            if rx_ignorar.search(linha):
                if atual:
                    if buffer:
                        atual.extend(buffer)
                        buffer = []
                    if rx_data.search(' '.join(atual)) and rx_valor.search(' '.join(atual)):
                        saida.append(fechar(atual))
                    atual = []
                buffer = []
                continue

            tem_data = bool(rx_data.search(linha))

            if tem_data:
                if atual:
                    if buffer:
                        if vai_para_proxima(buffer, linha, atual):
                            if rx_data.search(' '.join(atual)) and rx_valor.search(' '.join(atual)):
                                saida.append(fechar(atual))
                            atual = buffer + [linha]
                        else:
                            atual.extend(buffer)
                            if rx_data.search(' '.join(atual)) and rx_valor.search(' '.join(atual)):
                                saida.append(fechar(atual))
                            atual = [linha]
                        buffer = []
                    else:
                        if rx_data.search(' '.join(atual)) and rx_valor.search(' '.join(atual)):
                            saida.append(fechar(atual))
                        atual = [linha]
                else:
                    atual = buffer + [linha]
                    buffer = []
            else:
                if atual:
                    buffer.append(linha)
                else:
                    buffer.append(linha)

        if atual:
            if buffer:
                atual.extend(buffer)
            if rx_data.search(' '.join(atual)) and rx_valor.search(' '.join(atual)):
                saida.append(fechar(atual))

        padrao = r"^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})$"

        dados = []

        for linha in saida:
            match = re.match(padrao, linha)

            if match:
                data = match.group(1)
                descricao = match.group(2)
                valor = match.group(3)

                dados.append({
                    "DATA": data,
                    "DESCRIÇÃO": descricao,
                    "VALOR": valor
                })
        df = pd.json_normalize(dados)
        df["VALOR"] = (
            df["VALOR"]
            .str.strip()
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .astype(float)
        )
        df["TIPO"] = df.apply(lambda row: "C" if row["VALOR"] > 0 else "D", axis=1)
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
        df = df[~df["DESCRIÇÃO"].astype(str).str.upper().str.contains("SALDO", na=False)]
        return df
    
    @layout("extrato mensal ")
    def layout3(self, pdf):
        periodo = pdf[9].split(" ")[1]
        indice = next((i for i, item in enumerate(pdf) if "totalizador de aplicações automáticas" in item), None)
        pdf = pdf[28:indice]
        pdf = [item for item in pdf if not any(texto in item for texto in [
            "Este material está ",
            "extrato mensal ",
            "data descrição entradas R$ saídas R$ saldo R$",
            "(créditos) (débitos)",
            " B001A ",
            "SALDO APLIC ",
            "Saldo em C/C",
            "Saldo final"
        ])]

        rx_espacos = re.compile(r"\s+")
        rx_data_inicio = re.compile(r"^\s*(\d{2}/\d{2}(?:/\d{2,4})?)\b")

        # Valor fiel ao extrato: precisa ter vírgula decimal.
        # Não captura conta, CPF/CNPJ, códigos Rede ou dia solto 25/26/28.
        rx_valor = re.compile(
            r"(?:R\$\s*)?-?\d{1,3}(?:\.\d{3})*,\d{2}[CD-]?"
            r"|(?:R\$\s*)?-?\d+,\d{2}[CD-]?"
        )

        rx_linha_irrelevante = re.compile(
            r"extrato mensal|data\s+descrição|data\s+historico|data\s+histórico|"
            r"entradas\s*r\$|saídas\s*r\$|saidas\s*r\$|saldo\s*r\$|"
            r"\(créditos\)|\(creditos\)|\(débitos\)|\(debitos\)|"
            r"este material|internet|menu conta corrente|b001a|g0192|"
            r"ag\s*\d+|cc\s*\d+|página|pagina",
            re.I
        )

        rx_saldo_ou_total = re.compile(
            r"^("
            r"saldo anterior|saldo final|saldo do dia|saldo disponível|saldo disponivel|"
            r"saldo aplic|saldo em c/c|totalizador|total entradas|total saídas|total saidas"
            r")\b",
            re.I
        )

        resultado = []
        data_atual = None

        for bruto in pdf:
            linha = rx_espacos.sub(" ", str(bruto)).strip()
            if not linha:
                continue

            if rx_linha_irrelevante.search(linha):
                continue

            m_data = rx_data_inicio.match(linha)

            if m_data:
                data_atual = m_data.group(1)
                resto = linha[m_data.end():].strip()
            else:
                resto = linha

            if not data_atual:
                continue

            if rx_saldo_ou_total.search(resto):
                continue

            valores = list(rx_valor.finditer(resto))
            if not valores:
                continue

            # No Itaú, quando aparecem 2 valores na mesma linha,
            # geralmente o 2º é saldo. A movimentação é o 1º valor.
            m_valor = valores[0]
            valor = m_valor.group(0).strip()
            descricao = resto[:m_valor.start()].strip()

            if not descricao:
                continue

            descricao = rx_espacos.sub(" ", descricao)

            resultado.append(f"{data_atual} {descricao} {valor}")

        padrao = re.compile(
            r"^(?P<data>\d{2}/\d{2})\s+"
            r"(?P<descricao>.+?)\s+"
            r"(?P<valor>\d{1,3}(?:\.\d{3})*,\d{2}-?)$"
        )

        dados = []

        for item in resultado:
            item = " ".join(str(item).split())

            match = padrao.match(item)

            if match:
                data = match.group("data")
                descricao = match.group("descricao")
                valor = match.group("valor")
                tipo = "D" if valor.endswith("-") else "C"

                dados.append({
                    "DATA": data,
                    "DESCRIÇÃO": descricao,
                    "VALOR": valor,
                    "TIPO": tipo
                })
            else:
                print("Não capturado:", item)
        df = pd.json_normalize(dados)
        df["VALOR"] = (
            df["VALOR"]
            .str.strip()
            .str.replace("-", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .astype(float)
        )
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper().str.strip()
        df["DATA"] = df.apply(lambda row: f"{row["DATA"]}/{periodo}", axis=1)
        return df