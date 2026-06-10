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

        data_re = re.compile(r'\b\d{2}/\d{2}(?:/\d{2,4})?\b')
        valor_re = re.compile(
            r'(?:R\$\s*)?-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}[CD]?|'
            r'R\$\s*-?\d+(?:,\d{2})?'
        )
        doc_re = re.compile(
            r'\b(?:\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|'
            r'\d{3}\.\d{3}\.\d{3}-\d{2}|\d{11})\b'
        )
        ignorar_re = re.compile(
            r'\b(SALDO ANTERIOR|SALDO TOTAL|SALDO DISPON[IÍ]VEL|'
            r'DATA|LANÇAMENTOS|RAZ[ÃA]O SOCIAL|CNPJ/CPF|VALOR|'
            r'AG[ÊE]NCIA|CONTA|CNPJ|CPF|BANCO|P[ÁA]GINA|PER[IÍ]ODO)\b',
            re.I
        )

        limpas = []
        for linha in pdf:
            linha = re.sub(r'\s+', ' ', str(linha)).strip()
            if linha and not ignorar_re.search(linha):
                limpas.append(linha)

        movs = []
        pendentes = []

        for linha in limpas:
            if data_re.search(linha):
                bloco = pendentes
                pendentes = []

                if movs and bloco:
                    anterior = movs[-1]
                    texto_ant = ' '.join(anterior["pre"] + [anterior["linha"]] + anterior["pos"])

                    while bloco and not valor_re.search(texto_ant):
                        anterior["pos"].append(bloco.pop(0))
                        texto_ant = ' '.join(anterior["pre"] + [anterior["linha"]] + anterior["pos"])

                    if bloco and anterior["pre"]:
                        anterior["pos"].append(bloco.pop(0))

                movs.append({"pre": bloco, "linha": linha, "pos": []})
            else:
                pendentes.append(linha)

        if movs and pendentes:
            ultimo = movs[-1]
            texto_ult = ' '.join(ultimo["pre"] + [ultimo["linha"]] + ultimo["pos"])
            if ultimo["pre"] or not valor_re.search(texto_ult):
                ultimo["pos"].extend(pendentes)

        resultado = []

        for mov in movs:
            linha = mov["linha"]
            data = data_re.search(linha).group(0)

            corpo = data_re.sub('', linha, count=1).strip()
            complemento = mov["pre"] + mov["pos"]
            texto_total = ' '.join([corpo] + complemento)

            valores = list(valor_re.finditer(texto_total))
            if not valores:
                continue

            valor = valores[-1].group(0)

            if valor in corpo:
                corpo_sem_valor = corpo[:corpo.rfind(valor)].strip()
            else:
                corpo_sem_valor = corpo.strip()
                complemento = [v for v in complemento if v != valor]

            docs = list(doc_re.finditer(corpo_sem_valor))

            if docs:
                doc = docs[-1].group(0)
                antes_doc = corpo_sem_valor[:docs[-1].start()].strip()
                depois_doc = corpo_sem_valor[docs[-1].end():].strip()
                partes = [antes_doc] + complemento + [depois_doc, doc]
            else:
                partes = [corpo_sem_valor] + complemento

            descricao = re.sub(r'\s+', ' ', ' '.join(p for p in partes if p)).strip()
            resultado.append(f'{data} {descricao} {valor}')

        padrao = r"^(?P<data>\d{2}/\d{2}/\d{4})\s+(?P<descricao>.+?)\s+(?P<valor>-?\d{1,3}(?:\.\d{3})*,\d{2})$"

        dados = []

        for item in resultado:
            match = re.match(padrao, item.strip())

            if match:
                data = match.group("data")
                descricao = match.group("descricao")
                valor_texto = match.group("valor")

                tipo = "D" if valor_texto.startswith("-") else "C"

                valor = (
                    valor_texto
                    .replace(".", "")
                    .replace(",", ".")
                )

                dados.append({
                    "DATA": data,
                    "DESCRIÇÃO": descricao,
                    "VALOR": float(valor),
                    "TIPO": tipo
                })
            else:
                print("Não capturado:", item)
        df = pd.DataFrame(dados)
        df["VALOR"] = df["VALOR"].abs()
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
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