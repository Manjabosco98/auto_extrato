from abc import ABC

import pandas as pd


class ExcelBankHandler(ABC):
    """Handler base para extratos bancários em Excel.

    Os handlers de ``src/schemas/banks`` recebem as linhas de texto de um
    PDF; estes recebem a planilha aberta. Cada subclasse deve implementar
    ``matches`` e ``parse``.

    ``parse`` retorna um único DataFrame com as colunas
    ``DATA``, ``DESCRIÇÃO``, ``VALOR`` e ``TIPO`` — o contrato exigido por
    ``validar_dataframe_extrato`` no fluxo de conversão.
    """

    bank: str

    def matches(self, xls: pd.ExcelFile, file_stem: str = "") -> bool:
        raise NotImplementedError

    def parse(self, xls: pd.ExcelFile, file_stem: str = "") -> pd.DataFrame:
        raise NotImplementedError
