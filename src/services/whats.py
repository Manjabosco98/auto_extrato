import logging
import os
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
CHECKPOINT_LOOKUP_FAILURE = "Falha ao consultar checkpoint WhatsApp"
WEBHOOK_CHECKPOINT_GRACE_SECONDS = int(
    os.getenv("WHATS_WEBHOOK_CHECKPOINT_GRACE_SECONDS", "1800")
)


def _normalize_event(event: str | None) -> str:
    return (event or "").replace("_", ".").lower()


def _payload_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", {})

    if not isinstance(data, dict):
        return []

    messages = data.get("messages")
    if isinstance(messages, list):
        return [
            message
            for message in messages
            if isinstance(message, dict)
        ]

    return [data]


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

    messages = _payload_messages(payload)
    if not messages:
        return False, "Mensagem sem id", None

    candidate_messages = []
    last_reason = "Documento nao e PDF"

    for message in messages:
        message_id = _message_id(message)

        if not message_id:
            last_reason = "Mensagem sem id"
            continue

        if whatsapp.is_processed(message_id):
            last_reason = "PDF ja processado"
            continue

        if not whatsapp.is_pdf_message(message):
            last_reason = "Documento nao e PDF"
            continue

        candidate_messages.append(message)

    if not candidate_messages:
        return False, last_reason, None

    group_jid = whatsapp.find_group_name()
    if not group_jid:
        return False, f"Grupo nao encontrado: {whatsapp.group_name}", None

    valid_group_messages = []
    for message in candidate_messages:
        is_valid, reason = _validate_payload_pdf_message(
            whatsapp=whatsapp,
            message=message,
            group_jid=group_jid,
        )

        if is_valid:
            valid_group_messages.append(message)
            continue

        last_reason = reason

    if not valid_group_messages:
        return False, last_reason, None

    checkpoint_ok, _, checkpoint_timestamp, checkpoint_reason = _group_checkpoint_context(
        whatsapp=whatsapp,
        group_jid=group_jid,
        context="webhook",
    )
    if not checkpoint_ok:
        return False, checkpoint_reason or CHECKPOINT_LOOKUP_FAILURE, None

    for message in valid_group_messages:
        is_after_checkpoint, reason = _validate_message_after_checkpoint(
            message=message,
            checkpoint_timestamp=checkpoint_timestamp,
            before_checkpoint_grace_seconds=WEBHOOK_CHECKPOINT_GRACE_SECONDS,
        )

        if is_after_checkpoint:
            return True, "PDF aceito", message

        last_reason = reason

    return False, last_reason, None


def processar_webhook_whats(payload: dict[str, Any]) -> dict[str, Any]:
    if not _whats_batch_lock.acquire(blocking=False):
        logger.info("Lote WhatsApp ja em processamento. Aguardando para revalidar evento")
        _whats_batch_lock.acquire()

    try:
        return _processar_lote_whats(payload)
    finally:
        _whats_batch_lock.release()


def executar_fluxo_whatsapp() -> dict[str, Any]:
    if not _whats_batch_lock.acquire(blocking=False):
        logger.info("Fluxo WhatsApp ja em processamento. Aguardando para executar manualmente")
        _whats_batch_lock.acquire()

    try:
        return _executar_fluxo_whatsapp()
    finally:
        _whats_batch_lock.release()


def _processar_lote_whats(payload: dict[str, Any]) -> dict[str, Any]:
    setup_logging()
    whatsapp = WhatsAppChat()
    should_process, reason, message = should_process_webhook(payload, whatsapp=whatsapp)

    if not should_process:
        logger.info("Webhook WhatsApp ignorado: %s", reason)
        return {"status": "ignored", "message": reason}

    google_drive = GoogleDriveAuth(
        credentials_path=GOOGLE_OAUTH_CREDENTIALS,
        token_path=GOOGLE_OAUTH_TOKEN,
        token_secret_path=GOOGLE_OAUTH_TOKEN_SECRET,
    )

    pending_messages = _payload_pdf_messages(
        whatsapp=whatsapp,
        payload=payload,
    )
    logger.info("PDFs novos identificados no payload WhatsApp: %s", len(pending_messages))

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
        _send_checkpoint_safe(whatsapp, _group_jid_from_messages(pending_messages))

        return {
            "status": "processed",
            "message": "PDFs enviados ao Google Drive e conversao iniciada",
            "total_baixados": len(downloaded),
            "arquivos": downloaded,
        }
    except Exception:
        logger.exception("Erro durante conversao do lote WhatsApp")
        raise


