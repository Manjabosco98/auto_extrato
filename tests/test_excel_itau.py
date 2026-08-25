import io
import unittest

import pandas as pd

from src.schemas.excel.itau import ItauExcel
from src.schemas.excel.registry import ExcelLayoutNotRecognized, dispatch_excel


_CABECALHO_ITAU = [
    "Data",
    "Lançamento",
    "Razão Social",
    "CPF/CNPJ",
    "Valor (R$)",
    "Saldo (R$)",
]

# Bloco de identificação da conta que o Itaú imprime antes do cabeçalho.
_TOPO_ITAU = [
    [None, None, None, None, None, None],
    ["Atualização:", "07/08/2026 19:03:10", None, None, None, None],
    ["Nome:", "ACAO TECNOLOGIA", None, None, None, None],
    ["Agência:", "2903", None, None, None, None],
    ["Conta:", "0099019-6", None, None, None, None],
    [None, None, None, None, None, None],
    ["Lançamentos", None, None, None, None, None],
    ["Periodo:", "01/03/2026 até 31/07/2026", None, None, None, None],
    [None, None, None, None, None, None],
]

_LANCAMENTOS_PADRAO = [
    ["28/02/2026", "SALDO ANTERIOR", None, None, None, 6673.6],
    ["05/03/2026", "TAR/CUSTAS COBRANCA", None, None, -3.86, None],
    ["05/03/2026", "BOLETOS RECEBIDOS  05/03S", None, None, 998, None],
    ["05/03/2026", "SALDO TOTAL DISPONÍVEL DIA", None, None, None, 7667.74],
    [
        "10/03/2026",
        "BOLETO PAGO LM TRANSP IN",
        "LM TRANSP INTER SERV COM S A",
        "00.389.481/0001-79",
        -359.21,
        None,
    ],
]


