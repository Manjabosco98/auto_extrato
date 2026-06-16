import logging
import re
from datetime import datetime
from pathlib import Path

import requests
from openpyxl import Workbook, load_workbook

from src.app.gdrive.google_drive_auth import GoogleDriveAuth
from src.app.gdrive.settings import (
    GOOGLE_DRIVE_EMP_FOLDER_ID,
    GOOGLE_DRIVE_FOLDER_ID,
    GOOGLE_OAUTH_CREDENTIALS,
    GOOGLE_OAUTH_TOKEN,
    GOOGLE_OAUTH_TOKEN_SECRET,
    XLSX_MIME_TYPE,
)
from src.services.conversao import (
    BASE_EMP_ATIVAS,
    GOOGLE_CHAT_SEND_URL,
    GOOGLE_CHAT_SPACE_NAME,
    agora_historico,
    buscar_empresa_por_cliente,
    carregar_empresas_ativas,
    nome_pasta_empresa,
    normalizar_id_empresa,
    normalizar_nome_empresa,
    resolver_pasta_emp_base,
)
from src.utils.logging_config import setup_logging


logger = logging.getLogger(__name__)

DOCS_FOLDER_NAME = "DOCS"
DOCS_CAMINHO_NAME = "DOCS E CAMINHO.xlsx"
HISTORICO_DOCS = "HISTORICO_DOCS.xlsx"
HISTORICO_DOCS_HEADERS = (
    "ID",
    "EMP",
    "DATA",
    "HORA",
    "CODIGO",
    "NOME ARQUIVO",
    "PASTA DESTINO",
    "DATA HORA MOVIMENTO",
)


def normalizar_codigo(valor) -> str:
    if valor is None:
        return ""

    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor)).strip().upper()

    return str(valor).strip().upper()


def parse_nome_documento(nome_arquivo: str) -> dict[str, str]:
    caminho = Path(nome_arquivo)
    partes = [parte.strip() for parte in caminho.stem.split("_") if parte.strip()]

    if len(partes) < 3:
        raise ValueError(f"Nome do arquivo DOCS sem padrao MMAA_CODIGO_CLIENTE: {nome_arquivo}")

    periodo = partes[0]
    if not re.fullmatch(r"\d{4}", periodo):
        raise ValueError(f"Nome do arquivo DOCS sem periodo MMAA valido: {nome_arquivo}")

    return {
        "mes": periodo[:2],
        "ano": periodo[-2:],
        "codigo": normalizar_codigo(partes[1]),
        "cliente": partes[-1],
    }


def carregar_caminhos_docs(caminhos_path: Path) -> dict[str, str]:
    workbook = load_workbook(caminhos_path, read_only=True, data_only=True)

    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows(values_only=True)
        headers = next(rows, None)

        if not headers:
            logger.warning("Arquivo de caminhos DOCS sem cabecalho: %s", caminhos_path)
            return {}

        headers_normalizados = [str(header or "").strip() for header in headers]

        try:
            codigo_index = headers_normalizados.index("Codigo")
            destino_index = headers_normalizados.index("destino_final")
        except ValueError:
            logger.warning(
                "Arquivo de caminhos DOCS sem colunas Codigo/destino_final: %s",
                caminhos_path,
            )
            return {}

        caminhos = {}
        for row in rows:
            if not row:
                continue

            codigo = row[codigo_index] if codigo_index < len(row) else None
            destino = row[destino_index] if destino_index < len(row) else None
            codigo_normalizado = normalizar_codigo(codigo)
            destino_texto = str(destino or "").strip()

            if codigo_normalizado and destino_texto:
                caminhos[codigo_normalizado] = destino_texto

        return caminhos
    finally:
        workbook.close()