def _executar_fluxo_whatsapp() -> dict[str, Any]:
    setup_logging()
    whatsapp = WhatsAppChat()
    group_jid = whatsapp.find_group_name()

    if not group_jid:
        logger.info("Grupo WhatsApp nao encontrado: %s", whatsapp.group_name)
        return {
            "status": "ignored",
            "message": f"Grupo nao encontrado: {whatsapp.group_name}",
            "total_baixados": 0,
        }

    google_drive = GoogleDriveAuth(
        credentials_path=GOOGLE_OAUTH_CREDENTIALS,
        token_path=GOOGLE_OAUTH_TOKEN,
        token_secret_path=GOOGLE_OAUTH_TOKEN_SECRET,
    )

    checkpoint_ok, messages, checkpoint_timestamp, checkpoint_reason = _group_checkpoint_context(
        whatsapp=whatsapp,
        group_jid=group_jid,
        context="fluxo manual WhatsApp",
    )
    if not checkpoint_ok:
        return {
            "status": "ignored",
            "message": checkpoint_reason or CHECKPOINT_LOOKUP_FAILURE,
            "total_baixados": 0,
        }

    messages_by_id = {}

    for message in messages:
        is_after_checkpoint, reason = _validate_message_after_checkpoint(
            message=message,
            checkpoint_timestamp=checkpoint_timestamp,
        )
        if not is_after_checkpoint:
            logger.info("Mensagem ignorada pelo checkpoint: %s", reason)
            continue

        is_valid, reason = _validate_payload_pdf_message(
            whatsapp=whatsapp,
            message=message,
            group_jid=group_jid,
        )

        if not is_valid:
            logger.info("PDF do grupo ignorado no fluxo manual: %s", reason)
            continue

        messages_by_id[_message_id(message)] = message

    logger.info(
        "PDFs candidatos encontrados no fluxo manual WhatsApp: %s",
        len(messages_by_id),
    )

    downloaded = []
    for message in messages_by_id.values():
        result = _download_upload_pdf_message(
            whatsapp=whatsapp,
            google_drive=google_drive,
            message=message,
        )

        if result:
            downloaded.append(result)

    if not downloaded:
        logger.info("Nenhum PDF novo foi baixado no fluxo manual WhatsApp")
        return {
            "status": "ignored",
            "message": "Nenhum PDF novo baixado",
            "total_baixados": 0,
        }

    logger.info(
        "Downloads do fluxo manual WhatsApp finalizados. Iniciando conversao unica. Total=%s",
        len(downloaded),
    )
    executar_conversao()
    _send_checkpoint_safe(whatsapp, group_jid)

    return {
        "status": "processed",
        "message": "PDFs do WhatsApp enviados ao Google Drive e conversao iniciada",
        "total_baixados": len(downloaded),
        "arquivos": downloaded,
    }


def _message_id(message: dict[str, Any]) -> str | None:
    return message.get("key", {}).get("id")


def _group_jid_from_messages(messages: list[dict[str, Any]]) -> str | None:
    for message in messages:
        remote_jid = message.get("key", {}).get("remoteJid")

        if remote_jid:
            return remote_jid

    return None


def _group_checkpoint_context(
    whatsapp: WhatsAppChat,
    group_jid: str,
    context: str,
) -> tuple[bool, list[dict[str, Any]], int | None, str | None]:
    try:
        messages = whatsapp.group_messages(group_jid)
    except Exception:
        logger.exception(
            "Erro ao consultar mensagens do grupo para checkpoint no %s",
            context,
        )
        return False, [], None, CHECKPOINT_LOOKUP_FAILURE

    checkpoint_timestamp = whatsapp.last_checkpoint_timestamp(messages)
    if checkpoint_timestamp is not None:
        logger.info(
            "Checkpoint WhatsApp encontrado no %s. Considerando PDFs apos timestamp: %s",
            context,
            checkpoint_timestamp,
        )

    return True, messages, checkpoint_timestamp, None


