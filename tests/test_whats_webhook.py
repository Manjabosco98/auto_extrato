import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from src.api.main import app
from src.app.whats.whats import WhatsAppChat
from src.services import whats as whats_service


GROUP_JID = "120363404201868629@g.us"


def make_payload(
    *,
    event="MESSAGES_UPSERT",
    instance="ExtractPDFs",
    remote_jid=GROUP_JID,
    message_type="documentMessage",
    mimetype="application/pdf",
    message_id="MSG123",
):
    return {
        "event": event,
        "instance": instance,
        "data": {
            "key": {
                "id": message_id,
                "remoteJid": remote_jid,
                "fromMe": False,
                "participant": "551199999999@lid",
            },
            "pushName": "SUPORTE SGE",
            "messageTimestamp": 1780948081,
            "messageType": message_type,
            "message": {
                "documentMessage": {
                    "fileName": "0126_EXTBAN ASAAS_HABITAR.pdf",
                    "mimetype": mimetype,
                }
            },
        },
    }


class FakeWhatsApp:
    instance_name = "ExtractPDFs"
    group_name = "ROBO EXTRATO"

    def __init__(self, processed=False, group_jid=GROUP_JID, pending_messages=None):
        self.processed = processed
        self.group_jid = group_jid
        self.pending_messages = pending_messages or []
        self.processed_ids = set()
        self.pdf_messages_called = False
        self.group_messages_called = False
        self.checkpoints_sent = []

    def is_processed(self, message_id):
        return self.processed or message_id in self.processed_ids

    def find_group_name(self):
        return self.group_jid

    def is_pdf_message(self, message):
        document = message.get("message", {}).get("documentMessage", {})
        return (
            message.get("messageType") == "documentMessage"
            and document.get("mimetype") == "application/pdf"
        )

    def pdf_file_name(self, message):
        document = message.get("message", {}).get("documentMessage", {})
        return document.get("fileName") or f"{message['key']['id']}.pdf"

    def pdf_messages(self, remote_jid, limit=50):
        self.pdf_messages_called = True
        return self.pending_messages

    def group_messages(self, remote_jid, limit=50):
        self.group_messages_called = True
        return self.pending_messages

    def is_checkpoint_message(self, message):
        text = (
            message.get("message", {}).get("conversation")
            or message.get("message", {}).get("extendedTextMessage", {}).get("text")
            or ""
        )
        return text == "PDFS BAIXADOS ATE AQUI"

    def last_checkpoint_timestamp(self, messages):
        timestamps = [
            message.get("messageTimestamp")
            for message in messages
            if self.is_checkpoint_message(message)
        ]
        return max(timestamps) if timestamps else None

    def send_checkpoint(self, remote_jid):
        self.checkpoints_sent.append(remote_jid)
        return {"status": "sent"}


class WhatsWebhookFilterTest(unittest.TestCase):
    def test_ignora_instancia_diferente(self):
        should_process, reason, message = whats_service.should_process_webhook(
            make_payload(instance="OutraInstancia"),
            whatsapp=FakeWhatsApp(),
        )

        self.assertFalse(should_process)
        self.assertEqual(reason, "Instancia ignorada")
        self.assertIsNone(message)

    def test_ignora_evento_diferente(self):
        should_process, reason, message = whats_service.should_process_webhook(
            make_payload(event="MESSAGES_UPDATE"),
            whatsapp=FakeWhatsApp(),
        )

        self.assertFalse(should_process)
        self.assertEqual(reason, "Evento ignorado")
        self.assertIsNone(message)

    def test_ignora_documento_que_nao_e_pdf(self):
        should_process, reason, message = whats_service.should_process_webhook(
            make_payload(mimetype="image/png"),
            whatsapp=FakeWhatsApp(),
        )

        self.assertFalse(should_process)
        self.assertEqual(reason, "Documento nao e PDF")
        self.assertIsNone(message)

    def test_ignora_grupo_diferente(self):
        should_process, reason, message = whats_service.should_process_webhook(
            make_payload(remote_jid="999999@g.us"),
            whatsapp=FakeWhatsApp(),
        )

        self.assertFalse(should_process)
        self.assertEqual(reason, "Grupo ignorado")
        self.assertIsNone(message)

    def test_aceita_pdf_do_grupo_e_instancia_corretos(self):
        should_process, reason, message = whats_service.should_process_webhook(
            make_payload(),
            whatsapp=FakeWhatsApp(),
        )

        self.assertTrue(should_process)
        self.assertEqual(reason, "PDF aceito")
        self.assertEqual(message["key"]["id"], "MSG123")

    def test_nao_reprocessa_pdf_ja_registrado(self):
        should_process, reason, message = whats_service.should_process_webhook(
            make_payload(),
            whatsapp=FakeWhatsApp(processed=True),
        )

        self.assertFalse(should_process)
        self.assertEqual(reason, "PDF ja processado")
        self.assertIsNone(message)


