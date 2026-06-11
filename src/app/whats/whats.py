import base64
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


load_dotenv()


class EvolutionAPIError(Exception):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"Evolution API retornou status {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class WhatsAppChat:
    PDF_MIME_TYPE = "application/pdf"
    CHECKPOINT_TEXT = "PDFS BAIXADOS ATE AQUI"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        instance_name: str | None = None,
        group_name: str | None = None,
        controle_pdf: Path | str | None = None,
        downloads_dir: Path | str | None = None,
    ):
        self.base_url = (
            base_url
            or os.getenv("EVOLUTION_BASE_URL", "https://api-whatsapp-xoor.onrender.com/")
        ).rstrip("/")
        self.api_key = api_key or os.getenv("EVOLUTION_API_KEY")
        self.instance_name = instance_name or os.getenv("EVOLUTION_INSTANCE_NAME", "ExtractPDFs")
        self.group_name = group_name or os.getenv("EVOLUTION_GROUP_NAME", "ROBO EXTRATO")
        self.headers = {
            "apikey": self.api_key or "",
            "Content-Type": "application/json",
        }
        self.downloads_dir = Path(downloads_dir or Path.cwd() / "temp" / "whats")
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.controle_pdf = Path(
            controle_pdf or Path.cwd() / "src" / "app" / "whats" / "controle_pdf.json"
        )

    def _post(self, path: str, payload: dict[str, Any], timeout: int = 60) -> Any:
        data = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=self.headers,
            method="POST",
        )

        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise EvolutionAPIError(error.code, body) from error

    def read_pdfs(self) -> dict[str, Any]:
        if not self.controle_pdf.exists():
            return {}

        with open(self.controle_pdf, "r", encoding="utf-8") as file:
            content = file.read().strip()

        if not content:
            return {}

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}

    def save_pdfs_control(self, data: dict[str, Any]) -> None:
        self.controle_pdf.parent.mkdir(parents=True, exist_ok=True)

        with open(self.controle_pdf, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)

    def connect(self) -> Any:
        return self._post(f"/chat/findChats/{self.instance_name}", {}, timeout=60)

    def groups(self) -> list[dict[str, Any]]:
        chats = self.connect()
        groups = []

        for chat in chats:
            remote_jid = chat.get("remoteJid")

            if remote_jid and remote_jid.endswith("@g.us"):
                groups.append(chat)

        return groups

    def find_group_name(
        self,
        groups: list[dict[str, Any]] | None = None,
        group_name: str | None = None,
    ) -> str | None:
        expected_name = group_name or self.group_name
        groups = groups if groups is not None else self.groups()

        for group in groups:
            if group.get("pushName") == expected_name or group.get("name") == expected_name:
                return group.get("remoteJid")

        return None

    @staticmethod
    def payload_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
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

    def mensagens(self, remote_jid: str, limit: int = 50) -> dict[str, Any]:
        payload = {
            "where": {
                "key": {
                    "remoteJid": remote_jid,
                }
            },
            "limit": limit,
        }

        return self._post(
            f"/chat/findMessages/{self.instance_name}",
            payload,
            timeout=60,
        )

    def pdf_messages(self, remote_jid: str, limit: int = 50) -> list[dict[str, Any]]:
        response = self.mensagens(remote_jid=remote_jid, limit=limit)
        records = response.get("messages", {}).get("records", [])
        pdfs = []

        for message in records:
            if self.is_pdf_message(message):
                pdfs.append(message)

        return pdfs

    def group_messages(self, remote_jid: str, limit: int = 50) -> list[dict[str, Any]]:
        response = self.mensagens(remote_jid=remote_jid, limit=limit)
        return response.get("messages", {}).get("records", [])

    def send_text(self, remote_jid: str, text: str):
        payload = {
            "number": remote_jid,
            "text": text,
        }

        return self._post(
            f"/message/sendText/{self.instance_name}",
            payload,
            timeout=60,
        )

    def send_checkpoint(self, remote_jid: str):
        return self.send_text(remote_jid, self.CHECKPOINT_TEXT)

    def is_checkpoint_message(self, message: dict[str, Any]) -> bool:
        text = (
            message.get("message", {}).get("conversation")
            or message.get("message", {}).get("extendedTextMessage", {}).get("text")
            or message.get("text")
            or message.get("body")
            or ""
        )

        return text.strip().upper() == self.CHECKPOINT_TEXT

    def last_checkpoint_timestamp(self, messages: list[dict[str, Any]]) -> int | None:
        timestamps = [
            message.get("messageTimestamp")
            for message in messages
            if self.is_checkpoint_message(message)
        ]
        timestamps = [
            timestamp
            for timestamp in timestamps
            if isinstance(timestamp, int | float)
        ]

        return int(max(timestamps)) if timestamps else None

    def is_pdf_message(self, message: dict[str, Any]) -> bool:
        if message.get("messageType") != "documentMessage":
            return False

        document = message.get("message", {}).get("documentMessage", {})
        return document.get("mimetype") == self.PDF_MIME_TYPE

    def pdf_file_name(self, message: dict[str, Any]) -> str:
        key = message.get("key", {})
        document = message.get("message", {}).get("documentMessage", {})
        message_id = key.get("id") or "arquivo"
        return document.get("fileName") or f"{message_id}.pdf"

    def download_pdf(self, message: dict[str, Any], file_name: str) -> Path:
        media_response = self._post(
            f"/chat/getBase64FromMediaMessage/{self.instance_name}",
            {"message": message},
            timeout=120,
        )

        media_base64 = (
            media_response.get("base64")
            or media_response.get("data", {}).get("base64")
        )

        if not media_base64:
            raise ValueError(f"Base64 nao encontrado para o arquivo: {file_name}")

        if media_base64.startswith("data:"):
            media_base64 = media_base64.split(",", 1)[1]

        file_path = self.downloads_dir / file_name
        file_path.write_bytes(base64.b64decode(media_base64))
        return file_path

    def mark_processed(
        self,
        message_id: str,
        data: dict[str, Any],
    ) -> None:
        control = self.read_pdfs()
        control[message_id] = data
        self.save_pdfs_control(control)

    def is_processed(self, message_id: str) -> bool:
        return message_id in self.read_pdfs()
