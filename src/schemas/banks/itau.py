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
        # ============================================================
        # LIMPEZA INICIAL DO PDF
        # ============================================================

        pdf = pdf[5:]

        pdf = [
            item for item in pdf
            if not any(texto in str(item) for texto in [
                "aviso: ",
                "novos lançamentos",
                "atualizado em ",
                "Em caso de dúvidas, ",
                " 24 horas por dia "
            ])
        ]


        # ============================================================
        # REGEX
        # ============================================================

        regex_data = re.compile(
            r"^\s*\d{2}/\d{2}/\d{4}\b"
        )

        regex_linha_inicia_com_data = re.compile(
            r"""
            ^\s*
            (?P<data>\d{2}/\d{2}/\d{4})
            (?:\s+(?P<resto>.*))?
            \s*$
            """,
            re.VERBOSE
        )

        regex_valor_final = re.compile(
            r"-?\d{1,3}(?:\.\d{3})*,\d{2}\s*$"
        )

        regex_valor_somente = re.compile(
            r"^\s*-?\d{1,3}(?:\.\d{3})*,\d{2}\s*$"
        )

        regex_linha_com_data_valor = re.compile(
            r"""
            ^\s*
            (?P<data>\d{2}/\d{2}/\d{4})
            \s+
            (?P<descricao>.*?)
            \s+
            (?P<valor>-?\d{1,3}(?:\.\d{3})*,\d{2})
            \s*$
            """,
            re.VERBOSE
        )

        regex_documento = re.compile(
            r"\b(?:\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2})\b"
        )

        regex_data_curta_colada = re.compile(
            r"[A-ZÀ-Ú]{3,}\d{2}/\d{2}"
        )


        # ============================================================
        # NORMALIZAÇÃO DAS LINHAS
        # ============================================================

        linhas = []

        for item in pdf:
            item = str(item).replace("\n", " ")
            item = re.sub(r"\s+", " ", item).strip()

            if item:
                linhas.append(item)


        # ============================================================
        # EXTRAÇÃO DAS MOVIMENTAÇÕES
        # ============================================================

        movimentacoes = []
        pendentes_antes = []

        i = 0

        while i < len(linhas):
            linha = linhas[i].strip()

            match = regex_linha_com_data_valor.match(linha)

            # ========================================================
            # CASO 1:
            # Linha completa ou aparentemente completa:
            #
            # 08/05/2026 DESCRIÇÃO 0,01
            # 08/05/2026 0,01
            # ========================================================

            if match:
                data = match.group("data")
                descricao_base = match.group("descricao").strip()
                valor = match.group("valor").strip()

                partes_antes = pendentes_antes
                pendentes_antes = []

                partes_depois = []

                doc_match = regex_documento.search(descricao_base)

                deve_buscar_depois = False

                # ----------------------------------------------------
                # Regra nova:
                # Se a descrição veio vazia, obrigatoriamente procura
                # descrição depois.
                #
                # Exemplo:
                # 08/05/2026 0,01
                # RENDIMENTOS REND PAGO APLIC
                # AUT MAIS
                # ----------------------------------------------------
                if not descricao_base:
                    deve_buscar_depois = True

                elif doc_match:
                    texto_antes_doc = descricao_base[:doc_match.start()].strip()
                    palavras_antes_doc = texto_antes_doc.split()

                    if len(palavras_antes_doc) <= 4:
                        deve_buscar_depois = True

                    if regex_data_curta_colada.search(texto_antes_doc):
                        deve_buscar_depois = True

                else:
                    palavras_descricao = descricao_base.split()

                    if len(palavras_descricao) <= 2:
                        deve_buscar_depois = True

                if deve_buscar_depois:
                    j = i + 1

                    while j < len(linhas):
                        proxima = linhas[j].strip()

                        proxima_tem_data = bool(regex_data.match(proxima))
                        proxima_e_valor = bool(regex_valor_somente.match(proxima))

                        if proxima_tem_data:
                            break

                        if proxima_e_valor:
                            break

                        partes_depois.append(proxima)
                        j += 1

                else:
                    j = i + 1

                complemento_antes = " ".join(partes_antes).strip()
                complemento_depois = " ".join(partes_depois).strip()

                complemento_antes = re.sub(r"\s+", " ", complemento_antes)
                complemento_depois = re.sub(r"\s+", " ", complemento_depois)

                complemento = f"{complemento_antes} {complemento_depois}".strip()
                complemento = re.sub(r"\s+", " ", complemento)

                if doc_match and complemento:
                    documento = doc_match.group()

                    antes_doc = descricao_base[:doc_match.start()].strip()
                    depois_doc = descricao_base[doc_match.end():].strip()

                    descricao_final = f"{antes_doc} {complemento} {documento} {depois_doc}"

                elif descricao_base and complemento:
                    descricao_final = f"{descricao_base} {complemento}"

                elif complemento:
                    descricao_final = complemento

                else:
                    descricao_final = descricao_base

                descricao_final = re.sub(r"\s+", " ", descricao_final).strip()

                movimentacoes.append({
                    "DATA": data,
                    "DESCRICAO": descricao_final,
                    "VALOR": valor
                })

                i = j
                continue

            # ========================================================
            # CASO 2:
            # Linha começa com data, mas valor está depois.
            #
            # 28/05/2026
            # RENDIMENTOS REND PAGO APLIC
            # AUT MAIS
            # 0,01
            # ========================================================

            match_data = regex_linha_inicia_com_data.match(linha)

            if match_data:
                data = match_data.group("data")
                resto = match_data.group("resto") or ""

                partes_descricao = []

                if resto.strip():
                    resto = resto.strip()

                    valor_no_resto = regex_valor_final.search(resto)

                    if valor_no_resto:
                        valor = valor_no_resto.group().strip()
                        descricao = resto[:valor_no_resto.start()].strip()

                        j = i + 1

                        # ------------------------------------------------
                        # Regra nova:
                        # Se data + valor veio sem descrição,
                        # busca descrição nas próximas linhas.
                        # ------------------------------------------------
                        if not descricao:
                            while j < len(linhas):
                                proxima = linhas[j].strip()

                                if regex_data.match(proxima):
                                    break

                                if regex_valor_somente.match(proxima):
                                    break

                                partes_descricao.append(proxima)
                                j += 1

                            descricao = " ".join(partes_descricao).strip()
                            descricao = re.sub(r"\s+", " ", descricao)

                        movimentacoes.append({
                            "DATA": data,
                            "DESCRICAO": descricao,
                            "VALOR": valor
                        })

                        pendentes_antes = []
                        i = j
                        continue

                    else:
                        partes_descricao.append(resto)

                j = i + 1
                valor = None

                while j < len(linhas):
                    proxima = linhas[j].strip()

                    if regex_data.match(proxima):
                        break

                    if regex_valor_somente.match(proxima):
                        valor = proxima
                        j += 1
                        break

                    valor_final = regex_valor_final.search(proxima)

                    if valor_final:
                        valor = valor_final.group().strip()
                        descricao_antes_valor = proxima[:valor_final.start()].strip()

                        if descricao_antes_valor:
                            partes_descricao.append(descricao_antes_valor)

                        j += 1
                        break

                    partes_descricao.append(proxima)
                    j += 1

                if valor:
                    complemento_antes = " ".join(pendentes_antes).strip()
                    descricao_final = " ".join(partes_descricao).strip()

                    if complemento_antes:
                        descricao_final = f"{complemento_antes} {descricao_final}"

                    descricao_final = re.sub(r"\s+", " ", descricao_final).strip()

                    movimentacoes.append({
                        "DATA": data,
                        "DESCRICAO": descricao_final,
                        "VALOR": valor
                    })

                    pendentes_antes = []
                    i = j
                    continue

                pendentes_antes.append(linha)
                i += 1
                continue

            # ========================================================
            # CASO 3:
            # Linha solta/complemento.
            # ========================================================

            pendentes_antes.append(linha)
            i += 1


        # ============================================================
        # DATAFRAME FINAL
        # ============================================================

        df = pd.json_normalize(movimentacoes)

        if not df.empty:

            df["VALOR"] = (
                df["VALOR"]
                .astype(str)
                .str.strip()
                .str.replace(".", "", regex=False)
                .str.replace(",", ".", regex=False)
                .str.replace(" ", "", regex=False)
                .astype(float)
            )

            df["TIPO"] = df.apply(
                lambda x: "C" if x["VALOR"] > 0 else "D",
                axis=1
            )

            df["DESCRICAO"] = (
                df["DESCRICAO"]
                .astype(str)
                .str.upper()
                .str.strip()
            )

            # Remove somente descrições que começam com SALDO
            df = df[
                ~df["DESCRICAO"]
                .astype(str)
                .str.upper()
                .str.match(r"^\s*SALDO\b", na=False)
            ]

            # Segurança: remove lançamentos sem descrição
            # apenas se realmente ficaram vazios após toda tentativa de complemento.
            df = df[
                df["DESCRICAO"]
                .astype(str)
                .str.strip()
                .ne("")
            ]

            df["VALOR"] = df["VALOR"].abs()

            df = df.rename(columns={
                "DESCRICAO": "DESCRIÇÃO"
            })

        else:
            df = pd.DataFrame(columns=["DATA", "DESCRIÇÃO", "VALOR", "TIPO"])
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