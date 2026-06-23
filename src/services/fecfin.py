import logging
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from src.app.gdrive.google_drive_auth import GoogleDriveAuth
from src.app.gdrive.settings import (
    GOOGLE_DRIVE_FOLDER_ID,
    GOOGLE_OAUTH_CREDENTIALS,
    GOOGLE_OAUTH_TOKEN,
    GOOGLE_OAUTH_TOKEN_SECRET,
    XLSM_MIME_TYPE,
    XLS_MIME_TYPE,
    XLSX_MIME_TYPE,
)
from src.schemas.fecfin.registry import dispatch_fecwin
from src.services.conversao import (
    GOOGLE_CHAT_SEND_URL,
    GOOGLE_CHAT_SPACE_NAME,
    MODELO_LANCAMENTOS,
    agora_historico,
    resolver_pasta_emp_base,
)
from src.utils.helpers import planilha_lancamento
from src.utils.logging_config import setup_logging


logger = logging.getLogger(__name__)


def formatar_mensagem_fecfin_google_chat(
    arquivos_processados: list[str],
    arquivos_nao_reconhecidos: list[str] | None = None,
    momento: datetime | None = None,
) -> str:
    momento = momento or agora_historico()
    arquivos_nao_reconhecidos = arquivos_nao_reconhecidos or []
    blocos = []

    data = momento.strftime("%d/%m/%y")
    hora = momento.strftime("%H:%M")

    if arquivos_processados:
        lista = "\n".join(arquivos_processados)
        blocos.append(
            f"FECFIN processados {data} as {hora}:\n\n{lista}"
        )

    if arquivos_nao_reconhecidos:
        lista = "\n".join(arquivos_nao_reconhecidos)
        blocos.append(
            "Arquivos FECFIN sem layout reconhecido:\n\n"
            f"{lista}"
        )

    return "\n\n".join(blocos)


def enviar_notificacao_fecfin_google_chat(
    arquivos_processados: list[str],
    arquivos_nao_reconhecidos: list[str] | None = None,
    momento: datetime | None = None,
) -> bool:
    if not arquivos_processados and not arquivos_nao_reconhecidos:
        logger.info("Notificacao Google Chat FECFIN nao enviada: nenhum arquivo processado")
        return False

    mensagem = formatar_mensagem_fecfin_google_chat(
        arquivos_processados,
        arquivos_nao_reconhecidos=arquivos_nao_reconhecidos,
        momento=momento,
    )

    payload = {
        "space_name": GOOGLE_CHAT_SPACE_NAME,
        "message": mensagem,
    }

    try:
        logger.info(
            "Enviando notificacao FECFIN ao Google Chat com %s arquivo(s)",
            len(arquivos_processados),
        )
        response = requests.post(
            GOOGLE_CHAT_SEND_URL,
            json=payload,
            timeout=30,
        )
        resposta_texto = (response.text or "")[:500]
        logger.info(
            "Resposta Google Chat FECFIN: status=%s body=%s",
            response.status_code,
            resposta_texto,
        )

        if response.status_code < 200 or response.status_code >= 300:
            logger.error(
                "Falha ao enviar notificacao FECFIN para o Google Chat: status=%s body=%s",
                response.status_code,
                resposta_texto,
            )
            return False

        logger.info(
            "Notificacao FECFIN enviada ao Google Chat com %s arquivo(s)",
            len(arquivos_processados),
        )
        return True
    except Exception:
        logger.exception("Erro ao enviar notificacao FECFIN para o Google Chat")
        return False