class WhatsWebhookServiceTest(unittest.TestCase):
    def test_processa_pdf_upload_drive_e_chama_conversao(self):
        payload = make_payload()
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        downloaded_path = Path(temp_dir.name) / "0126_EXTBAN ASAAS_HABITAR.pdf"
        calls = {"processed": None}

        fake_whatsapp = FakeWhatsApp()

        def download_pdf(message, file_name):
            downloaded_path.write_bytes(b"%PDF-1.4")
            return downloaded_path

        def mark_processed(message_id, data):
            calls["processed"] = (message_id, data)

        fake_whatsapp.download_pdf = download_pdf
        fake_whatsapp.mark_processed = mark_processed

        drive = Mock()
        drive.find_by_app_property.return_value = None
        drive.upload.return_value = {"id": "drive-file-id"}

        with (
            patch.object(whats_service, "WhatsAppChat", return_value=fake_whatsapp),
            patch.object(whats_service, "GoogleDriveAuth", return_value=drive),
            patch.object(whats_service, "executar_conversao") as executar_conversao,
        ):
            result = whats_service.processar_webhook_whats(payload)

        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["total_baixados"], 1)
        self.assertEqual(result["arquivos"][0]["google_drive_file_id"], "drive-file-id")
        self.assertEqual(calls["processed"][0], "MSG123")
        self.assertEqual(calls["processed"][1]["status"], "enviado_drive")
        drive.upload.assert_called_once()
        executar_conversao.assert_called_once()
        self.assertFalse(downloaded_path.exists())
        self.assertFalse(fake_whatsapp.pdf_messages_called)
        self.assertEqual(fake_whatsapp.checkpoints_sent, [GROUP_JID])

    def test_baixa_pdfs_do_payload_e_chama_conversao_uma_vez(self):
        payload = make_payload(message_id="MSG123")
        payload["data"] = {
            "messages": [
                make_payload(message_id="MSG123")["data"],
                make_payload(message_id="MSG456")["data"],
            ]
        }
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        processed = []

        fake_whatsapp = FakeWhatsApp()

        def download_pdf(message, file_name):
            path = Path(temp_dir.name) / file_name
            path.write_bytes(b"%PDF-1.4")
            return path

        def mark_processed(message_id, data):
            fake_whatsapp.processed_ids.add(message_id)
            processed.append((message_id, data))

        fake_whatsapp.download_pdf = download_pdf
        fake_whatsapp.mark_processed = mark_processed

        drive = Mock()
        drive.find_by_app_property.return_value = None
        drive.upload.side_effect = [
            {"id": "drive-file-id-1"},
            {"id": "drive-file-id-2"},
        ]

        with (
            patch.object(whats_service, "WhatsAppChat", return_value=fake_whatsapp),
            patch.object(whats_service, "GoogleDriveAuth", return_value=drive),
            patch.object(whats_service, "executar_conversao") as executar_conversao,
        ):
            result = whats_service.processar_webhook_whats(payload)

        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["total_baixados"], 2)
        self.assertEqual([item[0] for item in processed], ["MSG123", "MSG456"])
        self.assertEqual(drive.upload.call_count, 2)
        executar_conversao.assert_called_once()
        self.assertFalse(fake_whatsapp.pdf_messages_called)
        self.assertEqual(fake_whatsapp.checkpoints_sent, [GROUP_JID])

    def test_pdf_ja_existente_no_drive_nao_chama_conversao(self):
        payload = make_payload(message_id="MSG123")
        fake_whatsapp = FakeWhatsApp()
        fake_whatsapp.download_pdf = Mock()
        fake_whatsapp.mark_processed = Mock()

        drive = Mock()
        drive.find_by_app_property.return_value = {
            "id": "drive-file-id",
            "name": "0126_EXTBAN ASAAS_HABITAR.pdf",
        }

        with (
            patch.object(whats_service, "WhatsAppChat", return_value=fake_whatsapp),
            patch.object(whats_service, "GoogleDriveAuth", return_value=drive),
            patch.object(whats_service, "executar_conversao") as executar_conversao,
        ):
            result = whats_service.processar_webhook_whats(payload)

        self.assertEqual(result["status"], "ignored")
        self.assertEqual(result["total_baixados"], 0)
        drive.upload.assert_not_called()
        fake_whatsapp.download_pdf.assert_not_called()
        fake_whatsapp.mark_processed.assert_called_once()
        executar_conversao.assert_not_called()
        self.assertFalse(fake_whatsapp.pdf_messages_called)
        self.assertEqual(fake_whatsapp.checkpoints_sent, [])

    def test_fluxo_manual_varre_grupo_baixa_novos_e_chama_conversao_uma_vez(self):
        messages = [
            make_payload(message_id="MSG123")["data"],
            make_payload(message_id="MSG456")["data"],
        ]
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        processed = []

        fake_whatsapp = FakeWhatsApp(pending_messages=messages)

        def download_pdf(message, file_name):
            path = Path(temp_dir.name) / file_name
            path.write_bytes(b"%PDF-1.4")
            return path

        def mark_processed(message_id, data):
            fake_whatsapp.processed_ids.add(message_id)
            processed.append((message_id, data))

        fake_whatsapp.download_pdf = download_pdf
        fake_whatsapp.mark_processed = mark_processed

        drive = Mock()
        drive.find_by_app_property.return_value = None
        drive.upload.side_effect = [
            {"id": "drive-file-id-1"},
            {"id": "drive-file-id-2"},
        ]

        with (
            patch.object(whats_service, "WhatsAppChat", return_value=fake_whatsapp),
            patch.object(whats_service, "GoogleDriveAuth", return_value=drive),
            patch.object(whats_service, "executar_conversao") as executar_conversao,
        ):
            result = whats_service.executar_fluxo_whatsapp()

        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["total_baixados"], 2)
        self.assertEqual([item[0] for item in processed], ["MSG123", "MSG456"])
        self.assertEqual(drive.upload.call_count, 2)
        executar_conversao.assert_called_once()
        self.assertFalse(fake_whatsapp.pdf_messages_called)
        self.assertTrue(fake_whatsapp.group_messages_called)
        self.assertEqual(fake_whatsapp.checkpoints_sent, [GROUP_JID])

    def test_fluxo_manual_ignora_pdfs_antes_do_checkpoint(self):
        checkpoint = {
            "key": {"id": "CHECKPOINT", "remoteJid": GROUP_JID},
            "messageTimestamp": 200,
            "messageType": "conversation",
            "message": {"conversation": "PDFS BAIXADOS ATE AQUI"},
        }
        old_pdf = make_payload(message_id="MSG_OLD")["data"]
        old_pdf["messageTimestamp"] = 100
        new_pdf = make_payload(message_id="MSG_NEW")["data"]
        new_pdf["messageTimestamp"] = 300
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        processed = []

        fake_whatsapp = FakeWhatsApp(pending_messages=[old_pdf, checkpoint, new_pdf])

        def download_pdf(message, file_name):
            path = Path(temp_dir.name) / file_name
            path.write_bytes(b"%PDF-1.4")
            return path

        def mark_processed(message_id, data):
            processed.append((message_id, data))

        fake_whatsapp.download_pdf = download_pdf
        fake_whatsapp.mark_processed = mark_processed

        drive = Mock()
        drive.find_by_app_property.return_value = None
        drive.upload.return_value = {"id": "drive-file-id-new"}

        with (
            patch.object(whats_service, "WhatsAppChat", return_value=fake_whatsapp),
            patch.object(whats_service, "GoogleDriveAuth", return_value=drive),
            patch.object(whats_service, "executar_conversao") as executar_conversao,
        ):
            result = whats_service.executar_fluxo_whatsapp()

        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["total_baixados"], 1)
        self.assertEqual([item[0] for item in processed], ["MSG_NEW"])
        drive.upload.assert_called_once()
        executar_conversao.assert_called_once()
        self.assertEqual(fake_whatsapp.checkpoints_sent, [GROUP_JID])

    def test_fluxo_manual_sem_pdfs_depois_do_checkpoint_nao_executa(self):
        checkpoint = {
            "key": {"id": "CHECKPOINT", "remoteJid": GROUP_JID},
            "messageTimestamp": 200,
            "messageType": "conversation",
            "message": {"conversation": "PDFS BAIXADOS ATE AQUI"},
        }
        old_pdf = make_payload(message_id="MSG_OLD")["data"]
        old_pdf["messageTimestamp"] = 100
        fake_whatsapp = FakeWhatsApp(pending_messages=[old_pdf, checkpoint])
        fake_whatsapp.download_pdf = Mock()

        drive = Mock()
        drive.find_by_app_property.return_value = None

        with (
            patch.object(whats_service, "WhatsAppChat", return_value=fake_whatsapp),
            patch.object(whats_service, "GoogleDriveAuth", return_value=drive),
            patch.object(whats_service, "executar_conversao") as executar_conversao,
        ):
            result = whats_service.executar_fluxo_whatsapp()

        self.assertEqual(result["status"], "ignored")
        self.assertEqual(result["total_baixados"], 0)
        fake_whatsapp.download_pdf.assert_not_called()
        drive.upload.assert_not_called()
        executar_conversao.assert_not_called()
        self.assertEqual(fake_whatsapp.checkpoints_sent, [])

    def test_mark_processed_salva_controle_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            controle = Path(temp_dir) / "controle_pdf.json"
            whatsapp = WhatsAppChat(
                base_url="https://example.com",
                api_key="key",
                instance_name="ExtractPDFs",
                group_name="ROBO EXTRATO",
                controle_pdf=controle,
                downloads_dir=Path(temp_dir) / "downloads",
            )

            whatsapp.mark_processed("MSG123", {"status": "enviado_drive"})

            self.assertTrue(whatsapp.is_processed("MSG123"))
            self.assertEqual(whatsapp.read_pdfs()["MSG123"]["status"], "enviado_drive")

    def test_checkpoint_helpers_identificam_ultimo_timestamp(self):
        whatsapp = WhatsAppChat(
            base_url="https://example.com",
            api_key="key",
            instance_name="ExtractPDFs",
            group_name="ROBO EXTRATO",
        )
        messages = [
            {
                "messageTimestamp": 100,
                "message": {"conversation": "PDFS BAIXADOS ATE AQUI"},
            },
            {
                "messageTimestamp": 300,
                "message": {"extendedTextMessage": {"text": "PDFS BAIXADOS ATE AQUI"}},
            },
            {
                "messageTimestamp": 500,
                "message": {"conversation": "outra mensagem"},
            },
        ]

        self.assertTrue(whatsapp.is_checkpoint_message(messages[0]))
        self.assertEqual(whatsapp.last_checkpoint_timestamp(messages), 300)


