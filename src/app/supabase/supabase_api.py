import logging
import os
import re
import time
import unicodedata
from datetime import date

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

SUPABASE_BASE_URL = os.getenv("SUPABASE_BASE_URL")
if not SUPABASE_BASE_URL:
    raise ValueError("SUPABASE_BASE_URL não configurada")
SUPABASE_CONTROLE_URL = f"{SUPABASE_BASE_URL}/controle/sync"
SUPABASE_CONTROLE_GET_URL = f"{SUPABASE_BASE_URL}/controle"
SUPABASE_CONTROLE_KEY = os.getenv("SUPABASE_CONTROLE_KEY")
if not SUPABASE_CONTROLE_KEY:
    raise ValueError("SUPABASE_CONTROLE_KEY não configurada")
SUPABASE_BAIXA_URL = f"{SUPABASE_BASE_URL}/controle/baixa"
SUPABASE_EMPRESAS_URL = os.getenv(
    "SUPABASE_EMPRESAS_URL",
    f"{SUPABASE_BASE_URL}/empresas",
)
SUPABASE_DOCUMENTOS_URL = os.getenv(
    "SUPABASE_DOCUMENTOS_URL",
    f"{SUPABASE_BASE_URL}/documentos",
)

_CACHE_EMPRESAS: dict[str, tuple[str, str]] | None = None
_CACHE_EMPRESAS_TS: float = 0
_CACHE_EMPRESAS_TTL: int = 300


def _normalizar_nome_empresa(valor) -> str:
    return " ".join(str(valor or "").strip().upper().split())


def _converter_competencia(competencia: str) -> str:
    """Converte 'MM-YYYY' para 'YYYY-MM' (formato esperado pelo SGE)."""
    partes = competencia.split("-")
    if len(partes) == 2 and len(partes[0]) == 2 and len(partes[1]) == 4:
        return f"{partes[1]}-{partes[0]}"
    return competencia


def buscar_id_empresa_supabase(
    empresa_codigo: str,
    competencia: str,
    codigo_documento: str,
    *,
    url: str = SUPABASE_CONTROLE_GET_URL,
    key: str = SUPABASE_CONTROLE_KEY,
) -> str | None:
    """GET /controle - busca id_empresa (UUID) pelo codigo da empresa."""
    params = {
        "empresa_codigo": empresa_codigo,
        "competencia": _converter_competencia(competencia),
        "codigo_documento": codigo_documento,
        "limit": 1,
    }
    headers = {
        "Authorization": f"Bearer {key}",
    }

    try:
        logger.info(
            "Buscando id_empresa no SGE: empresa=%s competencia=%s cod_doc=%s",
            empresa_codigo,
            competencia,
            codigo_documento,
        )
        response = requests.get(url, headers=headers, params=params, timeout=30)
        resposta_texto = (response.text or "")[:500]
        logger.info(
            "Resposta GET SGE: status=%s body=%s",
            response.status_code,
            resposta_texto,
        )

        if response.status_code < 200 or response.status_code >= 300:
            logger.error(
                "Falha ao buscar id_empresa no SGE: status=%s body=%s",
                response.status_code,
                resposta_texto,
            )
            return None

        data = response.json()
        registros = data.get("data", [])

        if not registros:
            logger.warning(
                "Nenhum registro encontrado no SGE: empresa=%s competencia=%s cod_doc=%s",
                empresa_codigo,
                competencia,
                codigo_documento,
            )
            return None

        id_empresa = registros[0].get("id_empresa")

        if not id_empresa:
            logger.warning(
                "Registro encontrado mas id_empresa ausente: empresa=%s competencia=%s",
                empresa_codigo,
                competencia,
            )
            return None

        logger.info(
            "id_empresa encontrado: %s para empresa=%s",
            id_empresa,
            empresa_codigo,
        )
        return str(id_empresa)
    except Exception:
        logger.exception(
            "Erro ao buscar id_empresa no SGE: empresa=%s",
            empresa_codigo,
        )
        return None


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
    id_empresa = buscar_id_empresa_supabase(
        empresa_codigo=empresa_codigo,
        competencia=competencia,
        codigo_documento=codigo_documento,
    )

    if not id_empresa:
        logger.warning(
            "Atualizacao SGE ignorada: id_empresa nao encontrado para empresa=%s competencia=%s",
            empresa_codigo,
            competencia,
        )
        return False

    payload = {
        "id_empresa": id_empresa,
        "empresa_codigo": empresa_codigo,
        "competencia": _converter_competencia(competencia),
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


# Aliases de banco: forma derivada do nome do arquivo -> forma gravada no SGE.
# A comparacao usa a chave alfanumerica (sem espacos/acentos), entao "BB" vira
# "BANCODOBRASIL" para casar com a descricao "EXTBAN-BANCO DO BRASIL".
_ALIAS_BANCO = {
    "BB": "BANCODOBRASIL",
    "SICCOB": "SICOOB",
}


def _chave_banco(texto: str) -> str:
    """Chave de comparacao de banco: maiuscula, sem acento e sem nao-alfanumerico.

    Torna o match insensivel a espacos e pontuacao, resolvendo casos como
    "C6BANK" (nome do arquivo) x "BANCO C6 BANK" (descricao da instancia).
    """
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", str(texto).upper())
        if not unicodedata.combining(c)
    )
    return re.sub(r"[^A-Z0-9]", "", sem_acento)


