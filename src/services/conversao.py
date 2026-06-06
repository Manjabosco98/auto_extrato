import logging
import shutil
from pathlib import Path

from src.app.gdrive.google_drive_auth import GoogleDriveAuth
from src.app.gdrive.settings import (
    GOOGLE_DRIVE_FOLDER_ID,
    GOOGLE_OAUTH_CREDENTIALS,
    GOOGLE_OAUTH_TOKEN,
    PDF_MIME_TYPE,
    XLS_MIME_TYPE,
    XLSX_MIME_TYPE,
)
from src.schemas import LayoutNotRecognized, dispatch
from src.schemas.parsers.pdf_extractor import PDFExtractor
from src.utils.helpers import planilha_lancamento, totalizador
from src.utils.logging_config import setup_logging


logger = logging.getLogger(__name__)


def executar_conversao():
    setup_logging()
    logger.info("Iniciando fluxo de conversao")
    logger.info("Autenticando Google Drive")

    google_drive = GoogleDriveAuth(
        credentials_path=GOOGLE_OAUTH_CREDENTIALS,
        token_path=GOOGLE_OAUTH_TOKEN,
    )

    extratos_id = GOOGLE_DRIVE_FOLDER_ID
    lancamento = Path.cwd() / "data" / "Lancamentos_Contabeis.xls"
    temp_dir = Path.cwd() / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Diretorio temporario preparado: %s", temp_dir)

    logger.info("Criando ou recuperando pasta de invalidos no Google Drive")
    pasta_invalidos = google_drive.get_or_create_folder(
        folder_id_pai=extratos_id,
        name_folder="00_INVALIDOS",
    )

    logger.info("Criando ou recuperando pasta de convertidos no Google Drive")
    pasta_convertidos = google_drive.get_or_create_folder(
        folder_id_pai=extratos_id,
        name_folder="00_CONVERTIDOS",
    )

    invalidos_id = pasta_invalidos["id"]
    convertidos_id = pasta_convertidos["id"]

    logger.info("Listando PDFs da pasta do Google Drive")
    arquivos = google_drive.pdfs(
        folder_id=extratos_id,
        pdf_type=PDF_MIME_TYPE,
    )
    logger.info("PDFs encontrados na pasta do Google Drive: %s", len(arquivos))

    if not arquivos:
        logger.info("Pasta vazia")

    processados = 0
    convertidos = 0
    invalidos = 0
    ignorados = 0

    for arquivo_drive in arquivos:
        arquivo_id = arquivo_drive["id"]
        arquivo_nome = arquivo_drive["name"]
        arquivo_stem = Path(arquivo_nome).stem

        if "EXT" not in arquivo_stem:
            ignorados += 1
            logger.info("Arquivo ignorado por nao conter EXT no nome: %s", arquivo_nome)
            continue

        processados += 1
        pdf_local = temp_dir / arquivo_nome
        dest_lancamento = None
        dest_excel = None

        logger.info("Processando PDF: %s", arquivo_nome)

        try:
            logger.info("Baixando PDF do Google Drive: %s", arquivo_nome)
            google_drive.download(
                file_id=arquivo_id,
                destino_local=pdf_local,
            )

            logger.info("Extraindo conteudo do PDF: %s", arquivo_nome)
            pdf = PDFExtractor(pdf_local).extract()

            if not pdf:
                logger.warning("PDF vazio. Movendo para invalidos: %s", arquivo_nome)
                google_drive.move_file(
                    file_id=arquivo_id,
                    folder_id_destino=invalidos_id,
                )
                invalidos += 1
                continue

            logger.info("Identificando layout e convertendo PDF em DataFrame: %s", arquivo_nome)
            try:
                df = dispatch(pdf)
            except LayoutNotRecognized:
                logger.exception("Layout nao reconhecido. Movendo para invalidos: %s", arquivo_nome)
                google_drive.move_file(
                    file_id=arquivo_id,
                    folder_id_destino=invalidos_id,
                )
                invalidos += 1
                continue

            if df is None or df.empty:
                logger.warning("DataFrame vazio. Movendo para invalidos: %s", arquivo_nome)
                google_drive.move_file(
                    file_id=arquivo_id,
                    folder_id_destino=invalidos_id,
                )
                invalidos += 1
                continue

            logger.info("Criando ou recuperando pasta do arquivo convertido: %s", arquivo_stem)
            pasta_arquivo = google_drive.get_or_create_folder(
                folder_id_pai=convertidos_id,
                name_folder=arquivo_stem,
            )
            pasta_arquivo_id = pasta_arquivo["id"]

            nome_lancamento = arquivo_stem.replace("EXT", "LANC")
            dest_lancamento = temp_dir / f"{nome_lancamento}.xls"
            dest_excel = temp_dir / f"{arquivo_stem}.xlsx"

            logger.info("Copiando modelo de lancamento para: %s", dest_lancamento)
            shutil.copy2(lancamento, dest_lancamento)

            logger.info("Preenchendo planilha de lancamento: %s", dest_lancamento)
            planilha_lancamento(df, dest_lancamento)

            logger.info("Gerando planilha totalizada: %s", dest_excel)
            df_totalizado = totalizador(df)
            df_totalizado.to_excel(dest_excel, index=False)

            logger.info("Enviando XLS para o Google Drive: %s", dest_lancamento.name)
            google_drive.upload(
                caminho_local=dest_lancamento,
                folder_id_destino=pasta_arquivo_id,
                type_file=XLS_MIME_TYPE,
                name_drive=dest_lancamento.name,
            )

            logger.info("Enviando XLSX para o Google Drive: %s", dest_excel.name)
            google_drive.upload(
                caminho_local=dest_excel,
                folder_id_destino=pasta_arquivo_id,
                type_file=XLSX_MIME_TYPE,
                name_drive=dest_excel.name,
            )

            logger.info("Movendo PDF original para pasta convertida: %s", arquivo_nome)
            google_drive.move_file(
                file_id=arquivo_id,
                folder_id_destino=pasta_arquivo_id,
            )

            convertidos += 1
            logger.info("PDF convertido com sucesso: %s", arquivo_nome)
            logger.info("Arquivos enviados para a pasta: %s", arquivo_stem)
        except Exception:
            logger.exception("Erro ao processar PDF: %s", arquivo_nome)
            raise
        finally:
            pdf_local.unlink(missing_ok=True)
            if dest_lancamento is not None:
                dest_lancamento.unlink(missing_ok=True)
            if dest_excel is not None:
                dest_excel.unlink(missing_ok=True)
            logger.info("Arquivos temporarios limpos para: %s", arquivo_nome)

    logger.info(
        "Fluxo de conversao concluido. Processados=%s Convertidos=%s Invalidos=%s Ignorados=%s",
        processados,
        convertidos,
        invalidos,
        ignorados,
    )


def main():
    executar_conversao()


if __name__ == "__main__":
    main()
