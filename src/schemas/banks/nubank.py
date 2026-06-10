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

        re_data = re.compile(
            r'\b\d{2}\s+(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+\d{4}\b',
            re.I
        )

        re_valor = re.compile(
            r'(?:R\$\s*)?[+-]?\s*\d{1,3}(?:\.\d{3})*,\d{2}|\b(?:R\$\s*)?[+-]?\s*\d+,\d{2}\b'
        )

        re_total = re.compile(
            r'\bTotal\s+de\s+(entradas|sa[ií]das)\b',
            re.I
        )

        re_inicio = re.compile(
            r'^(Transfer[êe]ncia|Pagamento|Reembolso|Valor adicionado|Aplica[çc][ãa]o|Resgate)',
            re.I
        )

        re_ignorar = re.compile(
            r'(CPF|Ag[êe]ncia\s+0001\s+Conta|VALORES EM R\$|'
            r'Movimenta[çc][õo]es|Ouvidoria|Atendimento|Extrato gerado|'
            r'Nu Financeira|Nu Pagamentos|CNPJ:|Tem alguma d[uú]vida|'
            r'nubank\.com|^\d+\s+de\s+\d+$|'
            r'01 DE ABRIL DE 2026 a 30 DE ABRIL DE 2026|'
            r'João Vitor Rodrigues|Não nos responsabilizamos|'
            r'Asseguramos a autenticidade)',
            re.I
        )

        resultado = []

        data_atual = ""
        tipo_atual = None
        bloco = []


        def fechar_bloco():
            nonlocal bloco, data_atual, tipo_atual, resultado

            if not bloco or not data_atual:
                bloco = []
                return

            texto = re.sub(r'\s+', ' ', ' '.join(bloco)).strip()

            valores = list(re_valor.finditer(texto))

            if not valores:
                bloco = []
                return

            valor_match = valores[-1]
            valor_bruto = valor_match.group().strip()

            descricao = (
                texto[:valor_match.start()] + texto[valor_match.end():]
            ).strip()

            descricao = re.sub(r'\s+', ' ', descricao).strip(" -")

            if descricao:
                resultado.append({
                    "DATA": data_atual,
                    "DESCRIÇÃO": descricao,
                    "VALOR_BRUTO": valor_bruto,
                    "TIPO": tipo_atual
                })

            bloco = []


        for linha in pdf:
            linha = re.sub(r'\s+', ' ', str(linha)).strip()

            if not linha:
                continue

            if re_ignorar.search(linha):
                continue

            achou_data = re_data.search(linha)

            if achou_data:
                fechar_bloco()

                data_atual = achou_data.group().upper()
                linha = linha[achou_data.end():].strip()

                if not linha:
                    continue

            total = re_total.search(linha)

            if total:
                fechar_bloco()

                categoria = total.group(1).lower()

                if "entrada" in categoria:
                    tipo_atual = "C"
                else:
                    tipo_atual = "D"

                continue

            if re_inicio.search(linha):
                fechar_bloco()
                bloco = [linha]
                continue

            if bloco:
                bloco.append(linha)

        # MUITO IMPORTANTE
        fechar_bloco()

        df = pd.json_normalize(resultado)
        df["VALOR_BRUTO"] = (
            df["VALOR_BRUTO"]
            .str.strip()
            .str.replace("-", "", regex=False)
            .str.replace(" ", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .astype(float)
        )
        df["TIPO"] = df.apply(lambda x: "C" if x["VALOR_BRUTO"] > 0 else "D", axis=1)
        df["VALOR_BRUTO"] = df["VALOR_BRUTO"].astype(str).str.strip().str.replace("-", "", regex=False).astype(float)
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
        return df