def _agencia_da_descricao(desc: str) -> str:
    """Retorna o trecho de agencia ("A:...") da descricao da instancia.

    Cobre os dois formatos gravados no SGE: "... - A:0001-9 - C: 1265692-5" e
    "...- A:RDC FLEX- C:1926-7". Usa a ULTIMA ocorrencia de "A:" antes da conta
    porque a descricao pode vir prefixada por texto livre ("Extratos Bancarios
    - Aplicacoes Financeiras - PDF/OFX - EXTAPL-SICOOB-A:RDC AUT-C:1926-7").
    Retorna "" quando a descricao nao traz agencia.
    """
    cabeca, separador, _ = desc.rpartition("C:")
    if not separador:
        cabeca = desc
    _, separador, agencia = cabeca.rpartition("A:")
    return agencia if separador else ""


def _chave_agencia(valor: str) -> str:
    """Chave de agencia: alfanumerica e sem zeros a esquerda ('0001-9' -> '19')."""
    return _chave_banco(valor).lstrip("0")


def _desempatar_por_agencia(
    candidatos: list[tuple[str, str]], agencia: str
) -> str | None:
    """Escolhe entre instancias que ja casaram banco (e conta) usando a agencia.

    A mesma conta pode ter mais de uma instancia quando o que as separa e a
    agencia — caso das aplicacoes SICOOB "A:RDC AUT" e "A:RDC FLEX", ambas na
    conta 1926-7. Sem criterio de desempate, retorna None: escolher a primeira
    daria baixa na instancia errada.
    """
    if not candidatos:
        return None
    if len(candidatos) == 1:
        return candidatos[0][0]

    agencia_key = _chave_agencia(agencia)
    if agencia_key:
        por_agencia = [
            codigo
            for codigo, desc in candidatos
            if _chave_agencia(_agencia_da_descricao(desc)) == agencia_key
        ]
        if len(por_agencia) == 1:
            return por_agencia[0]

    return None


