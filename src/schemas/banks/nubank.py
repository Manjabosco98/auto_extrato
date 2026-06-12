import re
import pandas as pd
from src.schemas.base import BankHandler, layout
from src.schemas.registry import register

@register
class Nubank(BankHandler):
    bank = "Nubank"
    layout_head_lines = 5
    layout_tail_lines = 30

    def _pick_layout(self, pdf: list[str]):
        head_tail = "\n".join(
            pdf[: self.layout_head_lines] + pdf[-self.layout_tail_lines:]
        )
        candidates = [
            fn
            for fn in self._layouts()
            if any(sig in head_tail for sig in fn._layout_signatures)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda fn: len(fn._layout_signatures))

    @layout("Nu Pagamentos S.A. ", "Nu Financeira S.A. ", "NU PAGAMENTOS ")
    def layout1(self, pdf):
        pdf = pdf.copy()
        empresa = pdf[0]
        conta = pdf[2]
        cpf = pdf[1]
        datas = pdf[3]
        pdf = pdf[11:]
        pdf = [item for item in pdf if not any(texto in item for texto in [empresa, cpf, conta, datas, "CNPJ", "Tem alguma dúvida?", "metropolitanas) ", "Caso a solução ", "disponíveis em nubank.com.br", "Extrato gerado ", "O saldo líquido ", "Não nos responsabilizamos ", "Asseguramos a autenticidade ", "Nu Financeira S.A.", "e Investimento", "Nu Pagamentos S.A. - Instituição de Pagamento", "Investimento Pagamento"])]

        def normalizar_movimentacoes(lista: list[str]) -> dict:
            re_espacos = re.compile(r"\s+")
            re_data = re.compile(
                r"^(?P<dia>\d{2})\s+"
                r"(?P<mes>JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+"
                r"(?P<ano>\d{4})(?P<resto>.*)$",
                re.I
            )
            re_total = re.compile(
                r"Total de (?P<tipo>entradas|saídas|saidas)\s+"
                r"(?P<sinal>[+-])\s+"
                r"(?P<valor>\d{1,3}(?:\.\d{3})*,\d{2})",
                re.I
            )
            re_valor = re.compile(
                r"(?<![\d.,])(?P<valor>\d{1,3}(?:\.\d{3})*,\d{2})(?![\d.,])"
            )
            re_inicio = re.compile(
                r"^(Transferência enviada pelo Pix|Transferencia enviada pelo Pix|"
                r"Transferência recebida pelo Pix|Transferencia recebida pelo Pix|"
                r"Transferência Recebida|Transferencia Recebida|"
                r"Pagamento de boleto efetuado|Aplicação RDB|Aplicacao RDB|"
                r"Resgate de empréstimo|Resgate de emprestimo|"
                r"Reembolso recebido pelo Pix|Valor adicionado na conta)",
                re.I
            )
            re_ignorar = re.compile(
                r"(Tem alguma dúvida|metropolitanas\)|Caso a solução|disponíveis em nubank|"
                r"Extrato gerado|O saldo líquido corresponde|Não nos responsabilizamos|"
                r"Asseguramos a autenticidade|Nu Financeira|Nu Pagamentos|CNPJ:|"
                r"VALORES EM R\$|CPF .* Agência 0001 Conta)",
                re.I
            )

            meses = {
                "JAN": "01", "FEV": "02", "MAR": "03", "ABR": "04",
                "MAI": "05", "JUN": "06", "JUL": "07", "AGO": "08",
                "SET": "09", "OUT": "10", "NOV": "11", "DEZ": "12"
            }

            resultado = {
                "status_geral": "ok",
                "movimentacoes": [],
                "totalizadores": [],
                "cabecalhos_data": [],
                "ignoradas_com_segurança": [],
                "revisar": [],
                "auditoria": {}
            }

            data_original = None
            data_iso = None
            contexto = None
            mov_aberta = None

            def limpar(texto: str) -> str:
                return re_espacos.sub(" ", texto).strip()

            def valor_decimal(valor: str) -> float:
                return float(valor.replace(".", "").replace(",", "."))

            def separar_valor(texto: str):
                achados = list(re_valor.finditer(texto))
                if not achados:
                    return None, texto
                m = achados[-1]
                valor = m.group("valor")
                descricao = limpar((texto[:m.start()] + " " + texto[m.end():]).strip())
                return valor, descricao

            def revisar_linha(idx: int, linha: str, motivo: str):
                resultado["revisar"].append({
                    "indices": [idx],
                    "linhas_origem": [linha],
                    "motivo": motivo
                })

            def fechar_movimentacao(motivo_fechamento: str = ""):
                nonlocal mov_aberta

                if mov_aberta is None:
                    return

                descricao = limpar(" ".join(mov_aberta["partes_descricao"])).strip(" -")
                motivos = []

                if not mov_aberta["data_original"]:
                    motivos.append("movimentação sem data herdada")
                if not mov_aberta["valor_original"]:
                    motivos.append("movimentação sem valor monetário")
                if not descricao:
                    motivos.append("movimentação sem descrição")
                if mov_aberta["categoria_contexto"] not in ("entrada", "saida"):
                    motivos.append("movimentação sem contexto seguro de entrada/saída")
                if mov_aberta["alertas"]:
                    motivos.extend(mov_aberta["alertas"])

                status = "revisar" if motivos else "ok"

                resultado["movimentacoes"].append({
                    "data_original": mov_aberta["data_original"],
                    "data": mov_aberta["data"],
                    "descricao": descricao,
                    "valor_original": mov_aberta["valor_original"],
                    "valor": valor_decimal(mov_aberta["valor_original"]) if mov_aberta["valor_original"] else None,
                    "tipo": "C" if mov_aberta["categoria_contexto"] == "entrada" else "D"
                            if mov_aberta["categoria_contexto"] == "saida" else None,
                    "categoria_contexto": mov_aberta["categoria_contexto"],
                    "indices_origem": mov_aberta["indices_origem"],
                    "linhas_origem": mov_aberta["linhas_origem"],
                    "status": status,
                    "motivo_revisao": "; ".join(motivos) if motivos else None
                })

                mov_aberta = None

            for idx, linha_original in enumerate(lista):
                linha_original = "" if linha_original is None else str(linha_original)
                linha = limpar(linha_original)

                if not linha:
                    fechar_movimentacao("linha vazia")
                    resultado["ignoradas_com_segurança"].append({
                        "indice": idx,
                        "linha": linha_original,
                        "motivo": "linha vazia"
                    })
                    continue

                if re_ignorar.search(linha):
                    fechar_movimentacao("linha institucional/cabeçalho/rodapé")
                    resultado["ignoradas_com_segurança"].append({
                        "indice": idx,
                        "linha": linha_original,
                        "motivo": "linha institucional/cabeçalho/rodapé"
                    })
                    continue

                m_data = re_data.match(linha)
                resto_apos_data = linha

                if m_data:
                    fechar_movimentacao("nova data encontrada")

                    data_original = f"{m_data.group('dia')} {m_data.group('mes').upper()} {m_data.group('ano')}"
                    data_iso = f"{m_data.group('ano')}-{meses[m_data.group('mes').upper()]}-{m_data.group('dia')}"
                    resto_apos_data = limpar(m_data.group("resto"))

                    resultado["cabecalhos_data"].append({
                        "data_original": data_original,
                        "data": data_iso,
                        "indice": idx,
                        "linha_original": linha_original
                    })

                m_total = re_total.search(resto_apos_data)

                if m_total:
                    fechar_movimentacao("totalizador encontrado")

                    tipo_total = "entrada" if m_total.group("tipo").lower().startswith("entrada") else "saida"
                    contexto = tipo_total
                    valor_total = m_total.group("valor")

                    resultado["totalizadores"].append({
                        "data_original": data_original,
                        "tipo": tipo_total,
                        "valor_original": valor_total,
                        "valor": valor_decimal(valor_total),
                        "indice": idx,
                        "linha_original": linha_original
                    })
                    continue

                if m_data and not resto_apos_data:
                    continue

                qtd_valores = len(list(re_valor.finditer(linha)))
                inicio_mov = bool(re_inicio.match(linha))

                if inicio_mov and qtd_valores == 1:
                    fechar_movimentacao("nova movimentação encontrada")

                    valor_original, desc_sem_valor = separar_valor(linha)

                    mov_aberta = {
                        "data_original": data_original,
                        "data": data_iso,
                        "categoria_contexto": contexto,
                        "valor_original": valor_original,
                        "partes_descricao": [desc_sem_valor] if desc_sem_valor else [],
                        "indices_origem": [idx],
                        "linhas_origem": [linha_original],
                        "alertas": []
                    }
                    continue

                if inicio_mov and qtd_valores != 1:
                    fechar_movimentacao("linha com possível início sem valor único")
                    revisar_linha(
                        idx,
                        linha_original,
                        "linha possui termo de início de movimentação, mas não possui exatamente um valor monetário"
                    )
                    continue

                if mov_aberta is not None:
                    mov_aberta["indices_origem"].append(idx)
                    mov_aberta["linhas_origem"].append(linha_original)
                    mov_aberta["partes_descricao"].append(linha)

                    if qtd_valores > 0:
                        mov_aberta["alertas"].append(
                            f"linha complementar com valor monetário no índice {idx}"
                        )
                    continue

                revisar_linha(
                    idx,
                    linha_original,
                    "linha não pôde ser associada com segurança a data, totalizador, movimentação ou ignorada segura"
                )

            fechar_movimentacao("fim da lista")

            todos = set(range(len(lista)))
            usados_mov = []
            usados_tot = []
            usados_datas = []
            usados_ign = []
            usados_rev = []

            for m in resultado["movimentacoes"]:
                usados_mov.extend(m["indices_origem"])

            for t in resultado["totalizadores"]:
                usados_tot.append(t["indice"])

            for c in resultado["cabecalhos_data"]:
                usados_datas.append(c["indice"])

            for i in resultado["ignoradas_com_segurança"]:
                usados_ign.append(i["indice"])

            for r in resultado["revisar"]:
                usados_rev.extend(r["indices"])

            todos_usados_lista = usados_mov + usados_tot + usados_datas + usados_ign + usados_rev
            todos_usados_set = set(todos_usados_lista)

            duplicados = sorted({
                x for x in todos_usados_lista
                if todos_usados_lista.count(x) > 1
            })
            nao_classificados = sorted(todos - todos_usados_set)

            for idx in nao_classificados:
                resultado["revisar"].append({
                    "indices": [idx],
                    "linhas_origem": [lista[idx]],
                    "motivo": "índice não classificado na auditoria final"
                })

            # recalcula revisão após inserir índices não classificados
            usados_rev = []
            for r in resultado["revisar"]:
                usados_rev.extend(r["indices"])

            resultado["auditoria"] = {
                "total_linhas_entrada": len(lista),
                "total_indices_classificados": len(set(todos_usados_lista) | set(nao_classificados)),
                "total_indices_em_movimentacoes": len(set(usados_mov)),
                "total_indices_totalizadores": len(set(usados_tot)),
                "total_indices_cabecalhos_data": len(set(usados_datas)),
                "total_indices_ignorados_com_segurança": len(set(usados_ign)),
                "total_indices_revisar": len(set(usados_rev)),
                "indices_nao_classificados": nao_classificados,
                "indices_duplicados": duplicados
            }

            existe_mov_revisar = any(m["status"] != "ok" for m in resultado["movimentacoes"])

            if nao_classificados or duplicados or resultado["revisar"] or existe_mov_revisar:
                resultado["status_geral"] = "revisar"
            else:
                resultado["status_geral"] = "ok"

            return resultado

        resultado = normalizar_movimentacoes(pdf)["movimentacoes"]
        df = pd.json_normalize(resultado)
        df = df[["data_original", "data", "descricao", "valor_original", "tipo"]]
        df = df.rename(columns={"data": "DATA", "descricao": "DESCRIÇÃO", "valor_original": "VALOR", "tipo": "TIPO"})
        df["VALOR"] = (
            df["VALOR"]
            .str.strip()
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .astype(float)
        )
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
        df["DATA"] = pd.to_datetime(df["DATA"], errors="coerce").dt.strftime("%d/%m/%Y")
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
        df["VALOR"] = df["VALOR"].abs()
        return df