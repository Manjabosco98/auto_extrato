from abc import ABC

import pandas as pd


class FecfinHandler(ABC):
    """Handler base para layouts FECFIN (Excel).

    Cada subclasse deve implementar ``matches`` e ``parse``.
    O método ``matches`` verifica se um arquivo Excel corresponde
    ao layout. O método ``parse`` extrai os dados e retorna uma
    lista de tuplas ``(nome_banco, DataFrame)``.
    """

    bank: str

    def matches(self, xls: pd.ExcelFile, file_stem: str = "") -> bool:
        raise NotImplementedError

    def parse(self, xls: pd.ExcelFile, file_stem: str) -> list[tuple[str, pd.DataFrame]]:
        raise NotImplementedError
