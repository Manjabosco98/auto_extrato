import logging
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from googleapiclient.errors import HttpError
from openpyxl import Workbook, load_workbook
from pypdf import PdfReader

from src.app.gdrive.google_drive_auth import GoogleDriveAuth
from src.app.gdrive.settings import (
    GOOGLE_DRIVE_EMP_FOLDER_ID,
    GOOGLE_DRIVE_FOLDER_ID,
    GOOGLE_OAUTH_CREDENTIALS,
    GOOGLE_OAUTH_TOKEN,
    PDF_MIME_TYPE,
    XLS_MIME_TYPE,
    XLSX_MIME_TYPE,
    XLSM_MIME_TYPE,
    GOOGLE_OAUTH_TOKEN_SECRET
)
from src.app.supabase.supabase_api import atualizar_controle_supabase
from src.schemas import LayoutNotRecognized, dispatch
from src.schemas.parsers.pdf_extractor import PDFExtractor
from src.utils.helpers import planilha_lancamento, remover_senha_pdf, totalizador
from src.utils.logging_config import setup_logging


logger = logging.getLogger(__name__)

SEM_MOV_PREFIX = "[SEM MOV] - "
SEM_IMAGEM_PREFIX = "[SEM IMAGEM] - "
NAO_LEGIVEL_PREFIX = "[NAO LEGIVEL] - "
PREFIXOS_INVALIDOS = (SEM_MOV_PREFIX, SEM_IMAGEM_PREFIX, NAO_LEGIVEL_PREFIX)
HISTORICO_CONVERSOES = "HISTORICO_CONVERSOES.xlsx"
TIMEZONE_HISTORICO = ZoneInfo("America/Sao_Paulo")
HISTORICO_HEADERS = ("ID", "EMP", "DATA", "HORA", "NOME ARQUIVO", "PASTA DESTINO", "DATA HORA MOVIMENTO", "BANCO")
HISTORICO_HEADERS_SEM_BANCO = ("ID", "EMP", "DATA", "HORA", "NOME ARQUIVO", "PASTA DESTINO", "DATA HORA MOVIMENTO")
HISTORICO_HEADERS_COM_HORA_MOVIMENTO = ("ID", "EMP", "DATA", "HORA", "HORA MOVIMENTO", "NOME ARQUIVO", "PASTA DESTINO")
HISTORICO_HEADERS_SEM_MOVIMENTO = ("ID", "EMP", "DATA", "HORA", "NOME ARQUIVO", "PASTA DESTINO")
HISTORICO_HEADERS_ANTIGO = ("nome_arquivo", "data_hora_conversao", "pasta_destino")
BASE_EMP_ATIVAS = Path.cwd() / "data" / "BaseEmpAtivas.xlsm"
BASE_EMP_ATIVAS_SHEET = "EmpAtivas"
GOOGLE_CHAT_SEND_URL = "https://google-chat-api.onrender.com/google-chat/send"
GOOGLE_CHAT_SPACE_NAME = "spaces/AAQAQEHQc-k"


def agora_historico() -> datetime:
    return datetime.now(TIMEZONE_HISTORICO)


def formatar_mensagem_google_chat(
    nomes_extratos: list[str],
    pdfs_sem_movimentacao: list[str] | None = None,
    pdfs_nao_legiveis: list[str] | None = None,
    atualizados_sge: int | None = None,
    momento: datetime | None = None,
) -> str:
    momento = momento or agora_historico()
    pdfs_sem_movimentacao = pdfs_sem_movimentacao or []
    pdfs_nao_legiveis = pdfs_nao_legiveis or []
    blocos = []

    if nomes_extratos:
        data = momento.strftime("%d/%m/%y")
        hora = momento.strftime("%H:%M")
        lista_extratos = "\n".join(nomes_extratos)
        blocos.append(
            f"Extratos salvos {data} as {hora} e atualizado na Base de Dados:\n\n"
            f"{lista_extratos}"
        )

    if pdfs_sem_movimentacao:
        lista_sem_movimentacao = "\n".join(pdfs_sem_movimentacao)
        blocos.append(
            "PDFs sem movimentacao salvos na pasta da empresa:\n\n"
            f"{lista_sem_movimentacao}"
        )

    if pdfs_nao_legiveis:
        lista_nao_legiveis = "\n".join(pdfs_nao_legiveis)
        blocos.append(
            "PDFs nao legiveis movidos para 00_INVALIDOS:\n\n"
            f"{lista_nao_legiveis}"
        )

    if atualizados_sge is not None and atualizados_sge > 0:
        blocos.append(
            f"Baixa dada no portal SGE: {atualizados_sge} documento(s) atualizado(s)"
        )

    return "\n\n".join(blocos)


