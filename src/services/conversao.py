import logging
import re
import shutil
from pathlib import Path

from pypdf import PdfReader

from src.app.gdrive.google_drive_auth import GoogleDriveAuth
from src.app.gdrive.settings import (
    GOOGLE_DRIVE_FOLDER_ID,
    GOOGLE_OAUTH_CREDENTIALS,
    GOOGLE_OAUTH_TOKEN,
    PDF_MIME_TYPE,
    XLS_MIME_TYPE,
    XLSX_MIME_TYPE,
    XLSM_MIME_TYPE,
    GOOGLE_OAUTH_TOKEN_SECRET
)
from src.schemas import LayoutNotRecognized, dispatch
from src.schemas.parsers.pdf_extractor import PDFExtractor
from src.utils.helpers import planilha_lancamento, remover_senha_pdf, totalizador
from src.utils.logging_config import setup_logging


logger = logging.getLogger(__name__)

SEM_MOV_PREFIX = "[SEM MOV] - "
SEM_IMAGEM_PREFIX = "[SEM IMAGEM] - "
PREFIXOS_INVALIDOS = (SEM_MOV_PREFIX, SEM_IMAGEM_PREFIX)


def pdf_possui_senha(pdf_path: Path) -> bool:
    try:
        reader = PdfReader(str(pdf_path))
        return bool(reader.is_encrypted)
    except Exception:
        logger.exception("Erro ao verificar se PDF possui senha: %s", pdf_path)
        return False


