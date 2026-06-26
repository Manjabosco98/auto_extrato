import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from src.api.endpoints import fecfin as fecfin_endpoint
from src.schemas.fecfin.registry import dispatch_fecwin


def _criar_excel_conta_azul(dados: list[list], colunas: list[str] | None = None) -> io.BytesIO:
    """Cria um Excel in-memory com layout Conta Azul."""
    if colunas is None:
        colunas = ["Data movimento", "Descrição", "Nome do fornecedor/cliente", "Valor (R$)", "Conta bancária"]
    df = pd.DataFrame(dados, columns=colunas)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
    buf.seek(0)
    return buf


def _criar_excel_multibanco(abas: dict[str, tuple[list[str], list[list]]]) -> io.BytesIO:
    """Cria um Excel in-memory com múltiplas abas (layout multi-banco).

    Cada valor em ``abas`` é uma tupla ``(colunas, dados)`` onde
    ``dados`` já inclui as linhas de cabeçalho e padding necessárias.
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for nome_aba, (colunas, dados) in abas.items():
            df = pd.DataFrame(dados, columns=colunas)
            df.to_excel(writer, index=False, sheet_name=nome_aba)
    buf.seek(0)
    return buf


class FecfinContaAzulTest(unittest.TestCase):
    def test_matches_detecta_conta_azul(self):
        buf = _criar_excel_conta_azul([
            ["2026-04-01", "PIX RECEBIDO", "EMPRESA X", 1500.00, "Banco Itau"],
            ["2026-04-02", "TARIFA", "EMPRESA X", -25.00, "Banco Itau"],
        ])
        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.conta_azul import ContaAzul
            handler = ContaAzul()
            self.assertTrue(handler.matches(xls))

    def test_parse_conta_azul_um_banco(self):
        buf = _criar_excel_conta_azul([
            ["2026-04-01", "PIX RECEBIDO", "FORNECEDOR A", 1500.00, "Itau"],
            ["2026-04-02", "TARIFA MENSAL", "FORNECEDOR A", -25.50, "Itau"],
            ["2026-04-03", "DEPOSITO", "FORNECEDOR B", 3000.00, "Itau"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0426_FECFIN_TESTE")

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "Itau")
        self.assertEqual(len(df), 3)
        self.assertIn("DESCRIÇÃO", df.columns)
        self.assertIn("VALOR", df.columns)
        self.assertIn("TIPO", df.columns)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[0]["VALOR"], 1500.00)
        self.assertEqual(df.iloc[1]["VALOR"], 25.50)

    def test_parse_conta_azul_dois_bancos(self):
        buf = _criar_excel_conta_azul([
            ["2026-04-01", "PIX", "FORNECEDOR", 1000.00, "Banco A"],
            ["2026-04-02", "TARIFA", "FORNECEDOR", -50.00, "Banco B"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0426_FECFIN_TESTE")

        self.assertEqual(len(resultados), 2)
        bancos = {banco for banco, _ in resultados}
        self.assertEqual(bancos, {"Banco A", "Banco B"})

    def test_parse_conta_azul_descricao_uppercase(self):
        buf = _criar_excel_conta_azul([
            ["2026-04-01", "pix recebido", "fornecedor", 100.00, "Banco"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertTrue(df.iloc[0]["DESCRIÇÃO"].isupper())

    def test_parse_conta_azul_remove_nan_da_descricao(self):
        buf = _criar_excel_conta_azul([
            ["2026-04-01", "DESCRICAO", None, 100.00, "Banco"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertNotIn("nan", df.iloc[0]["DESCRIÇÃO"].lower())


class FecfinMultiBancoTest(unittest.TestCase):
    COLUNAS_UNICRED = ["DATA", "OBS", "TIPO", "Nº DOC", "HISTÓRICO", "OBS P/ INTERNAS", "ENTRADA", "SAIDA", "SALDO"]
    COLUNAS_SICOOB = ["DATA", "OBS P/ CONT", "TIPO", "Nº DOC", "HISTÓRICO", "OBS P/ INTERNAS", "ENTRADA", "SAIDA", "SALDO"]
    COLUNAS_SICREDI = ["DATA", "OBS", "TIPO", "Nº DOC", "HISTÓRICO", "ENTRADA", "SAIDA", "SALDO"]
    COLUNAS_CAIXA = ["DATA", "OBS", "TIPO", "Nº DOC", "HISTÓRICO", "DADOS BANCÁRIOS", "ENTRADA", "SAIDA", "SALDO"]

    def _montar_aba(self, colunas: list[str], dados_reais: list[list], header_row: int) -> tuple[list[str], list[list]]:
        """Monta aba com padding + header + dados (replicando Excel real)."""
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(header_row)]
        header = [colunas]
        return colunas, padding + header + dados_reais

    def test_matches_detecta_multibanco(self):
        colunas, dados = self._montar_aba(
            self.COLUNAS_UNICRED,
            [["2026-04-01", "CREDITO", "DOC", "001", "PAGAMENTO", "", 1000, 0, 5000]],
            header_row=5,
        )
        buf = _criar_excel_multibanco({"UNICRED": (colunas, dados)})

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.multibanco import MultiBanco
            handler = MultiBanco()
            self.assertTrue(handler.matches(xls))

    def test_parse_multibanco_quatro_bancos(self):
        col_unicred, dados_unicred = self._montar_aba(
            self.COLUNAS_UNICRED,
            [["2026-04-01", "CREDITO", "DOC", "001", "PAGAMENTO", "INTERNO", 1000, 0, 5000]],
            header_row=5,
        )
        col_sicoob, dados_sicoob = self._montar_aba(
            self.COLUNAS_SICOOB,
            [["2026-04-01", "DEBITO", "TED", "002", "TRANSFERENCIA", "INTERNO", 0, 500, 4500]],
            header_row=5,
        )
        col_sicredi, dados_sicredi = self._montar_aba(
            self.COLUNAS_SICREDI,
            [["2026-04-01", "CREDITO", "DOC", "003", "RECEBIMENTO", 2000, 0, 7000]],
            header_row=6,
        )
        col_caixa, dados_caixa = self._montar_aba(
            self.COLUNAS_CAIXA,
            [["2026-04-01", "TARIFA", "DOC", "004", "TARIFA MENSAL", "AG 0001", 0, 30, 4970]],
            header_row=4,
        )
        buf = _criar_excel_multibanco({
            "UNICRED": (col_unicred, dados_unicred),
            "SICOOB": (col_sicoob, dados_sicoob),
            "SICREDI": (col_sicredi, dados_sicredi),
            "CAIXA": (col_caixa, dados_caixa),
        })

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0426_FECFIN_CEMAF OPE")

        self.assertEqual(len(resultados), 4)
        bancos = {banco for banco, _ in resultados}
        self.assertEqual(bancos, {"UNICRED", "SICOOB", "SICREDI", "CAIXA"})

    def test_parse_multibanco_ignora_aba_desconhecida(self):
        col_unicred, dados_unicred = self._montar_aba(
            self.COLUNAS_UNICRED,
            [["2026-04-01", "CREDITO", "DOC", "001", "PAGAMENTO", "", 1000, 0, 5000]],
            header_row=5,
        )
        col_resumo, dados_resumo = self._montar_aba(
            self.COLUNAS_UNICRED,
            [["RESUMO GERAL", "", "", "", "", "", 1000, 500, 4500]],
            header_row=5,
        )
        buf = _criar_excel_multibanco({
            "UNICRED": (col_unicred, dados_unicred),
            "RESUMO": (col_resumo, dados_resumo),
        })

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0][0], "UNICRED")

    def test_parse_multibanco_filtro_saldo(self):
        col_unicred, dados_unicred = self._montar_aba(
            self.COLUNAS_UNICRED,
            [
                ["2026-04-01", "CREDITO", "DOC", "001", "PAGAMENTO", "", 1000, 0, 5000],
                ["2026-04-01", "", "", "", "SALDO ANTERIOR", "", 0, 0, 4000],
            ],
            header_row=5,
        )
        buf = _criar_excel_multibanco({"UNICRED": (col_unicred, dados_unicred)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertEqual(len(df), 1)
        self.assertNotIn("SALDO", df.iloc[0]["DESCRIÇÃO"])

    def test_parse_multibanco_valores_absolutos(self):
        col_sicoob, dados_sicoob = self._montar_aba(
            self.COLUNAS_SICOOB,
            [["2026-04-01", "DEBITO", "TED", "001", "TRANSFERENCIA", "", 0, 500, 4500]],
            header_row=5,
        )
        buf = _criar_excel_multibanco({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["VALOR"], 500.0)
        self.assertEqual(df.iloc[0]["TIPO"], "D")


def _criar_excel_midia(dados: list[list], colunas: list[str] | None = None) -> io.BytesIO:
    """Cria um Excel in-memory com layout Mídia.

    Estrutura real do Excel:
      - Linha 0: título/cabeçalho vazio (pandas usa como header padrão)
      - Linha 1: colunas reais (Destinado à, CPF/CNPJ, etc.)
      - Linha 2+: dados

    O parse() faz: df.columns = df.iloc[0] e depois df = df[1:].
    """
    if colunas is None:
        colunas = ["Destinado à", "CPF/CNPJ", "Descrição", "Data", "Situação", "Valor"]
    # Linha 0: título vazio; Linha 1: colunas reais; Linhas 2+: dados
    linha_titulo = [""] * len(colunas)
    todas_as_linhas = [linha_titulo, colunas] + dados
    df = pd.DataFrame(todas_as_linhas)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, header=False, sheet_name="Sheet1")
    buf.seek(0)
    return buf


class FecfinMidiaTest(unittest.TestCase):
    def test_matches_detecta_layout_midia(self):
        buf = _criar_excel_midia([
            ["PJ", "12.345.678/0001-99", "PIX RECEBIDO", "01/04/2026", "Quitado", "+1.500,00"],
        ])
        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.midia import Midia
            handler = Midia()
            self.assertTrue(handler.matches(xls))

    def test_matches_rejeita_layout_diferente(self):
        buf = _criar_excel_conta_azul([
            ["2026-04-01", "PIX", "EMPRESA", 1000.00, "Itau"],
        ])
        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.midia import Midia
            handler = Midia()
            self.assertFalse(handler.matches(xls))

    def test_parse_midia_um_registro(self):
        buf = _criar_excel_midia([
            ["PJ", "12.345.678/0001-99", "PIX RECEBIDO", "01/04/2026", "Quitado", "+1.500,00"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0426_FECFIN_MIDIA")

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "MIDIA")
        self.assertEqual(len(df), 1)
        self.assertIn("DATA", df.columns)
        self.assertIn("DESCRIÇÃO", df.columns)
        self.assertIn("VALOR", df.columns)
        self.assertIn("TIPO", df.columns)

    def test_parse_midia_valores_absolutos(self):
        buf = _criar_excel_midia([
            ["PF", "111.222.333-44", "PAGAMENTO FORNECEDOR", "02/04/2026", "Quitado", "-250,50"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0426_FECFIN_MIDIA")

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["VALOR"], 250.50)
        self.assertEqual(df.iloc[0]["TIPO"], "D")

    def test_parse_midia_credito_e_debito(self):
        buf = _criar_excel_midia([
            ["PJ", "12.345.678/0001-99", "RECEBIMENTO", "01/04/2026", "Quitado", "+1000,00"],
            ["PF", "111.222.333-44", "TARIFA BANCARIA", "02/04/2026", "Quitado", "-25,90"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0426_FECFIN_MIDIA")

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 1000.00)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 25.90)

    def test_parse_midia_descricao_uppercase(self):
        buf = _criar_excel_midia([
            ["PJ", "12.345.678/0001-99", "pix recebido", "01/04/2026", "Quitado", "+100,00"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertTrue(df.iloc[0]["DESCRIÇÃO"].isupper())

    def test_parse_midia_remove_nan_da_descricao(self):
        buf = _criar_excel_midia([
            ["PJ", None, "DESCRICAO", "01/04/2026", "Quitado", "+100,00"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertNotIn("nan", df.iloc[0]["DESCRIÇÃO"].lower())

    def test_parse_midia_banco_extraido_do_nome_arquivo(self):
        buf = _criar_excel_midia([
            ["PJ", "12.345.678/0001-99", "TESTE", "01/04/2026", "Quitado", "+100,00"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0526_FECFIN_EXEMPLO")

        banco, _ = resultados[0]
        self.assertEqual(banco, "EXEMPLO")

    def test_parse_midia_banco_geral_fallback(self):
        buf = _criar_excel_midia([
            ["PJ", "12.345.678/0001-99", "TESTE", "01/04/2026", "Quitado", "+100,00"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "FECFIN")

        banco, _ = resultados[0]
        self.assertEqual(banco, "GERAL")

    def test_parse_midia_ignora_linhas_sem_data(self):
        buf = _criar_excel_midia([
            ["PJ", "12.345.678/0001-99", "PIX", "01/04/2026", "Quitado", "+1000,00"],
            ["", "", "", "texto invalido", "", ""],
            ["PF", "111.222.333-44", "TARIFA", "02/04/2026", "Quitado", "-50,00"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0426_FECFIN_MIDIA")

        _, df = resultados[0]
        self.assertEqual(len(df), 2)

    def test_parse_midia_formatacao_data(self):
        buf = _criar_excel_midia([
            ["PJ", "12.345.678/0001-99", "TESTE", "15/04/2026", "Quitado", "+100,00"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["DATA"], "15/04/2026")

    def test_parse_midia_valor_milhar_ponto(self):
        buf = _criar_excel_midia([
            ["PJ", "12.345.678/0001-99", "TESTE", "01/04/2026", "Quitado", "+1.500,00"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["VALOR"], 1500.00)


class DispatchFecwinTest(unittest.TestCase):
    def test_dispatch_retorna_vaziao_para_excel_nao_fecfin(self):
        buf = _criar_excel_conta_azul(
            [["2026-04-01", "DADO", "NOME", 100, "CONTA"]],
            colunas=["Coluna A", "Coluna B", "Coluna C", "Coluna D", "Coluna E"],
        )
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0426_EXTBAN_TESTE")

        self.assertEqual(resultados, [])

    def test_dispatch_retorna_vaziao_para_excel_vazio(self):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pd.DataFrame().to_excel(writer, index=False, sheet_name="Sheet1")
        buf.seek(0)

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "arquivo_vazio")

        self.assertEqual(resultados, [])


class FecfinEndpointTest(unittest.TestCase):
    def test_rota_fecfin_executar_responde_202(self):
        app = FastAPI()
        app.include_router(fecfin_endpoint.router, prefix="/fecfin")

        with patch.object(fecfin_endpoint, "executar_fecfin"):
            response = TestClient(app).post("/fecfin/executar")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"message": "Fluxo FECFIN iniciado"})

    def test_rota_fecfin_executar_responde_409_quando_em_execucao(self):
        app = FastAPI()
        app.include_router(fecfin_endpoint.router, prefix="/fecfin")
        fecfin_endpoint._fecfin_lock.acquire()

        try:
            response = TestClient(app).post("/fecfin/executar")
        finally:
            fecfin_endpoint._fecfin_lock.release()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"message": "Fluxo FECFIN ja esta em execucao"})


if __name__ == "__main__":
    unittest.main()
