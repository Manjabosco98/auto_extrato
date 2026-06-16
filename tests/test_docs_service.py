import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from src.api.endpoints import docs as docs_endpoint
from src.services import docs


class FakeDriveDocs:
    def __init__(self, temp_dir):
        self.temp_dir = Path(temp_dir)
        self.children = [
            {
                "id": "doc-1",
                "name": "0626_JURATPAS_BRITO.docx",
                "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        ]
        self.folders = []
        self.moved = []
        self.uploaded = []
        self.updated = []
        self.history_rows = []
        self.history_file = None
        self.caminhos_content = self._build_caminhos_content()

    def _build_caminhos_content(self):
        caminho = self.temp_dir / docs.DOCS_CAMINHO_NAME
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(["Codigo", "destino_final"])
        worksheet.append(["JURATPAS", "SRVARQ/EMP/{EMPRESA}/MOV/CONT/{ANO}/{MES}/REL"])
        workbook.save(caminho)
        return caminho.read_bytes()

    def get_or_create_folder(self, folder_id_pai, name_folder):
        self.folders.append((folder_id_pai, name_folder))
        return {"id": f"id-{name_folder}", "name": name_folder}

    def find_file_by_name(self, folder_id, name, mime_type=None):
        if name == docs.DOCS_CAMINHO_NAME:
            return {"id": "caminhos-id", "name": name, "mimeType": mime_type}

        if self.history_file and name == self.history_file["name"]:
            return {
                "id": self.history_file["id"],
                "name": self.history_file["name"],
                "mimeType": mime_type,
            }

        return None

    def download(self, file_id, destino_local):
        destino = Path(destino_local)

        if file_id == "caminhos-id":
            destino.write_bytes(self.caminhos_content)
            return str(destino)

        if self.history_file and file_id == self.history_file["id"]:
            destino.write_bytes(self.history_file["content"])
            return str(destino)

        raise AssertionError(f"download inesperado: {file_id}")

    def list_children(self, folder_id):
        if folder_id == "id-DOCS":
            return self.children

        return []

    def move_file(self, file_id, folder_id_destino):
        self.moved.append((file_id, folder_id_destino))

    def upload(self, caminho_local, folder_id_destino, type_file, name_drive=None):
        upload = {
            "id": f"upload-{len(self.uploaded) + 1}",
            "name": name_drive or Path(caminho_local).name,
            "folder_id_destino": folder_id_destino,
            "type_file": type_file,
        }
        self.uploaded.append(upload)
        self._capture_history(caminho_local, upload["name"])
        return upload

    def update_file(self, file_id, caminho_local, type_file, name_drive=None):
        update = {
            "id": file_id,
            "name": name_drive or Path(caminho_local).name,
            "type_file": type_file,
        }
        self.updated.append(update)
        self._capture_history(caminho_local, update["name"])
        return update

    def _capture_history(self, caminho_local, name):
        if name != docs.HISTORICO_DOCS:
            return

        workbook = load_workbook(caminho_local)
        worksheet = workbook.active
        self.history_rows = [tuple(row) for row in worksheet.iter_rows(values_only=True)]


class DocsServiceTest(unittest.TestCase):
    def test_parse_nome_documento(self):
        resultado = docs.parse_nome_documento("0626_JURATPAS_BRITO.docx")

        self.assertEqual(
            resultado,
            {
                "mes": "06",
                "ano": "26",
                "codigo": "JURATPAS",
                "cliente": "BRITO",
            },
        )

    def test_monta_destino_docs(self):
        resultado = docs.montar_destino_docs(
            destino_template="SRVARQ/EMP/{EMPRESA}/MOV/CONT/{ANO}/{MÊS}/REL",
            empresa_chave="196_BRITO",
            ano="26",
            mes="06",
        )

        self.assertEqual(
            resultado,
            ["SRVARQ", "EMP", "196_BRITO", "MOV", "CONT", "26", "06", "REL"],
        )

    def test_carrega_caminhos_docs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            caminho = Path(temp_dir) / docs.DOCS_CAMINHO_NAME
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.append(["Codigo", "destino_final"])
            worksheet.append(["JURATPAS", "SRVARQ/EMP/{EMPRESA}/MOV/CONT/{ANO}/{MES}/REL"])
            workbook.save(caminho)

            resultado = docs.carregar_caminhos_docs(caminho)

        self.assertEqual(
            resultado,
            {"JURATPAS": "SRVARQ/EMP/{EMPRESA}/MOV/CONT/{ANO}/{MES}/REL"},
        )

    def test_executar_docs_move_arquivo_registra_historico_e_notifica(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_drive = FakeDriveDocs(temp_dir)

            with (
                patch.object(docs, "GoogleDriveAuth", return_value=fake_drive),
                patch.object(docs, "GOOGLE_DRIVE_FOLDER_ID", "root-folder"),
                patch.object(docs, "carregar_empresas_ativas", return_value={"BRITO": (196, "BRITO")}),
                patch.object(docs, "enviar_notificacao_docs_google_chat") as notificacao,
                patch.object(docs, "agora_historico", return_value=docs.datetime(2026, 6, 16, 12, 30, 5)),
            ):
                resultado = docs.executar_docs()

        self.assertEqual(resultado, {"processados": 1, "movidos": 1, "ignorados": 0, "erros": 0})
        self.assertIn(("root-folder", "DOCS"), fake_drive.folders)
        self.assertEqual(
            fake_drive.folders[-8:],
            [
                ("id-DOCS", "SRVARQ"),
                ("id-SRVARQ", "EMP"),
                ("id-EMP", "196_BRITO"),
                ("id-196_BRITO", "MOV"),
                ("id-MOV", "CONT"),
                ("id-CONT", "26"),
                ("id-26", "06"),
                ("id-06", "REL"),
            ],
        )
        self.assertEqual(fake_drive.moved, [("doc-1", "id-REL")])
        self.assertEqual(fake_drive.uploaded[-1]["name"], docs.HISTORICO_DOCS)
        self.assertEqual(
            fake_drive.history_rows,
            [
                docs.HISTORICO_DOCS_HEADERS,
                (
                    "196",
                    "BRITO",
                    "2026-06-16",
                    "12:30:05",
                    "JURATPAS",
                    "0626_JURATPAS_BRITO.docx",
                    "DOCS/SRVARQ/EMP/196_BRITO/MOV/CONT/26/06/REL",
                    "2026-06-16 12:30:05",
                ),
            ],
        )
        notificacao.assert_called_once_with(["0626_JURATPAS_BRITO.docx"])

    def test_executar_docs_ignora_empresa_inexistente(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_drive = FakeDriveDocs(temp_dir)

            with (
                patch.object(docs, "GoogleDriveAuth", return_value=fake_drive),
                patch.object(docs, "GOOGLE_DRIVE_FOLDER_ID", "root-folder"),
                patch.object(docs, "carregar_empresas_ativas", return_value={}),
                patch.object(docs, "enviar_notificacao_docs_google_chat") as notificacao,
            ):
                resultado = docs.executar_docs()

        self.assertEqual(resultado, {"processados": 1, "movidos": 0, "ignorados": 1, "erros": 0})
        self.assertEqual(fake_drive.moved, [])
        self.assertEqual(fake_drive.uploaded, [])
        notificacao.assert_called_once_with([])

    def test_notificacao_docs_google_chat_payload(self):
        class FakeResponse:
            status_code = 200
            text = "ok"

        with patch.object(docs.requests, "post", return_value=FakeResponse()) as post:
            resultado = docs.enviar_notificacao_docs_google_chat(
                ["0626_JURATPAS_BRITO.docx"],
                momento=docs.datetime(2026, 6, 16, 12, 30, 0),
            )

        self.assertTrue(resultado)
        post.assert_called_once_with(
            docs.GOOGLE_CHAT_SEND_URL,
            json={
                "space_name": "spaces/AAQAQEHQc-k",
                "message": (
                    "Documentos salvos 16/06/26 as 12:30 e atualizado na Base de Dados:\n\n"
                    "0626_JURATPAS_BRITO.docx"
                ),
            },
            timeout=30,
        )

    def test_rota_docs_executar_responde_202(self):
        app = FastAPI()
        app.include_router(docs_endpoint.router, prefix="/docs")

        with patch.object(docs_endpoint, "executar_docs"):
            response = TestClient(app).post("/docs/executar")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"message": "Fluxo DOCS iniciado"})

    def test_rota_docs_executar_responde_409_quando_em_execucao(self):
        app = FastAPI()
        app.include_router(docs_endpoint.router, prefix="/docs")
        docs_endpoint._docs_lock.acquire()

        try:
            response = TestClient(app).post("/docs/executar")
        finally:
            docs_endpoint._docs_lock.release()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"message": "Fluxo DOCS ja esta em execucao"})


if __name__ == "__main__":
    unittest.main()
