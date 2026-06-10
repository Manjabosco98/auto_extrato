import re
import pandas as pd

from src.schemas.base import BankHandler, layout
from src.schemas.registry import register


@register
class Cora(BankHandler):
    bank = "Cora"
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

    @layout("Cora SCFI ")
    def layout1(self, pdf):
        empresa = pdf[0]
        pdf = pdf[9:]
        pdf = [item for item in pdf if not any(texto in item for texto in [
            empresa,
            "CNPJ ",
            "Agência: ",
            "Cora SCFI - CNPJ 37.880.206/0001-63",
            "Ouvidoria:",
            "Extrato gerado no dia ",
        ])]

        regex_saldo = r"^(?P<data>\d{2}/\d{2}/\d{4})\s+Saldo do dia\s+R\$\s+(?P<saldo>[\d\.]+,\d{2})$"
        regex_mov = r"^(?P<descricao>.+?)\s+(?P<sinal>[+-])\s+R\$\s+(?P<valor>[\d\.]+,\d{2})$"
        movimentacoes = []
        data_atual = None
        for item in pdf:
            item = item.strip()

            match_saldo = re.match(regex_saldo, item)

            if match_saldo:
                data_atual = match_saldo.group("data")
                continue

            match_mov = re.match(regex_mov, item)

            if match_mov and data_atual:
                sinal = match_mov.group("sinal")

                tipo = "C" if sinal == "+" else "D"

                movimentacoes.append({
                    "DATA": data_atual,
                    "DESCRIÇÃO": match_mov.group("descricao").strip(),
                    "VALOR": match_mov.group("valor"),
                    "TIPO": tipo
                })

        df = pd.json_normalize(movimentacoes)
        df["VALOR"] = (
            df["VALOR"]
            .str.strip()
            .str.replace("-", "", regex=False)
            .str.replace("+", "", regex=False)
            .str.replace("R$", "", regex=False)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
            .astype(float)
        )
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()
        return df
        