def _criar_excel_itau(lancamentos=None, topo=None, cabecalho=None) -> io.BytesIO:
    linhas = list(topo if topo is not None else _TOPO_ITAU)
    linhas.append(list(cabecalho if cabecalho is not None else _CABECALHO_ITAU))
    linhas.extend(
        _LANCAMENTOS_PADRAO if lancamentos is None else lancamentos
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(linhas).to_excel(
            writer, sheet_name="Lançamentos", index=False, header=False
        )
    buffer.seek(0)
    return buffer


def _criar_excel_outro_layout() -> io.BytesIO:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(
            {
                "DATA": ["01/03/2026"],
                "HISTÓRICO": ["PIX RECEBIDO"],
                "ENTRADA": [100.0],
                "SAÍDA": [None],
            }
        ).to_excel(writer, sheet_name="Extrato", index=False)
    buffer.seek(0)
    return buffer


def _converter(buffer) -> pd.DataFrame:
    with pd.ExcelFile(buffer) as xls:
        return dispatch_excel(xls, "0326_EXTBAN_ITAU_ACAO_AG 2903_CC 99019-6")


class ExcelItauMatchTest(unittest.TestCase):
    def test_matches_detecta_layout_itau(self):
        with pd.ExcelFile(_criar_excel_itau()) as xls:
            self.assertTrue(ItauExcel().matches(xls))

    def test_matches_ignora_outro_layout(self):
        with pd.ExcelFile(_criar_excel_outro_layout()) as xls:
            self.assertFalse(ItauExcel().matches(xls))

    def test_dispatch_levanta_para_layout_desconhecido(self):
        with pd.ExcelFile(_criar_excel_outro_layout()) as xls:
            with self.assertRaises(ExcelLayoutNotRecognized):
                dispatch_excel(xls, "0326_EXTBAN_OUTRO_X_AG 1_CC 1")

    def test_matches_com_cabecalho_sem_acento(self):
        cabecalho = ["DATA", "LANCAMENTO", "RAZAO SOCIAL", "CPF/CNPJ", "VALOR (R$)", "SALDO (R$)"]
        with pd.ExcelFile(_criar_excel_itau(cabecalho=cabecalho)) as xls:
            self.assertTrue(ItauExcel().matches(xls))

    def test_matches_com_bloco_de_topo_maior(self):
        topo = _TOPO_ITAU + [[None] * 6, ["Observacao:", "extrato consolidado", None, None, None, None]]
        with pd.ExcelFile(_criar_excel_itau(topo=topo)) as xls:
            self.assertTrue(ItauExcel().matches(xls))


class ExcelItauParseTest(unittest.TestCase):
    def test_colunas_do_contrato(self):
        df = _converter(_criar_excel_itau())
        self.assertEqual(list(df.columns), ["DATA", "DESCRIÇÃO", "VALOR", "TIPO"])

    def test_remove_linhas_de_saldo(self):
        df = _converter(_criar_excel_itau())

        self.assertEqual(len(df), 3)
        self.assertFalse(df["DESCRIÇÃO"].str.contains("SALDO").any())

    def test_tipo_e_valor_absoluto(self):
        df = _converter(_criar_excel_itau())

        self.assertEqual(
            df.set_index("DESCRIÇÃO")["TIPO"]["TAR/CUSTAS COBRANCA"], "D"
        )
        self.assertEqual(
            df.set_index("DESCRIÇÃO")["VALOR"]["TAR/CUSTAS COBRANCA"], 3.86
        )
        self.assertEqual(
            df.set_index("DESCRIÇÃO")["TIPO"]["BOLETOS RECEBIDOS 05/03S"], "C"
        )
        self.assertTrue((df["VALOR"] > 0).all())

    def test_descricao_junta_razao_social_e_documento(self):
        df = _converter(_criar_excel_itau())

        self.assertIn(
            "BOLETO PAGO LM TRANSP IN LM TRANSP INTER SERV COM S A 00.389.481/0001-79",
            df["DESCRIÇÃO"].tolist(),
        )

    def test_descricao_preserva_nomes_que_contem_nan(self):
        """Regressao: replace("nan", "") no prototipo transformava FERNANDO em FERDO."""
        df = _converter(
            _criar_excel_itau(
                lancamentos=[
                    [
                        "12/03/2026",
                        "PIX ENVIADO",
                        "Fernando Nantes",
                        "013.044.341-76",
                        -50.0,
                        None,
                    ]
                ]
            )
        )

        self.assertEqual(
            df["DESCRIÇÃO"].iloc[0],
            "PIX ENVIADO FERNANDO NANTES 013.044.341-76",
        )

    def test_descricao_sem_residuo_de_colunas_vazias(self):
        df = _converter(_criar_excel_itau())

        self.assertEqual(df["DESCRIÇÃO"].iloc[0], "TAR/CUSTAS COBRANCA")

    def test_data_formatada_como_texto(self):
        df = _converter(_criar_excel_itau())

        self.assertTrue(df["DATA"].str.match(r"^\d{2}/\d{2}/\d{4}$").all())
        self.assertEqual(df["DATA"].iloc[0], "05/03/2026")

    def test_data_vinda_como_datetime(self):
        df = _converter(
            _criar_excel_itau(
                lancamentos=[
                    [pd.Timestamp("2026-03-05"), "IOF", None, None, -16.08, None]
                ]
            )
        )

        self.assertEqual(df["DATA"].iloc[0], "05/03/2026")

    def test_extrato_so_com_saldos_retorna_vazio(self):
        df = _converter(
            _criar_excel_itau(
                lancamentos=[
                    ["28/02/2026", "SALDO ANTERIOR", None, None, None, 6673.6],
                    ["28/02/2026", "SALDO TOTAL DISPONÍVEL DIA", None, None, None, 6673.6],
                ]
            )
        )

        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), ["DATA", "DESCRIÇÃO", "VALOR", "TIPO"])


if __name__ == "__main__":
    unittest.main()
