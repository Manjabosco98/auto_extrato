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

        rx_data = re.compile(r'\b\d{2}/\d{2}/\d{4}\b')
        rx_valor = re.compile(
            r'(?<![\d./-])-?\d{1,3}(?:\.\d{3})*,\d{2}(?![\d/])'
        )
        rx_cnpj_cpf = re.compile(
            r'\b(?:\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2})\b'
        )
        rx_ignorar_linha = re.compile(
            r'^(DATA\b|LANÇAMENTOS\b|RAZÃO SOCIAL\b|CNPJ/CPF\b|VALOR\b|SALDO\b|'
            r'AG[ÊE]NCIA\b|CONTA\b|CPF\b|CNPJ\b|EXTRATO\b|P[ÁA]GINA\b|'
            r'BANCO\b|OUVIDORIA\b|SAC\b|CENTRAL\b|PER[IÍ]ODO\b|AVISO:)',
            re.I
        )
        rx_ignorar_movimentacao = re.compile(
            r'\b(SALDO ANTERIOR|SALDO TOTAL DISPON[IÍ]VEL DIA|SALDO FINAL)\b',
            re.I
        )

        def limpar(txt):
            return re.sub(r'\s+', ' ', str(txt)).strip()


        def tem_valor(bloco):
            texto = limpar(" ".join(bloco))
            return bool(rx_valor.search(texto))


        def montar(bloco):
            texto = limpar(" ".join(bloco))

            m_data = rx_data.search(texto)
            if not m_data:
                return None

            data = m_data.group(0)

            valores = list(rx_valor.finditer(texto))
            if not valores:
                return None

            # Pega sempre o último valor como valor da movimentação
            m_valor = valores[-1]
            valor = m_valor.group(0)

            antes_data = texto[:m_data.start()].strip()
            depois_data = texto[m_data.end():].strip()

            # Remove o valor final do texto
            if m_valor.start() >= m_data.end():
                depois_data = (
                    texto[m_data.end():m_valor.start()] + " " + texto[m_valor.end():]
                ).strip()
                descricao = limpar(f"{depois_data} {antes_data}")
            else:
                descricao = limpar(f"{depois_data} {antes_data}")

            descricao = descricao.strip(" -")

            if rx_ignorar_movimentacao.search(descricao):
                return None

            return limpar(f"{data} {descricao} {valor}")


        resultado = []
        atual = []
        prefixo = []

        for linha in pdf:
            linha = limpar(linha)

            if not linha:
                continue

            if rx_ignorar_linha.search(linha):
                if atual and tem_valor(atual):
                    mov = montar(atual)
                    if mov:
                        resultado.append(mov)

                atual = []
                prefixo = []
                continue

            tem_data = bool(rx_data.search(linha))

            # Caso 1: linha com data inicia nova movimentação
            if tem_data:
                if atual and tem_valor(atual):
                    mov = montar(atual)
                    if mov:
                        resultado.append(mov)

                atual = prefixo + [linha]
                prefixo = []
                continue

            # Caso 2: linha sem data
            if atual and tem_valor(atual):
                # PONTO PRINCIPAL:
                # Se a movimentação atual já tem valor, ela está fechada.
                # Esta linha sem data provavelmente é continuação visual da próxima movimentação.
                mov = montar(atual)
                if mov:
                    resultado.append(mov)

                atual = []
                prefixo = [linha]
                continue

            if atual:
                # Ainda não encontrou valor, então é complemento da movimentação atual
                atual.append(linha)
            else:
                # Linha solta antes da próxima data
                prefixo.append(linha)

        if atual and tem_valor(atual):
            mov = montar(atual)
            if mov:
                resultado.append(mov)

        padrao = r"^(?P<data>\d{2}/\d{2}/\d{4})\s+(?P<descricao>.*)\s+(?P<valor>-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+)$"

        dados = []

        for item in resultado:
            item = item.strip()

            match = re.match(padrao, item)

            if match:
                data = match.group("data").strip()
                descricao = match.group("descricao").strip()
                valor = match.group("valor").strip()

                tipo = "D" if valor.startswith("-") else "C"

                dados.append({
                    "DATA": data,
                    "DESCRIÇÃO": descricao,
                    "VALOR": valor,
                    "TIPO": tipo
                })
            else:
                print("NÃO CAPTUROU:", item)

        df = pd.DataFrame(dados)
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