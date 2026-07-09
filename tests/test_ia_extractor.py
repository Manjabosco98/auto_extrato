import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.schemas.parsers import ia_extractor


def _resposta_ok(texto):
    resposta = MagicMock()
    resposta.output_text = texto
    resposta.status = "completed"
    return resposta


def _resposta_truncada():
    resposta = MagicMock()
    resposta.output_text = "{"
    resposta.status = "incomplete"
    resposta.incomplete_details = MagicMock(reason="max_output_tokens")
    return resposta


def _fake_client(resposta_texto):
    client = MagicMock()
    upload = MagicMock()
    upload.id = "file-abc123"
    client.files.create.return_value = upload
    client.responses.create.return_value = _resposta_ok(resposta_texto)
    return client


def _fake_client_sequencial(respostas):
    """Client cujo responses.create devolve uma resposta diferente por chamada."""
    client = MagicMock()
    upload = MagicMock()
    upload.id = "file-abc123"
    client.files.create.return_value = upload
    client.responses.create.side_effect = respostas
    return client


def _mov(dia, valor, descricao="Recebimento"):
    return {
        "data": f"2026-05-{dia:02d}",
        "descricao": descricao,
        "documento": None,
        "valor": valor,
        "tipo": "C",
        "categoria_original": "PIX",
    }


def _criar_pdf(caminho, paginas):
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(paginas):
        writer.add_blank_page(width=200, height=200)
    with open(caminho, "wb") as arquivo:
        writer.write(arquivo)


