import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.endpoints import conversao as conversao_endpoint
from src.api.main import app


class DummyLock:
    def __init__(self, acquired: bool):
        self.acquired = acquired
        self.released = False

    def acquire(self, blocking: bool = True):
        return self.acquired

    def release(self):
        self.released = True


class ConversaoEndpointTest(unittest.TestCase):
    def test_executar_conversao_inicia_background_task(self):
        chamadas = []
        lock = DummyLock(acquired=True)

        with (
            patch.object(conversao_endpoint, "_conversion_lock", lock),
            patch.object(
                conversao_endpoint,
                "executar_conversao",
                side_effect=lambda: chamadas.append("executado"),
            ),
        ):
            client = TestClient(app)
            response = client.post("/api/conversao/executar")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"message": "Conversão iniciada"})
        self.assertEqual(chamadas, ["executado"])
        self.assertTrue(lock.released)

    def test_executar_conversao_bloqueia_chamada_duplicada(self):
        lock = DummyLock(acquired=False)

        with patch.object(conversao_endpoint, "_conversion_lock", lock):
            client = TestClient(app)
            response = client.post("/api/conversao/executar")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"message": "Conversão já está em execução"})
        self.assertFalse(lock.released)


if __name__ == "__main__":
    unittest.main()