def montar_destino_docs(
    destino_template: str,
    empresa_chave: str,
    ano: str,
    mes: str,
) -> list[str]:
    destino = (
        destino_template
        .replace("{EMPRESA}", empresa_chave)
        .replace("{ANO}", ano)
        .replace("{MES}", mes)
        .replace("{MÊS}", mes)
    )
    destino = destino.replace("\\", "/").strip("/")
    partes = [parte for parte in destino.split("/") if parte]

    if len(partes) >= 2 and partes[0].upper() == "SRVARQ" and partes[1].upper() == "EMP":
        partes = partes[2:]
    elif partes and partes[0].upper() == "EMP":
        partes = partes[1:]

    return partes


def resolver_pasta_destino_docs(
    google_drive: GoogleDriveAuth,
    emp_folder_id: str,
    destino_partes: list[str],
) -> tuple[str, str]:
    pasta_atual_id = emp_folder_id

    for nome_pasta in destino_partes:
        pasta = google_drive.get_or_create_folder(
            folder_id_pai=pasta_atual_id,
            name_folder=nome_pasta,
        )
        pasta_atual_id = pasta["id"]

    return pasta_atual_id, "/".join(["EMP", *destino_partes])


def registrar_historico_docs(
    google_drive: GoogleDriveAuth,
    pasta_raiz_id: str,
    temp_dir: Path,
    registros: list[dict[str, object]],
) -> None:
    if not registros:
        logger.info("Historico DOCS nao atualizado: nenhum documento movido")
        return

    temp_dir.mkdir(parents=True, exist_ok=True)
    historico_local = temp_dir / HISTORICO_DOCS
    arquivo_historico = google_drive.find_file_by_name(
        folder_id=pasta_raiz_id,
        name=HISTORICO_DOCS,
        mime_type=XLSX_MIME_TYPE,
    )

    if arquivo_historico:
        logger.info("Baixando historico DOCS existente")
        google_drive.download(
            file_id=arquivo_historico["id"],
            destino_local=historico_local,
        )
        workbook = load_workbook(historico_local)
        worksheet = workbook.active
        headers = tuple(cell.value for cell in worksheet[1]) if worksheet.max_row else ()

        if headers != HISTORICO_DOCS_HEADERS:
            linhas_antigas = list(worksheet.iter_rows(min_row=2, values_only=True))
            worksheet.delete_rows(1, worksheet.max_row)
            worksheet.append(HISTORICO_DOCS_HEADERS)
            for linha in linhas_antigas:
                valores = list(linha or [])
                worksheet.append((valores + [""] * len(HISTORICO_DOCS_HEADERS))[:len(HISTORICO_DOCS_HEADERS)])
    else:
        logger.info("Criando historico DOCS")
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "historico"
        worksheet.append(HISTORICO_DOCS_HEADERS)

    for registro in registros:
        worksheet.append(
            [
                registro["empresa_id"],
                registro["empresa_nome"],
                registro["data"],
                registro["hora"],
                registro["codigo"],
                registro["nome_arquivo"],
                registro["pasta_destino"],
                registro["data_hora_movimento"],
            ]
        )

    workbook.save(historico_local)

    if arquivo_historico:
        google_drive.update_file(
            file_id=arquivo_historico["id"],
            caminho_local=historico_local,
            type_file=XLSX_MIME_TYPE,
            name_drive=HISTORICO_DOCS,
        )
        logger.info("Historico DOCS atualizado: %s", HISTORICO_DOCS)
    else:
        google_drive.upload(
            caminho_local=historico_local,
            folder_id_destino=pasta_raiz_id,
            type_file=XLSX_MIME_TYPE,
            name_drive=HISTORICO_DOCS,
        )
        logger.info("Historico DOCS criado: %s", HISTORICO_DOCS)

    historico_local.unlink(missing_ok=True)


