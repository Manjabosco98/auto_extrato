import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from src.api.endpoints import fecfin as fecfin_endpoint
from src.app.supabase import supabase_api
from src.schemas.fecfin import registry
from src.schemas.fecfin.base import FecfinHandler
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


def _criar_excel_geral(dados: list[list], colunas: list[str] | None = None) -> io.BytesIO:
    """Cria um Excel in-memory com layout Geral (header na linha 4)."""
    if colunas is None:
        colunas = [
            "Data Competência", "Cliente / Fornecedor / Funcionario",
            "Conta", "Descrição", "Banco", "Forma de Pagamento",
            "Parc", "Valor", "Data Pagamento | Recebimento",
            "NF", "Pedido", "CONC", "Entrada | Saida",
        ]
    num_colunas = len(colunas)
    padding = [[""] * num_colunas for _ in range(4)]
    todas_as_linhas = padding + [colunas] + dados
    df = pd.DataFrame(todas_as_linhas)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, header=False, sheet_name="Sheet1")
    buf.seek(0)
    return buf


class FecfinGeralTest(unittest.TestCase):
    def test_matches_detecta_geral(self):
        buf = _criar_excel_geral([
            ["2026-05-06", "CLIENTE A", "Cliente Final", "DESC", "Caixa",
             "Pix", "01", 1000.0, "2026-05-06", 1234.0, 5678, True, "Entrada"],
        ])
        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.geral import Geral
            handler = Geral()
            self.assertTrue(handler.matches(xls))

    def test_parse_geral_um_banco(self):
        buf = _criar_excel_geral([
            ["2026-05-06", "CLIENTE A", "Cliente Final", "PIX", "Caixa",
             "Pix", "01", 1000.0, "2026-05-06", 1234.0, 5678, True, "Entrada"],
            ["2026-05-07", "CLIENTE B", "Revenda", "TARIFA", "Caixa",
             "TED", "02", -250.0, "2026-05-07", 1235.0, 5679, True, "Saida"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0526_FECFIN_TESTE")

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "Caixa")
        self.assertEqual(len(df), 2)
        self.assertIn("DESCRIÇÃO", df.columns)
        self.assertIn("VALOR", df.columns)
        self.assertIn("TIPO", df.columns)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[0]["VALOR"], 1000.0)
        self.assertEqual(df.iloc[1]["VALOR"], 250.0)

    def test_parse_geral_dois_bancos(self):
        buf = _criar_excel_geral([
            ["2026-05-06", "CLIENTE A", "Conta", "PIX", "Banco A",
             "Pix", "01", 1000.0, "2026-05-06", 1234.0, 5678, True, "Entrada"],
            ["2026-05-07", "CLIENTE B", "Conta", "TED", "Banco B",
             "TED", "02", 500.0, "2026-05-07", 1235.0, 5679, True, "Saida"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0526_FECFIN_TESTE")

        self.assertEqual(len(resultados), 2)
        bancos = {banco for banco, _ in resultados}
        self.assertEqual(bancos, {"Banco A", "Banco B"})

    def test_parse_geral_descricao_uppercase(self):
        buf = _criar_excel_geral([
            ["2026-05-06", "cliente a", "conta", "pix", "Banco",
             "Pix", "01", 100.0, "2026-05-06", 1234.0, 5678, True, "Entrada"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertTrue(df.iloc[0]["DESCRIÇÃO"].isupper())

    def test_parse_geral_remove_nan_da_descricao(self):
        buf = _criar_excel_geral([
            ["2026-05-06", "CLIENTE", "Conta", None, "Banco",
             "Pix", "", 100.0, "2026-05-06", None, None, True, "Entrada"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertNotIn("nan", df.iloc[0]["DESCRIÇÃO"].lower())

    def test_parse_geral_nf_na_descricao(self):
        buf = _criar_excel_geral([
            ["2026-05-06", "CLIENTE", "Conta", "VENDA", "Banco",
             "Pix", "01", 100.0, "2026-05-06", 1234.0, 5678, True, "Entrada"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertIn("NF 1234", df.iloc[0]["DESCRIÇÃO"])

    def test_parse_geral_data_formatada(self):
        buf = _criar_excel_geral([
            ["2026-05-06", "CLIENTE", "Conta", "DESC", "Banco",
             "Pix", "01", 100.0, "2026-05-15", 1234.0, 5678, True, "Entrada"],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["DATA"], "15/05/2026")


def _criar_excel_up380(dados: list[list], colunas: list[str] | None = None) -> io.BytesIO:
    """Cria um Excel in-memory com layout UP380/Kamino."""
    if colunas is None:
        colunas = ["Unnamed: 0", "Classificação", "Descrição", "Valor (R$)", "Unnamed: 4", "Unnamed: 5"]
    df = pd.DataFrame(dados, columns=colunas)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
    buf.seek(0)
    return buf


class FecfinUp380Test(unittest.TestCase):
    def test_matches_detecta_up380(self):
        buf = _criar_excel_up380([
            ["01/05/2026", "Saldo inicial", "Saldo inicial", "5.849,23", None, None],
            [None, "Tarifas Bancárias", "Tarifa Pix", "-050", None, None],
        ])
        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.up380 import Up380
            handler = Up380()
            self.assertTrue(handler.matches(xls))

    def test_parse_up380_um_banco(self):
        buf = _criar_excel_up380([
            ["01/05/2026", "Saldo inicial", "Saldo inicial", "5.849,23", None, None],
            [None, "Tarifas Bancárias", "Tarifa Pix", "-050", None, None],
            [None, "Pró-Labore", "Referente ao pró-labore", "-5.000,00", None, None],
            [None, "Receita", "Recebimento cliente", "29.040", None, None],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0526_FECFIN_KAMINO_UP380")

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "KAMINO")
        self.assertEqual(len(df), 3)
        self.assertIn("DESCRIÇÃO", df.columns)
        self.assertIn("VALOR", df.columns)
        self.assertIn("TIPO", df.columns)
        self.assertEqual(df.iloc[0]["TIPO"], "D")
        self.assertEqual(df.iloc[0]["VALOR"], 50.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 5000.0)
        self.assertEqual(df.iloc[2]["TIPO"], "C")
        self.assertEqual(df.iloc[2]["VALOR"], 29040.0)

    def test_parse_up380_filtro_saldo(self):
        buf = _criar_excel_up380([
            ["01/05/2026", "Saldo inicial", "Saldo inicial", "5.849,23", None, None],
            [None, "Tarifas Bancárias", "Tarifa Pix", "-050", None, None],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertEqual(len(df), 1)
        self.assertNotIn("Saldo", df.iloc[0]["DESCRIÇÃO"])

    def test_parse_up380_filtro_linhas(self):
        buf = _criar_excel_up380([
            ["01/05/2026", "Tarifas Bancárias", "Tarifa Pix", "-050", None, None],
            ["106 linhas", "106 linhas", "106 linhas", "106 linhas", None, None],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertEqual(len(df), 1)

    def test_parse_up380_valor_br(self):
        buf = _criar_excel_up380([
            ["01/05/2026", "Tarifas Bancárias", "Tarifa Pix", "-050", None, None],
            [None, "Receita", "Recebimento", "1.234,56", None, None],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["VALOR"], 50.0)
        self.assertEqual(df.iloc[1]["VALOR"], 1234.56)

    def test_parse_up380_banco_extraido_do_stem(self):
        buf = _criar_excel_up380([
            ["01/05/2026", "Receita", "Recebimento", "100,00", None, None],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0526_FECFIN_BANCOX_TESTE")

        banco, _ = resultados[0]
        self.assertEqual(banco, "BANCOX")

    def test_parse_up380_descricao_uppercase(self):
        buf = _criar_excel_up380([
            ["01/05/2026", "Tarifas Bancárias", "tarifa pix enviada", "-050", None, None],
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "test")

        _, df = resultados[0]
        self.assertTrue(df.iloc[0]["DESCRIÇÃO"].isupper())


class ParseNomeFecfinTest(unittest.TestCase):
    def test_parse_extrai_competencia_e_cliente(self):
        from src.services.fecfin import parse_nome_fecfin

        mes, ano, cliente = parse_nome_fecfin("0626_FECFIN_DAFF.xlsx")
        self.assertEqual((mes, ano, cliente), ("06", "26", "DAFF"))

    def test_parse_usa_ultimo_segmento_como_cliente(self):
        from src.services.fecfin import parse_nome_fecfin

        _, _, cliente = parse_nome_fecfin("0526_FECFIN_ALGO_COLEGIO WR.xlsx")
        self.assertEqual(cliente, "COLEGIO WR")

    def test_parse_rejeita_periodo_invalido(self):
        from src.services.fecfin import parse_nome_fecfin

        with self.assertRaises(ValueError):
            parse_nome_fecfin("FECFIN_TESTE.xlsx")


class ExecutarFecfinBaixaTest(unittest.TestCase):
    def test_baixa_fecfin_sem_instancia(self):
        from src.services import fecfin as fecfin_service
        from openpyxl import Workbook

        df = pd.DataFrame(
            [["01/06/2026", "PIX", 100.0, "C"]],
            columns=["DATA", "DESCRIÇÃO", "VALOR", "TIPO"],
        )

        drive = MagicMock()
        drive.get_or_create_folder.return_value = {"id": "ext"}
        drive.list_children.return_value = [
            {"id": "f1", "name": "0626_FECFIN_DAFF.xlsx", "mimeType": fecfin_service.XLSX_MIME_TYPE}
        ]

        def _download(file_id, destino_local):
            Workbook().save(destino_local)
            return str(destino_local)

        drive.download.side_effect = _download

        with patch.object(fecfin_service, "GoogleDriveAuth", return_value=drive), \
             patch.object(fecfin_service, "resolver_pasta_emp_base", return_value="emp"), \
             patch.object(fecfin_service, "carregar_empresas_ativas", return_value={"DAFF": ("114", "DAFF LTDA")}), \
             patch.object(fecfin_service, "buscar_empresa_por_cliente", return_value=("114", "DAFF LTDA")), \
             patch.object(fecfin_service, "_carregar_destino_fecfin", return_value="SRVARQ\\EMP\\{EMPRESA}\\MOV\\CONT\\{ANO}\\{MES}\\EXT"), \
             patch.object(fecfin_service, "nome_pasta_empresa", return_value="114_DAFF LTDA"), \
             patch.object(fecfin_service, "montar_destino_docs", return_value=("EMP", ["114_DAFF LTDA", "MOV", "CONT", "26", "06", "EXT"])), \
             patch.object(fecfin_service, "resolver_pasta_destino_docs", return_value=("dest", "EMP/114_DAFF LTDA/MOV/CONT/26/06/EXT")), \
             patch.object(fecfin_service, "dispatch_fecwin_detalhado", return_value=registry.ResultadoDispatch(resultados=[("ITAU", df)])), \
             patch.object(fecfin_service.shutil, "copy2"), \
             patch.object(fecfin_service, "planilha_lancamento"), \
             patch.object(fecfin_service, "consultar_situacao_controle", return_value=supabase_api.SituacaoControle(cadastrado=True, id_empresa="uuid-114")), \
             patch.object(fecfin_service, "baixar_controle_supabase", return_value=(True, None)) as mock_baixa, \
             patch.object(fecfin_service, "enviar_notificacao_fecfin_google_chat"):
            resultado = fecfin_service.executar_fecfin()

        self.assertEqual(resultado["atualizados_sge"], 1)
        self.assertEqual(resultado["movidos"], 1)
        drive.move_file.assert_called_once()
        kwargs = mock_baixa.call_args.kwargs
        self.assertEqual(kwargs["codigo_documento"], "FECFIN")
        self.assertEqual(kwargs["competencia"], "06-2026")
        self.assertEqual(kwargs["empresa_codigo"], "114")
        self.assertEqual(kwargs["banco"], "")
        self.assertEqual(kwargs["conta"], "")

    def test_fecfin_ja_baixado_preserva_baixa_e_move(self):
        # FECFIN ja baixado: nao sobrescreve o SGE, mas gera os [LANC] e move o
        # arquivo para a pasta da empresa.
        from src.services import fecfin as fecfin_service
        from openpyxl import Workbook

        df = pd.DataFrame(
            [["01/06/2026", "PIX", 100.0, "C"]],
            columns=["DATA", "DESCRIÇÃO", "VALOR", "TIPO"],
        )

        drive = MagicMock()
        drive.get_or_create_folder.return_value = {"id": "ext"}
        drive.list_children.return_value = [
            {"id": "f1", "name": "0626_FECFIN_DAFF.xlsx", "mimeType": fecfin_service.XLSX_MIME_TYPE}
        ]

        def _download(file_id, destino_local):
            Workbook().save(destino_local)
            return str(destino_local)

        drive.download.side_effect = _download

        situacao = supabase_api.SituacaoControle(
            cadastrado=True,
            id_empresa="uuid-114",
            status_envio="Enviado",
            nome_arquivo="0626_FECFIN_DAFF.xlsx",
            data_recebimento="2026-07-02",
        )

        with patch.object(fecfin_service, "GoogleDriveAuth", return_value=drive), \
             patch.object(fecfin_service, "resolver_pasta_emp_base", return_value="emp"), \
             patch.object(fecfin_service, "carregar_empresas_ativas", return_value={"DAFF": ("114", "DAFF LTDA")}), \
             patch.object(fecfin_service, "buscar_empresa_por_cliente", return_value=("114", "DAFF LTDA")), \
             patch.object(fecfin_service, "_carregar_destino_fecfin", return_value="SRVARQ\\EMP\\{EMPRESA}\\MOV\\CONT\\{ANO}\\{MES}\\EXT"), \
             patch.object(fecfin_service, "nome_pasta_empresa", return_value="114_DAFF LTDA"), \
             patch.object(fecfin_service, "montar_destino_docs", return_value=("EMP", ["114_DAFF LTDA", "MOV", "CONT", "26", "06", "EXT"])), \
             patch.object(fecfin_service, "resolver_pasta_destino_docs", return_value=("dest", "EMP/114_DAFF LTDA/MOV/CONT/26/06/EXT")), \
             patch.object(fecfin_service, "dispatch_fecwin_detalhado", return_value=registry.ResultadoDispatch(resultados=[("ITAU", df)])), \
             patch.object(fecfin_service.shutil, "copy2"), \
             patch.object(fecfin_service, "planilha_lancamento"), \
             patch.object(fecfin_service, "consultar_situacao_controle", return_value=situacao), \
             patch.object(fecfin_service, "baixar_controle_supabase") as mock_baixa, \
             patch.object(fecfin_service, "enviar_notificacao_fecfin_google_chat") as notificacao:
            resultado = fecfin_service.executar_fecfin()

        mock_baixa.assert_not_called()
        self.assertEqual(resultado["atualizados_sge"], 0)
        self.assertEqual(resultado["baixas_preservadas"], 1)
        self.assertEqual(resultado["movidos"], 1)
        self.assertEqual(resultado["lancamentos"], 1)
        drive.move_file.assert_called_once()

        preservadas = notificacao.call_args.kwargs["baixas_preservadas"]
        self.assertEqual(len(preservadas), 1)
        self.assertIn("0626_FECFIN_DAFF.xlsx", preservadas[0])
        self.assertIn("2026-07-02", preservadas[0])


def _criar_excel_ce_part(
    abas: dict[str, tuple[list[str], list[list]]],
    file_stem: str = "0426_FECFIN_CE PART_TESTE",
) -> io.BytesIO:
    """Cria um Excel in-memory com layout CE PART (CORA/CAIXA)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for nome_aba, (colunas, dados) in abas.items():
            df = pd.DataFrame(dados, columns=colunas)
            df.to_excel(writer, index=False, sheet_name=nome_aba)
    buf.seek(0)
    return buf, file_stem


class FecfinCePartTest(unittest.TestCase):
    """Testes para o layout FECFIN CE PART (CORA + CAIXA)."""

    def _montar_aba_cora(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "OBS", "OBS INTERNA", "TIPO", "DOC", "HISTÓRICO", "ENTRADA", "SAÍDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(1)]
        return colunas, padding + [colunas] + dados_reais

    def _montar_aba_caixa(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "HISTÓRICO", "Nº DOC", "TIPO", "OBS", "OBS INT", "ENTRADA", "SAÍDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(4)]
        return colunas, padding + [colunas] + dados_reais

    def test_matches_detecta_ce_part(self):
        col_cora, dados_cora = self._montar_aba_cora(
            [["01/04/2026", "CREDITO", "INTERNO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        buf, stem = _criar_excel_ce_part({"CORA": (col_cora, dados_cora)})

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertTrue(handler.matches(xls, file_stem=stem))

    def test_matches_rejeita_sem_ce_part_no_nome(self):
        col_cora, dados_cora = self._montar_aba_cora(
            [["01/04/2026", "CREDITO", "INTERNO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        buf, _ = _criar_excel_ce_part(
            {"CORA": (col_cora, dados_cora)},
            file_stem="0426_FECFIN_OUTRO",
        )

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertFalse(handler.matches(xls, file_stem="0426_FECFIN_OUTRO"))

    def test_parse_ce_part_cora(self):
        col_cora, dados_cora = self._montar_aba_cora(
            [["01/04/2026", "CREDITO", "INTERNO", "DOC", "001", "PAGAMENTO", 1500, 0],
             ["02/04/2026", "DEBITO", "", "TED", "002", "TARIFA", 0, 25]]
        )
        buf, stem = _criar_excel_ce_part({"CORA": (col_cora, dados_cora)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "CORA")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 1500.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 25.0)

    def test_parse_ce_part_caixa(self):
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/04/2026", "RECEBIMENTO", "001", "DOC", "CREDITO", "", 2000, 0],
             ["02/04/2026", "TARIFA BANCARIA", "002", "DOC", "DEBITO", "", 0, 50]]
        )
        buf, stem = _criar_excel_ce_part({"CAIXA": (col_caixa, dados_caixa)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "CAIXA")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 2000.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 50.0)

    def test_parse_ce_part_dois_bancos(self):
        col_cora, dados_cora = self._montar_aba_cora(
            [["01/04/2026", "CREDITO", "", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/04/2026", "RECEBIMENTO", "002", "DOC", "CREDITO", "", 2000, 0]]
        )
        buf, stem = _criar_excel_ce_part({
            "CORA": (col_cora, dados_cora),
            "CAIXA": (col_caixa, dados_caixa),
        })

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 2)
        bancos = {banco for banco, _ in resultados}
        self.assertEqual(bancos, {"CORA", "CAIXA"})

    def test_parse_ce_part_descricao_uppercase(self):
        col_cora, dados_cora = self._montar_aba_cora(
            [["01/04/2026", "credito", "interno", "doc", "001", "pagamento", 100, 0]]
        )
        buf, stem = _criar_excel_ce_part({"CORA": (col_cora, dados_cora)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertTrue(df.iloc[0]["DESCRIÇÃO"].isupper())

    def test_parse_ce_part_remove_nan_da_descricao(self):
        col_cora, dados_cora = self._montar_aba_cora(
            [["01/04/2026", None, None, "DOC", "001", "PAGAMENTO", 100, 0]]
        )
        buf, stem = _criar_excel_ce_part({"CORA": (col_cora, dados_cora)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertNotIn("nan", df.iloc[0]["DESCRIÇÃO"].lower())

    def test_parse_ce_part_filtro_saldo(self):
        col_cora, dados_cora = self._montar_aba_cora(
            [["01/04/2026", "CREDITO", "", "DOC", "001", "PAGAMENTO", 1000, 0],
             ["01/04/2026", "", "", "", "", "SALDO ANTERIOR", 0, 0]]
        )
        buf, stem = _criar_excel_ce_part({"CORA": (col_cora, dados_cora)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(len(df), 1)

    def test_parse_ce_part_data_formatada(self):
        col_cora, dados_cora = self._montar_aba_cora(
            [["2026-04-15", "CREDITO", "", "DOC", "001", "PAGAMENTO", 100, 0]]
        )
        buf, stem = _criar_excel_ce_part({"CORA": (col_cora, dados_cora)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["DATA"], "15/04/2026")


def _criar_excel_cemaf_part(
    abas: dict[str, tuple[list[str], list[list]]],
    file_stem: str = "0426_FECFIN_CEMAF PART_TESTE",
) -> io.BytesIO:
    """Cria um Excel in-memory com layout CEMAF PART (UNICRED/CAIXA)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for nome_aba, (colunas, dados) in abas.items():
            df = pd.DataFrame(dados, columns=colunas)
            df.to_excel(writer, index=False, sheet_name=nome_aba)
    buf.seek(0)
    return buf, file_stem


class FecfinCemafPartTest(unittest.TestCase):
    """Testes para o layout FECFIN CEMAF PART (UNICRED + CAIXA)."""

    def _montar_aba_unicred(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "OBS", "TIPO", "Nº DOC", "HISTÓRICO", "ENTRADA", "SAIDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(5)]
        return colunas, padding + [colunas] + dados_reais

    def _montar_aba_caixa(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "OBS", "TIPO", "Nº DOC", "HISTÓRICO", "ENTRADA", "SAIDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(4)]
        return colunas, padding + [colunas] + dados_reais

    def test_matches_detecta_cemaf_part(self):
        col_unicred, dados_unicred = self._montar_aba_unicred(
            [["01/04/2026", "CREDITO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        buf, stem = _criar_excel_cemaf_part({"UNICRED": (col_unicred, dados_unicred)})

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertTrue(handler.matches(xls, file_stem=stem))

    def test_matches_rejeita_sem_cemaf_part_no_nome(self):
        col_unicred, dados_unicred = self._montar_aba_unicred(
            [["01/04/2026", "CREDITO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        buf, _ = _criar_excel_cemaf_part(
            {"UNICRED": (col_unicred, dados_unicred)},
            file_stem="0426_FECFIN_OUTRO",
        )

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertFalse(handler.matches(xls, file_stem="0426_FECFIN_OUTRO"))

    def test_parse_cemaf_part_unicred(self):
        col_unicred, dados_unicred = self._montar_aba_unicred(
            [["01/04/2026", "CREDITO", "DOC", "001", "PAGAMENTO", 1500, 0],
             ["02/04/2026", "DEBITO", "TED", "002", "TARIFA", 0, 30]]
        )
        buf, stem = _criar_excel_cemaf_part({"UNICRED": (col_unicred, dados_unicred)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "UNICRED")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 1500.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 30.0)

    def test_parse_cemaf_part_caixa(self):
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/04/2026", "RECEBIMENTO", "DOC", "001", "CREDITO", 2000, 0],
             ["02/04/2026", "TARIFA", "DOC", "002", "DEBITO", 0, 40]]
        )
        buf, stem = _criar_excel_cemaf_part({"CAIXA": (col_caixa, dados_caixa)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "CAIXA")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 2000.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 40.0)

    def test_parse_cemaf_part_dois_bancos(self):
        col_unicred, dados_unicred = self._montar_aba_unicred(
            [["01/04/2026", "CREDITO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/04/2026", "RECEBIMENTO", "DOC", "002", "CREDITO", 2000, 0]]
        )
        buf, stem = _criar_excel_cemaf_part({
            "UNICRED": (col_unicred, dados_unicred),
            "CAIXA": (col_caixa, dados_caixa),
        })

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 2)
        bancos = {banco for banco, _ in resultados}
        self.assertEqual(bancos, {"UNICRED", "CAIXA"})

    def test_parse_cemaf_part_descricao_uppercase(self):
        col_unicred, dados_unicred = self._montar_aba_unicred(
            [["01/04/2026", "credito", "doc", "001", "pagamento", 100, 0]]
        )
        buf, stem = _criar_excel_cemaf_part({"UNICRED": (col_unicred, dados_unicred)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertTrue(df.iloc[0]["DESCRIÇÃO"].isupper())

    def test_parse_cemaf_part_remove_nan_da_descricao(self):
        col_unicred, dados_unicred = self._montar_aba_unicred(
            [["01/04/2026", None, "DOC", "001", None, 100, 0]]
        )
        buf, stem = _criar_excel_cemaf_part({"UNICRED": (col_unicred, dados_unicred)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertNotIn("nan", df.iloc[0]["DESCRIÇÃO"].lower())

    def test_parse_cemaf_part_filtro_saldo(self):
        col_unicred, dados_unicred = self._montar_aba_unicred(
            [["01/04/2026", "CREDITO", "DOC", "001", "PAGAMENTO", 1000, 0],
             ["01/04/2026", "", "", "", "SALDO ANTERIOR", 0, 0]]
        )
        buf, stem = _criar_excel_cemaf_part({"UNICRED": (col_unicred, dados_unicred)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(len(df), 1)

    def test_parse_cemaf_part_data_formatada(self):
        col_unicred, dados_unicred = self._montar_aba_unicred(
            [["2026-04-15", "CREDITO", "DOC", "001", "PAGAMENTO", 100, 0]]
        )
        buf, stem = _criar_excel_cemaf_part({"UNICRED": (col_unicred, dados_unicred)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["DATA"], "15/04/2026")


def _criar_excel_aloha(
    abas: dict[str, tuple[list[str], list[list]]],
    file_stem: str = "0626_FECFIN_ALOHA",
) -> io.BytesIO:
    """Cria um Excel in-memory com layout ALOHA (INTER/CAIXA)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for nome_aba, (colunas, dados) in abas.items():
            df = pd.DataFrame(dados, columns=colunas)
            df.to_excel(writer, index=False, sheet_name=nome_aba)
    buf.seek(0)
    return buf, file_stem


class FecfinAlohaTest(unittest.TestCase):
    """Testes para o layout FECFIN ALOHA (INTER + CAIXA)."""

    def _montar_aba_inter(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "OBS", "OBS INTERNA", "TIPO", "DOC", "HISTÓRICO", "ENTRADA", "SAÍDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(1)]
        return colunas, padding + [colunas] + dados_reais

    def _montar_aba_caixa(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "HISTÓRICO", "Nº DOC", "TIPO", "OBS", "OBS INT", "ENTRADA", "SAÍDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(4)]
        return colunas, padding + [colunas] + dados_reais

    def test_matches_detecta_aloha(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "CREDITO", "INTERNO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        buf, stem = _criar_excel_aloha({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertTrue(handler.matches(xls, file_stem=stem))

    def test_matches_rejeita_sem_aloha_no_nome(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "CREDITO", "INTERNO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        buf, _ = _criar_excel_aloha(
            {"INTER": (col_inter, dados_inter)},
            file_stem="0626_FECFIN_OUTRO",
        )

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertFalse(handler.matches(xls, file_stem="0626_FECFIN_OUTRO"))

    def test_parse_aloha_inter(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "CREDITO", "INTERNO", "DOC", "001", "PAGAMENTO", 1500, 0],
             ["02/06/2026", "DEBITO", "", "TED", "002", "TARIFA", 0, 25]]
        )
        buf, stem = _criar_excel_aloha({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "INTER")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 1500.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 25.0)

    def test_parse_aloha_caixa(self):
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "RECEBIMENTO", "001", "DOC", "CREDITO", "", 2000, 0],
             ["02/06/2026", "TARIFA BANCARIA", "002", "DOC", "DEBITO", "", 0, 50]]
        )
        buf, stem = _criar_excel_aloha({"CAIXA": (col_caixa, dados_caixa)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "CAIXA")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 2000.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 50.0)

    def test_parse_aloha_dois_bancos(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "CREDITO", "", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "RECEBIMENTO", "002", "DOC", "CREDITO", "", 2000, 0]]
        )
        buf, stem = _criar_excel_aloha({
            "INTER": (col_inter, dados_inter),
            "CAIXA": (col_caixa, dados_caixa),
        })

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 2)
        bancos = {banco for banco, _ in resultados}
        self.assertEqual(bancos, {"INTER", "CAIXA"})

    def test_parse_aloha_descricao_uppercase(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "credito", "interno", "doc", "001", "pagamento", 100, 0]]
        )
        buf, stem = _criar_excel_aloha({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertTrue(df.iloc[0]["DESCRIÇÃO"].isupper())

    def test_parse_aloha_remove_nan_da_descricao(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", None, None, "DOC", "001", "PAGAMENTO", 100, 0]]
        )
        buf, stem = _criar_excel_aloha({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertNotIn("nan", df.iloc[0]["DESCRIÇÃO"].lower())

    def test_parse_aloha_filtro_saldo(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "CREDITO", "", "DOC", "001", "PAGAMENTO", 1000, 0],
             ["01/06/2026", "", "", "", "", "SALDO ANTERIOR", 0, 0]]
        )
        buf, stem = _criar_excel_aloha({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(len(df), 1)

    def test_parse_aloha_data_formatada(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["2026-06-15", "CREDITO", "", "DOC", "001", "PAGAMENTO", 100, 0]]
        )
        buf, stem = _criar_excel_aloha({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["DATA"], "15/06/2026")


def _criar_excel_adp_part(
    abas: dict[str, tuple[list[str], list[list]]],
    file_stem: str = "0626_FECFIN_ADP PART",
) -> io.BytesIO:
    """Cria um Excel in-memory com layout ADP PART (INTER/ITAU/CAIXA)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for nome_aba, (colunas, dados) in abas.items():
            df = pd.DataFrame(dados, columns=colunas)
            df.to_excel(writer, index=False, sheet_name=nome_aba)
    buf.seek(0)
    return buf, file_stem


class FecfinAdpPartTest(unittest.TestCase):
    """Testes para o layout FECFIN ADP PART (INTER + ITAU + CAIXA)."""

    def _montar_aba_inter(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "OBS CONTABILIDADE", "TIPO", "DOC", "HISTÓRICO", "ENTRADA", "SAÍDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(1)]
        return colunas, padding + [colunas] + dados_reais

    def _montar_aba_itau(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "OBS CONTABILIDADE", "OBS INTERNA", "TIPO", "DOC", "HISTÓRICO", "ENTRADA", "SAÍDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(1)]
        return colunas, padding + [colunas] + dados_reais

    def _montar_aba_caixa(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "HISTÓRICO", "Nº DOC", "TIPO", "OBS", "ENTRADA", "SAÍDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(4)]
        return colunas, padding + [colunas] + dados_reais

    def test_matches_detecta_adp_part(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "CREDITO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        buf, stem = _criar_excel_adp_part({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertTrue(handler.matches(xls, file_stem=stem))

    def test_matches_rejeita_sem_adp_part_no_nome(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "CREDITO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        buf, _ = _criar_excel_adp_part(
            {"INTER": (col_inter, dados_inter)},
            file_stem="0626_FECFIN_OUTRO",
        )

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertFalse(handler.matches(xls, file_stem="0626_FECFIN_OUTRO"))

    def test_parse_adp_part_inter(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "CREDITO", "DOC", "001", "PAGAMENTO", 1500, 0],
             ["02/06/2026", "DEBITO", "TED", "002", "TARIFA", 0, 25]]
        )
        buf, stem = _criar_excel_adp_part({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "INTER")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 1500.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 25.0)

    def test_parse_adp_part_itau(self):
        col_itau, dados_itau = self._montar_aba_itau(
            [["01/06/2026", "CREDITO", "INTERNO", "DOC", "001", "RECEBIMENTO", 2000, 0],
             ["02/06/2026", "DEBITO", "", "TED", "002", "TARIFA", 0, 50]]
        )
        buf, stem = _criar_excel_adp_part({"ITAU": (col_itau, dados_itau)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "ITAU")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 2000.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 50.0)

    def test_parse_adp_part_caixa(self):
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "RECEBIMENTO", "001", "DOC", "CREDITO", 3000, 0],
             ["02/06/2026", "TARIFA", "002", "DOC", "DEBITO", 0, 30]]
        )
        buf, stem = _criar_excel_adp_part({"CAIXA": (col_caixa, dados_caixa)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "CAIXA")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 3000.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 30.0)

    def test_parse_adp_part_tres_bancos(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "CREDITO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        col_itau, dados_itau = self._montar_aba_itau(
            [["01/06/2026", "CREDITO", "", "DOC", "002", "RECEBIMENTO", 2000, 0]]
        )
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "RECEBIMENTO", "003", "DOC", "CREDITO", 3000, 0]]
        )
        buf, stem = _criar_excel_adp_part({
            "INTER": (col_inter, dados_inter),
            "ITAU": (col_itau, dados_itau),
            "CAIXA": (col_caixa, dados_caixa),
        })

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 3)
        bancos = {banco for banco, _ in resultados}
        self.assertEqual(bancos, {"INTER", "ITAU", "CAIXA"})

    def test_parse_adp_part_descricao_uppercase(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "credito", "doc", "001", "pagamento", 100, 0]]
        )
        buf, stem = _criar_excel_adp_part({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertTrue(df.iloc[0]["DESCRIÇÃO"].isupper())

    def test_parse_adp_part_remove_nan_da_descricao(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", None, "DOC", "001", None, 100, 0]]
        )
        buf, stem = _criar_excel_adp_part({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertNotIn("nan", df.iloc[0]["DESCRIÇÃO"].lower())

    def test_parse_adp_part_filtro_saldo(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "CREDITO", "DOC", "001", "PAGAMENTO", 1000, 0],
             ["01/06/2026", "", "", "", "SALDO ANTERIOR", 0, 0]]
        )
        buf, stem = _criar_excel_adp_part({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(len(df), 1)

    def test_parse_adp_part_data_formatada(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["2026-06-15", "CREDITO", "DOC", "001", "PAGAMENTO", 100, 0]]
        )
        buf, stem = _criar_excel_adp_part({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["DATA"], "15/06/2026")


def _criar_excel_acr(
    abas: dict[str, tuple[list[str], list[list]]],
    file_stem: str = "0626_FECFIN_ACR",
) -> io.BytesIO:
    """Cria um Excel in-memory com layout ACR (SICOOB/CAIXA)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for nome_aba, (colunas, dados) in abas.items():
            df = pd.DataFrame(dados, columns=colunas)
            df.to_excel(writer, index=False, sheet_name=nome_aba)
    buf.seek(0)
    return buf, file_stem


class FecfinAcrTest(unittest.TestCase):
    """Testes para o layout FECFIN ACR (SICOOB + CAIXA)."""

    def _montar_aba_sicoob(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "OBS / CONT", "OBS / INT", "TIPO", "DOC", "HISTÓRICO", "ENTRADA", "SAÍDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(1)]
        return colunas, padding + [colunas] + dados_reais

    def _montar_aba_caixa(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "HISTÓRICO", "Nº DOC", "TIPO", "OBS", "ENTRADA", "SAÍDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(4)]
        return colunas, padding + [colunas] + dados_reais

    def test_matches_detecta_acr(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "CREDITO", "INTERNO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        buf, stem = _criar_excel_acr({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertTrue(handler.matches(xls, file_stem=stem))

    def test_matches_rejeita_sem_acr_no_nome(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "CREDITO", "INTERNO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        buf, _ = _criar_excel_acr(
            {"SICOOB": (col_sicoob, dados_sicoob)},
            file_stem="0626_FECFIN_OUTRO",
        )

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertFalse(handler.matches(xls, file_stem="0626_FECFIN_OUTRO"))

    def test_parse_acr_sicoob(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "CREDITO", "INTERNO", "DOC", "001", "PAGAMENTO", 1500, 0],
             ["02/06/2026", "DEBITO", "", "TED", "002", "TARIFA", 0, 25]]
        )
        buf, stem = _criar_excel_acr({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "SICOOB")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 1500.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 25.0)

    def test_parse_acr_caixa(self):
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "RECEBIMENTO", "001", "DOC", "CREDITO", 2000, 0],
             ["02/06/2026", "TARIFA BANCARIA", "002", "DOC", "DEBITO", 0, 50]]
        )
        buf, stem = _criar_excel_acr({"CAIXA": (col_caixa, dados_caixa)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "CAIXA")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 2000.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 50.0)

    def test_parse_acr_caixa_remove_linhas_vazias(self):
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "RECEBIMENTO", "001", "DOC", "CREDITO", 2000, 0],
             [None, None, None, None, None, None, None]]
        )
        buf, stem = _criar_excel_acr({"CAIXA": (col_caixa, dados_caixa)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(len(df), 1)

    def test_parse_acr_dois_bancos(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "CREDITO", "", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "RECEBIMENTO", "002", "DOC", "CREDITO", 2000, 0]]
        )
        buf, stem = _criar_excel_acr({
            "SICOOB": (col_sicoob, dados_sicoob),
            "CAIXA": (col_caixa, dados_caixa),
        })

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 2)
        bancos = {banco for banco, _ in resultados}
        self.assertEqual(bancos, {"SICOOB", "CAIXA"})

    def test_parse_acr_descricao_uppercase(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "credito", "interno", "doc", "001", "pagamento", 100, 0]]
        )
        buf, stem = _criar_excel_acr({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertTrue(df.iloc[0]["DESCRIÇÃO"].isupper())

    def test_parse_acr_remove_nan_da_descricao(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", None, None, "DOC", "001", "PAGAMENTO", 100, 0]]
        )
        buf, stem = _criar_excel_acr({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertNotIn("nan", df.iloc[0]["DESCRIÇÃO"].lower())

    def test_parse_acr_filtro_saldo(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "CREDITO", "", "DOC", "001", "PAGAMENTO", 1000, 0],
             ["01/06/2026", "", "", "", "", "SALDO ANTERIOR", 0, 0]]
        )
        buf, stem = _criar_excel_acr({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(len(df), 1)

    def test_parse_acr_data_formatada(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["2026-06-15", "CREDITO", "", "DOC", "001", "PAGAMENTO", 100, 0]]
        )
        buf, stem = _criar_excel_acr({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["DATA"], "15/06/2026")


def _criar_excel_cemaf_60(
    abas: dict[str, tuple[list[str], list[list]]],
    file_stem: str = "0626_FECFIN_CEMAF 60",
) -> io.BytesIO:
    """Cria um Excel in-memory com layout CEMAF 60 (SICOOB/CAIXA)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for nome_aba, (colunas, dados) in abas.items():
            df = pd.DataFrame(dados, columns=colunas)
            df.to_excel(writer, index=False, sheet_name=nome_aba)
    buf.seek(0)
    return buf, file_stem


class FecfinCemaf60Test(unittest.TestCase):
    """Testes para o layout FECFIN CEMAF 60 (SICOOB + CAIXA)."""

    def _montar_aba_sicoob(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "OBS", "OBS / INT", "TIPO", "DOC", "HISTÓRICO", "ENTRADA", "SAÍDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(1)]
        return colunas, padding + [colunas] + dados_reais

    def _montar_aba_caixa(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "HISTÓRICO", "Nº DOC", "TIPO", "OBS", "ENTRADA", "SAÍDA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(4)]
        return colunas, padding + [colunas] + dados_reais

    def test_matches_detecta_cemaf_60(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "CREDITO", "INTERNO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        buf, stem = _criar_excel_cemaf_60({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertTrue(handler.matches(xls, file_stem=stem))

    def test_matches_detecta_cemaf_60_de_julho_com_espaco_nao_separavel(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/07/2026", "CREDITO", "", "DOC", "001", "PAGAMENTO", 100, 0]]
        )
        buf, _ = _criar_excel_cemaf_60({"SICOOB\u00a0- 15619-1": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            self.assertTrue(
                CePart().matches(xls, file_stem="0726_FECFIN_CEMAF\u00a060")
            )

    def test_matches_rejeita_sem_cemaf_60_no_nome(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "CREDITO", "INTERNO", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        buf, _ = _criar_excel_cemaf_60(
            {"SICOOB": (col_sicoob, dados_sicoob)},
            file_stem="0626_FECFIN_OUTRO",
        )

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertFalse(handler.matches(xls, file_stem="0626_FECFIN_OUTRO"))

    def test_parse_cemaf_60_sicoob(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "CREDITO", "INTERNO", "DOC", "001", "PAGAMENTO", 1500, 0],
             ["02/06/2026", "DEBITO", "", "TED", "002", "TARIFA", 0, 25]]
        )
        buf, stem = _criar_excel_cemaf_60({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "SICOOB")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 1500.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 25.0)

    def test_parse_cemaf_60_sicoob_sem_coluna_obs_int(self):
        """A planilha de 07/2026 veio sem a coluna "OBS / INT".

        Antes o KeyError derrubava a unica aba com lancamentos e o arquivo
        era notificado como "sem layout reconhecido".
        """
        colunas = ["DATA", "TIPO", "DOC", "HISTÓRICO", "ENTRADA", "SAÍDA", "SALDO", "OBS"]
        padding = [[""] * len(colunas)]
        dados = padding + [colunas] + [
            ["10/07/2026", "", "", "AD CONSTRUTORA", 500, 0, 503.6, "EMP ENTRE TERCEIROS"],
            ["10/07/2026", "NFS", "29", "WESLEY BENTO", 0, 500, 3.6, "PAGAMENTO A FORNECEDOR"],
        ]
        buf, stem = _criar_excel_cemaf_60({"SICOOB - 15619-1": (colunas, dados)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "SICOOB")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 500.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 500.0)
        self.assertIn("AD CONSTRUTORA", df.iloc[0]["DESCRIÇÃO"])
        self.assertIn("EMP ENTRE TERCEIROS", df.iloc[0]["DESCRIÇÃO"])

    def test_parse_cemaf_60_caixa(self):
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "RECEBIMENTO", "001", "DOC", "CREDITO", 2000, 0],
             ["02/06/2026", "TARIFA BANCARIA", "002", "DOC", "DEBITO", 0, 50]]
        )
        buf, stem = _criar_excel_cemaf_60({"CAIXA": (col_caixa, dados_caixa)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "CAIXA")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 2000.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 50.0)

    def test_parse_cemaf_60_caixa_remove_linhas_vazias(self):
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "RECEBIMENTO", "001", "DOC", "CREDITO", 2000, 0],
             [None, None, None, None, None, None, None]]
        )
        buf, stem = _criar_excel_cemaf_60({"CAIXA": (col_caixa, dados_caixa)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(len(df), 1)

    def test_parse_cemaf_60_dois_bancos(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "CREDITO", "", "DOC", "001", "PAGAMENTO", 1000, 0]]
        )
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "RECEBIMENTO", "002", "DOC", "CREDITO", 2000, 0]]
        )
        buf, stem = _criar_excel_cemaf_60({
            "SICOOB": (col_sicoob, dados_sicoob),
            "CAIXA": (col_caixa, dados_caixa),
        })

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 2)
        bancos = {banco for banco, _ in resultados}
        self.assertEqual(bancos, {"SICOOB", "CAIXA"})

    def test_parse_cemaf_60_descricao_uppercase(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "credito", "interno", "doc", "001", "pagamento", 100, 0]]
        )
        buf, stem = _criar_excel_cemaf_60({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertTrue(df.iloc[0]["DESCRIÇÃO"].isupper())

    def test_parse_cemaf_60_remove_nan_da_descricao(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", None, None, "DOC", "001", "PAGAMENTO", 100, 0]]
        )
        buf, stem = _criar_excel_cemaf_60({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertNotIn("nan", df.iloc[0]["DESCRIÇÃO"].lower())

    def test_parse_cemaf_60_filtro_saldo(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "CREDITO", "", "DOC", "001", "PAGAMENTO", 1000, 0],
             ["01/06/2026", "", "", "", "", "SALDO ANTERIOR", 0, 0]]
        )
        buf, stem = _criar_excel_cemaf_60({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(len(df), 1)

    def test_parse_cemaf_60_data_formatada(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["2026-06-15", "CREDITO", "", "DOC", "001", "PAGAMENTO", 100, 0]]
        )
        buf, stem = _criar_excel_cemaf_60({"SICOOB": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["DATA"], "15/06/2026")


def _criar_excel_ad_52(
    abas: dict[str, tuple[list[str], list[list]]],
    file_stem: str = "0626_FECFIN_AD 52",
) -> io.BytesIO:
    """Cria um Excel in-memory com layout AD 52 (SICOOB/INTER/CAIXA)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for nome_aba, (colunas, dados) in abas.items():
            df = pd.DataFrame(dados, columns=colunas)
            df.to_excel(writer, index=False, sheet_name=nome_aba)
    buf.seek(0)
    return buf, file_stem


class FecfinAd52Test(unittest.TestCase):
    """Testes para o layout FECFIN AD 52 (SICOOB + INTER + CAIXA)."""

    def _montar_aba_sicoob(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "TIPO", "DOC", "HISTÓRICO", "ENTRADA", "SAÍDA", "SALDO", "OBS", "OBS INTERNA"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(2)]
        return colunas, padding + [colunas] + dados_reais

    def _montar_aba_inter(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "TIPO", "DOC", "HISTÓRICO", "ENTRADA", "SAÍDA", "SALDO", "OBS"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(4)]
        return colunas, padding + [colunas] + dados_reais

    def _montar_aba_caixa(self, dados_reais: list[list]) -> tuple[list[str], list[list]]:
        colunas = ["DATA", "Nº DOC", "TIPO", "HISTÓRICO", "ENTRADA", "SAÍDA", "SALDO", "OBS"]
        num_colunas = len(colunas)
        padding = [[""] * num_colunas for _ in range(3)]
        return colunas, padding + [colunas] + dados_reais

    def test_matches_detecta_ad_52(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "SAQUE", "001", "PAGAMENTO", 1000, 0, 1000, "CREDITO", "INTERNO"]]
        )
        buf, stem = _criar_excel_ad_52({"SICOOB - 11.280-1": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertTrue(handler.matches(xls, file_stem=stem))

    def test_matches_detecta_ad_52_de_julho_com_separador_variavel(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/07/2026", "SAQUE", "001", "PAGAMENTO", 1000, 0, 1000, "CREDITO", "INTERNO"]]
        )
        buf, _ = _criar_excel_ad_52(
            {"SICOOB\u00a0- 11.280-1": (col_sicoob, dados_sicoob)}
        )

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            self.assertTrue(CePart().matches(xls, file_stem="0726_FECFIN_AD_52"))

    def test_matches_rejeita_sem_ad_52_no_nome(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "SAQUE", "001", "PAGAMENTO", 1000, 0, 1000, "CREDITO", "INTERNO"]]
        )
        buf, _ = _criar_excel_ad_52(
            {"SICOOB - 11.280-1": (col_sicoob, dados_sicoob)},
            file_stem="0626_FECFIN_OUTRO",
        )

        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.ce_part import CePart
            handler = CePart()
            self.assertFalse(handler.matches(xls, file_stem="0626_FECFIN_OUTRO"))

    def test_parse_ad_52_sicoob(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "FAT", "001", "PAGAMENTO", 1500, 0, 1500, "CREDITO", ""],
             ["02/06/2026", "SAQUE", "002", "TARIFA", 0, 25, 1475, "DEBITO", "INTERNO"]]
        )
        buf, stem = _criar_excel_ad_52({"SICOOB - 11.280-1": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "SICOOB")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 1500.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 25.0)

    def test_parse_ad_52_sicoob_sem_coluna_obs_interna(self):
        """A planilha de 07/2026 veio sem a coluna "OBS INTERNA".

        Antes o KeyError derrubava a unica aba com lancamentos e o arquivo
        era notificado como "sem layout reconhecido".
        """
        colunas = ["DATA", "TIPO", "DOC", "HISTÓRICO", "ENTRADA", "SAÍDA", "SALDO", "OBS"]
        padding = [[""] * len(colunas) for _ in range(2)]
        dados = padding + [colunas] + [
            ["01/07/2026", "", "129", "SICOOB", 0, 99.9, 3814.55, "TARIFAS BANCÁRIAS"],
            ["06/07/2026", "", "8104127", "CEMAF", 2900, 0, 6714.55, "EMP ENTRE TERCEIROS"],
        ]
        buf, stem = _criar_excel_ad_52({"SICOOB - 11.280-1": (colunas, dados)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "SICOOB")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "D")
        self.assertEqual(df.iloc[0]["VALOR"], 99.9)
        self.assertEqual(df.iloc[1]["TIPO"], "C")
        self.assertEqual(df.iloc[1]["VALOR"], 2900.0)
        self.assertIn("TARIFAS BANCÁRIAS", df.iloc[0]["DESCRIÇÃO"])
        self.assertIn("SICOOB", df.iloc[0]["DESCRIÇÃO"])

    def test_parse_ad_52_inter(self):
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "PIX", "001", "RECEBIMENTO", 2000, 0, 2000, "CREDITO"],
             ["02/06/2026", "TED", "002", "TARIFA", 0, 50, 1950, "DEBITO"]]
        )
        buf, stem = _criar_excel_ad_52({"INTER": (col_inter, dados_inter)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "INTER")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 2000.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 50.0)

    def test_parse_ad_52_caixa(self):
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "001", "MEDIÇÃO", "RECEBIMENTO", 3000, 0, 3000, "APORT SOC"],
             ["02/06/2026", "002", "BOL", "TARIFA", 0, 30, 2970, "BOLETO"]]
        )
        buf, stem = _criar_excel_ad_52({"CAIXA": (col_caixa, dados_caixa)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "CAIXA")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[0]["VALOR"], 3000.0)
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[1]["VALOR"], 30.0)

    def test_parse_ad_52_caixa_remove_linhas_vazias(self):
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "001", "MEDIÇÃO", "RECEBIMENTO", 3000, 0, 3000, "APORT SOC"],
             [None, None, None, None, None, None, None, None]]
        )
        buf, stem = _criar_excel_ad_52({"CAIXA": (col_caixa, dados_caixa)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(len(df), 1)

    def test_parse_ad_52_tres_bancos(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "FAT", "001", "PAGAMENTO", 1000, 0, 1000, "CREDITO", ""]]
        )
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "PIX", "002", "RECEBIMENTO", 2000, 0, 2000, "CREDITO"]]
        )
        col_caixa, dados_caixa = self._montar_aba_caixa(
            [["01/06/2026", "003", "MEDIÇÃO", "RECEBIMENTO", 3000, 0, 3000, "APORT SOC"]]
        )
        buf, stem = _criar_excel_ad_52({
            "SICOOB - 11.280-1": (col_sicoob, dados_sicoob),
            "INTER": (col_inter, dados_inter),
            "CAIXA": (col_caixa, dados_caixa),
        })

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        self.assertEqual(len(resultados), 3)
        bancos = {banco for banco, _ in resultados}
        self.assertEqual(bancos, {"SICOOB", "INTER", "CAIXA"})

    def test_parse_ad_52_inter_so_com_saldo_e_omitido(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "FAT", "001", "PAGAMENTO", 1000, 0, 1000, "CREDITO", ""]]
        )
        col_inter, dados_inter = self._montar_aba_inter(
            [["01/06/2026", "", "", "SALDO ANTERIOR", 0, 0, 0, ""],
             ["30/06/2026", "", "", "SALDO FINAL", 0, 0, 0, ""]]
        )
        buf, stem = _criar_excel_ad_52({
            "SICOOB - 11.280-1": (col_sicoob, dados_sicoob),
            "INTER": (col_inter, dados_inter),
        })

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        bancos = {banco for banco, _ in resultados}
        self.assertEqual(bancos, {"SICOOB"})

    def test_parse_ad_52_descricao_uppercase(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "fat", "001", "pagamento", 100, 0, 100, "credito", "interno"]]
        )
        buf, stem = _criar_excel_ad_52({"SICOOB - 11.280-1": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertTrue(df.iloc[0]["DESCRIÇÃO"].isupper())

    def test_parse_ad_52_remove_nan_da_descricao(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", None, "001", "PAGAMENTO", 100, 0, 100, None, None]]
        )
        buf, stem = _criar_excel_ad_52({"SICOOB - 11.280-1": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertNotIn("nan", df.iloc[0]["DESCRIÇÃO"].lower())

    def test_parse_ad_52_filtro_saldo(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["01/06/2026", "FAT", "001", "PAGAMENTO", 1000, 0, 1000, "CREDITO", ""],
             ["01/06/2026", "", "", "SALDO ANTERIOR", 0, 0, 0, "", ""]]
        )
        buf, stem = _criar_excel_ad_52({"SICOOB - 11.280-1": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(len(df), 1)

    def test_parse_ad_52_data_formatada(self):
        col_sicoob, dados_sicoob = self._montar_aba_sicoob(
            [["2026-06-15", "FAT", "001", "PAGAMENTO", 100, 0, 100, "CREDITO", ""]]
        )
        buf, stem = _criar_excel_ad_52({"SICOOB - 11.280-1": (col_sicoob, dados_sicoob)})

        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, stem)

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["DATA"], "15/06/2026")


_COLUNAS_KMC = [
    "OBRA", "DESCRIÇÃO", "NF", "CATEGORIA",
    "CODIGO PLANO DE CONTAS", "BANCO", "ENTRADA", "SAÍDA", "DATA",
]


def _criar_excel_kmc(
    dados: list[list],
    aba: str = "07-2026",
    abas_extras: dict[str, list[list]] | None = None,
) -> io.BytesIO:
    """Cria um Excel in-memory com layout KMC.

    Cada aba tem a linha 0 vazia, a linha 1 com os rótulos, a linha 2 com
    o ``Saldo anterior ->`` e os dados a partir da linha 3.  As colunas 0-5
    reproduzem o bloco lateral "PLANO DE CONTAS" da planilha real.
    """
    def _montar(linhas: list[list]) -> pd.DataFrame:
        lateral = [None] * 6
        vazia = [None] * (len(lateral) + len(_COLUNAS_KMC))
        cabecalho = lateral + _COLUNAS_KMC
        saldo = lateral + ["Saldo anterior ->"] + [None] * (len(_COLUNAS_KMC) - 1)
        return pd.DataFrame([vazia, cabecalho, saldo] + [lateral + linha for linha in linhas])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        _montar(dados).to_excel(writer, index=False, header=False, sheet_name=aba)
        for nome_aba, linhas in (abas_extras or {}).items():
            _montar(linhas).to_excel(writer, index=False, header=False, sheet_name=nome_aba)
    buf.seek(0)
    return buf


class FecfinKmcTest(unittest.TestCase):
    def _linha(self, obra="-", descricao="DESC", nf=None, categoria="Cat",
               banco="Sicoob - KMC", entrada=None, saida=None, data="2026-07-01"):
        return [obra, descricao, nf, categoria, "1.1", banco, entrada, saida, data]

    def test_matches_detecta_kmc(self):
        buf = _criar_excel_kmc([self._linha(saida=100.0)])
        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.kmc import Kmc
            self.assertTrue(Kmc().matches(xls, file_stem="0726_FECFIN_KMC"))

    def test_matches_ignora_outro_layout(self):
        buf = _criar_excel_conta_azul([
            ["2026-07-01", "PIX", "FORNECEDOR", 100.0, "Banco A"],
        ])
        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.kmc import Kmc
            self.assertFalse(Kmc().matches(xls, file_stem="0726_FECFIN_KMC"))

    def test_matches_ignora_abas_nao_competencia(self):
        buf = _criar_excel_kmc([self._linha(saida=100.0)], aba="DRE-07-2026")
        with pd.ExcelFile(buf) as xls:
            from src.schemas.fecfin.kmc import Kmc
            self.assertFalse(Kmc().matches(xls, file_stem="0726_FECFIN_KMC"))

    def test_parse_kmc_um_banco(self):
        buf = _criar_excel_kmc([
            self._linha(descricao="PIX RECEBIDO", entrada=1000.0),
            self._linha(descricao="TARIFA", saida=54.99, data="2026-07-02"),
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0726_FECFIN_KMC")

        self.assertEqual(len(resultados), 1)
        banco, df = resultados[0]
        self.assertEqual(banco, "SICOOB")
        self.assertEqual(list(df.columns), ["DATA", "DESCRIÇÃO", "VALOR", "TIPO"])
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["TIPO"], "C")
        self.assertEqual(df.iloc[1]["TIPO"], "D")
        self.assertEqual(df.iloc[0]["VALOR"], 1000.0)
        self.assertEqual(df.iloc[1]["VALOR"], 54.99)

    def test_parse_kmc_usa_aba_da_competencia(self):
        buf = _criar_excel_kmc(
            [self._linha(descricao="JULHO", saida=10.0)],
            abas_extras={"06-2026": [self._linha(descricao="JUNHO", saida=20.0, data="2026-06-01")]},
        )
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0626_FECFIN_KMC")

        _, df = resultados[0]
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["DESCRIÇÃO"], "JUNHO")
        self.assertEqual(df.iloc[0]["DATA"], "01/06/2026")

    def test_parse_kmc_sem_competencia_no_nome_usa_ultima_aba(self):
        buf = _criar_excel_kmc(
            [self._linha(descricao="JUNHO", saida=20.0, data="2026-06-01")],
            aba="06-2026",
            abas_extras={"07-2026": [self._linha(descricao="JULHO", saida=10.0)]},
        )
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "FECFIN_KMC")

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["DESCRIÇÃO"], "JULHO")

    def test_parse_kmc_ignora_linhas_sem_banco(self):
        buf = _criar_excel_kmc([
            self._linha(descricao="PAGAMENTO BANCO", saida=100.0),
            self._linha(descricao="CARTÃO DE CRÉDITO", banco=None, saida=200.0),
            self._linha(descricao="LUCRO LÍQUIDO", banco=None),
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0726_FECFIN_KMC")

        _, df = resultados[0]
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["DESCRIÇÃO"], "PAGAMENTO BANCO")

    def test_parse_kmc_preenche_datas_mescladas(self):
        buf = _criar_excel_kmc([
            self._linha(descricao="PRIMEIRA", saida=10.0, data="2026-07-03"),
            self._linha(descricao="SEGUNDA", saida=20.0, data=None),
            self._linha(descricao="TERCEIRA", saida=30.0, data=None),
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0726_FECFIN_KMC")

        _, df = resultados[0]
        self.assertEqual(list(df["DATA"]), ["03/07/2026"] * 3)

    def test_parse_kmc_data_formatada(self):
        buf = _criar_excel_kmc([self._linha(saida=10.0, data="2026-07-15")])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0726_FECFIN_KMC")

        _, df = resultados[0]
        self.assertEqual(df.iloc[0]["DATA"], "15/07/2026")

    def test_parse_kmc_descricao_preserva_numeros(self):
        buf = _criar_excel_kmc([
            self._linha(
                obra="Obra - 170 - Acompanhamento Fazenda",
                descricao="Locação de equipamentos",
                nf=1043.0,
                saida=470.12,
            ),
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0726_FECFIN_KMC")

        _, df = resultados[0]
        descricao = df.iloc[0]["DESCRIÇÃO"]
        self.assertEqual(
            descricao,
            "OBRA - 170 - ACOMPANHAMENTO FAZENDA NF 1043 LOCAÇÃO DE EQUIPAMENTOS",
        )

    def test_parse_kmc_descricao_sem_nan_obra_vazia_e_pipe(self):
        buf = _criar_excel_kmc([
            self._linha(obra="-", descricao="Reembolso | Obra 150", nf=None, saida=10.0),
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0726_FECFIN_KMC")

        _, df = resultados[0]
        descricao = df.iloc[0]["DESCRIÇÃO"]
        self.assertEqual(descricao, "REEMBOLSO OBRA 150")
        self.assertNotIn("nan", descricao.lower())
        self.assertNotIn("|", descricao)

    def test_parse_kmc_dois_bancos(self):
        buf = _criar_excel_kmc([
            self._linha(descricao="SICOOB", saida=10.0),
            self._linha(descricao="CAIXA", banco="Caixa - KMC", entrada=20.0),
        ])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0726_FECFIN_KMC")

        self.assertEqual(len(resultados), 2)
        self.assertEqual({banco for banco, _ in resultados}, {"SICOOB", "CAIXA"})

    def test_parse_kmc_valor_absoluto(self):
        buf = _criar_excel_kmc([self._linha(saida=1275.41)])
        with pd.ExcelFile(buf) as xls:
            resultados = dispatch_fecwin(xls, "0726_FECFIN_KMC")

        _, df = resultados[0]
        self.assertTrue((df["VALOR"] > 0).all())


class FecfinDispatchDetalhadoTest(unittest.TestCase):
    """O dispatch nao pode parar no primeiro layout que casa e nao extrai nada."""

    class _HandlerVazio(FecfinHandler):
        bank = "Vazio"

        def matches(self, xls, file_stem=""):
            return True

        def parse(self, xls, file_stem):
            return []

    class _HandlerComDados(FecfinHandler):
        bank = "ComDados"

        def matches(self, xls, file_stem=""):
            return True

        def parse(self, xls, file_stem):
            return [("SICOOB", pd.DataFrame({"VALOR": [1.0]}))]

    def _excel_qualquer(self) -> io.BytesIO:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pd.DataFrame({"A": [1]}).to_excel(writer, index=False, sheet_name="Sheet1")
        buf.seek(0)
        return buf

    def _dispatch_com(self, handlers):
        buf = self._excel_qualquer()
        with patch.object(registry, "_FECFIN_HANDLERS", handlers):
            with pd.ExcelFile(buf) as xls:
                return registry.dispatch_fecwin_detalhado(xls, "0726_FECFIN_TESTE")

    def test_handler_vazio_nao_impede_o_proximo(self):
        resultado = self._dispatch_com([self._HandlerVazio(), self._HandlerComDados()])

        self.assertTrue(resultado.reconhecido)
        self.assertEqual(len(resultado.resultados), 1)
        self.assertEqual(resultado.resultados[0][0], "SICOOB")
        self.assertEqual(resultado.layouts_tentados, ["_HandlerVazio", "_HandlerComDados"])
        self.assertEqual(resultado.motivo(), "")

    def test_motivo_quando_layout_casa_mas_nao_extrai(self):
        resultado = self._dispatch_com([self._HandlerVazio()])

        self.assertFalse(resultado.reconhecido)
        self.assertIn("_HandlerVazio", resultado.motivo())
        self.assertIn("nenhuma aba gerou lancamentos", resultado.motivo())

    def test_motivo_quando_nenhum_layout_casa(self):
        resultado = self._dispatch_com([])

        self.assertFalse(resultado.reconhecido)
        self.assertEqual(resultado.motivo(), "nenhum layout reconheceu o arquivo")

    def test_dispatch_fecwin_mantem_a_assinatura_de_lista(self):
        buf = self._excel_qualquer()
        with patch.object(registry, "_FECFIN_HANDLERS", [self._HandlerComDados()]):
            with pd.ExcelFile(buf) as xls:
                resultados = dispatch_fecwin(xls, "0726_FECFIN_TESTE")

        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0][0], "SICOOB")


if __name__ == "__main__":
    unittest.main()
