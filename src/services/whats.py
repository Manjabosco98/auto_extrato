import logging
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


load_dotenv()

from src.app.gdrive.google_drive_auth import GoogleDriveAuth
from src.app.gdrive.settings import (
    GOOGLE_DRIVE_FOLDER_ID,
    GOOGLE_OAUTH_CREDENTIALS,
    GOOGLE_OAUTH_TOKEN,
    GOOGLE_OAUTH_TOKEN_SECRET,
    PDF_MIME_TYPE,
)
from src.app.whats.whats import EvolutionAPIError, WhatsAppChat
from src.services.conversao import executar_conversao
from src.utils.logging_config import setup_logging


logger = logging.getLogger(__name__)


def _normalize_event(event: str | None) -> str:
    return (event or "").replace("_", ".").lower()


def _payload_message(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", {})

    if isinstance(data, dict):
        messages = data.get("messages")
        if isinstance(messages, list) and messages:
            return messages[0]
        return data

    return {}


def should_process_webhook(
    payload: dict[str, Any],
    whatsapp: WhatsAppChat | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    event = _normalize_event(payload.get("event"))
    if event != "messages.upsert":
        return False, "Evento ignorado", None

    whatsapp = whatsapp or WhatsAppChat()

    instance = payload.get("instance")
    if instance and instance != whatsapp.instance_name:
        return False, "Instancia ignorada", None

    message = _payload_message(payload)
    key = message.get("key", {})
    remote_jid = key.get("remoteJid")
    message_id = key.get("id")
    message_type = message.get("messageType")

    if not message_id:
        return False, "Mensagem sem id", None

    if whatsapp.is_processed(message_id):
        return False, "PDF ja processado", message

    if message_type != "documentMessage":
        return False, "Mensagem nao e documento", None

    document = message.get("message", {}).get("documentMessage", {})
    if document.get("mimetype") != PDF_MIME_TYPE:
        return False, "Documento nao e PDF", None

    group_jid = whatsapp.find_group_name()
    if not group_jid:
        return False, f"Grupo nao encontrado: {whatsapp.group_name}", None

    if remote_jid != group_jid:
        return False, "Grupo ignorado", None

    return True, "PDF aceito", message


def processar_webhook_whats(payload: dict[str, Any]) -> dict[str, Any]:
    setup_logging()
    whatsapp = WhatsAppChat()
    should_process, reason, message = should_process_webhook(payload, whatsapp=whatsapp)

    if not should_process:
        logger.info("Webhook WhatsApp ignorado: %s", reason)
        return {"status": "ignored", "message": reason}

    key = message.get("key", {})
    document = message.get("message", {}).get("documentMessage", {})
    message_id = key["id"]
    file_name = document.get("fileName") or f"{message_id}.pdf"
    local_path: Path | None = None

    logger.info("Processando PDF recebido pelo WhatsApp: %s", file_name)

    try:
        local_path = whatsapp.download_pdf(message, file_name)

        google_drive = GoogleDriveAuth(
            credentials_path=GOOGLE_OAUTH_CREDENTIALS,
            token_path=GOOGLE_OAUTH_TOKEN,
            token_secret_path=GOOGLE_OAUTH_TOKEN_SECRET,
        )
        uploaded = google_drive.upload(
            caminho_local=local_path,
            folder_id_destino=GOOGLE_DRIVE_FOLDER_ID,
            type_file=PDF_MIME_TYPE,
            name_drive=file_name,
        )

        whatsapp.mark_processed(
            message_id,
            {
                "file_name": file_name,
                "mimetype": document.get("mimetype"),
                "remote_jid": key.get("remoteJid"),
                "participant": key.get("participant"),
                "from_me": key.get("fromMe"),
                "push_name": message.get("pushName"),
                "message_timestamp": message.get("messageTimestamp"),
                "local_path": str(local_path.resolve()),
                "google_drive_file_id": uploaded.get("id"),
                "google_drive_folder_id": GOOGLE_DRIVE_FOLDER_ID,
                "status": "enviado_drive",
            },
        )

        logger.info("PDF enviado ao Google Drive. Iniciando conversao: %s", file_name)
        executar_conversao()

        return {
            "status": "processed",
            "message": "PDF enviado ao Google Drive e conversao iniciada",
            "file_name": file_name,
            "google_drive_file_id": uploaded.get("id"),
        }
    except EvolutionAPIError as error:
        whatsapp.mark_processed(
            message_id,
            {
                "file_name": file_name,
                "mimetype": document.get("mimetype"),
                "remote_jid": key.get("remoteJid"),
                "participant": key.get("participant"),
                "from_me": key.get("fromMe"),
                "push_name": message.get("pushName"),
                "message_timestamp": message.get("messageTimestamp"),
                "local_path": None,
                "status": f"indisponivel_{error.status_code}",
                "error_response": error.body,
            },
        )
        logger.exception("Erro ao baixar PDF pela Evolution API: %s", file_name)
        raise
    finally:
        if local_path is not None:
            local_path.unlink(missing_ok=True)