def formatar_mensagem_docs_google_chat(
    nomes_documentos: list[str],
    momento: datetime | None = None,
) -> str:
    momento = momento or agora_historico()
    data = momento.strftime("%d/%m/%y")
    hora = momento.strftime("%H:%M")
    lista_documentos = "\n".join(nomes_documentos)
    return (
        f"Documentos salvos {data} as {hora} e atualizado na Base de Dados:\n\n"
        f"{lista_documentos}"
    )


def enviar_notificacao_docs_google_chat(
    nomes_documentos: list[str],
    momento: datetime | None = None,
) -> bool:
    if not nomes_documentos:
        logger.info("Notificacao Google Chat DOCS nao enviada: nenhum documento movido")
        return False

    mensagem = formatar_mensagem_docs_google_chat(nomes_documentos, momento=momento)
    payload = {
        "space_name": GOOGLE_CHAT_SPACE_NAME,
        "message": mensagem,
    }

    try:
        logger.info("Enviando notificacao DOCS ao Google Chat com %s documento(s)", len(nomes_documentos))
        response = requests.post(
            GOOGLE_CHAT_SEND_URL,
            json=payload,
            timeout=30,
        )
        resposta_texto = (response.text or "")[:500]
        logger.info(
            "Resposta Google Chat DOCS: status=%s body=%s",
            response.status_code,
            resposta_texto,
        )

        if response.status_code < 200 or response.status_code >= 300:
            logger.error(
                "Falha ao enviar notificacao DOCS para o Google Chat: status=%s body=%s",
                response.status_code,
                resposta_texto,
            )
            return False

        logger.info("Notificacao DOCS enviada ao Google Chat com %s documento(s)", len(nomes_documentos))
        return True
    except Exception:
        logger.exception("Erro ao enviar notificacao DOCS para o Google Chat")
        return False


def _mime_type_historico(arquivo: dict) -> bool:
    return arquivo.get("name") == HISTORICO_DOCS