def executar_fecfin() -> dict[str, int]:
    setup_logging()
    logger.info("Iniciando fluxo FECFIN")
    logger.info("Autenticando Google Drive")

    google_drive = GoogleDriveAuth(
        credentials_path=GOOGLE_OAUTH_CREDENTIALS,
        token_path=GOOGLE_OAUTH_TOKEN,
        token_secret_path=GOOGLE_OAUTH_TOKEN_SECRET,
    )

    extratos_id = GOOGLE_DRIVE_FOLDER_ID
    lancamento = MODELO_LANCAMENTOS
    temp_dir = Path.cwd() / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Diretorio temporario preparado: %s", temp_dir)

    emp_raiz_id = resolver_pasta_emp_base(
        google_drive=google_drive,
        pasta_base_id=extratos_id,
    )

    logger.info("Criando ou recuperando pasta de entrada EXT no Google Drive")
    pasta_entrada_ext = google_drive.get_or_create_folder(
        folder_id_pai=extratos_id,
        name_folder="EXT",
    )
    entrada_ext_id = pasta_entrada_ext["id"]
    logger.info("Pasta de entrada EXT do Google Drive: %s", entrada_ext_id)

    logger.info("Listando Excel FECFIN da pasta EXT do Google Drive")
    arquivos_excel_todos = google_drive.list_children(entrada_ext_id)
    arquivos_fecfin = [
        a for a in arquivos_excel_todos
        if a.get("mimeType") in (XLSX_MIME_TYPE, XLS_MIME_TYPE)
        and a.get("mimeType") != GoogleDriveAuth.FOLDER_MIME_TYPE
        and "FECFIN" in a.get("name", "").upper()
    ]
    logger.info("Arquivos FECFIN encontrados na pasta EXT: %s", len(arquivos_fecfin))

    processados = 0
    lancamentos_gerados = 0
    erros = 0
    arquivos_lancamento_notificacao: list[str] = []
    arquivos_nao_reconhecidos_notificacao: list[str] = []

    for arquivo_fecfin in arquivos_fecfin:
        fecfin_id = arquivo_fecfin["id"]
        fecfin_nome = arquivo_fecfin["name"]
        fecfin_stem = Path(fecfin_nome).stem
        fecfin_local = temp_dir / fecfin_nome
        processados += 1

        logger.info("Processando arquivo FECFIN: %s", fecfin_nome)

        try:
            google_drive.download(
                file_id=fecfin_id,
                destino_local=fecfin_local,
            )

            with pd.ExcelFile(fecfin_local) as xls:
                resultados = dispatch_fecwin(xls, fecfin_stem)

            if not resultados:
                logger.warning("Layout FECFIN nao reconhecido: %s", fecfin_nome)
                arquivos_nao_reconhecidos_notificacao.append(fecfin_nome)
                continue

            for banco_nome, df_banco in resultados:
                if df_banco is None or df_banco.empty:
                    logger.warning(
                        "FECFIN %s — banco %s sem dados, ignorado",
                        fecfin_nome,
                        banco_nome,
                    )
                    continue

                nome_lanc = f"[LANC] {fecfin_stem}_{banco_nome}.xlsm"
                destino_lanc = temp_dir / nome_lanc

                logger.info("Copiando modelo de lancamento para: %s", nome_lanc)
                shutil.copy2(lancamento, destino_lanc)

                logger.info("Preenchendo planilha de lancamento: %s", nome_lanc)
                planilha_lancamento(df_banco, destino_lanc)

                logger.info(
                    "Enviando XLSM FECFIN para o Google Drive: %s", nome_lanc
                )
                google_drive.upload(
                    caminho_local=destino_lanc,
                    folder_id_destino=entrada_ext_id,
                    type_file=XLSM_MIME_TYPE,
                    name_drive=nome_lanc,
                )

                lancamentos_gerados += 1
                arquivos_lancamento_notificacao.append(nome_lanc)
                destino_lanc.unlink(missing_ok=True)

        except Exception:
            erros += 1
            logger.exception("Erro ao processar arquivo FECFIN: %s", fecfin_nome)
            arquivos_nao_reconhecidos_notificacao.append(
                f"{fecfin_nome} - erro de processamento"
            )
        finally:
            fecfin_local.unlink(missing_ok=True)

    logger.info(
        "Fluxo FECFIN concluido. Processados=%s Lancamentos=%s Erros=%s",
        processados,
        lancamentos_gerados,
        erros,
    )

    enviar_notificacao_fecfin_google_chat(
        arquivos_lancamento_notificacao,
        arquivos_nao_reconhecidos=arquivos_nao_reconhecidos_notificacao,
    )

    return {
        "processados": processados,
        "lancamentos": lancamentos_gerados,
        "erros": erros,
    }