class IaExtractorTest(unittest.TestCase):
    def _run_com_client(self, resposta_texto, caminho="extrato.pdf"):
        client = _fake_client(resposta_texto)
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "chave-teste"}, clear=False),
            patch("openai.OpenAI", return_value=client) as client_cls,
            patch.object(ia_extractor, "_contar_paginas", return_value=1),
        ):
            resultado = ia_extractor.extrair_extrato_ia(caminho)
        client_cls.assert_called_once()
        return resultado, client

    def _run_com_client_sequencial(self, client, caminho):
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "chave-teste"}, clear=False),
            patch("openai.OpenAI", return_value=client),
        ):
            return ia_extractor.extrair_extrato_ia(caminho)

    def test_json_valido_gera_dataframe_no_contrato(self):
        resposta = json.dumps(
            {
                "documento_valido": True,
                "movimentacoes": [
                    {
                        "data": "2026-05-01",
                        "descricao": "Recebimento",
                        "documento": "12345",
                        "valor": 1234.56,
                        "tipo": "c",
                        "categoria_original": "PIX",
                    },
                    {
                        "data": "2026-05-02",
                        "descricao": "Pagamento",
                        "documento": None,
                        "valor": -50.0,
                        "tipo": "D",
                        "categoria_original": None,
                    },
                ],
                "total_movimentacoes": 2,
            }
        )

        # o teste usa um caminho inexistente; o PDF nao e aberto porque files.create
        # e mockado antes -- mas o open() real precisa de um arquivo valido
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "extrato.pdf"
            _criar_pdf(caminho, paginas=1)
            df, client = self._run_com_client(resposta, caminho)

        self.assertIsNotNone(df)
        self.assertEqual(list(df.columns), ["DATA", "DESCRIÇÃO", "VALOR", "TIPO"])
        self.assertEqual(df.loc[0, "DATA"], "01/05/2026")
        self.assertEqual(df.loc[0, "DESCRIÇÃO"], "PIX RECEBIMENTO 12345")
        self.assertEqual(df.loc[0, "VALOR"], 1234.56)
        self.assertEqual(df.loc[0, "TIPO"], "C")
        # valor negativo vira positivo (contrato usa VALOR abs + TIPO)
        self.assertEqual(df.loc[1, "VALOR"], 50.0)
        self.assertEqual(df.loc[1, "TIPO"], "D")
        # "None" textual removido da descricao
        self.assertNotIn("NONE", df.loc[1, "DESCRIÇÃO"])
        # arquivo enviado a OpenAI e removido depois
        client.files.delete.assert_called_once_with("file-abc123")

    def test_contrato_compativel_com_validador(self):
        from src.services import conversao

        resposta = json.dumps(
            {
                "documento_valido": True,
                "movimentacoes": [
                    {
                        "data": "2026-05-01",
                        "descricao": "Recebimento",
                        "documento": "1",
                        "valor": 10.0,
                        "tipo": "C",
                        "categoria_original": "PIX",
                    }
                ],
                "total_movimentacoes": 1,
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "extrato.pdf"
            _criar_pdf(caminho, paginas=1)
            df, _ = self._run_com_client(resposta, caminho)
        # nao deve levantar
        conversao.validar_dataframe_extrato(df)

    def test_documento_invalido_retorna_none(self):
        resposta = json.dumps({"mensagem": "O PDF enviado não é um extrato bancário."})
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "extrato.pdf"
            _criar_pdf(caminho, paginas=1)
            df, _ = self._run_com_client(resposta, caminho)
        self.assertIsNone(df)

    def test_json_com_fence_markdown_e_tolerado(self):
        resposta = (
            "```json\n"
            + json.dumps(
                {
                    "documento_valido": True,
                    "movimentacoes": [
                        {
                            "data": "2026-05-01",
                            "descricao": "Recebimento",
                            "documento": "1",
                            "valor": 10.0,
                            "tipo": "C",
                            "categoria_original": "PIX",
                        }
                    ],
                    "total_movimentacoes": 1,
                }
            )
            + "\n```"
        )
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "extrato.pdf"
            _criar_pdf(caminho, paginas=1)
            df, _ = self._run_com_client(resposta, caminho)
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 1)

    def test_erro_na_api_retorna_none(self):
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "chave-teste"}, clear=False),
            patch("openai.OpenAI", side_effect=RuntimeError("api fora do ar")),
        ):
            df = ia_extractor.extrair_extrato_ia("extrato.pdf")
        self.assertIsNone(df)

    def test_sem_api_key_retorna_none(self):
        with patch.dict("os.environ", {}, clear=True):
            df = ia_extractor.extrair_extrato_ia("extrato.pdf")
        self.assertIsNone(df)

    def test_pdf_longo_e_processado_em_lotes(self):
        # 6 paginas com lote de 4 -> 2 chamadas (validacao + continuacao)
        resposta_lote1 = json.dumps(
            {
                "documento_valido": True,
                "banco": {"nome": "Banco X"},
                "extrato": {
                    "competencia": "05/2026",
                    "data_inicio": "2026-05-01",
                    "data_fim": "2026-05-31",
                },
                "movimentacoes": [_mov(1, 10.0), _mov(2, 20.0)],
                "total_movimentacoes": 2,
            }
        )
        resposta_lote2 = json.dumps(
            {
                "documento_valido": True,
                "movimentacoes": [_mov(3, 30.0)],
                "total_movimentacoes": 1,
            }
        )
        client = _fake_client_sequencial(
            [_resposta_ok(resposta_lote1), _resposta_ok(resposta_lote2)]
        )

        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "extrato.pdf"
            _criar_pdf(caminho, paginas=6)
            df = self._run_com_client_sequencial(client, caminho)

        self.assertIsNotNone(df)
        self.assertEqual(len(df), 3)
        self.assertEqual(client.responses.create.call_count, 2)
        # segunda chamada usa o prompt de continuacao com o contexto do extrato
        prompt_lote2 = client.responses.create.call_args_list[1].kwargs["input"][0][
            "content"
        ][1]["text"]
        self.assertIn("continuação", prompt_lote2)
        self.assertIn("Banco X", prompt_lote2)

    def test_resposta_truncada_reprocessa_pagina_a_pagina(self):
        resposta_pagina1 = json.dumps(
            {
                "documento_valido": True,
                "banco": {"nome": "Banco X"},
                "extrato": {"data_inicio": "2026-05-01", "data_fim": "2026-05-31"},
                "movimentacoes": [_mov(1, 10.0)],
                "total_movimentacoes": 1,
            }
        )
        resposta_pagina2 = json.dumps(
            {
                "documento_valido": True,
                "movimentacoes": [_mov(2, 20.0)],
                "total_movimentacoes": 1,
            }
        )
        client = _fake_client_sequencial(
            [
                _resposta_truncada(),
                _resposta_ok(resposta_pagina1),
                _resposta_ok(resposta_pagina2),
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "extrato.pdf"
            _criar_pdf(caminho, paginas=2)
            df = self._run_com_client_sequencial(client, caminho)

        self.assertIsNotNone(df)
        self.assertEqual(len(df), 2)
        self.assertEqual(client.responses.create.call_count, 3)

    def test_contagem_divergente_refaz_chamada_e_usa_resposta_completa(self):
        incompleta = json.dumps(
            {
                "documento_valido": True,
                "movimentacoes": [_mov(1, 10.0)],
                "total_movimentacoes": 2,
            }
        )
        completa = json.dumps(
            {
                "documento_valido": True,
                "movimentacoes": [_mov(1, 10.0), _mov(2, 20.0)],
                "total_movimentacoes": 2,
            }
        )
        client = _fake_client_sequencial(
            [_resposta_ok(incompleta), _resposta_ok(completa)]
        )

        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "extrato.pdf"
            _criar_pdf(caminho, paginas=1)
            df = self._run_com_client_sequencial(client, caminho)

        self.assertIsNotNone(df)
        self.assertEqual(len(df), 2)
        self.assertEqual(client.responses.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