def extrair_senha_nome_pdf(nome_arquivo: str) -> tuple[str, str] | None:
    caminho = Path(nome_arquivo)
    match = re.match(
        r"^(?P<prefixo>.+?)\s+SENHA\s+(?P<senha>[^\s_]+)(?P<sufixo>.*)$",
        caminho.stem,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    senha = match.group("senha").strip()
    sufixo = match.group("sufixo").strip()
    nome_limpo_stem = match.group("prefixo").rstrip()

    if sufixo.startswith("_"):
        nome_limpo_stem = f"{nome_limpo_stem}{sufixo}"
    elif sufixo:
        nome_limpo_stem = f"{nome_limpo_stem} {sufixo}"

    nome_limpo = f"{nome_limpo_stem}{caminho.suffix}"
    return senha, nome_limpo


def nome_indica_sem_movimentacao(nome_arquivo: str) -> bool:
    stem = Path(nome_arquivo).stem.upper()
    return bool(re.search(r"(^|[\s_-])SM_", stem))


def nome_com_prefixo_invalido(nome_arquivo: str, prefixo: str) -> str:
    if nome_arquivo.startswith(PREFIXOS_INVALIDOS):
        return nome_arquivo
    return f"{prefixo}{nome_arquivo}"


def renomear_e_mover_para_invalidos(
    google_drive: GoogleDriveAuth,
    arquivo_id: str,
    arquivo_nome: str,
    invalidos_id: str,
    prefixo: str,
    motivo: str,
) -> str:
    nome_final = nome_com_prefixo_invalido(arquivo_nome, prefixo)

    if nome_final != arquivo_nome:
        google_drive.rename_file(arquivo_id, nome_final)

    google_drive.move_file(
        file_id=arquivo_id,
        folder_id_destino=invalidos_id,
    )
    logger.warning("%s. Movendo para 00_INVALIDOS: %s", motivo, nome_final)
    return nome_final


def processar_pdfs_com_senha(
    google_drive: GoogleDriveAuth,
    pasta_pdfs_com_senhas_id: str,
    pasta_raiz_id: str,
    temp_dir: Path,
) -> dict[str, int]:
    logger.info("Verificando pasta PDFS_COM_SENHAS para desbloqueio")

    arquivos = google_drive.pdfs(
        folder_id=pasta_pdfs_com_senhas_id,
        pdf_type=PDF_MIME_TYPE,
    )

    if not arquivos:
        logger.info("Pasta PDFS_COM_SENHAS sem PDFs para desbloquear")

    desbloqueados = 0
    ignorados = 0
    erros = 0

    for arquivo_drive in arquivos:
        arquivo_id = arquivo_drive["id"]
        arquivo_nome = arquivo_drive["name"]
        dados_senha = extrair_senha_nome_pdf(arquivo_nome)

        if not dados_senha:
            ignorados += 1
            logger.info(
                "PDF em PDFS_COM_SENHAS ignorado por nao conter padrao SENHA: %s",
                arquivo_nome,
            )
            continue

        senha, nome_limpo = dados_senha
        pdf_com_senha = temp_dir / arquivo_nome
        pdf_sem_senha = temp_dir / nome_limpo

        try:
            logger.info("Baixando PDF com senha para desbloqueio: %s", arquivo_nome)
            google_drive.download(
                file_id=arquivo_id,
                destino_local=pdf_com_senha,
            )

            logger.info("Removendo senha do PDF: %s", arquivo_nome)
            remover_senha_pdf(
                caminho_pdf=str(pdf_com_senha),
                senha=senha,
                caminho_saida=str(pdf_sem_senha),
            )

            logger.info("Enviando PDF desbloqueado para pasta raiz: %s", nome_limpo)
            google_drive.upload(
                caminho_local=pdf_sem_senha,
                folder_id_destino=pasta_raiz_id,
                type_file=PDF_MIME_TYPE,
                name_drive=nome_limpo,
            )

            logger.info("Movendo PDF original com senha para lixeira: %s", arquivo_nome)
            google_drive.trash_file(arquivo_id)
            desbloqueados += 1
        except Exception:
            erros += 1
            logger.exception("Erro ao desbloquear PDF com senha: %s", arquivo_nome)
        finally:
            pdf_com_senha.unlink(missing_ok=True)
            pdf_sem_senha.unlink(missing_ok=True)

    restantes = google_drive.list_children(pasta_pdfs_com_senhas_id)

    if not restantes:
        logger.info("Pasta PDFS_COM_SENHAS vazia. Movendo pasta para lixeira")
        google_drive.trash_file(pasta_pdfs_com_senhas_id)
    else:
        logger.info(
            "Pasta PDFS_COM_SENHAS mantida com %s item(ns) restante(s)",
            len(restantes),
        )

    return {
        "desbloqueados": desbloqueados,
        "ignorados": ignorados,
        "erros": erros,
    }


def executar_conversao():
    setup_logging()
    logger.info("Iniciando fluxo de conversao")
    logger.info("Autenticando Google Drive")

    google_drive = GoogleDriveAuth(
        credentials_path=GOOGLE_OAUTH_CREDENTIALS,
        token_path=GOOGLE_OAUTH_TOKEN,
        token_secret_path=GOOGLE_OAUTH_TOKEN_SECRET,
    )

    extratos_id = GOOGLE_DRIVE_FOLDER_ID
    lancamento = Path.cwd() / "data" / "Lancamentos_Contabeis.xlsm"
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

    logger.info("Criando ou recuperando pasta de PDFs com senha no Google Drive")
    pasta_pdfs_com_senhas = google_drive.get_or_create_folder(
        folder_id_pai=extratos_id,
        name_folder="PDFS_COM_SENHAS",
    )

    invalidos_id = pasta_invalidos["id"]
    convertidos_id = pasta_convertidos["id"]
    pdfs_com_senhas_id = pasta_pdfs_com_senhas["id"]

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
    com_senha = 0

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

            if pdf_possui_senha(pdf_local):
                logger.warning(
                    "PDF protegido por senha. Movendo para PDFS_COM_SENHAS: %s",
                    arquivo_nome,
                )
                google_drive.move_file(
                    file_id=arquivo_id,
                    folder_id_destino=pdfs_com_senhas_id,
                )
                com_senha += 1
                continue

            if nome_indica_sem_movimentacao(arquivo_nome):
                renomear_e_mover_para_invalidos(
                    google_drive=google_drive,
                    arquivo_id=arquivo_id,
                    arquivo_nome=arquivo_nome,
                    invalidos_id=invalidos_id,
                    prefixo=SEM_MOV_PREFIX,
                    motivo="Nome do PDF indica extrato sem movimentacao",
                )
                invalidos += 1
                continue

            logger.info("Extraindo conteudo do PDF: %s", arquivo_nome)
            pdf = PDFExtractor(pdf_local).extract()

            if not pdf:
                renomear_e_mover_para_invalidos(
                    google_drive=google_drive,
                    arquivo_id=arquivo_id,
                    arquivo_nome=arquivo_nome,
                    invalidos_id=invalidos_id,
                    prefixo=SEM_IMAGEM_PREFIX,
                    motivo="PDF sem texto extraivel, possivelmente imagem",
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
                renomear_e_mover_para_invalidos(
                    google_drive=google_drive,
                    arquivo_id=arquivo_id,
                    arquivo_nome=arquivo_nome,
                    invalidos_id=invalidos_id,
                    prefixo=SEM_MOV_PREFIX,
                    motivo="DataFrame vazio, extrato sem movimentacao",
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
            dest_lancamento = temp_dir / f"{nome_lancamento}.xlsm"
            dest_excel = temp_dir / f"{arquivo_stem}.xlsx"

            logger.info("Copiando modelo de lancamento para: %s", dest_lancamento)
            shutil.copy2(lancamento, dest_lancamento)

            logger.info("Preenchendo planilha de lancamento: %s", dest_lancamento)
            planilha_lancamento(df, dest_lancamento)

            logger.info("Gerando planilha totalizada: %s", dest_excel)
            df_totalizado = totalizador(df)
            df_totalizado.to_excel(dest_excel, index=False)

            logger.info("Enviando XLSM para o Google Drive: %s", dest_lancamento.name)
            google_drive.upload(
                caminho_local=dest_lancamento,
                folder_id_destino=pasta_arquivo_id,
                type_file=XLSM_MIME_TYPE,
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

    resultado_senhas = processar_pdfs_com_senha(
        google_drive=google_drive,
        pasta_pdfs_com_senhas_id=pdfs_com_senhas_id,
        pasta_raiz_id=extratos_id,
        temp_dir=temp_dir,
    )

    logger.info(
        (
            "Fluxo de conversao concluido. "
            "Processados=%s Convertidos=%s Invalidos=%s Ignorados=%s ComSenha=%s "
            "Desbloqueados=%s SenhasIgnoradas=%s SenhasErros=%s"
        ),
        processados,
        convertidos,
        invalidos,
        ignorados,
        com_senha,
        resultado_senhas["desbloqueados"],
        resultado_senhas["ignorados"],
        resultado_senhas["erros"],
    )


def main():
    executar_conversao()


if __name__ == "__main__":
    main()