def enviar_notificacao_google_chat(
    nomes_extratos: list[str],
    pdfs_sem_movimentacao: list[str] | None = None,
    pdfs_nao_legiveis: list[str] | None = None,
    atualizados_sge: int | None = None,
    momento: datetime | None = None,
) -> bool:
    pdfs_sem_movimentacao = pdfs_sem_movimentacao or []
    pdfs_nao_legiveis = pdfs_nao_legiveis or []

    if not nomes_extratos and not pdfs_sem_movimentacao and not pdfs_nao_legiveis:
        logger.info("Notificacao Google Chat nao enviada: nenhum PDF convertido, sem movimentacao ou nao legivel")
        return False

    mensagem = formatar_mensagem_google_chat(
        nomes_extratos,
        pdfs_sem_movimentacao=pdfs_sem_movimentacao,
        pdfs_nao_legiveis=pdfs_nao_legiveis,
        atualizados_sge=atualizados_sge,
        momento=momento,
    )
    payload = {
        "space_name": GOOGLE_CHAT_SPACE_NAME,
        "message": mensagem,
    }

    try:
        logger.info(
            "Enviando notificacao ao Google Chat com %s extrato(s), %s PDF(s) sem movimentacao e %s PDF(s) nao legivel(is)",
            len(nomes_extratos),
            len(pdfs_sem_movimentacao),
            len(pdfs_nao_legiveis),
        )
        response = requests.post(
            GOOGLE_CHAT_SEND_URL,
            json=payload,
            timeout=30,
        )
        resposta_texto = (response.text or "")[:500]
        logger.info(
            "Resposta Google Chat: status=%s body=%s",
            response.status_code,
            resposta_texto,
        )

        if response.status_code < 200 or response.status_code >= 300:
            logger.error(
                "Falha ao enviar notificacao para o Google Chat: status=%s body=%s",
                response.status_code,
                resposta_texto,
            )
            return False

        logger.info(
            "Notificacao enviada ao Google Chat com %s extrato(s), %s PDF(s) sem movimentacao e %s PDF(s) nao legivel(is)",
            len(nomes_extratos),
            len(pdfs_sem_movimentacao),
            len(pdfs_nao_legiveis),
        )
        return True
    except Exception:
        logger.exception("Erro ao enviar notificacao para o Google Chat")
        return False


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


def normalizar_nome_empresa(valor) -> str:
    return " ".join(str(valor or "").strip().upper().split())


def normalizar_cabecalho_base(valor) -> str:
    texto = normalizar_nome_empresa(valor)
    texto_sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(caractere)
    )

    if "RAZ" in texto_sem_acentos and "SOCIAL" in texto_sem_acentos:
        return "RAZ" + chr(195) + "O SOCIAL"

    return texto_sem_acentos


def extrair_cliente_nome_arquivo(nome_arquivo: str) -> str:
    stem = Path(nome_arquivo).stem
    partes = [parte.strip() for parte in stem.split("_") if parte.strip()]

    for i, parte in enumerate(partes):
        if parte.upper().startswith("AG ") or parte.upper() == "AG":
            return partes[i - 1] if i > 0 else stem.strip()

    return partes[-1] if partes else stem.strip()


