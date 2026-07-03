import json
import unittest
from unittest.mock import MagicMock, patch

from src.schemas.parsers import ia_extractor


def _fake_client(resposta_texto):
    client = MagicMock()
    upload = MagicMock()
    upload.name = "files/abc123"
    client.files.upload.return_value = upload
    client.models.generate_content.return_value = MagicMock(text=resposta_texto)
    return client


class IaExtractorTest(unittest.TestCase):
    def _run_com_client(self, resposta_texto):
        client = _fake_client(resposta_texto)
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "chave-teste"}, clear=False),
            patch("google.genai.Client", return_value=client) as client_cls,
        ):
            resultado = ia_extractor.extrair_extrato_ia("extrato.pdf")
        client_cls.assert_called_once()
        return resultado, client

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
            }
        )

        df, client = self._run_com_client(resposta)

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
        # arquivo enviado ao Gemini e removido depois
        client.files.delete.assert_called_once_with(name="files/abc123")

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
            }
        )

        df, _ = self._run_com_client(resposta)
        # nao deve levantar
        conversao.validar_dataframe_extrato(df)

    def test_documento_invalido_retorna_none(self):
        resposta = json.dumps({"mensagem": "O PDF enviado não é um extrato bancário."})
        df, _ = self._run_com_client(resposta)
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
                }
            )
            + "\n```"
        )
        df, _ = self._run_com_client(resposta)
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 1)

    def test_erro_na_api_retorna_none(self):
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "chave-teste"}, clear=False),
            patch("google.genai.Client", side_effect=RuntimeError("api fora do ar")),
        ):
            df = ia_extractor.extrair_extrato_ia("extrato.pdf")
        self.assertIsNone(df)

    def test_sem_api_key_retorna_none(self):
        with patch.dict("os.environ", {}, clear=True):
            df = ia_extractor.extrair_extrato_ia("extrato.pdf")
        self.assertIsNone(df)


if __name__ == "__main__":
    unittest.main()
