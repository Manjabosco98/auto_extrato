"""
Baixa em lote: lê HISTORICO_CONVERSOES.xlsx, verifica quais não deram
baixa no SGE, dá baixa e envia notificação no Google Chat.
"""
import logging
import re
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app.gdrive.google_drive_auth import GoogleDriveAuth
from src.app.gdrive.settings import (
    GOOGLE_DRIVE_FOLDER_ID,
    GOOGLE_OAUTH_CREDENTIALS,
    GOOGLE_OAUTH_TOKEN,
    GOOGLE_OAUTH_TOKEN_SECRET,
    XLSX_MIME_TYPE,
)
from src.app.supabase.supabase_api import (
    SUPABASE_CONTROLE_GET_URL,
    SUPABASE_CONTROLE_KEY,
    _converter_competencia,
    baixar_controle_supabase,
)
from src.services.conversao import (
    GOOGLE_CHAT_SEND_URL,
    GOOGLE_CHAT_SPACE_NAME,
    agora_historico,
    carregar_empresas_ativas,
    extrair_banco_nome_arquivo,
    extrair_cliente_nome_arquivo,
    extrair_competencia_nome_arquivo,
    normalizar_id_empresa,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def competencia_de_data(valor) -> str:
    if pd.isna(valor) or valor is None:
        return ""
    texto = str(valor).strip()
    match = re.match(r"^(\d{4})-(\d{2})$", texto)
    if match:
        return f"{match.group(2)}-{match.group(1)}"
    if len(texto) >= 10:
        partes = texto[:10].split("-")
        if len(partes) >= 2:
            return f"{partes[1]}-{partes[0]}"
    return ""


def data_recebimento_de_valor(valor) -> str:
    if pd.isna(valor) or valor is None:
        return ""
    texto = str(valor).strip()
    if len(texto) >= 10:
        return texto[:10]
    return ""


def consultar_sge(empresa_codigo, competencia):
    import requests
    headers = {"Authorization": f"Bearer {SUPABASE_CONTROLE_KEY}"}
    params = {
        "empresa_codigo": empresa_codigo,
        "competencia": _converter_competencia(competencia),
        "codigo_documento": "EXTBAN",
        "limit": 500,
    }
    try:
        r = requests.get(SUPABASE_CONTROLE_GET_URL, headers=headers, params=params, timeout=30)
        if r.status_code < 200 or r.status_code >= 300:
            return []
        return r.json().get("data", [])
    except Exception:
        return []


def enviar_chat(mensagem):
    import requests
    payload = {"space_name": GOOGLE_CHAT_SPACE_NAME, "message": mensagem}
    try:
        r = requests.post(GOOGLE_CHAT_SEND_URL, json=payload, timeout=30)
        logger.info("Chat: status=%s", r.status_code)
    except Exception:
        logger.exception("Erro ao enviar chat")


def main():
    t0 = time.perf_counter()

    # 1. Baixar historico do Drive
    with TemporaryDirectory() as td:
        td = Path(td)
        logger.info("Autenticando Google Drive")
        gdrive = GoogleDriveAuth(
            credentials_path=GOOGLE_OAUTH_CREDENTIALS,
            token_path=GOOGLE_OAUTH_TOKEN,
            token_secret_path=GOOGLE_OAUTH_TOKEN_SECRET,
        )

        logger.info("Buscando HISTORICO_CONVERSOES.xlsx")
        arq = gdrive.find_file_by_name(
            folder_id=GOOGLE_DRIVE_FOLDER_ID,
            name="HISTORICO_CONVERSOES.xlsx",
            mime_type=XLSX_MIME_TYPE,
        )
        if not arq:
            logger.error("HISTORICO_CONVERSOES.xlsx nao encontrado")
            return

        destino = td / "HISTORICO_CONVERSOES.xlsx"
        gdrive.download(file_id=arq["id"], destino_local=destino)
        df = pd.read_excel(destino, engine="openpyxl")
        df.columns = [str(c).strip().upper() for c in df.columns]
        logger.info("Registros no historico: %s", len(df))

        # 2. Carregar empresas
        empresas = carregar_empresas_ativas()

        # 3. Agrupar por (empresa_id, competencia, banco)
        grupos = {}
        for _, row in df.iterrows():
            emp_raw = row.get("EMP", "")
            # O campo EMP contem o nome curto (ex: "SILVA"), nao o ID numerico
            # Precisa buscar o ID numerico no dict de empresas
            empresa_id = ""
            empresa_nome = ""
            if emp_raw:
                emp_normalizado = " ".join(str(emp_raw).strip().upper().split())
                if emp_normalizado in empresas:
                    empresa_id, empresa_nome = empresas[emp_normalizado]
                else:
                    # Tentar extrair do nome do arquivo
                    arq_temp = str(row.get("NOME ARQUIVO", "")).strip()
                    if arq_temp:
                        cliente = extrair_cliente_nome_arquivo(arq_temp)
                        cliente_norm = " ".join(cliente.upper().split())
                        if cliente_norm in empresas:
                            empresa_id, empresa_nome = empresas[cliente_norm]

            comp = competencia_de_data(row.get("DATA HORA MOVIMENTO"))
            banco = str(row.get("BANCO", "")).strip()
            arq_nome = str(row.get("NOME ARQUIVO", "")).strip()
            pasta = str(row.get("PASTA DESTINO", "")).strip()
            data_mov = row.get("DATA HORA MOVIMENTO", "")

            if not empresa_id or not comp:
                continue

            # Extrair competencia do nome do arquivo se nao tiver na data
            if not comp:
                comp = extrair_competencia_nome_arquivo(arq_nome)

            if not banco:
                banco = extrair_banco_nome_arquivo(arq_nome)

            key = (empresa_id, comp, banco)
            if key not in grupos:
                grupos[key] = {
                    "empresa_id": empresa_id,
                    "competencia": comp,
                    "banco": banco,
                    "arquivos": [],
                    "data_recebimento": data_recebimento_de_valor(data_mov),
                    "pasta": pasta,
                }
            grupos[key]["arquivos"].append(arq_nome)

        logger.info("Grupos unicos: %s", len(grupos))

        # 4. Para cada grupo, verificar SGE e dar baixa se necessario
        sucesso = 0
        ja_baixado = 0
        erros = 0
        pulados = 0
        detalhes = []

        for (emp_id, comp, banco), info in grupos.items():
            # Verificar SGE
            registros_sge = consultar_sge(emp_id, comp)

            # Verificar se ja tem registro Enviado para este banco
            ja_enviado = False
            for reg in registros_sge:
                if reg.get("status_envio") == "Enviado" and reg.get("nome_arquivo"):
                    # Verificar se o arquivo bate
                    nome_sge = str(reg.get("nome_arquivo", "")).strip()
                    for arq in info["arquivos"]:
                        if Path(arq).stem in nome_sge or nome_sge in Path(arq).stem:
                            ja_enviado = True
                            break
                if ja_enviado:
                    break

            if ja_enviado:
                ja_baixado += 1
                continue

            # Determinar arquivo PDF para dar baixa
            pdf_nome = None
            for arq in info["arquivos"]:
                if arq.lower().endswith(".pdf"):
                    pdf_nome = arq
                    break
            if not pdf_nome and info["arquivos"]:
                pdf_nome = info["arquivos"][0]

            # Dar baixa
            local = f"Google Drive / {info['pasta']}" if info["pasta"] else "Google Drive"

            ok, detalhe = baixar_controle_supabase(
                empresa_codigo=emp_id,
                codigo_documento="EXTBAN",
                competencia=comp,
                banco=banco,
                agencia="",
                conta="",
                nome_arquivo=pdf_nome or "",
                local_arquivo=local,
                quantidade_arquivos=3,
                status="Enviado",
                data_recebimento=info["data_recebimento"],
            )

            if ok:
                sucesso += 1
                detalhes.append(f"OK: empresa={emp_id} comp={comp} banco={banco}")
                logger.info("Baixa OK: empresa=%s comp=%s banco=%s", emp_id, comp, banco)
            else:
                erros += 1
                detalhes.append(f"ERRO: empresa={emp_id} comp={comp} banco={banco} - {detalhe or 'desconhecido'}")
                logger.warning("Baixa ERRO: empresa=%s comp=%s banco=%s", emp_id, comp, banco)

            time.sleep(0.3)

        elapsed = time.perf_counter() - t0

        # 5. Resumo
        resumo = (
            f"Baixa em lote concluida em {elapsed:.1f}s\n\n"
            f"Sucesso: {sucesso}\n"
            f"Ja baixados: {ja_baixado}\n"
            f"Erros: {erros}\n"
            f"Total: {len(grupos)}"
        )
        print(resumo)

        # 6. Notificar chat
        if sucesso > 0:
            msg = (
                f"Baixa em lote no SGE - {sucesso} registro(s) dado(s) baixa\n\n"
                f"Detalhes:\n" + "\n".join(detalhes[:20])
            )
            if len(detalhes) > 20:
                msg += f"\n... e mais {len(detalhes) - 20}"
            enviar_chat(msg)
            logger.info("Notificacao enviada ao chat")


if __name__ == "__main__":
    main()