def _match_instancia(
    instancias: list[dict], banco: str, conta: str, agencia: str = ""
) -> str | None:
    """Encontra o instancia_codigo que bate com banco, conta e agencia.

    Cada instancia tem: {"codigo": "7-12656925", "descricao": "EXTBAN-BANCO INTER - A:0001-9 - C: 1265692-5"}
    """
    # Normaliza conta ignorando zeros a esquerda (o SGE grava contas com
    # padding, ex.: '0099460-5' para o mesmo '99460-5').
    conta_clean = conta.replace("-", "").replace(" ", "").strip().lstrip("0")

    # Chave alfanumerica do banco (+ alias). Sem banco nao ha como casar
    # instancia (ex.: documentos que nao sao extrato bancario); retornar None
    # evita casar uma instancia arbitraria.
    banco_key = _chave_banco(banco)
    banco_key = _ALIAS_BANCO.get(banco_key, banco_key)
    if not banco_key:
        return None

    # Fallback so para instancia do banco SEM conta na descricao (ex.: "KAMINO",
    # "BTG PACTUAL"). Se a instancia tem uma conta DIFERENTE, nao serve como
    # fallback — senao a baixa cairia numa conta errada do mesmo banco.
    candidatos: list[tuple[str, str]] = []
    fallback_sem_conta: list[tuple[str, str]] = []
    for inst in instancias:
        codigo = str(inst.get("codigo", "")).strip()
        desc = str(inst.get("descricao", "")).upper()

        # Verificar se o banco esta na descricao (insensivel a espaco/acento)
        if banco_key not in _chave_banco(desc):
            continue

        # Sem conta no arquivo, toda instancia do banco e candidata.
        if not conta_clean:
            candidatos.append((codigo, desc))
            continue

        # Conta da instancia (ignorando zeros a esquerda), se houver.
        desc_conta_clean = ""
        if "C:" in desc:
            desc_conta_clean = re.sub(r"[^0-9]", "", desc.split("C:")[-1]).lstrip("0")

        if desc_conta_clean:
            if desc_conta_clean == conta_clean:
                candidatos.append((codigo, desc))
            # conta diferente: nao casa e nao vira fallback
        else:
            # instancia do banco sem conta na descricao
            fallback_sem_conta.append((codigo, desc))

    return _desempatar_por_agencia(candidatos or fallback_sem_conta, agencia)


def _detalhe_404_baixa(
    response,
    empresa_codigo: str,
    nome_arquivo: str,
    codigo_documento: str = "",
    competencia: str = "",
) -> str:
    """Constroi mensagem amigavel e detalhada para os 404 da baixa SGE.

    A API SGE retorna 404 em cenarios distintos:
    - documento/empresa nao encontrado -> {"error": {"message": ...}}
    - registro de controle inexistente -> {"action": "not_found", "message": ...}

    A mensagem inclui arquivo + codigo + competencia para deixar claro QUAL
    documento falhou e o que revisar (antes vinha so "Empresa X: <msg crua>").
    """
    try:
        corpo = response.json()
    except Exception:
        corpo = None

    contexto = f"{nome_arquivo} (empresa {empresa_codigo}"
    if codigo_documento:
        contexto += f", doc {codigo_documento}"
    if competencia:
        contexto += f", competencia {competencia}"
    contexto += ")"

    if isinstance(corpo, dict):
        if corpo.get("action") == "not_found":
            return (
                f"{contexto}: registro de controle nao encontrado no SGE - "
                "gere a competencia/instancia desse documento no portal"
            )

        erro = corpo.get("error")
        mensagem = erro.get("message") if isinstance(erro, dict) else None
        if mensagem:
            return (
                f"{contexto}: SGE respondeu '{mensagem}' - verifique se o documento "
                f"{codigo_documento or 'informado'} esta cadastrado para a empresa nessa competencia"
            )

    return f"{contexto}: empresa nao cadastrada na plataforma SGECONT"


def _corpo_json(response):
    try:
        return response.json()
    except Exception:
        return None


def _instancias_disponiveis(response) -> list[dict]:
    """Extrai instancias_disponiveis do 400 'documento exige instancia'."""
    corpo = _corpo_json(response)
    if not isinstance(corpo, dict):
        return []
    detalhes = corpo.get("error", corpo)
    detalhes = detalhes.get("details", {}) if isinstance(detalhes, dict) else {}
    lista = detalhes.get("instancias_disponiveis", []) if isinstance(detalhes, dict) else []
    return lista if isinstance(lista, list) else []


def _descrever_candidatas(itens: list[dict], limite: int = 5) -> str:
    partes = [
        str(c.get("descricao") or c.get("codigo") or "").strip()
        for c in itens[:limite]
        if isinstance(c, dict)
    ]
    return "; ".join(p for p in partes if p)


def _detalhe_instancia_indeterminada(
    empresa_codigo: str,
    banco: str,
    conta: str,
    nome_arquivo: str,
    instancias: list[dict],
    agencia: str = "",
) -> str:
    return (
        f"{nome_arquivo}: documento exige instancia no SGE (empresa {empresa_codigo}), "
        f"mas nao foi possivel determinar por banco={banco or '-'} "
        f"agencia={agencia or '-'} conta={conta or '-'}. "
        f"Candidatas: {_descrever_candidatas(instancias) or 'nenhuma'}"
    )


