import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.services import chat_notifications as notifications
from src.services import conversao


class FakeResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class FakeOutboxDrive:
    def __init__(self):
        self.files = {}
        self.events = []

    def find_file_by_name(self, folder_id, name, mime_type=None):
        file = self.files.get(name)
        if not file:
            return None
        return {"id": file["id"], "name": name, "mimeType": mime_type}

    def download(self, file_id, destino_local):
        for file in self.files.values():
            if file["id"] == file_id:
                Path(destino_local).write_bytes(file["content"])
                return str(destino_local)
        raise FileNotFoundError(file_id)

    def upload(self, caminho_local, folder_id_destino, type_file, name_drive=None):
        name = name_drive or Path(caminho_local).name
        self.files[name] = {
            "id": f"id-{name}",
            "content": Path(caminho_local).read_bytes(),
        }
        self.events.append(f"persist:{name}")
        return {"id": f"id-{name}", "name": name}

    def update_file(self, file_id, caminho_local, type_file, name_drive=None):
        name = name_drive or Path(caminho_local).name
        self.files[name] = {
            "id": file_id,
            "content": Path(caminho_local).read_bytes(),
        }
        self.events.append(f"persist:{name}")
        return {"id": file_id, "name": name}


class ChatNotificationsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.fallback_patcher = patch.object(
            notifications,
            "LOCAL_FALLBACK_PATH",
            Path(self.temp_dir.name) / "fallback.xlsx",
        )
        self.fallback_patcher.start()

    def tearDown(self):
        self.fallback_patcher.stop()
        self.temp_dir.cleanup()

    def test_divide_mensagem_em_partes_seguras(self):
        partes = notifications.dividir_mensagem("A" * 8_100)

        self.assertEqual(len(partes), 3)
        self.assertTrue(all(len(parte) <= 3_500 for parte in partes))
        self.assertEqual("".join(partes), "A" * 8_100)

    def test_persiste_antes_de_enviar_e_marca_como_enviada(self):
        drive = FakeOutboxDrive()

        def enviar(mensagem):
            self.assertIn(notifications.CHAT_OUTBOX_NAME, drive.files)
            records = notifications.listar_registros_drive(drive, "root")
            self.assertEqual(records[0].status, notifications.STATUS_PENDENTE)
            drive.events.append("send")
            return notifications.SendResult(True, 1)

        with patch.object(notifications, "tentar_enviar_mensagem", side_effect=enviar):
            resultado = notifications.registrar_e_enviar_notificacao(
                google_drive=drive,
                pasta_raiz_id="root",
                execucao_id="exec-1",
                tipo="CONCLUSAO",
                mensagem="Conversao concluida",
            )

        self.assertTrue(resultado)
        self.assertEqual(drive.events[0], f"persist:{notifications.CHAT_OUTBOX_NAME}")
        self.assertEqual(drive.events[1], "send")
        records = notifications.listar_registros_drive(drive, "root")
        self.assertEqual(records[0].status, notifications.STATUS_ENVIADA)
        self.assertEqual(records[0].tentativas, 1)

    def test_falha_permanece_pendente_com_tentativas(self):
        drive = FakeOutboxDrive()
        with patch.object(
            notifications,
            "tentar_enviar_mensagem",
            return_value=notifications.SendResult(False, 3, "offline"),
        ):
            resultado = notifications.registrar_e_enviar_notificacao(
                google_drive=drive,
                pasta_raiz_id="root",
                execucao_id="exec-2",
                tipo="FALHA",
                mensagem="Falha critica",
            )

        self.assertFalse(resultado)
        record = notifications.listar_registros_drive(drive, "root")[0]
        self.assertEqual(record.status, notifications.STATUS_PENDENTE)
        self.assertEqual(record.tentativas, 3)
        self.assertEqual(record.ultimo_erro, "offline")

    def test_worker_reprocessa_pendente(self):
        drive = FakeOutboxDrive()
        record = notifications.criar_registros_notificacao(
            "exec-3",
            "CONCLUSAO",
            "Pendente",
        )[0]
        notifications.persistir_registros_drive(drive, "root", [record])

        with patch.object(
            notifications,
            "tentar_enviar_mensagem",
            return_value=notifications.SendResult(True, 1),
        ):
            resultado = notifications.reprocessar_notificacoes_pendentes(
                google_drive=drive,
                pasta_raiz_id="root",
            )

        self.assertEqual(resultado, {"pendentes": 1, "enviadas": 1, "falhas": 0})
        record = notifications.listar_registros_drive(drive, "root")[0]
        self.assertEqual(record.status, notifications.STATUS_ENVIADA)

    def test_worker_retoma_pendencia_apos_reinicio_simulado(self):
        drive = FakeOutboxDrive()
        with patch.object(
            notifications,
            "tentar_enviar_mensagem",
            return_value=notifications.SendResult(False, 3, "offline"),
        ):
            notifications.registrar_e_enviar_notificacao(
                google_drive=drive,
                pasta_raiz_id="root",
                execucao_id="exec-reinicio",
                tipo="CONCLUSAO",
                mensagem="Persistida antes do reinicio",
            )

        with patch.object(
            notifications,
            "tentar_enviar_mensagem",
            return_value=notifications.SendResult(True, 1),
        ):
            resultado = notifications.reprocessar_notificacoes_pendentes(
                google_drive=drive,
                pasta_raiz_id="root",
            )

        self.assertEqual(resultado, {"pendentes": 1, "enviadas": 1, "falhas": 0})
        record = notifications.listar_registros_drive(drive, "root")[0]
        self.assertEqual(record.status, notifications.STATUS_ENVIADA)
        self.assertEqual(record.tentativas, 4)

    def test_envio_tenta_tres_vezes_com_backoff(self):
        post = MagicMock(
            side_effect=[
                FakeResponse(500, "erro"),
                RuntimeError("offline"),
                FakeResponse(200, "ok"),
            ]
        )
        with (
            patch.object(notifications.requests, "post", post),
            patch.object(notifications.time, "sleep") as sleep,
        ):
            resultado = notifications.tentar_enviar_mensagem("mensagem")

        self.assertTrue(resultado.success)
        self.assertEqual(resultado.attempts, 3)
        self.assertEqual(post.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 5])

    def test_envio_permanece_falho_apos_tres_excecoes(self):
        with (
            patch.object(
                notifications.requests,
                "post",
                side_effect=TimeoutError("timeout"),
            ) as post,
            patch.object(notifications.time, "sleep"),
        ):
            resultado = notifications.tentar_enviar_mensagem("mensagem")

        self.assertFalse(resultado.success)
        self.assertEqual(resultado.attempts, 3)
        self.assertEqual(post.call_count, 3)
        self.assertIn("TimeoutError", resultado.error)

    def test_respostas_4xx_e_5xx_nao_sao_marcadas_como_entregues(self):
        for status_code in (400, 503):
            with self.subTest(status_code=status_code):
                with (
                    patch.object(
                        notifications.requests,
                        "post",
                        return_value=FakeResponse(status_code, "indisponivel"),
                    ) as post,
                    patch.object(notifications.time, "sleep"),
                ):
                    resultado = notifications.tentar_enviar_mensagem("mensagem")

                self.assertFalse(resultado.success)
                self.assertEqual(resultado.attempts, 3)
                self.assertEqual(post.call_count, 3)
                self.assertIn(f"HTTP {status_code}", resultado.error)

    def test_partes_tem_status_independente(self):
        drive = FakeOutboxDrive()
        with patch.object(
            notifications,
            "tentar_enviar_mensagem",
            side_effect=[
                notifications.SendResult(True, 1),
                notifications.SendResult(False, 3, "offline"),
            ],
        ):
            resultado = notifications.registrar_e_enviar_notificacao(
                google_drive=drive,
                pasta_raiz_id="root",
                execucao_id="exec-partes",
                tipo="CONCLUSAO",
                mensagem="A" * 7_000,
            )

        self.assertFalse(resultado)
        records = notifications.listar_registros_drive(drive, "root")
        self.assertEqual(
            [record.status for record in records],
            [notifications.STATUS_ENVIADA, notifications.STATUS_PENDENTE],
        )

    def test_resumo_de_execucao_vazia_e_enfileirado(self):
        with patch.object(
            conversao,
            "registrar_e_enviar_notificacao",
            return_value=True,
        ) as registrar:
            resultado = conversao.enviar_notificacao_google_chat(
                [],
                execucao_id="exec-vazia",
                tipo="SEM_ARQUIVOS",
                status_execucao="SUCESSO",
                total_processados=0,
            )

        self.assertTrue(resultado)
        registrar.assert_called_once()
        kwargs = registrar.call_args.kwargs
        self.assertEqual(kwargs["tipo"], "SEM_ARQUIVOS")
        self.assertIn("Nenhum PDF encontrado na pasta EXT", kwargs["mensagem"])
        self.assertIn("Status: SUCESSO", kwargs["mensagem"])

    def test_fallback_local_preserva_pendencia(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fallback = Path(temp_dir) / "fallback.xlsx"
            drive = MagicMock()
            drive.find_file_by_name.side_effect = RuntimeError("Drive offline")

            with (
                patch.object(notifications, "LOCAL_FALLBACK_PATH", fallback),
                patch.object(
                    notifications,
                    "tentar_enviar_mensagem",
                    return_value=notifications.SendResult(False, 3, "Chat offline"),
                ),
            ):
                resultado = notifications.registrar_e_enviar_notificacao(
                    google_drive=drive,
                    pasta_raiz_id="root",
                    execucao_id="exec-4",
                    tipo="FALHA",
                    mensagem="Falha",
                )
                records = notifications.listar_registros_local()

        self.assertFalse(resultado)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, notifications.STATUS_PENDENTE)
        self.assertEqual(records[0].tentativas, 3)


if __name__ == "__main__":
    unittest.main()