class WhatsWebhookEndpointTest(unittest.TestCase):
    def test_webhook_retorna_200_quando_ignorado(self):
        with patch(
            "src.api.endpoints.whats.should_process_webhook",
            return_value=(False, "Evento ignorado", None),
        ):
            client = TestClient(app)
            response = client.post("/api/whats/webhook", json=make_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ignored", "message": "Evento ignorado"},
        )

    def test_webhook_messages_upsert_retorna_202_quando_aceito(self):
        calls = []

        with (
            patch(
                "src.api.endpoints.whats.should_process_webhook",
                return_value=(True, "PDF aceito", make_payload()["data"]),
            ),
            patch(
                "src.api.endpoints.whats.processar_webhook_whats",
                side_effect=lambda payload: calls.append(payload),
            ),
        ):
            client = TestClient(app)
            response = client.post("/api/whats/webhook/messages-upsert", json=make_payload())

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json(),
            {"status": "accepted", "message": "Webhook WhatsApp recebido"},
        )
        self.assertEqual(len(calls), 1)

    def test_executar_fluxo_whatsapp_manual_retorna_202(self):
        calls = []

        with patch(
            "src.api.endpoints.whats.executar_fluxo_whatsapp",
            side_effect=lambda: calls.append("executado"),
        ):
            client = TestClient(app)
            response = client.post("/api/whats/executar")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"message": "Fluxo WhatsApp iniciado"})
        self.assertEqual(calls, ["executado"])


if __name__ == "__main__":
    unittest.main()
