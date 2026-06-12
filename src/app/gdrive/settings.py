import os
from pathlib import Path

BASE_DIR = Path.cwd()

GOOGLE_OAUTH_CREDENTIALS = Path(
    os.getenv(
        "GOOGLE_OAUTH_CREDENTIALS",
        BASE_DIR / "credentials" / "api_google_drive_auth.json"
    )
)

GOOGLE_OAUTH_TOKEN = Path(
    os.getenv(
        "GOOGLE_OAUTH_TOKEN",
        BASE_DIR / "credentials" / "token_drive.json"
    )
)

GOOGLE_OAUTH_TOKEN_SECRET = Path(
    os.getenv(
        "GOOGLE_OAUTH_TOKEN_SECRET",
        BASE_DIR / "credentials" / "token_drive.json"
    )
)

GOOGLE_DRIVE_FOLDER_ID = os.getenv(
    "GOOGLE_DRIVE_FOLDER_ID",
    "1w3AY5xp-hHyQh263jfvhGoUdzH-0Ls0F"
)

GOOGLE_DRIVE_EMP_FOLDER_ID = os.getenv(
    "GOOGLE_DRIVE_EMP_FOLDER_ID",
    GOOGLE_DRIVE_FOLDER_ID
)

PDF_MIME_TYPE = "application/pdf"
XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSM_MIME_TYPE = "application/vnd.ms-excel.sheet.macroenabled.12"
XLS_MIME_TYPE = "application/vnd.ms-excel"
