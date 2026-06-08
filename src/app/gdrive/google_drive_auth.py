import shutil
from pathlib import Path
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow


class GoogleDriveAuth:
    SCOPES = ["https://www.googleapis.com/auth/drive"]

    FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
    PDF_MIME_TYPE = "application/pdf"

    def __init__(
        self,
        credentials_path: str,
        token_path: str,
        token_secret_path: Optional[str] = None
    ):
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.token_secret_path = Path(token_secret_path) if token_secret_path else None

        self._preparar_token_gravavel()

        self.service = self._service()

    def _preparar_token_gravavel(self):
        """
        No Render, /etc/secrets é somente leitura.
        Por isso, copiamos o token original para /tmp antes de autenticar.
        """

        if self.token_path.exists():
            return

        if self.token_secret_path and self.token_secret_path.exists():
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.token_secret_path, self.token_path)
            return

        raise FileNotFoundError(
            f"Token do Google Drive não encontrado. "
            f"token_path={self.token_path} | "
            f"token_secret_path={self.token_secret_path}"
        )

    def _service(self):
        creds = None

        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(
                str(self.token_path),
                self.SCOPES
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path),
                    self.SCOPES
                )

                creds = flow.run_local_server(port=0)

            self.token_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.token_path, "w", encoding="utf-8") as token:
                token.write(creds.to_json())

        return build("drive", "v3", credentials=creds)

    def get_file_info(self, file_id: str):
        return self.service.files().get(
            fileId=file_id,
            fields="id, name, mimeType, parents, size, modifiedTime",
            supportsAllDrives=True
        ).execute()

    def list_folder_by_name(self, folder_id: str, name_folder: str):
        query = (
            f"'{folder_id}' in parents "
            f"and name = '{name_folder}' "
            f"and mimeType = '{self.FOLDER_MIME_TYPE}' "
            f"and trashed = false"
        )

        response = self.service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name, mimeType)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        pastas = response.get("files", [])
        return pastas[0] if pastas else None

    def create_folder(self, name_folder: str, folder_id_pai: Optional[str] = None):
        metadata = {
            "name": name_folder,
            "mimeType": self.FOLDER_MIME_TYPE
        }

        if folder_id_pai:
            metadata["parents"] = [folder_id_pai]

        return self.service.files().create(
            body=metadata,
            fields="id, name, mimeType, parents",
            supportsAllDrives=True
        ).execute()

    def get_or_create_folder(self, folder_id_pai: str, name_folder: str):
        pasta = self.list_folder_by_name(
            folder_id=folder_id_pai,
            name_folder=name_folder
        )

        if pasta:
            return pasta

        return self.create_folder(
            name_folder=name_folder,
            folder_id_pai=folder_id_pai
        )

    def pdfs(self, folder_id: str, pdf_type: str = None):
        pdf_type = pdf_type or self.PDF_MIME_TYPE

        query = (
            f"'{folder_id}' in parents "
            f"and mimeType = '{pdf_type}' "
            f"and trashed = false"
        )

        arquivos = []
        page_token = None

        while True:
            response = self.service.files().list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()

            arquivos.extend(response.get("files", []))
            page_token = response.get("nextPageToken")

            if not page_token:
                break

        return arquivos

    def download(self, file_id: str, destino_local):
        destino = Path(destino_local)
        destino.parent.mkdir(parents=True, exist_ok=True)

        request = self.service.files().get_media(
            fileId=file_id,
            supportsAllDrives=True
        )

        with open(destino, "wb") as arquivo_local:
            downloader = MediaIoBaseDownload(arquivo_local, request)
            done = False

            while not done:
                status, done = downloader.next_chunk()

        return str(destino)

    def upload(
        self,
        caminho_local,
        folder_id_destino: str,
        type_file: str,
        name_drive: Optional[str] = None
    ):
        caminho = Path(caminho_local)

        if not caminho.exists():
            raise FileNotFoundError(f"Arquivo não encontrado: {caminho_local}")

        nome_final = name_drive or caminho.name

        metadata = {
            "name": nome_final,
            "parents": [folder_id_destino]
        }

        media = MediaFileUpload(
            filename=str(caminho),
            mimetype=type_file,
            resumable=True
        )

        return self.service.files().create(
            body=metadata,
            media_body=media,
            fields="id, name, mimeType, size, modifiedTime, parents",
            supportsAllDrives=True
        ).execute()

    def move_file(self, file_id: str, folder_id_destino: str):
        arquivo = self.service.files().get(
            fileId=file_id,
            fields="id, name, parents",
            supportsAllDrives=True
        ).execute()

        parents_atuais = ",".join(arquivo.get("parents", []))

        return self.service.files().update(
            fileId=file_id,
            addParents=folder_id_destino,
            removeParents=parents_atuais,
            fields="id, name, parents",
            supportsAllDrives=True
        ).execute()
