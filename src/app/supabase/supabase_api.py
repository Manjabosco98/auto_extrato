import logging

import requests

logger = logging.getLogger(__name__)

SUPABASE_CONTROLE_URL = (
    "https://ngsgvitzkdcbrhfvbymu.supabase.co"
    "/functions/v1/api-documentos/controle/sync"
)
SUPABASE_CONTROLE_KEY = (
    "sk_live_9e8b6a71-166e-4a57-ac3e-91b3fac1c3a6"
    "-b24c8d1c-3802-4206-8c36-1a9711ed89bc"
)


def atualizar_controle_supabase(
    empresa_codigo: str,
    competencia: str,
    codigo_documento: str,
    data_recebimento: str,
    quantidade_arquivos: int,
    nome_arquivo: str,
    local_arquivo: str,
    *,
    url: str = SUPABASE_CONTROLE_URL,
    key: str = SUPABASE_CONTROLE_KEY,
) -> bool:
    """PUT /controle/sync - marca documento como recebido no portal SGE."""
    payload = {
        "empresa_codigo": empresa_codigo,
        "competencia": competencia,
        "codigo_documento": codigo_documento,
        "status_envio": "Enviado",
        "data_recebimento": data_recebimento,
        "quantidade_arquivos": quantidade_arquivos,
        "nome_arquivo": nome_arquivo,
        "local_arquivo": local_arquivo,
        "origem_informacao": "Automacao Python",
        "observacao": "Atualizado via API",
        "atualizado_por": "AutoExtrato",
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    try:
        logger.info(
            "Atualizando controle SGE: empresa=%s competencia=%s cod_doc=%s arquivo=%s",
            empresa_codigo,
            competencia,
            codigo_documento,
            nome_arquivo,
        )
        response = requests.put(url, json=payload, headers=headers, timeout=30)
        resposta_texto = (response.text or "")[:500]
        logger.info(
            "Resposta SGE: status=%s body=%s",
            response.status_code,
            resposta_texto,
        )

        if response.status_code < 200 or response.status_code >= 300:
            logger.error(
                "Falha ao atualizar controle no SGE: status=%s body=%s",
                response.status_code,
                resposta_texto,
            )
            return False

        logger.info(
            "Controle SGE atualizado com sucesso: empresa=%s competencia=%s arquivo=%s",
            empresa_codigo,
            competencia,
            nome_arquivo,
        )
        return True
    except Exception:
        logger.exception(
            "Erro ao atualizar controle no SGE: empresa=%s arquivo=%s",
            empresa_codigo,
            nome_arquivo,
        )
        return False