def extrair_banco_nome_arquivo(nome_arquivo: str) -> str:
    stem = Path(nome_arquivo).stem
    partes = [parte.strip() for parte in stem.split("_") if parte.strip()]

    if len(partes) < 2:
        return ""

    descricao = re.sub(
        r"^(?:EXT|LANC)BAN\s+",
        "",
        partes[1],
        flags=re.IGNORECASE,
    ).strip()

    if not descricao:
        return ""

    tokens = descricao.split()

    while tokens and re.fullmatch(r"[\d./-]+", tokens[-1]):
        tokens.pop()

    return " ".join(tokens).strip().upper()


def extrair_periodo_nome_arquivo(nome_arquivo: str) -> tuple[str, str]:
    prefixo_periodo = Path(nome_arquivo).stem.split("_", maxsplit=1)[0]
    match = re.match(r"^(?P<mes>\d{2})(?P<ano>\d{2})$", prefixo_periodo)

    if not match:
        raise ValueError(f"Nome do arquivo sem periodo MMAA valido: {nome_arquivo}")

    return match.group("mes"), match.group("ano")


def normalizar_id_empresa(empresa_id) -> str:
    if empresa_id is None:
        return ""

    if isinstance(empresa_id, float) and empresa_id.is_integer():
        return str(int(empresa_id))

    return str(empresa_id).strip()


def extrair_competencia_nome_arquivo(nome_arquivo: str) -> str:
    stem = Path(nome_arquivo).stem
    prefixo = stem.split("_", maxsplit=1)[0]
    match = re.match(r"^(?P<mes>\d{2})(?P<ano>\d{2})$", prefixo)

    if not match:
        raise ValueError(f"Arquivo sem periodo MMAA valido: {nome_arquivo}")

    return f"20{match.group('ano')}-{match.group('mes')}"


def extrair_codigo_documento(nome_arquivo: str) -> str:
    partes = Path(nome_arquivo).stem.split("_")

    if len(partes) < 2:
        return "EXTBAN"

    cod_doc = partes[1].strip().upper().split()[0]
    return cod_doc


def nome_pasta_empresa(empresa_id, empresa_nome: str) -> str:
    empresa_id_normalizado = normalizar_id_empresa(empresa_id)
    empresa_nome_normalizado = normalizar_nome_empresa(empresa_nome)

    if not empresa_id_normalizado or not empresa_nome_normalizado:
        raise ValueError("ID/EMP ausente para resolver pasta da empresa")

    return f"{empresa_id_normalizado}_{empresa_nome_normalizado}"


def resolver_pasta_emp_base(
    google_drive: GoogleDriveAuth,
    pasta_base_id: str,
) -> str:
    logger.info("Localizando pasta EMP dentro da pasta base SRVARQ")
    try:
        pasta_emp = google_drive.get_or_create_folder(
            folder_id_pai=pasta_base_id,
            name_folder="EMP",
        )
    except HttpError as erro:
        if erro.resp.status != 404:
            raise

        logger.warning(
            "Pasta base SRVARQ nao acessivel pelo ID configurado. Tentando localizar por nome."
        )
        pasta_base = (
            google_drive.find_folder_by_name("SRVARQ", shared_with_me=True)
            or google_drive.find_folder_by_name("SRVARQ")
        )

        if not pasta_base:
            raise

        logger.info("Pasta SRVARQ localizada por nome: %s", pasta_base["id"])
        pasta_emp = google_drive.get_or_create_folder(
            folder_id_pai=pasta_base["id"],
            name_folder="EMP",
        )

    return pasta_emp["id"]


