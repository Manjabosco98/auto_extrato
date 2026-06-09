import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import conversao


class FakeDrive:
    def __init__(self, temp_dir, root_pdfs=None, password_pdfs=None):
        self.temp_dir = Path(temp_dir)
        self.root_pdfs = root_pdfs if root_pdfs is not None else [
            {
                "id": "pdf-1",
                "name": "0126_EXTBAN TESTE.pdf",
                "mimeType": "application/pdf",
            }
        ]
        self.password_pdfs = password_pdfs if password_pdfs is not None else []
        self.moved = []
        self.uploaded = []
        self.trashed = set()

    def get_or_create_folder(self, folder_id_pai, name_folder):
        return {"id": f"id-{name_folder}", "name": name_folder}

    def pdfs(self, folder_id, pdf_type=None):
        if folder_id == "id-PDFS_COM_SENHAS":
            return [
                arquivo
                for arquivo in self.password_pdfs
                if arquivo["id"] not in self.trashed
            ]

        return [
            arquivo
            for arquivo in self.root_pdfs
            if arquivo["id"] not in self.trashed
        ]

    def download(self, file_id, destino_local):
        destino = Path(destino_local)
        destino.write_bytes(b"%PDF-1.4")
        return str(destino)

    def upload(self, caminho_local, folder_id_destino, type_file, name_drive=None):
        upload = {
            "id": f"upload-{len(self.uploaded) + 1}",
            "name": name_drive or Path(caminho_local).name,
            "folder_id_destino": folder_id_destino,
            "type_file": type_file,
        }
        self.uploaded.append(upload)
        return upload

    def move_file(self, file_id, folder_id_destino):
        self.moved.append((file_id, folder_id_destino))

    def list_children(self, folder_id):
        if folder_id != "id-PDFS_COM_SENHAS":
            return []

        return [
            arquivo
            for arquivo in self.password_pdfs
            if arquivo["id"] not in self.trashed
        ]

    def trash_file(self, file_id):
        self.trashed.add(file_id)
        return {"id": file_id, "trashed": True}


class ConversaoPasswordTest(unittest.TestCase):
    def test_extrai_senha_e_nome_limpo(self):
        resultado = conversao.extrair_senha_nome_pdf(
            "0526_EXTBAN C6BANK SENHA 639256_GRB.pdf"
        )

        self.assertEqual(resultado, ("639256", "0526_EXTBAN C6BANK_GRB.pdf"))

    def test_retorna_none_quando_nome_nao_tem_padrao_senha(self):
        resultado = conversao.extrair_senha_nome_pdf("0526_EXTBAN C6BANK_GRB.pdf")

        self.assertIsNone(resultado)

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

    def test_processa_pdf_com_senha_e_limpa_pasta_vazia(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_drive = FakeDrive(
                temp_dir,
                root_pdfs=[],
                password_pdfs=[
                    {
                        "id": "senha-1",
                        "name": "0526_EXTBAN C6BANK SENHA 639256_GRB.pdf",
                        "mimeType": "application/pdf",
                    }
                ],
            )

            with patch.object(
                conversao,
                "remover_senha_pdf",
                side_effect=lambda caminho_pdf, senha, caminho_saida: Path(caminho_saida).write_bytes(
                    b"%PDF-1.4 sem senha"
                ),
            ) as remover_senha:
                resultado = conversao.processar_pdfs_com_senha(
                    google_drive=fake_drive,
                    pasta_pdfs_com_senhas_id="id-PDFS_COM_SENHAS",
                    pasta_raiz_id="root-folder",
                    temp_dir=Path(temp_dir),
                )

        self.assertEqual(resultado, {"desbloqueados": 1, "ignorados": 0, "erros": 0})
        remover_senha.assert_called_once()
        self.assertEqual(fake_drive.uploaded[0]["name"], "0526_EXTBAN C6BANK_GRB.pdf")
        self.assertEqual(fake_drive.uploaded[0]["folder_id_destino"], "root-folder")
        self.assertIn("senha-1", fake_drive.trashed)
        self.assertIn("id-PDFS_COM_SENHAS", fake_drive.trashed)

    def test_nome_sem_padrao_mantem_pdf_e_pasta(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_drive = FakeDrive(
                temp_dir,
                root_pdfs=[],
                password_pdfs=[
                    {
                        "id": "senha-1",
                        "name": "0526_EXTBAN C6BANK_GRB.pdf",
                        "mimeType": "application/pdf",
                    }
                ],
            )

            resultado = conversao.processar_pdfs_com_senha(
                google_drive=fake_drive,
                pasta_pdfs_com_senhas_id="id-PDFS_COM_SENHAS",
                pasta_raiz_id="root-folder",
                temp_dir=Path(temp_dir),
            )

        self.assertEqual(resultado, {"desbloqueados": 0, "ignorados": 1, "erros": 0})
        self.assertEqual(fake_drive.uploaded, [])
        self.assertEqual(fake_drive.trashed, set())

    def test_senha_invalida_mantem_pdf_e_pasta(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_drive = FakeDrive(
                temp_dir,
                root_pdfs=[],
                password_pdfs=[
                    {
                        "id": "senha-1",
                        "name": "0526_EXTBAN C6BANK SENHA 000000_GRB.pdf",
                        "mimeType": "application/pdf",
                    }
                ],
            )

            with patch.object(
                conversao,
                "remover_senha_pdf",
                side_effect=ValueError("Senha invalida"),
            ):
                resultado = conversao.processar_pdfs_com_senha(
                    google_drive=fake_drive,
                    pasta_pdfs_com_senhas_id="id-PDFS_COM_SENHAS",
                    pasta_raiz_id="root-folder",
                    temp_dir=Path(temp_dir),
                )

        self.assertEqual(resultado, {"desbloqueados": 0, "ignorados": 0, "erros": 1})
        self.assertEqual(fake_drive.uploaded, [])
        self.assertEqual(fake_drive.trashed, set())


if __name__ == "__main__":
    unittest.main()
