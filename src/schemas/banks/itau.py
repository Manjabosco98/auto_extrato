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

        re_data = re.compile(r'\b\d{2}/\d{2}(?:/\d{2,4})?\b')
        re_valor = re.compile(
            r'(?:R\$\s*)?-?\d{1,3}(?:\.\d{3})*,\d{2}[CD]?|'
            r'R\$\s*-?\d+(?:,\d{2})?[CD]?'
        )

        re_cnpj_cpf = re.compile(
            r'\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b|'
            r'\b\d{3}\.\d{3}\.\d{3}-\d{2}\b'
        )

        re_lixeira = re.compile(
            r'aviso:|novos lançamentos|atualizado em|em caso de dúvidas|'
            r'central|ouvidoria|sac|data\s+lançamentos|razão social|cnpj/cpf',
            re.I
        )

        re_aceita_quebra = re.compile(
            r'\b(PIX|TED|DOC|TRANSFER[ÊE]NCIA|PAGAMENTOS?|RENDIMENTOS?)\b',
            re.I
        )

        # NOVO: identifica linha que tem apenas DATA + VALOR
        # Exemplo: "21/05/2026 0,03"
        re_linha_so_data_valor = re.compile(
            r'^\s*\d{2}/\d{2}(?:/\d{2,4})?\s+'
            r'(?:R\$\s*)?-?\d{1,3}(?:\.\d{3})*,\d{2}[CD]?\s*$'
        )

        limpas = []

        for linha in pdf:
            linha = re.sub(r'\s+', ' ', linha or '').strip()

            if linha and not re_lixeira.search(linha):
                limpas.append(linha)


        resultado = []
        pendentes = []

        i = 0

        while i < len(limpas):
            linha = limpas[i]

            # Se a linha não tem data, ela pode ser complemento/descrição quebrada
            if not re_data.search(linha):
                pendentes.append(linha)
                i += 1
                continue

            data = re_data.search(linha).group(0)

            # NOVO: verifica se a linha atual é apenas "data + valor"
            linha_so_data_valor = bool(re_linha_so_data_valor.search(linha))

            # Ajustado: agora também usa pendentes quando a linha atual for só data + valor
            usar_pendentes = bool(
                pendentes and (
                    re_aceita_quebra.search(linha) or
                    re_cnpj_cpf.search(linha) or
                    linha_so_data_valor
                )
            )

            partes = []

            if usar_pendentes:
                partes.extend(pendentes)

            partes.append(linha)
            pendentes = []

            j = i + 1

            while j < len(limpas) and not re_data.search(limpas[j]):

                # Ajustado: também aceita complemento depois quando a linha for só data + valor
                if (
                    re_aceita_quebra.search(linha) or
                    re_cnpj_cpf.search(linha) or
                    linha_so_data_valor
                ):
                    partes.append(limpas[j])
                else:
                    pendentes.append(limpas[j])

                j += 1

            texto = re.sub(r'\s+', ' ', ' '.join(partes)).strip()
            valores = list(re_valor.finditer(texto))

            if valores:
                valor = valores[-1].group(0)

                ini, fim = valores[-1].span()

                descricao = texto[:ini] + ' ' + texto[fim:]

                # Remove a data principal da descrição
                descricao = descricao.replace(data, ' ', 1)

                # Remove valores duplicados que ficaram no meio da descrição
                descricao = re_valor.sub(' ', descricao)

                descricao = re.sub(r'\s+', ' ', descricao).strip()

                resultado.append(f'{data} {descricao} {valor}'.strip())

            i = j if j > i + 1 else i + 1

        padrao = r"^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})$"

        dados = []

        for linha in resultado:
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