def carregar_empresas_ativas(
    base_path: Path = BASE_EMP_ATIVAS,
    sheet_name: str = BASE_EMP_ATIVAS_SHEET,
) -> dict[str, tuple[object, str]]:
    if not base_path.exists():
        logger.warning("Base de empresas ativas nao encontrada: %s", base_path)
        return {}

    workbook = load_workbook(base_path, read_only=True, data_only=True)

    try:
        if sheet_name not in workbook.sheetnames:
            logger.warning("Aba %s nao encontrada na base: %s", sheet_name, base_path)
            return {}

        worksheet = workbook[sheet_name]
        rows = worksheet.iter_rows(values_only=True)
        headers = next(rows, None)

        if not headers:
            logger.warning("Base de empresas ativas sem cabecalho: %s", base_path)
            return {}

        normalized_headers = [
            normalizar_cabecalho_base(header)
            for header in headers
        ]

        try:
            id_index = normalized_headers.index("ID")
            emp_index = normalized_headers.index("RAZÃO SOCIAL")
        except ValueError:
            logger.warning("Base de empresas ativas sem colunas ID/Razao social: %s", base_path)
            return {}

        empresas = {}

        for row in rows:
            if not row:
                continue

            empresa = row[emp_index] if emp_index < len(row) else None
            empresa_normalizada = normalizar_nome_empresa(empresa)

            if not empresa_normalizada:
                continue

            empresa_id = row[id_index] if id_index < len(row) else ""
            empresas[empresa_normalizada] = (empresa_id or "", str(empresa).strip())

        return empresas
    finally:
        workbook.close()


def buscar_empresa_por_cliente(
    cliente: str,
    empresas: dict[str, tuple[object, str]] | None = None,
) -> tuple[object, str]:
    empresas = empresas if empresas is not None else carregar_empresas_ativas()
    cliente_normalizado = normalizar_nome_empresa(cliente)
    empresa = empresas.get(cliente_normalizado)

    if not empresa:
        logger.warning("Cliente nao encontrado na BaseEmpAtivas: %s", cliente)
        return "", ""

    return empresa


def resolver_pasta_destino_emp(
    google_drive: GoogleDriveAuth,
    pasta_emp_id: str,
    arquivo_nome: str,
    empresa_id,
    empresa_nome: str,
) -> tuple[str, str]:
    mes, ano = extrair_periodo_nome_arquivo(arquivo_nome)
    nome_cliente = nome_pasta_empresa(empresa_id, empresa_nome)
    caminho_partes = ["EMP", nome_cliente, "MOV", "CONT", ano, mes, "EXT"]
    pasta_atual_id = pasta_emp_id

    for nome_pasta in caminho_partes[1:]:
        pasta_atual = google_drive.get_or_create_folder(
            folder_id_pai=pasta_atual_id,
            name_folder=nome_pasta,
        )
        pasta_atual_id = pasta_atual["id"]

    return pasta_atual_id, "/".join(caminho_partes)


def separar_data_hora_historico(valor) -> tuple[str, str]:
    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d"), valor.strftime("%H:%M:%S")

    texto = str(valor or "").strip()
    if not texto:
        return "", ""

    partes = texto.split(maxsplit=1)
    data = partes[0]
    hora = partes[1] if len(partes) > 1 else ""
    return data, hora


def preencher_banco_historico(worksheet) -> None:
    headers = tuple(
        cell.value
        for cell in worksheet[1]
    ) if worksheet.max_row else ()

    if "NOME ARQUIVO" not in headers or "BANCO" not in headers:
        return

    nome_arquivo_coluna = headers.index("NOME ARQUIVO") + 1
    banco_coluna = headers.index("BANCO") + 1

    for row_index in range(2, worksheet.max_row + 1):
        banco_cell = worksheet.cell(row=row_index, column=banco_coluna)

        if banco_cell.value:
            continue

        nome_arquivo = worksheet.cell(row=row_index, column=nome_arquivo_coluna).value
        banco = extrair_banco_nome_arquivo(nome_arquivo or "")

        if banco:
            banco_cell.value = banco