def _detalhe_400_baixa(
    response, empresa_codigo: str, banco: str, conta: str, nome_arquivo: str
) -> str:
    """Mensagem para 400 remanescente (ex.: exige instancia sem nenhuma cadastrada)."""
    corpo = _corpo_json(response)
    mensagem = ""
    if isinstance(corpo, dict):
        erro = corpo.get("error", corpo)
        if isinstance(erro, dict):
            mensagem = str(erro.get("message") or "")
    if "instancia" in mensagem.lower() and not _instancias_disponiveis(response):
        return (
            f"{nome_arquivo}: documento exige instancia no SGE (empresa {empresa_codigo}), "
            f"mas nenhuma cadastrada para banco={banco or '-'} conta={conta or '-'}"
        )
    if mensagem:
        return f"{nome_arquivo} (empresa {empresa_codigo}): {mensagem}"
    return f"{nome_arquivo}: falha 400 na baixa SGE (empresa {empresa_codigo})"


def _candidatas_409(response) -> list[dict]:
    """Extrai as candidatas do 409 'instancia_codigo ambiguo'.

    Cada candidata traz ``id`` (UUID da instancia), ``codigo``, ``descricao``,
    ``banco`` e ``conta``. O SGE nomeia a chave como ``candidatas``; aceitamos
    ``candidatos`` por seguranca.
    """
    corpo = _corpo_json(response)
    if not isinstance(corpo, dict):
        return []
    detalhes = corpo.get("error", corpo)
    detalhes = detalhes.get("details", {}) if isinstance(detalhes, dict) else {}
    if not isinstance(detalhes, dict):
        return []
    lista = detalhes.get("candidatas") or detalhes.get("candidatos") or []
    return lista if isinstance(lista, list) else []


def _instancia_id_das_candidatas(
    candidatas: list[dict], banco: str, agencia: str, conta: str
) -> str | None:
    """Escolhe o instancia_id (UUID) da candidata que casa banco/agencia/conta.

    Reaproveita ``_match_instancia`` tratando o UUID como "codigo" — e o UUID,
    nao o instancia_codigo, que identifica a instancia sem ambiguidade.
    """
    return _match_instancia(
        [
            {"codigo": c.get("id"), "descricao": c.get("descricao")}
            for c in candidatas
            if isinstance(c, dict) and c.get("id")
        ],
        banco,
        conta,
        agencia,
    )


def _detalhe_409_baixa(
    response,
    empresa_codigo: str,
    banco: str,
    conta: str,
    nome_arquivo: str,
    agencia: str = "",
) -> str:
    """Mensagem para o 409 'instancia_codigo ambiguo' do endpoint corrigido."""
    return (
        f"{nome_arquivo}: instancia ambigua no SGE (empresa {empresa_codigo}, "
        f"banco={banco or '-'} agencia={agencia or '-'} conta={conta or '-'}). "
        f"Candidatas: {_descrever_candidatas(_candidatas_409(response)) or 'nao informadas'}. "
        "Informe conta/instancia para desambiguar."
    )


