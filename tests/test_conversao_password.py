import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import conversao


class FakeDrive:
    def __init__(self, temp_dir):
        self.temp_dir = Path(temp_dir)
        self.moved = []

    def get_or_create_folder(self, folder_id_pai, name_folder):
        return {"id": f"id-{name_folder}", "name": name_folder}

    def pdfs(self, folder_id, pdf_type=None):
        return [
            {
                "id": "pdf-1",
                "name": "0126_EXTBAN TESTE.pdf",
                "mimeType": "application/pdf",
            }
        ]

    def download(self, file_id, destino_local):
        destino = Path(destino_local)
        destino.write_bytes(b"%PDF-1.4")
        return str(destino)

    def move_file(self, file_id, folder_id_destino):
        self.moved.append((file_id, folder_id_destino))


class ConversaoPasswordTest(unittest.TestCase):
    def test_move_pdf_com_senha_sem_interromper_conversao(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_drive = FakeDrive(temp_dir)

            with (
                patch.object(conversao, "GoogleDriveAuth", return_value=fake_drive),
                patch.object(conversao, "pdf_possui_senha", return_value=True),
                patch.object(conversao, "PDFExtractor") as pdf_extractor,
            ):
                conversao.executar_conversao()

        self.assertEqual(fake_drive.moved, [("pdf-1", "id-PDFS_COM_SENHAS")])
        pdf_extractor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