def migrar_historico_antigo(worksheet) -> None:
    headers = tuple(
        cell.value
        for cell in worksheet[1]
    ) if worksheet.max_row else ()

    if headers == HISTORICO_HEADERS:
        preencher_banco_historico(worksheet)
        return

    linhas_antigas = list(worksheet.iter_rows(min_row=2, values_only=True))
    worksheet.delete_rows(1, worksheet.max_row)
    worksheet.append(HISTORICO_HEADERS)

    if headers == HISTORICO_HEADERS_SEM_BANCO:
        for empresa_id, empresa_nome, data, hora, nome_arquivo, pasta_destino, data_hora_movimento in linhas_antigas:
            worksheet.append(
                [
                    empresa_id or "",
                    empresa_nome or "",
                    data or "",
                    hora or "",
                    nome_arquivo or "",
                    pasta_destino or "",
                    data_hora_movimento or "",
                    extrair_banco_nome_arquivo(nome_arquivo or ""),
                ]
            )
        return

    if headers == HISTORICO_HEADERS_COM_HORA_MOVIMENTO:
        for empresa_id, empresa_nome, data, hora, hora_movimento, nome_arquivo, pasta_destino in linhas_antigas:
            data_hora_movimento = ""
            if data and hora_movimento:
                data_hora_movimento = f"{data} {hora_movimento}"

            worksheet.append(
                [
                    empresa_id or "",
                    empresa_nome or "",
                    data or "",
                    hora or "",
                    nome_arquivo or "",
                    pasta_destino or "",
                    data_hora_movimento,
                    extrair_banco_nome_arquivo(nome_arquivo or ""),
                ]
            )
        return

    if headers == HISTORICO_HEADERS_SEM_MOVIMENTO:
        for empresa_id, empresa_nome, data, hora, nome_arquivo, pasta_destino in linhas_antigas:
            worksheet.append(
                [
                    empresa_id or "",
                    empresa_nome or "",
                    data or "",
                    hora or "",
                    nome_arquivo or "",
                    pasta_destino or "",
                    "",
                    extrair_banco_nome_arquivo(nome_arquivo or ""),
                ]
            )
        return

    if headers != HISTORICO_HEADERS_ANTIGO:
        return

    for nome_arquivo, data_hora_conversao, pasta_destino in linhas_antigas:
        data, hora = separar_data_hora_historico(data_hora_conversao)
        worksheet.append(
            [
                "",
                "",
                data,
                hora,
                nome_arquivo or "",
                pasta_destino or "",
                "",
                extrair_banco_nome_arquivo(nome_arquivo or ""),
            ]
        )


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


def registrar_historico_conversao(
    google_drive: GoogleDriveAuth,
    pasta_raiz_id: str,
    temp_dir: Path,
    nomes_arquivos: list[str],
    pasta_destino: str,
    data_hora: datetime | None = None,
    data_hora_movimento: datetime | None = None,
    empresa_id=None,
    empresa_nome: str | None = None,
    empresas: dict[str, tuple[object, str]] | None = None,
) -> None:
    temp_dir.mkdir(parents=True, exist_ok=True)
    historico_local = temp_dir / HISTORICO_CONVERSOES
    arquivo_historico = google_drive.find_file_by_name(
        folder_id=pasta_raiz_id,
        name=HISTORICO_CONVERSOES,
        mime_type=XLSX_MIME_TYPE,
    )

    if arquivo_historico:
        logger.info("Baixando historico de conversoes existente")
        google_drive.download(
            file_id=arquivo_historico["id"],
            destino_local=historico_local,
        )
        workbook = load_workbook(historico_local)
        worksheet = workbook.active
        migrar_historico_antigo(worksheet)
    else:
        logger.info("Criando historico de conversoes")
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "historico"
        worksheet.append(HISTORICO_HEADERS)

    momento = data_hora or agora_historico()
    momento_movimento = data_hora_movimento or momento
    data_formatada = momento.strftime("%Y-%m-%d")
    hora_formatada = momento.strftime("%H:%M:%S")
    data_hora_movimento_formatada = momento_movimento.strftime("%Y-%m-%d %H:%M:%S")
    if empresa_id is None or empresa_nome is None:
        cliente = extrair_cliente_nome_arquivo(nomes_arquivos[0]) if nomes_arquivos else ""
        empresa_id, empresa_nome = buscar_empresa_por_cliente(cliente, empresas=empresas)

    for nome_arquivo in nomes_arquivos:
        worksheet.append(
            [
                empresa_id,
                empresa_nome,
                data_formatada,
                hora_formatada,
                nome_arquivo,
                pasta_destino,
                data_hora_movimento_formatada,
                extrair_banco_nome_arquivo(nome_arquivo),
            ]
        )

    workbook.save(historico_local)

    if arquivo_historico:
        google_drive.update_file(
            file_id=arquivo_historico["id"],
            caminho_local=historico_local,
            type_file=XLSX_MIME_TYPE,
            name_drive=HISTORICO_CONVERSOES,
        )
        logger.info("Historico de conversoes atualizado: %s", HISTORICO_CONVERSOES)
    else:
        google_drive.upload(
            caminho_local=historico_local,
            folder_id_destino=pasta_raiz_id,
            type_file=XLSX_MIME_TYPE,
            name_drive=HISTORICO_CONVERSOES,
        )
        logger.info("Historico de conversoes criado: %s", HISTORICO_CONVERSOES)

    historico_local.unlink(missing_ok=True)