def baixar_controle_supabase(
    empresa_codigo: str,
    codigo_documento: str,
    competencia: str,
    banco: str,
    agencia: str,
    conta: str,
    nome_arquivo: str,
    local_arquivo: str,
    quantidade_arquivos: int,
    status: str = "Enviado",
    data_recebimento: str | None = None,
    *,
    url: str = SUPABASE_BAIXA_URL,
    key: str = SUPABASE_CONTROLE_KEY,
) -> tuple[bool, str | None]:
    """POST /controle/baixa - marca documento via API SGECONT.

    Retorna (True, None) em sucesso ou (False, detalhe) em falha.
    ``detalhe`` contem uma mensagem amigavel quando a empresa nao
    esta cadastrada no SGECONT (HTTP 404).
    """
    if data_recebimento is None:
        data_recebimento = date.today().isoformat()
    payload = {
        "empresa_codigo": empresa_codigo,
        "codigo_documento": codigo_documento,
        "competencia": _converter_competencia(competencia),
        "banco": banco,
        "agencia": agencia,
        "conta": conta,
        "nome_arquivo": nome_arquivo,
        "local_arquivo": local_arquivo,
        "quantidade_arquivos": quantidade_arquivos,
        "status_envio": status,
        "data_recebimento": data_recebimento,
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    try:
        logger.info(
            "Baixa no SGECONT: empresa=%s competencia=%s cod_doc=%s banco=%s arquivo=%s status_envio=%s",
            empresa_codigo,
            competencia,
            codigo_documento,
            banco,
            nome_arquivo,
            status,
        )
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        resposta_texto = (response.text or "")[:500]
        logger.info(
            "Resposta SGECONT baixa: status=%s body=%s",
            response.status_code,
            resposta_texto,
        )

        # 400: documento exige instancia -> desambiguar por banco+conta e refazer.
        if response.status_code == 400:
            instancias = _instancias_disponiveis(response)
            if instancias:
                instancia_codigo = _match_instancia(instancias, banco, conta, agencia)
                if instancia_codigo:
                    payload = {**payload, "instancia_codigo": instancia_codigo}
                    logger.info(
                        "Retry com instancia_codigo=%s para empresa=%s banco=%s",
                        instancia_codigo,
                        empresa_codigo,
                        banco,
                    )
                    response = requests.post(url, json=payload, headers=headers, timeout=30)
                    resposta_texto = (response.text or "")[:500]
                    logger.info(
                        "Resposta SGECONT baixa retry: status=%s body=%s",
                        response.status_code,
                        resposta_texto,
                    )
                else:
                    detalhe = _detalhe_instancia_indeterminada(
                        empresa_codigo, banco, conta, nome_arquivo, instancias, agencia
                    )
                    logger.warning(detalhe)
                    return False, detalhe

        # 409: o SGE repete o mesmo instancia_codigo em instancias distintas da
        # mesma conta (ex.: 458-4-19267 serve tanto para "A:RDC AUT" quanto para
        # "A:RDC FLEX"), entao o codigo nunca desambigua — so o instancia_id
        # (UUID), que vem nas candidatas do proprio 409.
        if response.status_code == 409:
            instancia_id = _instancia_id_das_candidatas(
                _candidatas_409(response), banco, agencia, conta
            )
            if instancia_id:
                payload = {
                    chave: valor
                    for chave, valor in payload.items()
                    if chave != "instancia_codigo"
                }
                payload["instancia_id"] = instancia_id
                logger.info(
                    "Retry com instancia_id=%s para empresa=%s banco=%s agencia=%s",
                    instancia_id,
                    empresa_codigo,
                    banco,
                    agencia,
                )
                response = requests.post(url, json=payload, headers=headers, timeout=30)
                resposta_texto = (response.text or "")[:500]
                logger.info(
                    "Resposta SGECONT baixa retry instancia_id: status=%s body=%s",
                    response.status_code,
                    resposta_texto,
                )

        if response.status_code == 404:
            detalhe = _detalhe_404_baixa(
                response, empresa_codigo, nome_arquivo, codigo_documento, competencia
            )
            logger.warning(detalhe)
            return False, detalhe

        if response.status_code == 409:
            detalhe = _detalhe_409_baixa(
                response, empresa_codigo, banco, conta, nome_arquivo, agencia
            )
            logger.warning(detalhe)
            return False, detalhe

        if response.status_code == 400:
            detalhe = _detalhe_400_baixa(
                response, empresa_codigo, banco, conta, nome_arquivo
            )
            logger.warning(detalhe)
            return False, detalhe

        if response.status_code < 200 or response.status_code >= 300:
            logger.error(
                "Falha na baixa SGECONT: status=%s body=%s",
                response.status_code,
                resposta_texto,
            )
            return False, None

        logger.info(
            "Baixa SGECONT realizada: empresa=%s competencia=%s arquivo=%s",
            empresa_codigo,
            competencia,
            nome_arquivo,
        )
        return True, None
    except Exception:
        logger.exception(
            "Erro na baixa SGECONT: empresa=%s arquivo=%s",
            empresa_codigo,
            nome_arquivo,
        )
        return False, None


def limpar_cache_empresas() -> None:
    """Limpa o cache em memória de empresas ativas."""
    global _CACHE_EMPRESAS, _CACHE_EMPRESAS_TS
    _CACHE_EMPRESAS = None
    _CACHE_EMPRESAS_TS = 0


def carregar_empresas_ativas_api(
    *,
    empresas_url: str | None = None,
    empresas_key: str | None = None,
    ttl: int = _CACHE_EMPRESAS_TTL,
    _agora: float | None = None,
) -> dict[str, tuple[str, str]]:
    """Busca empresas ativas via API SGE, com cache em memória.

    Retorna um dict ``{nome_normalizado: (id_empresa, razao_social)}``
    contendo apenas empresas com ``ativo=True``.
    """
    global _CACHE_EMPRESAS, _CACHE_EMPRESAS_TS

    agora = _agora if _agora is not None else time.time()
    if _CACHE_EMPRESAS is not None and (agora - _CACHE_EMPRESAS_TS) < ttl:
        logger.debug("Empresas ativas retornadas do cache (%s registros)", len(_CACHE_EMPRESAS))
        return _CACHE_EMPRESAS

    url = empresas_url or SUPABASE_EMPRESAS_URL
    key = empresas_key or SUPABASE_CONTROLE_KEY

    headers = {"Authorization": f"Bearer {key}"}
    params = {"limit": 300}

    logger.info("Buscando empresas ativas via API: %s", url)
    response = requests.get(url, headers=headers, params=params, timeout=30)
    resposta_texto = (response.text or "")[:500]
    logger.info("Resposta API empresas: status=%s body=%s", response.status_code, resposta_texto)

    if response.status_code < 200 or response.status_code >= 300:
        logger.error(
            "Falha ao buscar empresas ativas: status=%s body=%s",
            response.status_code,
            resposta_texto,
        )
        raise RuntimeError(
            f"API empresas retornou status {response.status_code}"
        )

    data = response.json().get("data", [])
    empresas: dict[str, tuple[str, str]] = {}

    for item in data:
        if not item.get("ativo"):
            continue
        razao_social = (item.get("razao_social") or "").strip()
        nome = (item.get("nome") or "").strip()
        id_empresa = str(item.get("id_empresa") or "").strip()
        if razao_social and id_empresa:
            chave_razao = _normalizar_nome_empresa(razao_social)
            empresas[chave_razao] = (id_empresa, razao_social)
            # Indexar tambem pelo nome curto (usado nos nomes de arquivo)
            if nome:
                chave_nome = _normalizar_nome_empresa(nome)
                if chave_nome not in empresas:
                    empresas[chave_nome] = (id_empresa, nome)

    _CACHE_EMPRESAS = empresas
    _CACHE_EMPRESAS_TS = agora

    logger.info("Empresas ativas carregadas: %s registros", len(empresas))
    return empresas


def carregar_caminhos_documentos_api(
    *,
    documentos_url: str | None = None,
    documentos_key: str | None = None,
) -> dict[str, str]:
    """GET /documentos - retorna ``{codigo_documento: destino_final}`` dos ativos.

    Substitui a planilha DOCS E CAMINHO.xlsx: o destino final de cada codigo
    agora e mantido no cadastro de Documentos do portal SGE.
    """
    url = documentos_url or SUPABASE_DOCUMENTOS_URL
    key = documentos_key or SUPABASE_CONTROLE_KEY

    headers = {"Authorization": f"Bearer {key}"}
    limite = 300
    offset = 0
    caminhos: dict[str, str] = {}

    logger.info("Buscando documentos (destino final) via API: %s", url)
    while True:
        params = {"limit": limite, "offset": offset}
        response = requests.get(url, headers=headers, params=params, timeout=30)

        if response.status_code < 200 or response.status_code >= 300:
            logger.error(
                "Falha ao buscar documentos no SGE: status=%s body=%s",
                response.status_code,
                (response.text or "")[:500],
            )
            raise RuntimeError(
                f"API documentos retornou status {response.status_code}"
            )

        corpo = response.json()
        data = corpo.get("data", [])

        for item in data:
            if not item.get("ativo"):
                continue
            codigo = str(item.get("codigo_documento") or "").strip().upper()
            destino = str(item.get("destino_final") or "").strip()
            if codigo and destino:
                caminhos[codigo] = destino

        offset += len(data)
        total = corpo.get("count")
        if not data or total is None or offset >= total:
            break

    logger.info("Documentos com destino final carregados: %s registros", len(caminhos))
    return caminhos
