import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from openpyxl import Workbook, load_workbook

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
        self.renamed = []
        self.uploaded = []
        self.updated = []
        self.trashed = set()
        self.history_file = None
        self.history_rows = []
        self.folders = []

    def get_or_create_folder(self, folder_id_pai, name_folder):
        self.folders.append((folder_id_pai, name_folder))
        return {"id": f"id-{name_folder}", "name": name_folder}

    def get_file_info(self, file_id):
        raise AssertionError("get_file_info nao deve ser chamado para resolver SRVARQ > EMP")

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

        if self.history_file and file_id == self.history_file["id"]:
            destino.write_bytes(self.history_file["content"])
            return str(destino)

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
        self._capture_history_rows(caminho_local, upload["name"])
        return upload

    def find_file_by_name(self, folder_id, name, mime_type=None):
        if self.history_file and name == self.history_file["name"]:
            return {
                "id": self.history_file["id"],
                "name": self.history_file["name"],
                "mimeType": mime_type,
            }

        return None

    def update_file(self, file_id, caminho_local, type_file, name_drive=None):
        update = {
            "id": file_id,
            "name": name_drive or Path(caminho_local).name,
            "type_file": type_file,
        }
        self.updated.append(update)
        self._capture_history_rows(caminho_local, update["name"])
        return update

    def move_file(self, file_id, folder_id_destino):
        self.moved.append((file_id, folder_id_destino))

    def rename_file(self, file_id, new_name):
        self.renamed.append((file_id, new_name))
        return {"id": file_id, "name": new_name}

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

    def _capture_history_rows(self, caminho_local, name):
        if name != conversao.HISTORICO_CONVERSOES:
            return

        workbook = load_workbook(caminho_local)
        worksheet = workbook.active
        self.history_rows = [tuple(row) for row in worksheet.iter_rows(values_only=True)]


