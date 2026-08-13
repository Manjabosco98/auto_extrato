import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from src.app.gdrive.google_drive_auth import GoogleDriveAuth
from src.app.gdrive.settings import (
    GOOGLE_DRIVE_EMP_FOLDER_ID,
    GOOGLE_DRIVE_EXT_FOLDER_ID,
    GOOGLE_DRIVE_FOLDER_ID,
    GOOGLE_OAUTH_CREDENTIALS,
    GOOGLE_OAUTH_TOKEN,
    GOOGLE_OAUTH_TOKEN_SECRET,
    XLSM_MIME_TYPE,
    XLS_MIME_TYPE,
    XLSX_MIME_TYPE,
)
from src.app.supabase.supabase_api import (
    baixar_controle_supabase,
    carregar_caminhos_documentos_api,
    consultar_situacao_controle,
)
from src.schemas.fecfin.registry import dispatch_fecwin_detalhado
from src.services.conversao import (
    GOOGLE_CHAT_SEND_URL,
    GOOGLE_CHAT_SPACE_NAME,
    MODELO_LANCAMENTOS,
    agora_historico,
    buscar_empresa_por_cliente,
    carregar_empresas_ativas,
    nome_pasta_empresa,
    resolver_pasta_emp_base,
)
from src.services.docs import (
    montar_destino_docs,
    resolver_pasta_destino_docs,
    resolver_raiz_destino,
)
from src.utils.helpers import planilha_lancamento
from src.utils.logging_config import setup_logging


logger = logging.getLogger(__name__)

# Codigo do documento FECFIN no controle SGE. Nao exige instancia
# (Exige Instancia=Nao no cadastro de Documentos do portal), logo a
# baixa e uma por empresa+competencia, sem banco/agencia/conta.
FECFIN_CODIGO = "FECFIN"


def parse_nome_fecfin(nome_arquivo: str) -> tuple[str, str, str]:
    """De 'MMAA_FECFIN_..._CLIENTE' retorna (mes, ano, cliente).

    O cliente e o ultimo segmento do nome (apos os '_').
    """
    partes = [p.strip() for p in Path(nome_arquivo).stem.split("_") if p.strip()]
    if len(partes) < 3:
        raise ValueError(
            f"Nome FECFIN fora do padrao MMAA_FECFIN_CLIENTE: {nome_arquivo}"
        )
    periodo = re.fullmatch(r"(\d{2})(\d{2})", partes[0])
    if not periodo:
        raise ValueError(f"Nome FECFIN sem periodo MMAA valido: {nome_arquivo}")
    return periodo.group(1), periodo.group(2), partes[-1]


def _carregar_destino_fecfin() -> str | None:
    """Le o template de destino do codigo FECFIN no cadastro de Documentos do SGE."""
    try:
        caminhos = carregar_caminhos_documentos_api()
    except Exception:
        logger.exception("Falha ao carregar destino final dos documentos no SGE")
        return None
    return caminhos.get(FECFIN_CODIGO)


def formatar_mensagem_fecfin_google_chat(
    arquivos_processados: list[str],
    arquivos_nao_reconhecidos: list[str] | None = None,
    empresas_nao_encontradas: list[str] | None = None,
    atualizados_sge: int = 0,
    erros_sge: int = 0,
    erros_sge_detalhe: list[str] | None = None,
    baixas_preservadas: list[str] | None = None,
    momento: datetime | None = None,
) -> str:
    momento = momento or agora_historico()
    arquivos_nao_reconhecidos = arquivos_nao_reconhecidos or []
    empresas_nao_encontradas = empresas_nao_encontradas or []
    erros_sge_detalhe = erros_sge_detalhe or []
    baixas_preservadas = baixas_preservadas or []
    blocos = []

    data = momento.strftime("%d/%m/%y")
    hora = momento.strftime("%H:%M")

    if arquivos_processados:
        lista = "\n".join(arquivos_processados)
        blocos.append(f"FECFIN processados {data} as {hora} e salvos na pasta da empresa:\n\n{lista}")

    if atualizados_sge > 0:
        blocos.append(f"Baixa dada no portal SGE: {atualizados_sge} documento(s) FECFIN")

    if baixas_preservadas:
        lista = "\n".join(baixas_preservadas)
        blocos.append(
            "FECFIN ja baixados no SGE (baixa preservada, arquivos salvos na pasta da empresa):\n\n"
            f"{lista}"
        )

    if empresas_nao_encontradas:
        lista = "\n".join(empresas_nao_encontradas)
        blocos.append(f"FECFIN sem empresa correspondente (mantidos na EXT):\n\n{lista}")

    if arquivos_nao_reconhecidos:
        lista = "\n".join(arquivos_nao_reconhecidos)
        blocos.append(f"Arquivos FECFIN sem layout reconhecido:\n\n{lista}")

    if erros_sge > 0:
        blocos.append(f"Erros ao dar baixa no SGE: {erros_sge}")

    if erros_sge_detalhe:
        blocos.append("Detalhes dos erros SGE:\n\n" + "\n".join(erros_sge_detalhe))

    return "\n\n".join(blocos)


