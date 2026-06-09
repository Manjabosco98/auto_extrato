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

        padrao_linha_principal = re.compile(
            r"^\s*"
            r"(?P<data>\d{2}/\d{2}/\d{4})"
            r"\s+"
            r"(?P<descricao>.*?)"
            r"\s*"
            r"(?P<valor>-?\d{1,3}(?:\.\d{3})*,\d{2})"
            r"\s*$"
        )

        padrao_documento = re.compile(
            r"(?P<documento>(?:\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})|(?:\d{3}\.\d{3}\.\d{3}-\d{2}))"
        )

        movimentacoes = []

        linhas_antes = []
        mov_atual = None

        for linha in pdf:
            linha = str(linha).strip()

            if not linha:
                continue

            match = padrao_linha_principal.match(linha)

            if match:
                # Finaliza movimentação anterior
                if mov_atual:
                    movimentacoes.append(mov_atual)

                data = match.group("data")
                descricao_base = match.group("descricao").strip()
                valor = match.group("valor").strip()

                mov_atual = {
                    "DATA": data,
                    "DESCRICAO_BASE": descricao_base,
                    "LINHAS_ANTES": linhas_antes.copy(),
                    "LINHAS_DEPOIS": [],
                    "VALOR": valor
                }

                linhas_antes = []

            else:
                if mov_atual is None:
                    # Linha sem data antes da primeira movimentação
                    linhas_antes.append(linha)
                else:
                    # Linha sem data depois da linha principal
                    mov_atual["LINHAS_DEPOIS"].append(linha)

        # Finaliza última movimentação
        if mov_atual:
            movimentacoes.append(mov_atual)

        dados_normalizados = []

        for mov in movimentacoes:
            data = mov["DATA"]
            descricao_base = mov["DESCRICAO_BASE"]
            linhas_antes = mov["LINHAS_ANTES"]
            linhas_depois = mov["LINHAS_DEPOIS"]
            valor = mov["VALOR"]

            partes_quebradas = " ".join(linhas_antes + linhas_depois).strip()

            match_doc = padrao_documento.search(descricao_base)

            if match_doc:
                inicio_doc = match_doc.start()

                descricao_antes_doc = descricao_base[:inicio_doc].strip()
                descricao_doc_em_diante = descricao_base[inicio_doc:].strip()

                descricao_final = " ".join(
                    parte for parte in [
                        descricao_antes_doc,
                        partes_quebradas,
                        descricao_doc_em_diante
                    ]
                    if parte
                )

            else:
                descricao_final = " ".join(
                    parte for parte in [
                        descricao_base,
                        partes_quebradas
                    ]
                    if parte
                )

            descricao_final = re.sub(r"\s+", " ", descricao_final).strip()

            tipo = "D" if valor.startswith("-") else "C"

            dados_normalizados.append({
                "DATA": data,
                "DESCRIÇÃO": descricao_final,
                "VALOR": valor,
                "TIPO": tipo
            })

        df = pd.DataFrame(dados_normalizados)
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
        df["VALOR"] = df["VALOR"].abs()
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