class ConversaoPasswordTest(unittest.TestCase):
    def test_extrai_senha_e_nome_limpo(self):
        resultado = conversao.extrair_senha_nome_pdf(
            "0526_EXTBAN C6BANK SENHA 639256_GRB.pdf"
        )

        self.assertEqual(resultado, ("639256", "0526_EXTBAN C6BANK_GRB.pdf"))

    def test_extrai_senha_preservando_sm_no_nome_limpo(self):
        resultado = conversao.extrair_senha_nome_pdf(
            "0526_EXTBAN C6BANK SENHA 663861 SM_METRIKAL.pdf"
        )

        self.assertEqual(resultado, ("663861", "0526_EXTBAN C6BANK SM_METRIKAL.pdf"))

    def test_retorna_none_quando_nome_nao_tem_padrao_senha(self):
        resultado = conversao.extrair_senha_nome_pdf("0526_EXTBAN C6BANK_GRB.pdf")

        self.assertIsNone(resultado)

    def test_extrai_cliente_do_nome_do_arquivo(self):
        resultado = conversao.extrair_cliente_nome_arquivo(
            "0526_EXTBAN ITAU_CAMARGOS.pdf"
        )

        self.assertEqual(resultado, "CAMARGOS")

    def test_extrai_periodo_do_nome_do_arquivo(self):
        resultado = conversao.extrair_periodo_nome_arquivo(
            "0526_EXTBAN ITAU_CAMARGOS.pdf"
        )

        self.assertEqual(resultado, ("05", "26"))

    def test_nome_pasta_empresa_usa_id_e_razao_social(self):
        resultado = conversao.nome_pasta_empresa(23, " Camargos ")

        self.assertEqual(resultado, "23_CAMARGOS")

    def test_carrega_empresa_ativa_por_razao_social(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir) / "BaseEmpAtivas.xlsm"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "EmpAtivas"
            worksheet.append(["ID", "Razão social", "CNPJ", "Status"])
            worksheet.append([437, "CAMARGOS", "00.000.000/0001-00", "Ativa"])
            workbook.save(base_path)

            empresas = conversao.carregar_empresas_ativas(base_path=base_path)
            resultado = conversao.buscar_empresa_por_cliente(
                " camargos ",
                empresas=empresas,
            )

        self.assertEqual(resultado, (437, "CAMARGOS"))

    def test_cliente_inexistente_retorna_campos_vazios(self):
        resultado = conversao.buscar_empresa_por_cliente(
            "NAO EXISTE",
            empresas={},
        )

        self.assertEqual(resultado, ("", ""))

    def test_registrar_historico_cria_planilha_na_raiz(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_drive = FakeDrive(temp_dir)

            with patch.object(
                conversao,
                "buscar_empresa_por_cliente",
                return_value=(437, "CAMARGOS"),
            ):
                conversao.registrar_historico_conversao(
                    google_drive=fake_drive,
                    pasta_raiz_id="root-folder",
                    temp_dir=Path(temp_dir),
                    nomes_arquivos=[
                        "0526_EXTBAN C6BANK_CAMARGOS.pdf",
                        "0526_EXTBAN C6BANK_CAMARGOS.xlsx",
                        "0526_LANCBAN C6BANK_CAMARGOS.xlsm",
                    ],
                    pasta_destino="EMP/437_CAMARGOS/MOV/CONT/26/05/EXT",
                    data_hora=conversao.datetime(2026, 6, 12, 9, 30, 0),
                )

        self.assertEqual(fake_drive.updated, [])
        self.assertEqual(fake_drive.uploaded[-1]["name"], conversao.HISTORICO_CONVERSOES)
        self.assertEqual(fake_drive.uploaded[-1]["folder_id_destino"], "root-folder")
        self.assertEqual(
            fake_drive.history_rows,
            [
                conversao.HISTORICO_HEADERS,
                (
                    437,
                    "CAMARGOS",
                    "2026-06-12",
                    "09:30:00",
                    "0526_EXTBAN C6BANK_CAMARGOS.pdf",
                    "EMP/437_CAMARGOS/MOV/CONT/26/05/EXT",
                ),
                (
                    437,
                    "CAMARGOS",
                    "2026-06-12",
                    "09:30:00",
                    "0526_EXTBAN C6BANK_CAMARGOS.xlsx",
                    "EMP/437_CAMARGOS/MOV/CONT/26/05/EXT",
                ),
                (
                    437,
                    "CAMARGOS",
                    "2026-06-12",
                    "09:30:00",
                    "0526_LANCBAN C6BANK_CAMARGOS.xlsm",
                    "EMP/437_CAMARGOS/MOV/CONT/26/05/EXT",
                ),
            ],
        )

    def test_registrar_historico_atualiza_planilha_existente(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            historico_existente = temp_dir_path / conversao.HISTORICO_CONVERSOES
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.append(conversao.HISTORICO_HEADERS_ANTIGO)
            worksheet.append(
                [
                    "0426_EXTBAN C6BANK_LF.pdf",
                    "2026-06-11 08:00:00",
                    "00_CONVERTIDOS/0426_EXTBAN C6BANK_LF",
                ]
            )
            workbook.save(historico_existente)

            fake_drive = FakeDrive(temp_dir)
            fake_drive.history_file = {
                "id": "history-id",
                "name": conversao.HISTORICO_CONVERSOES,
                "content": historico_existente.read_bytes(),
            }

            with patch.object(
                conversao,
                "buscar_empresa_por_cliente",
                return_value=(437, "CAMARGOS"),
            ):
                conversao.registrar_historico_conversao(
                    google_drive=fake_drive,
                    pasta_raiz_id="root-folder",
                    temp_dir=temp_dir_path,
                    nomes_arquivos=[
                        "0526_EXTBAN C6BANK_CAMARGOS.pdf",
                        "0526_EXTBAN C6BANK_CAMARGOS.xlsx",
                        "0526_LANCBAN C6BANK_CAMARGOS.xlsm",
                    ],
                    pasta_destino="EMP/437_CAMARGOS/MOV/CONT/26/05/EXT",
                    data_hora=conversao.datetime(2026, 6, 12, 9, 30, 0),
                )

        self.assertEqual(fake_drive.updated[-1]["id"], "history-id")
        self.assertEqual(
            fake_drive.history_rows,
            [
                conversao.HISTORICO_HEADERS,
                (
                    None,
                    None,
                    "2026-06-11",
                    "08:00:00",
                    "0426_EXTBAN C6BANK_LF.pdf",
                    "00_CONVERTIDOS/0426_EXTBAN C6BANK_LF",
                ),
                (
                    437,
                    "CAMARGOS",
                    "2026-06-12",
                    "09:30:00",
                    "0526_EXTBAN C6BANK_CAMARGOS.pdf",
                    "EMP/437_CAMARGOS/MOV/CONT/26/05/EXT",
                ),
                (
                    437,
                    "CAMARGOS",
                    "2026-06-12",
                    "09:30:00",
                    "0526_EXTBAN C6BANK_CAMARGOS.xlsx",
                    "EMP/437_CAMARGOS/MOV/CONT/26/05/EXT",
                ),
                (
                    437,
                    "CAMARGOS",
                    "2026-06-12",
                    "09:30:00",
                    "0526_LANCBAN C6BANK_CAMARGOS.xlsm",
                    "EMP/437_CAMARGOS/MOV/CONT/26/05/EXT",
                ),
            ],
        )

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

    def test_pdf_convertido_registra_historico_apos_mover_para_convertidos(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_drive = FakeDrive(
                temp_dir,
                root_pdfs=[
                    {
                        "id": "pdf-1",
                        "name": "0526_EXTBAN C6BANK_CAMARGOS.pdf",
                        "mimeType": "application/pdf",
                    }
                ],
            )
            eventos = []

            def copy_modelo(origem, destino):
                Path(destino).write_bytes(b"modelo")

            def move_file(file_id, folder_id_destino):
                fake_drive.moved.append((file_id, folder_id_destino))
                eventos.append(f"move:{folder_id_destino}")

            def registrar_historico(**kwargs):
                eventos.append("historico")
                self.assertEqual(
                    kwargs["nomes_arquivos"],
                    [
                        "0526_EXTBAN C6BANK_CAMARGOS.pdf",
                        "0526_EXTBAN C6BANK_CAMARGOS.xlsx",
                        "0526_LANCBAN C6BANK_CAMARGOS.xlsm",
                    ],
                )
                self.assertEqual(
                    kwargs["pasta_destino"],
                    "EMP/23_CAMARGOS/MOV/CONT/26/05/EXT",
                )
                self.assertEqual(kwargs["empresa_id"], 23)
                self.assertEqual(kwargs["empresa_nome"], "CAMARGOS")

            fake_drive.move_file = move_file

            df = pd.DataFrame(
                [{"DATA": "01/05/2026", "VALOR": 10.0, "TIPO": "C", "DESCRIÃ‡ÃƒO": "PIX"}]
            )

            with (
                patch.object(conversao, "GoogleDriveAuth", return_value=fake_drive),
                patch.object(conversao, "carregar_empresas_ativas", return_value={"CAMARGOS": (23, "CAMARGOS")}),
                patch.object(conversao, "pdf_possui_senha", return_value=False),
                patch.object(conversao, "PDFExtractor") as pdf_extractor,
                patch.object(conversao, "dispatch", return_value=df),
                patch.object(conversao, "planilha_lancamento"),
                patch.object(conversao.shutil, "copy2", side_effect=copy_modelo),
                patch.object(conversao, "registrar_historico_conversao", side_effect=registrar_historico),
            ):
                pdf_extractor.return_value.extract.return_value = ["texto extraido"]
                conversao.executar_conversao()

        self.assertEqual(
            eventos,
            [
                "move:id-EXT",
                "historico",
            ],
        )
        self.assertIn((conversao.GOOGLE_DRIVE_EMP_FOLDER_ID, "EMP"), fake_drive.folders)
        self.assertIn(("id-EMP", "23_CAMARGOS"), fake_drive.folders)

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

    def test_nome_com_sm_move_para_invalidos_com_prefixo_sem_mov(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_drive = FakeDrive(
                temp_dir,
                root_pdfs=[
                    {
                        "id": "pdf-sm",
                        "name": "0526_EXTBAN ITAU 45440-7 SM_CIAL.pdf",
                        "mimeType": "application/pdf",
                    }
                ],
            )

            with (
                patch.object(conversao, "GoogleDriveAuth", return_value=fake_drive),
                patch.object(conversao, "pdf_possui_senha", return_value=False),
                patch.object(conversao, "PDFExtractor") as pdf_extractor,
            ):
                conversao.executar_conversao()

        self.assertEqual(
            fake_drive.renamed,
            [
                (
                    "pdf-sm",
                    "[SEM MOV] - 0526_EXTBAN ITAU 45440-7 SM_CIAL.pdf",
                )
            ],
        )
        self.assertIn(("pdf-sm", "id-00_INVALIDOS"), fake_drive.moved)
        pdf_extractor.assert_not_called()

    def test_prefixo_sem_mov_nao_duplica(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_drive = FakeDrive(
                temp_dir,
                root_pdfs=[
                    {
                        "id": "pdf-sm",
                        "name": "[SEM MOV] - 0526_EXTBAN ITAU 45440-7 SM_CIAL.pdf",
                        "mimeType": "application/pdf",
                    }
                ],
            )

            with (
                patch.object(conversao, "GoogleDriveAuth", return_value=fake_drive),
                patch.object(conversao, "pdf_possui_senha", return_value=False),
                patch.object(conversao, "PDFExtractor") as pdf_extractor,
            ):
                conversao.executar_conversao()

        self.assertEqual(fake_drive.renamed, [])
        self.assertIn(("pdf-sm", "id-00_INVALIDOS"), fake_drive.moved)
        pdf_extractor.assert_not_called()

    def test_pdf_sem_texto_move_para_invalidos_com_prefixo_sem_imagem(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_drive = FakeDrive(
                temp_dir,
                root_pdfs=[
                    {
                        "id": "pdf-imagem",
                        "name": "0526_EXTBAN IMAGEM_CIAL.pdf",
                        "mimeType": "application/pdf",
                    }
                ],
            )

            with (
                patch.object(conversao, "GoogleDriveAuth", return_value=fake_drive),
                patch.object(conversao, "pdf_possui_senha", return_value=False),
                patch.object(conversao, "PDFExtractor") as pdf_extractor,
                patch.object(conversao, "dispatch") as dispatch,
            ):
                pdf_extractor.return_value.extract.return_value = []
                conversao.executar_conversao()

        self.assertEqual(
            fake_drive.renamed,
            [
                (
                    "pdf-imagem",
                    "[SEM IMAGEM] - 0526_EXTBAN IMAGEM_CIAL.pdf",
                )
            ],
        )
        self.assertIn(("pdf-imagem", "id-00_INVALIDOS"), fake_drive.moved)
        dispatch.assert_not_called()

    def test_dataframe_vazio_move_para_invalidos_com_prefixo_sem_mov(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_drive = FakeDrive(
                temp_dir,
                root_pdfs=[
                    {
                        "id": "pdf-vazio",
                        "name": "0526_EXTBAN NORMAL_CIAL.pdf",
                        "mimeType": "application/pdf",
                    }
                ],
            )

            with (
                patch.object(conversao, "GoogleDriveAuth", return_value=fake_drive),
                patch.object(conversao, "pdf_possui_senha", return_value=False),
                patch.object(conversao, "PDFExtractor") as pdf_extractor,
                patch.object(conversao, "dispatch", return_value=pd.DataFrame()),
            ):
                pdf_extractor.return_value.extract.return_value = ["texto extraido"]
                conversao.executar_conversao()

        self.assertEqual(
            fake_drive.renamed,
            [
                (
                    "pdf-vazio",
                    "[SEM MOV] - 0526_EXTBAN NORMAL_CIAL.pdf",
                )
            ],
        )
        self.assertIn(("pdf-vazio", "id-00_INVALIDOS"), fake_drive.moved)


if __name__ == "__main__":
    unittest.main()
