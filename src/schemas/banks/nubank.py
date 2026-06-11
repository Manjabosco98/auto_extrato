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

        regex_valor = re.compile(
            r'(?<![\d.-])(?P<valor>\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})$'
        )

        regex_data = re.compile(
            r'^(?P<data>\d{2}\s+[A-Z]{3}\s+\d{4})'
            r'(?:\s+Total de (?P<secao>entradas|saídas)\s+(?P<sinal>[+-])\s*(?P<total>\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}))?$',
            re.IGNORECASE
        )

        regex_total_secao = re.compile(
            r'^Total de (?P<secao>entradas|saídas)\s+(?P<sinal>[+-])\s*(?P<total>\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})$',
            re.IGNORECASE
        )

        regex_inicio_mov = re.compile(
            r'^(?:'
            r'Transferência recebida pelo Pix|'
            r'Transferência Recebida|'
            r'Reembolso recebido pelo Pix|'
            r'Valor adicionado na conta por cartão|'
            r'Transferência enviada pelo Pix|'
            r'Pagamento de boleto efetuado|'
            r'Aplicação RDB|'
            r'Resgate de empréstimo'
            r')\b',
            re.IGNORECASE
        )


        # =========================
        # 2. VARIÁVEIS DE CONTROLE
        # =========================

        movimentacoes = []

        data_atual = None
        secao_atual = None
        buffer = []


        # =========================
        # 3. LOOP PRINCIPAL
        # =========================

        for linha in pdf:
            linha = str(linha).strip()
            linha = re.sub(r'\s+', ' ', linha)

            if not linha:
                continue

            # =========================
            # Ignorar cabeçalhos e rodapés
            # =========================

            if linha.startswith('João Vitor Rodrigues de Cerqueira'):
                continue

            if linha.startswith('CPF '):
                continue

            if 'VALORES EM R$' in linha:
                continue

            if linha.startswith('Saldo final do período'):
                continue

            if linha.startswith('Saldo inicial'):
                continue

            if linha.startswith('Rendimento líquido'):
                continue

            if linha == 'Movimentações':
                continue

            if linha.startswith('Tem alguma dúvida?'):
                continue

            if linha.startswith('Caso a solução fornecida'):
                continue

            if linha.startswith('Extrato gerado dia'):
                continue

            if linha.endswith('de 15'):
                continue

            if linha.startswith('O saldo líquido corresponde'):
                continue

            if linha.startswith('Não nos responsabilizamos'):
                continue

            if linha.startswith('Asseguramos a autenticidade'):
                continue

            if linha.startswith('Nu Financeira'):
                continue

            if linha.startswith('Nu Pagamentos'):
                continue

            if linha.startswith('CNPJ:'):
                continue


            # =========================
            # Detecta data
            # Exemplo:
            # 30 ABR 2026 Total de entradas + 1.015,00
            # 10 ABR 2026
            # =========================

            match_data = regex_data.match(linha)

            if match_data:
                # Antes de trocar a data, tenta finalizar movimentação pendente
                if buffer:
                    texto = ' '.join(buffer)
                    texto = re.sub(r'\s+', ' ', texto).strip()

                    match_valor = regex_valor.search(texto)

                    if match_valor:
                        valor_txt = match_valor.group('valor')
                        valor = float(valor_txt.replace('.', '').replace(',', '.'))

                        descricao = texto[:match_valor.start()].strip()
                        descricao = re.sub(r'\s+', ' ', descricao)

                        if secao_atual == 'entradas':
                            tipo = 'C'
                        elif secao_atual == 'saídas':
                            tipo = 'D'
                        else:
                            tipo = None

                        if descricao and data_atual and tipo:
                            movimentacoes.append({
                                'DATA': data_atual,
                                'DESCRICAO': descricao,
                                'VALOR': valor,
                                'TIPO': tipo,
                                'VALOR_ORIGINAL': valor_txt,
                                'SECAO': secao_atual
                            })

                buffer = []
                data_atual = match_data.group('data').upper()

                if match_data.group('secao'):
                    secao_atual = match_data.group('secao').lower()

                continue


            # =========================
            # Detecta Total de entradas / Total de saídas
            # =========================

            match_total = regex_total_secao.match(linha)

            if match_total:
                # Antes de trocar a seção, tenta finalizar movimentação pendente
                if buffer:
                    texto = ' '.join(buffer)
                    texto = re.sub(r'\s+', ' ', texto).strip()

                    match_valor = regex_valor.search(texto)

                    if match_valor:
                        valor_txt = match_valor.group('valor')
                        valor = float(valor_txt.replace('.', '').replace(',', '.'))

                        descricao = texto[:match_valor.start()].strip()
                        descricao = re.sub(r'\s+', ' ', descricao)

                        if secao_atual == 'entradas':
                            tipo = 'C'
                        elif secao_atual == 'saídas':
                            tipo = 'D'
                        else:
                            tipo = None

                        if descricao and data_atual and tipo:
                            movimentacoes.append({
                                'DATA': data_atual,
                                'DESCRICAO': descricao,
                                'VALOR': valor,
                                'TIPO': tipo,
                                'VALOR_ORIGINAL': valor_txt,
                                'SECAO': secao_atual
                            })

                buffer = []
                secao_atual = match_total.group('secao').lower()

                continue


            # =========================
            # Detecta início de uma nova movimentação
            # =========================

            if regex_inicio_mov.match(linha):
                # Antes de iniciar nova movimentação, tenta finalizar a anterior
                if buffer:
                    texto = ' '.join(buffer)
                    texto = re.sub(r'\s+', ' ', texto).strip()

                    match_valor = regex_valor.search(texto)

                    if match_valor:
                        valor_txt = match_valor.group('valor')
                        valor = float(valor_txt.replace('.', '').replace(',', '.'))

                        descricao = texto[:match_valor.start()].strip()
                        descricao = re.sub(r'\s+', ' ', descricao)

                        if secao_atual == 'entradas':
                            tipo = 'C'
                        elif secao_atual == 'saídas':
                            tipo = 'D'
                        else:
                            tipo = None

                        if descricao and data_atual and tipo:
                            movimentacoes.append({
                                'DATA': data_atual,
                                'DESCRICAO': descricao,
                                'VALOR': valor,
                                'TIPO': tipo,
                                'VALOR_ORIGINAL': valor_txt,
                                'SECAO': secao_atual
                            })

                        buffer = [linha]

                    else:
                        # Se a anterior ainda não tinha valor, mantém cuidado:
                        # inicia nova somente se a anterior estava incompleta.
                        # Na prática, isso evita carregar sujeira para outra movimentação.
                        buffer = [linha]

                else:
                    buffer = [linha]

                continue


            # =========================
            # Continuação da movimentação anterior
            # =========================

            if buffer:
                buffer.append(linha)


        # =========================
        # 4. FINALIZA A ÚLTIMA MOVIMENTAÇÃO
        # =========================

        if buffer:
            texto = ' '.join(buffer)
            texto = re.sub(r'\s+', ' ', texto).strip()

            match_valor = regex_valor.search(texto)

            if match_valor:
                valor_txt = match_valor.group('valor')
                valor = float(valor_txt.replace('.', '').replace(',', '.'))

                descricao = texto[:match_valor.start()].strip()
                descricao = re.sub(r'\s+', ' ', descricao)

                if secao_atual == 'entradas':
                    tipo = 'C'
                elif secao_atual == 'saídas':
                    tipo = 'D'
                else:
                    tipo = None

                if descricao and data_atual and tipo:
                    movimentacoes.append({
                        'DATA': data_atual,
                        'DESCRICAO': descricao,
                        'VALOR': valor,
                        'TIPO': tipo,
                        'VALOR_ORIGINAL': valor_txt,
                        'SECAO': secao_atual
                    })


        # =========================
        # 5. DATAFRAME FINAL
        # =========================

        df = pd.DataFrame(movimentacoes)
        df = df.rename(columns={"DESCRICAO": "DESCRIÇÃO"})
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
        df = df.rename(columns={"VALOR_BRUTO": "VALOR"})
        df["DATA"] = (
            df["DATA"]
            .astype(str)
            .str.strip()
            .str.upper()
            .str.replace("JAN", "01", regex=False)
            .str.replace("FEV", "02", regex=False)
            .str.replace("MAR", "03", regex=False)
            .str.replace("ABR", "04", regex=False)
            .str.replace("MAI", "05", regex=False)
            .str.replace("JUN", "06", regex=False)
            .str.replace("JUL", "07", regex=False)
            .str.replace("AGO", "08", regex=False)
            .str.replace("SET", "09", regex=False)
            .str.replace("OUT", "10", regex=False)
            .str.replace("NOV", "11", regex=False)
            .str.replace("DEZ", "12", regex=False)
            .str.replace(" ", "/", regex=False)
        )
        df["DATA"] = pd.to_datetime(df["DATA"], format="%d/%m/%Y", errors="coerce").dt.strftime("%d/%m/%Y")
        df["VALOR"] = df["VALOR"].abs()
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]
        return df