def _validate_message_after_checkpoint(
    message: dict[str, Any],
    checkpoint_timestamp: int | None,
    before_checkpoint_grace_seconds: int = 0,
) -> tuple[bool, str]:
    if checkpoint_timestamp is None:
        return True, "Sem checkpoint"

    message_timestamp = message.get("messageTimestamp")

    if not isinstance(message_timestamp, int | float):
        return False, "Mensagem sem timestamp valido apos checkpoint"

    if message_timestamp <= checkpoint_timestamp:
        seconds_before_checkpoint = checkpoint_timestamp - message_timestamp

        if (
            before_checkpoint_grace_seconds > 0
            and seconds_before_checkpoint <= before_checkpoint_grace_seconds
        ):
            return True, "Mensagem antes do checkpoint dentro da tolerancia do webhook"

        return False, "Mensagem antes do checkpoint"

    return True, "Mensagem apos checkpoint"


def _send_checkpoint_safe(whatsapp: WhatsAppChat, group_jid: str | None) -> None:
    if not group_jid:
        logger.info("Checkpoint WhatsApp nao enviado: grupo nao identificado")
        return

    try:
        whatsapp.send_checkpoint(group_jid)
        logger.info("Checkpoint WhatsApp enviado para o grupo: %s", group_jid)
    except Exception:
        logger.exception("Erro ao enviar checkpoint WhatsApp para o grupo: %s", group_jid)


def _validate_payload_pdf_message(
    whatsapp: WhatsAppChat,
    message: dict[str, Any],
    group_jid: str,
) -> tuple[bool, str]:
    key = message.get("key", {})
    message_id = key.get("id")
    remote_jid = key.get("remoteJid")

    if not message_id:
        return False, "Mensagem sem id"

    if whatsapp.is_processed(message_id):
        return False, "PDF ja processado"

    if not whatsapp.is_pdf_message(message):
        return False, "Documento nao e PDF"

    if remote_jid != group_jid:
        return False, "Grupo ignorado"

    return True, "PDF aceito"


def _payload_pdf_messages(
    whatsapp: WhatsAppChat,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    group_jid = whatsapp.find_group_name()

    if not group_jid:
        logger.info("Grupo nao encontrado ao coletar PDFs do payload: %s", whatsapp.group_name)
        return []

    messages_by_id = {}

    for message in _payload_messages(payload):
        is_valid, reason = _validate_payload_pdf_message(
            whatsapp=whatsapp,
            message=message,
            group_jid=group_jid,
        )

        if not is_valid:
            logger.info("Mensagem do payload ignorada: %s", reason)
            continue

        messages_by_id[_message_id(message)] = message

    if not messages_by_id:
        return []

    checkpoint_ok, _, checkpoint_timestamp, checkpoint_reason = _group_checkpoint_context(
        whatsapp=whatsapp,
        group_jid=group_jid,
        context="webhook",
    )
    if not checkpoint_ok:
        logger.info("Webhook WhatsApp ignorado: %s", checkpoint_reason)
        return []

    filtered_messages = {}
    for message_id, message in messages_by_id.items():
        is_after_checkpoint, reason = _validate_message_after_checkpoint(
            message=message,
            checkpoint_timestamp=checkpoint_timestamp,
            before_checkpoint_grace_seconds=WEBHOOK_CHECKPOINT_GRACE_SECONDS,
        )

        if not is_after_checkpoint:
            logger.info("Mensagem do payload ignorada pelo checkpoint: %s", reason)
            continue

        filtered_messages[message_id] = message

    return list(filtered_messages.values())


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

    uploaded_before = google_drive.find_by_app_property(
        key="whatsapp_message_id",
        value=message_id,
    )

    if uploaded_before:
        logger.info(
            "PDF do WhatsApp ja existe no Google Drive, ignorando: %s | %s",
            message_id,
            uploaded_before.get("name"),
        )
        whatsapp.mark_processed(
            message_id,
            _control_data(
                message=message,
                file_name=file_name,
                local_path=None,
                status="ja_enviado_drive",
                google_drive_file_id=uploaded_before.get("id"),
            ),
        )
        return None

    logger.info("Baixando PDF do WhatsApp: %s", file_name)

    try:
        local_path = whatsapp.download_pdf(message, file_name)
        uploaded = google_drive.upload(
            caminho_local=local_path,
            folder_id_destino=GOOGLE_DRIVE_FOLDER_ID,
            type_file=PDF_MIME_TYPE,
            name_drive=file_name,
            app_properties={
                "whatsapp_message_id": message_id,
                "whatsapp_remote_jid": key.get("remoteJid") or "",
                "whatsapp_instance": whatsapp.instance_name,
            },
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