def executar_docs() -> dict[str, int]:
    setup_logging()
    logger.info("Iniciando fluxo DOCS")
    logger.info("Autenticando Google Drive")

    google_drive = GoogleDriveAuth(
        credentials_path=GOOGLE_OAUTH_CREDENTIALS,
        token_path=GOOGLE_OAUTH_TOKEN,
        token_secret_path=GOOGLE_OAUTH_TOKEN_SECRET,
    )

    raiz_id = GOOGLE_DRIVE_FOLDER_ID
    temp_dir = Path.cwd() / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Diretorio temporario preparado: %s", temp_dir)
    logger.info("Pasta raiz operacional DOCS: %s", raiz_id)

    docs_folder = google_drive.get_or_create_folder(
        folder_id_pai=raiz_id,
        name_folder=DOCS_FOLDER_NAME,
    )
    docs_folder_id = docs_folder["id"]
    logger.info("Pasta DOCS localizada: %s", docs_folder_id)

    logger.info("Localizando pasta EMP dentro da pasta base SRVARQ para DOCS")
    emp_raiz_id = resolver_pasta_emp_base(
        google_drive=google_drive,
        pasta_base_id=GOOGLE_DRIVE_EMP_FOLDER_ID,
    )

    caminhos_drive = (
        google_drive.find_file_by_name(
            folder_id=raiz_id,
            name=DOCS_CAMINHO_NAME,
            mime_type=XLSX_MIME_TYPE,
        )
        or google_drive.find_file_by_name(
            folder_id=raiz_id,
            name=DOCS_CAMINHO_NAME,
        )
    )

    if not caminhos_drive:
        logger.warning("Arquivo de caminhos DOCS nao encontrado na raiz: %s", DOCS_CAMINHO_NAME)
        return {"processados": 0, "movidos": 0, "ignorados": 0, "erros": 1}

    caminhos_local = temp_dir / DOCS_CAMINHO_NAME
    try:
        google_drive.download(
            file_id=caminhos_drive["id"],
            destino_local=caminhos_local,
        )
        caminhos = carregar_caminhos_docs(caminhos_local)
    finally:
        caminhos_local.unlink(missing_ok=True)

    empresas = carregar_empresas_ativas(BASE_EMP_ATIVAS)
    arquivos = google_drive.list_children(docs_folder_id)
    arquivos = [
        arquivo
        for arquivo in arquivos
        if arquivo.get("mimeType") != GoogleDriveAuth.FOLDER_MIME_TYPE
        and arquivo.get("name", "").lower() != "desktop.ini"
        and not _mime_type_historico(arquivo)
    ]
    logger.info("Arquivos encontrados na pasta DOCS: %s", len(arquivos))

    processados = 0
    movidos = 0
    ignorados = 0
    erros = 0
    registros_historico: list[dict[str, object]] = []
    documentos_notificacao: list[str] = []

    for arquivo in arquivos:
        arquivo_id = arquivo["id"]
        arquivo_nome = arquivo["name"]
        processados += 1
        logger.info("Processando documento DOCS: %s", arquivo_nome)

        try:
            dados_nome = parse_nome_documento(arquivo_nome)
        except ValueError as erro:
            ignorados += 1
            logger.warning("%s", erro)
            continue

        codigo = dados_nome["codigo"]
        destino_template = caminhos.get(codigo)

        if not destino_template:
            ignorados += 1
            logger.warning("Codigo DOCS nao encontrado em %s: %s", DOCS_CAMINHO_NAME, codigo)
            continue

        empresa_id, empresa_nome = buscar_empresa_por_cliente(
            dados_nome["cliente"],
            empresas=empresas,
        )

        if not empresa_id or not empresa_nome:
            ignorados += 1
            logger.warning(
                "Empresa DOCS nao encontrada para cliente %s. Arquivo mantido em DOCS: %s",
                dados_nome["cliente"],
                arquivo_nome,
            )
            continue

        try:
            empresa_chave = nome_pasta_empresa(empresa_id, empresa_nome)
            destino_partes = montar_destino_docs(
                destino_template=destino_template,
                empresa_chave=empresa_chave,
                ano=dados_nome["ano"],
                mes=dados_nome["mes"],
            )
            pasta_destino_id, pasta_destino_historico = resolver_pasta_destino_docs(
                google_drive=google_drive,
                emp_folder_id=emp_raiz_id,
                destino_partes=destino_partes,
            )

            logger.info("Movendo documento DOCS para: %s", pasta_destino_historico)
            google_drive.move_file(
                file_id=arquivo_id,
                folder_id_destino=pasta_destino_id,
            )
            momento_movimento = agora_historico()
            registros_historico.append(
                {
                    "empresa_id": normalizar_id_empresa(empresa_id),
                    "empresa_nome": normalizar_nome_empresa(empresa_nome),
                    "data": momento_movimento.strftime("%Y-%m-%d"),
                    "hora": momento_movimento.strftime("%H:%M:%S"),
                    "codigo": codigo,
                    "nome_arquivo": arquivo_nome,
                    "pasta_destino": pasta_destino_historico,
                    "data_hora_movimento": momento_movimento.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            documentos_notificacao.append(f"{arquivo_nome} - {pasta_destino_historico}")
            movidos += 1
        except Exception:
            erros += 1
            logger.exception("Erro ao mover documento DOCS: %s", arquivo_nome)

    registrar_historico_docs(
        google_drive=google_drive,
        pasta_raiz_id=raiz_id,
        temp_dir=temp_dir,
        registros=registros_historico,
    )
    enviar_notificacao_docs_google_chat(documentos_notificacao)

    logger.info(
        "Fluxo DOCS concluido. Processados=%s Movidos=%s Ignorados=%s Erros=%s",
        processados,
        movidos,
        ignorados,
        erros,
    )

    return {
        "processados": processados,
        "movidos": movidos,
        "ignorados": ignorados,
        "erros": erros,
    }


def main():
    executar_docs()


if __name__ == "__main__":
    main()