def enviar_notificacao_fecfin_google_chat(
    arquivos_processados: list[str],
    arquivos_nao_reconhecidos: list[str] | None = None,
    empresas_nao_encontradas: list[str] | None = None,
    atualizados_sge: int = 0,
    erros_sge: int = 0,
    erros_sge_detalhe: list[str] | None = None,
    baixas_preservadas: list[str] | None = None,
    momento: datetime | None = None,
) -> bool:
    if not any(
        (arquivos_processados, arquivos_nao_reconhecidos, empresas_nao_encontradas)
    ):
        logger.info("Notificacao Google Chat FECFIN nao enviada: nada a informar")
        return False

    mensagem = formatar_mensagem_fecfin_google_chat(
        arquivos_processados,
        arquivos_nao_reconhecidos=arquivos_nao_reconhecidos,
        empresas_nao_encontradas=empresas_nao_encontradas,
        atualizados_sge=atualizados_sge,
        erros_sge=erros_sge,
        erros_sge_detalhe=erros_sge_detalhe,
        baixas_preservadas=baixas_preservadas,
        momento=momento,
    )

    payload = {"space_name": GOOGLE_CHAT_SPACE_NAME, "message": mensagem}

    try:
        logger.info(
            "Enviando notificacao FECFIN ao Google Chat com %s arquivo(s)",
            len(arquivos_processados),
        )
        response = requests.post(GOOGLE_CHAT_SEND_URL, json=payload, timeout=30)
        resposta_texto = (response.text or "")[:500]
        logger.info(
            "Resposta Google Chat FECFIN: status=%s body=%s",
            response.status_code,
            resposta_texto,
        )
        if response.status_code < 200 or response.status_code >= 300:
            logger.error(
                "Falha ao enviar notificacao FECFIN: status=%s body=%s",
                response.status_code,
                resposta_texto,
            )
            return False
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

    raiz_id = GOOGLE_DRIVE_FOLDER_ID
    extratos_id = GOOGLE_DRIVE_EXT_FOLDER_ID
    lancamento = MODELO_LANCAMENTOS
    temp_dir = Path.cwd() / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Diretorio temporario preparado: %s", temp_dir)

    # Cache de raizes de destino (EMP resolvida ja; PUBLICO sob demanda).
    raizes_ids: dict[str, str] = {
        "EMP": resolver_pasta_emp_base(
            google_drive=google_drive,
            pasta_base_id=GOOGLE_DRIVE_EMP_FOLDER_ID,
        )
    }

    pasta_entrada_ext = google_drive.get_or_create_folder(
        folder_id_pai=extratos_id,
        name_folder="EXT",
    )
    entrada_ext_id = pasta_entrada_ext["id"]
    logger.info("Pasta de entrada EXT do Google Drive: %s", entrada_ext_id)

    empresas = carregar_empresas_ativas()
    destino_template = _carregar_destino_fecfin()
    if not destino_template:
        logger.error(
            "Template de destino FECFIN nao encontrado no cadastro de Documentos do SGE; abortando"
        )
        return {"processados": 0, "movidos": 0, "lancamentos": 0, "erros": 1, "atualizados_sge": 0}

    logger.info("Listando Excel FECFIN da pasta EXT do Google Drive")
    arquivos_todos = google_drive.list_children(entrada_ext_id)
    arquivos_fecfin = [
        a
        for a in arquivos_todos
        if a.get("mimeType") in (XLSX_MIME_TYPE, XLS_MIME_TYPE)
        and a.get("mimeType") != GoogleDriveAuth.FOLDER_MIME_TYPE
        and "FECFIN" in a.get("name", "").upper()
        and not a.get("name", "").upper().startswith("[LANC]")
    ]
    logger.info("Arquivos FECFIN encontrados na pasta EXT: %s", len(arquivos_fecfin))

    processados = 0
    movidos = 0
    lancamentos_gerados = 0
    erros = 0
    atualizados_sge = 0
    erros_sge = 0
    arquivos_notificacao: list[str] = []
    nao_reconhecidos_notificacao: list[str] = []
    empresas_nao_encontradas: list[str] = []
    erros_sge_detalhe: list[str] = []
    baixas_preservadas: list[str] = []

    for arquivo_fecfin in arquivos_fecfin:
        fecfin_id = arquivo_fecfin["id"]
        fecfin_nome = arquivo_fecfin["name"]
        fecfin_stem = Path(fecfin_nome).stem
        fecfin_local = temp_dir / fecfin_nome
        lancs_locais: list[Path] = []
        processados += 1
        logger.info("Processando arquivo FECFIN: %s", fecfin_nome)

        try:
            try:
                mes, ano, cliente = parse_nome_fecfin(fecfin_nome)
            except ValueError as erro_nome:
                nao_reconhecidos_notificacao.append(f"{fecfin_nome} - {erro_nome}")
                continue

            empresa_id, empresa_nome = buscar_empresa_por_cliente(cliente, empresas=empresas)
            if not empresa_id or not empresa_nome:
                logger.warning(
                    "Empresa FECFIN nao encontrada para cliente %s. Mantido na EXT: %s",
                    cliente,
                    fecfin_nome,
                )
                empresas_nao_encontradas.append(f"{fecfin_nome} - cliente {cliente}")
                continue

            google_drive.download(file_id=fecfin_id, destino_local=fecfin_local)

            with pd.ExcelFile(fecfin_local) as xls:
                dispatch = dispatch_fecwin_detalhado(xls, fecfin_stem)
            resultados = dispatch.resultados

            if not resultados:
                motivo = dispatch.motivo()
                logger.warning(
                    "Layout FECFIN nao reconhecido: %s (%s)", fecfin_nome, motivo
                )
                nao_reconhecidos_notificacao.append(f"{fecfin_nome} - {motivo}")
                continue

            empresa_chave = nome_pasta_empresa(empresa_id, empresa_nome)
            raiz_nome, destino_partes = montar_destino_docs(
                destino_template=destino_template,
                empresa_chave=empresa_chave,
                ano=ano,
                mes=mes,
            )
            raiz_folder_id = resolver_raiz_destino(google_drive, raizes_ids, raiz_nome)
            pasta_destino_id, pasta_destino_historico = resolver_pasta_destino_docs(
                google_drive=google_drive,
                raiz_folder_id=raiz_folder_id,
                destino_partes=destino_partes,
                raiz_nome=raiz_nome,
            )

            # Tentar baixa no SGE antes de mover/enviar arquivos
            momento = agora_historico()
            situacao = consultar_situacao_controle(
                empresa_codigo=str(empresa_id),
                competencia=f"{mes}-20{ano}",
                codigo_documento=FECFIN_CODIGO,
            )
            if not situacao.cadastrado:
                logger.warning(
                    "Controle nao cadastrado no SGE: empresa=%s competencia=%s — %s mantido na EXT",
                    empresa_id,
                    f"{mes}-20{ano}",
                    fecfin_nome,
                )
                empresas_nao_encontradas.append(f"{fecfin_nome} - empresa/pasta nao cadastrada no SGE")
                continue

            # Documento ja baixado: preservar a baixa existente e seguir com os
            # arquivos, senao o FECFIN ficaria parado na EXT a cada execucao.
            baixa_preservada = situacao.ja_baixado
            if baixa_preservada:
                logger.info(
                    "Baixa ja existente no SGE para %s (%s); baixa preservada",
                    fecfin_nome,
                    situacao.resumo(),
                )
            else:
                quantidade_lanc = sum(
                    1 for banco_nome, df_banco in resultados
                    if df_banco is not None and not df_banco.empty
                )
                quantidade_total = 1 + quantidade_lanc
                sucesso, detalhe = baixar_controle_supabase(
                    empresa_codigo=str(empresa_id),
                    codigo_documento=FECFIN_CODIGO,
                    competencia=f"{mes}-20{ano}",
                    banco="",
                    agencia="",
                    conta="",
                    nome_arquivo=fecfin_nome,
                    local_arquivo=f"Google Drive / {pasta_destino_historico}",
                    quantidade_arquivos=quantidade_total,
                    status="Enviado",
                    data_recebimento=momento.strftime("%Y-%m-%d"),
                )
                if not sucesso:
                    logger.warning(
                        "Baixa SGE falhou para %s: %s — mantido na EXT",
                        fecfin_nome,
                        detalhe,
                    )
                    erros_sge += 1
                    if detalhe:
                        erros_sge_detalhe.append(detalhe)
                    continue

            # Baixa OK — gerar e enviar os lancamentos [LANC] por banco
            for banco_nome, df_banco in resultados:
                if df_banco is None or df_banco.empty:
                    logger.warning("FECFIN %s - banco %s sem dados", fecfin_nome, banco_nome)
                    continue
                nome_lanc = f"[LANC] {fecfin_stem}_{banco_nome}.xlsm"
                destino_lanc = temp_dir / nome_lanc
                lancs_locais.append(destino_lanc)
                shutil.copy2(lancamento, destino_lanc)
                planilha_lancamento(df_banco, destino_lanc)
                google_drive.upload(
                    caminho_local=destino_lanc,
                    folder_id_destino=pasta_destino_id,
                    type_file=XLSM_MIME_TYPE,
                    name_drive=nome_lanc,
                )
                lancamentos_gerados += 1
                logger.info("Lancamento FECFIN enviado: %s -> %s", nome_lanc, pasta_destino_historico)

            # Mover o FECFIN original para a pasta da empresa
            google_drive.move_file(file_id=fecfin_id, folder_id_destino=pasta_destino_id)
            movidos += 1
            arquivos_notificacao.append(f"{fecfin_nome} - {pasta_destino_historico}")
            if baixa_preservada:
                baixas_preservadas.append(f"{fecfin_nome} - {situacao.resumo()}")
            else:
                atualizados_sge += 1

        except Exception:
            erros += 1
            logger.exception("Erro ao processar arquivo FECFIN: %s", fecfin_nome)
            nao_reconhecidos_notificacao.append(f"{fecfin_nome} - erro de processamento")
        finally:
            fecfin_local.unlink(missing_ok=True)
            for lanc in lancs_locais:
                lanc.unlink(missing_ok=True)

    logger.info(
        "Fluxo FECFIN concluido. Processados=%s Movidos=%s Lancamentos=%s "
        "AtualizadosSGE=%s BaixasPreservadas=%s ErrosSGE=%s Erros=%s",
        processados,
        movidos,
        lancamentos_gerados,
        atualizados_sge,
        len(baixas_preservadas),
        erros_sge,
        erros,
    )

    enviar_notificacao_fecfin_google_chat(
        arquivos_notificacao,
        arquivos_nao_reconhecidos=nao_reconhecidos_notificacao,
        empresas_nao_encontradas=empresas_nao_encontradas,
        atualizados_sge=atualizados_sge,
        erros_sge=erros_sge,
        erros_sge_detalhe=erros_sge_detalhe,
        baixas_preservadas=baixas_preservadas,
    )

    return {
        "processados": processados,
        "movidos": movidos,
        "lancamentos": lancamentos_gerados,
        "erros": erros,
        "atualizados_sge": atualizados_sge,
        "erros_sge": erros_sge,
        "baixas_preservadas": len(baixas_preservadas),
    }
