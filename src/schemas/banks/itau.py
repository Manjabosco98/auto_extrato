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

        padrao_linha_completa = re.compile(
            r"^(?P<data>\d{2}/\d{2}/\d{4})\s+"
            r"(?P<descricao>.+?)\s+"
            r"(?P<valor>-?\d{1,3}(?:\.\d{3})*,\d{2})$"
        )
        padrao_data_valor = re.compile(
            r"^(?P<data>\d{2}/\d{2}/\d{4})\s+"
            r"(?P<valor>-?\d{1,3}(?:\.\d{3})*,\d{2})$"
        )

        movimentacoes = []
        pendentes_antes = []

        i = 0

        while i < len(pdf):
            linha = pdf[i].strip()

            if not linha:
                i += 1
                continue

            match_completo = padrao_linha_completa.match(linha)
            match_data_valor = padrao_data_valor.match(linha)

            if match_completo:
                dados = match_completo.groupdict()

                descricao_extra_antes = " ".join(pendentes_antes).strip()

                descricao = dados["descricao"].strip()

                if descricao_extra_antes:
                    descricao = f"{descricao_extra_antes} {descricao}".strip()

                movimentacoes.append({
                    "DATA": dados["data"],
                    "DESCRICAO": descricao,
                    "VALOR": dados["valor"],
                    "TIPO": "D" if dados["valor"].startswith("-") else "C"
                })

                pendentes_antes = []
                i += 1
                continue

            if match_data_valor:
                dados = match_data_valor.groupdict()

                partes_descricao = []

                if pendentes_antes:
                    partes_descricao.extend(pendentes_antes)

                j = i + 1

                while j < len(pdf):
                    proxima = pdf[j].strip()

                    if not proxima:
                        j += 1
                        continue

                    if re.match(r"^\d{2}/\d{2}/\d{4}", proxima):
                        break

                    partes_descricao.append(proxima)
                    j += 1

                descricao = " ".join(partes_descricao).strip()

                movimentacoes.append({
                    "DATA": dados["data"],
                    "DESCRICAO": descricao,
                    "VALOR": dados["valor"],
                    "TIPO": "D" if dados["valor"].startswith("-") else "C"
                })

                pendentes_antes = []
                i = j
                continue

            pendentes_antes.append(linha)
            i += 1

        df = pd.json_normalize(movimentacoes)
        df["VALOR"] = (
            df["VALOR"]
            .str.strip()
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .str.replace("-", "", regex=False)
            .astype(float)
        )
        df["DESCRICAO"] = df["DESCRICAO"].str.upper()
        df = df.rename(columns={"DESCRICAO": "DESCRIÇÃO"})
        df = df[
            ~df["DESCRIÇÃO"]
            .astype(str)
            .str.replace(r"\s+", "", regex=True)
            .str.upper()
            .str.contains("SALDO", na=False)
        ]
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
        if df.empty:
            return pd.DataFrame(columns=["DATA", "DESCRIÇÃO", "VALOR", "TIPO"])
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