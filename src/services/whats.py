import logging
from pathlib import Path
from threading import Lock
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
_whats_batch_lock = Lock()
_BATCH_MESSAGES_LIMIT = 50


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

    if not message_id:
        return False, "Mensagem sem id", None

    if whatsapp.is_processed(message_id):
        return False, "PDF ja processado", message

    if not whatsapp.is_pdf_message(message):
        return False, "Documento nao e PDF", None

    group_jid = whatsapp.find_group_name()
    if not group_jid:
        return False, f"Grupo nao encontrado: {whatsapp.group_name}", None

    if remote_jid != group_jid:
        return False, "Grupo ignorado", None

    return True, "PDF aceito", message


def processar_webhook_whats(payload: dict[str, Any]) -> dict[str, Any]:
    if not _whats_batch_lock.acquire(blocking=False):
        logger.info("Lote WhatsApp ja em processamento. Aguardando para revalidar evento")
        _whats_batch_lock.acquire()

    try:
        return _processar_lote_whats(payload)
    finally:
        _whats_batch_lock.release()


def _processar_lote_whats(payload: dict[str, Any]) -> dict[str, Any]:
    setup_logging()
    whatsapp = WhatsAppChat()
    should_process, reason, message = should_process_webhook(payload, whatsapp=whatsapp)

    if not should_process:
        logger.info("Webhook WhatsApp ignorado: %s", reason)
        return {"status": "ignored", "message": reason}

    group_jid = message.get("key", {}).get("remoteJid")
    google_drive = GoogleDriveAuth(
        credentials_path=GOOGLE_OAUTH_CREDENTIALS,
        token_path=GOOGLE_OAUTH_TOKEN,
        token_secret_path=GOOGLE_OAUTH_TOKEN_SECRET,
    )

    pending_messages = _pending_pdf_messages(
        whatsapp=whatsapp,
        group_jid=group_jid,
        seed_message=message,
    )
    logger.info("PDFs pendentes identificados no lote WhatsApp: %s", len(pending_messages))

    downloaded = []
    for pending_message in pending_messages:
        result = _download_upload_pdf_message(
            whatsapp=whatsapp,
            google_drive=google_drive,
            message=pending_message,
        )

        if result:
            downloaded.append(result)

    if not downloaded:
        logger.info("Nenhum PDF novo foi baixado no lote WhatsApp")
        return {
            "status": "ignored",
            "message": "Nenhum PDF novo baixado",
            "total_baixados": 0,
        }

    try:
        logger.info(
            "Downloads do lote WhatsApp finalizados. Iniciando conversao unica. Total=%s",
            len(downloaded),
        )
        executar_conversao()

        return {
            "status": "processed",
            "message": "PDFs enviados ao Google Drive e conversao iniciada",
            "total_baixados": len(downloaded),
            "arquivos": downloaded,
        }
    except Exception:
        logger.exception("Erro durante conversao do lote WhatsApp")
        raise


def _message_id(message: dict[str, Any]) -> str | None:
    return message.get("key", {}).get("id")


def _pending_pdf_messages(
    whatsapp: WhatsAppChat,
    group_jid: str,
    seed_message: dict[str, Any],
) -> list[dict[str, Any]]:
    messages_by_id = {}

    if whatsapp.is_pdf_message(seed_message):
        seed_id = _message_id(seed_message)
        if seed_id:
            messages_by_id[seed_id] = seed_message

    try:
        for message in whatsapp.pdf_messages(group_jid, limit=_BATCH_MESSAGES_LIMIT):
            message_id = _message_id(message)

            if not message_id:
                continue

            messages_by_id[message_id] = message
    except Exception:
        logger.exception("Erro ao consultar PDFs pendentes do grupo WhatsApp")

    pending = []
    for message_id, message in messages_by_id.items():
        if whatsapp.is_processed(message_id):
            logger.info("PDF ja registrado no controle, ignorando: %s", message_id)
            continue

        pending.append(message)

    return pending


def _download_upload_pdf_message(
    whatsapp: WhatsAppChat,
    google_drive: GoogleDriveAuth,
    message: dict[str, Any],
) -> dict[str, Any] | None:
    key = message.get("key", {})
    document = message.get("message", {}).get("documentMessage", {})
    message_id = key.get("id")

    if not message_id:
        logger.info("Mensagem PDF ignorada por nao possuir id")
        return None

    if whatsapp.is_processed(message_id):
        logger.info("PDF ja baixado anteriormente, ignorando: %s", message_id)
        return None

    file_name = whatsapp.pdf_file_name(message)
    local_path: Path | None = None

    logger.info("Baixando PDF do WhatsApp: %s", file_name)

    try:
        local_path = whatsapp.download_pdf(message, file_name)
        uploaded = google_drive.upload(
            caminho_local=local_path,
            folder_id_destino=GOOGLE_DRIVE_FOLDER_ID,
            type_file=PDF_MIME_TYPE,
            name_drive=file_name,
        )

        whatsapp.mark_processed(
            message_id,
            _control_data(
                message=message,
                file_name=file_name,
                local_path=local_path,
                status="enviado_drive",
                google_drive_file_id=uploaded.get("id"),
            ),
        )

        logger.info("PDF enviado ao Google Drive: %s", file_name)
        return {
            "file_name": file_name,
            "message_id": message_id,
            "google_drive_file_id": uploaded.get("id"),
        }
    except EvolutionAPIError as error:
        whatsapp.mark_processed(
            message_id,
            _control_data(
                message=message,
                file_name=file_name,
                local_path=None,
                status=f"indisponivel_{error.status_code}",
                error_response=error.body,
            ),
        )
        logger.exception("PDF indisponivel na Evolution API: %s", file_name)
        return None
    except Exception:
        logger.exception("Erro ao baixar ou enviar PDF do WhatsApp: %s", file_name)
        return None
    finally:
        if local_path is not None:
            local_path.unlink(missing_ok=True)


def _control_data(
    message: dict[str, Any],
    file_name: str,
    local_path: Path | None,
    status: str,
    google_drive_file_id: str | None = None,
    error_response: str | None = None,
) -> dict[str, Any]:
    key = message.get("key", {})
    document = message.get("message", {}).get("documentMessage", {})
    data = {
        "file_name": file_name,
        "mimetype": document.get("mimetype"),
        "remote_jid": key.get("remoteJid"),
        "participant": key.get("participant"),
        "from_me": key.get("fromMe"),
        "push_name": message.get("pushName"),
        "message_timestamp": message.get("messageTimestamp"),
        "local_path": str(local_path.resolve()) if local_path else None,
        "google_drive_folder_id": GOOGLE_DRIVE_FOLDER_ID,
        "status": status,
    }

    if google_drive_file_id:
        data["google_drive_file_id"] = google_drive_file_id

    if error_response:
        data["error_response"] = error_response

    return data