def arquivar_pdf_sem_movimentacao(
    google_drive: GoogleDriveAuth,
    pasta_raiz_id: str,
    pasta_emp_id: str,
    temp_dir: Path,
    arquivo_id: str,
    nome_arquivo: str,
    empresas: dict[str, tuple[object, str]] | None = None,
) -> str | None:
    cliente = extrair_cliente_nome_arquivo(nome_arquivo)
    empresa_id, empresa_nome = buscar_empresa_por_cliente(cliente, empresas=empresas)

    try:
        pasta_destino_id, pasta_destino_historico = resolver_pasta_destino_emp(
            google_drive=google_drive,
            pasta_emp_id=pasta_emp_id,
            arquivo_nome=nome_arquivo,
            empresa_id=empresa_id,
            empresa_nome=empresa_nome,
        )
    except ValueError:
        logger.exception(
            "Nao foi possivel resolver pasta EMP para PDF sem movimentacao. Arquivo mantido na pasta EXT: %s",
            nome_arquivo,
        )
        return None

    momento = agora_historico()
    logger.info("Movendo PDF sem movimentacao para pasta destino EMP: %s", nome_arquivo)
    google_drive.move_file(
        file_id=arquivo_id,
        folder_id_destino=pasta_destino_id,
    )
    momento_movimento = agora_historico()

    registrar_historico_conversao(
        google_drive=google_drive,
        pasta_raiz_id=pasta_raiz_id,
        temp_dir=temp_dir,
        nomes_arquivos=[nome_arquivo],
        pasta_destino=pasta_destino_historico,
        data_hora=momento,
        data_hora_movimento=momento_movimento,
        empresa_id=empresa_id,
        empresa_nome=empresa_nome,
        empresas=empresas,
    )
    logger.info(
        "PDF sem movimentacao salvo na pasta da empresa: %s -> %s",
        nome_arquivo,
        pasta_destino_historico,
    )
    return f"{nome_arquivo} - {pasta_destino_historico}"


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
    emp_id = GOOGLE_DRIVE_EMP_FOLDER_ID
    lancamento = Path.cwd() / "data" / "Lancamentos_Contabeis.xlsm"
    temp_dir = Path.cwd() / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Diretorio temporario preparado: %s", temp_dir)
    empresas = carregar_empresas_ativas()
    emp_raiz_id = resolver_pasta_emp_base(
        google_drive=google_drive,
        pasta_base_id=emp_id,
    )
    logger.info("Pasta raiz operacional do Google Drive: %s", extratos_id)

    logger.info("Criando ou recuperando pasta de entrada EXT no Google Drive")
    pasta_entrada_ext = google_drive.get_or_create_folder(
        folder_id_pai=extratos_id,
        name_folder="EXT",
    )
    entrada_ext_id = pasta_entrada_ext["id"]
    logger.info("Pasta de entrada EXT do Google Drive: %s", entrada_ext_id)

    logger.info("Criando ou recuperando pasta de invalidos no Google Drive")
    pasta_invalidos = google_drive.get_or_create_folder(
        folder_id_pai=extratos_id,
        name_folder="00_INVALIDOS",
    )

    logger.info("Criando ou recuperando pasta de PDFs com senha no Google Drive")
    pasta_pdfs_com_senhas = google_drive.get_or_create_folder(
        folder_id_pai=extratos_id,
        name_folder="PDFS_COM_SENHAS",
    )

    invalidos_id = pasta_invalidos["id"]
    pdfs_com_senhas_id = pasta_pdfs_com_senhas["id"]

    logger.info("Listando PDFs da pasta EXT do Google Drive")
    arquivos = google_drive.pdfs(
        folder_id=entrada_ext_id,
        pdf_type=PDF_MIME_TYPE,
    )
    logger.info("PDFs encontrados na pasta EXT do Google Drive: %s", len(arquivos))

    if not arquivos:
        logger.info("Pasta EXT vazia")

    processados = 0
    convertidos = 0
    invalidos = 0
    ignorados = 0
    com_senha = 0
    sem_movimentacao = 0
    extratos_notificacao = []
    pdfs_sem_movimentacao_notificacao = []
    pdfs_nao_legiveis_notificacao = []
    documentos_convertidos = []

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
                linha_sem_movimentacao = arquivar_pdf_sem_movimentacao(
                    google_drive=google_drive,
                    pasta_raiz_id=extratos_id,
                    pasta_emp_id=emp_raiz_id,
                    temp_dir=temp_dir,
                    arquivo_id=arquivo_id,
                    nome_arquivo=arquivo_nome,
                    empresas=empresas,
                )
                if linha_sem_movimentacao:
                    pdfs_sem_movimentacao_notificacao.append(linha_sem_movimentacao)
                    sem_movimentacao += 1
                continue

            logger.info("Extraindo conteudo do PDF: %s", arquivo_nome)
            pdf = PDFExtractor(pdf_local).extract()

            if not pdf:
                nome_nao_legivel = renomear_e_mover_para_invalidos(
                    google_drive=google_drive,
                    arquivo_id=arquivo_id,
                    arquivo_nome=arquivo_nome,
                    invalidos_id=invalidos_id,
                    prefixo=NAO_LEGIVEL_PREFIX,
                    motivo="PDF sem texto extraivel, nao legivel ou possivelmente imagem",
                )
                pdfs_nao_legiveis_notificacao.append(nome_nao_legivel)
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
                linha_sem_movimentacao = arquivar_pdf_sem_movimentacao(
                    google_drive=google_drive,
                    pasta_raiz_id=extratos_id,
                    pasta_emp_id=emp_raiz_id,
                    temp_dir=temp_dir,
                    arquivo_id=arquivo_id,
                    nome_arquivo=arquivo_nome,
                    empresas=empresas,
                )
                if linha_sem_movimentacao:
                    pdfs_sem_movimentacao_notificacao.append(linha_sem_movimentacao)
                    sem_movimentacao += 1
                continue

            cliente = extrair_cliente_nome_arquivo(arquivo_nome)
            empresa_id, empresa_nome = buscar_empresa_por_cliente(cliente, empresas=empresas)

            try:
                pasta_destino_id, pasta_destino_historico = resolver_pasta_destino_emp(
                    google_drive=google_drive,
                    pasta_emp_id=emp_raiz_id,
                    arquivo_nome=arquivo_nome,
                    empresa_id=empresa_id,
                    empresa_nome=empresa_nome,
                )
            except ValueError:
                logger.exception(
                    "Nao foi possivel resolver pasta EMP para o PDF. Movendo para invalidos: %s",
                    arquivo_nome,
                )
                google_drive.move_file(
                    file_id=arquivo_id,
                    folder_id_destino=invalidos_id,
                )
                invalidos += 1
                continue

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
            momento_conversao = agora_historico()

            logger.info("Enviando XLSM para o Google Drive: %s", dest_lancamento.name)
            google_drive.upload(
                caminho_local=dest_lancamento,
                folder_id_destino=pasta_destino_id,
                type_file=XLSM_MIME_TYPE,
                name_drive=dest_lancamento.name,
            )

            logger.info("Enviando XLSX para o Google Drive: %s", dest_excel.name)
            google_drive.upload(
                caminho_local=dest_excel,
                folder_id_destino=pasta_destino_id,
                type_file=XLSX_MIME_TYPE,
                name_drive=dest_excel.name,
            )

            logger.info("Movendo PDF original para pasta destino EMP: %s", arquivo_nome)
            google_drive.move_file(
                file_id=arquivo_id,
                folder_id_destino=pasta_destino_id,
            )
            momento_movimento = agora_historico()

            registrar_historico_conversao(
                google_drive=google_drive,
                pasta_raiz_id=extratos_id,
                temp_dir=temp_dir,
                nomes_arquivos=[arquivo_nome, dest_excel.name, dest_lancamento.name],
                pasta_destino=pasta_destino_historico,
                data_hora=momento_conversao,
                data_hora_movimento=momento_movimento,
                empresa_id=empresa_id,
                empresa_nome=empresa_nome,
            )

            documentos_convertidos.append({
                "empresa_id": empresa_id,
                "mes": extrair_periodo_nome_arquivo(arquivo_nome)[0],
                "ano": extrair_periodo_nome_arquivo(arquivo_nome)[1],
                "arquivo_nome": arquivo_nome,
                "pasta_destino": pasta_destino_historico,
                "momento": momento_conversao,
            })

            convertidos += 1
            extratos_notificacao.append(arquivo_stem)
            logger.info("PDF convertido com sucesso: %s", arquivo_nome)
            logger.info("Arquivos enviados para a pasta: %s", pasta_destino_historico)
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

    logger.info("Atualizando controle no portal SGE para %s documento(s)", len(documentos_convertidos))
    atualizados_sge = 0
    erros_sge = 0

    for doc in documentos_convertidos:
        try:
            competencia = extrair_competencia_nome_arquivo(doc["arquivo_nome"])
            cod_doc = extrair_codigo_documento(doc["arquivo_nome"])
            local = f"Google Drive / {doc['pasta_destino']}"
            sucesso = atualizar_controle_supabase(
                empresa_codigo=str(doc["empresa_id"]),
                competencia=competencia,
                codigo_documento=cod_doc,
                data_recebimento=doc["momento"].strftime("%Y-%m-%d"),
                quantidade_arquivos=3,
                nome_arquivo=doc["arquivo_nome"],
                local_arquivo=local,
            )
            if sucesso:
                atualizados_sge += 1
            else:
                erros_sge += 1
        except Exception:
            erros_sge += 1
            logger.exception("Erro ao atualizar controle no SGE: %s", doc["arquivo_nome"])

    enviar_notificacao_google_chat(
        extratos_notificacao,
        pdfs_sem_movimentacao=pdfs_sem_movimentacao_notificacao,
        pdfs_nao_legiveis=pdfs_nao_legiveis_notificacao,
        atualizados_sge=atualizados_sge,
    )

    logger.info(
        (
            "Fluxo de conversao concluido. "
            "Processados=%s Convertidos=%s SemMovimentacao=%s Invalidos=%s Ignorados=%s ComSenha=%s "
            "Desbloqueados=%s SenhasIgnoradas=%s SenhasErros=%s "
            "AtualizadosSGE=%s ErrosSGE=%s"
        ),
        processados,
        convertidos,
        sem_movimentacao,
        invalidos,
        ignorados,
        com_senha,
        resultado_senhas["desbloqueados"],
        resultado_senhas["ignorados"],
        resultado_senhas["erros"],
        atualizados_sge,
        erros_sge,
    )


def main():
    executar_conversao()


if __name__ == "__main__":
    main()
