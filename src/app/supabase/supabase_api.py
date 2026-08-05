import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
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

# Registros lidos por consulta de controle. Um documento tem no maximo uma
# linha por instancia da empresa na competencia, entao 200 cobre com folga.
LIMITE_REGISTROS_CONTROLE = 200

# Status do SGE que representam documento ja resolvido: dar baixa de novo
# sobrescreveria nome do arquivo, quantidade, local e data de recebimento ja
# gravados. Fora deste conjunto sobra "Nao Enviado", que e o que espera baixa.
STATUS_JA_BAIXADO = {"ENVIADO", "NAOAPLICAVEL", "ENCERRADA"}


def _normalizar_nome_empresa(valor) -> str:
    return " ".join(str(valor or "").strip().upper().split())


def _converter_competencia(competencia: str) -> str:
    """Converte 'MM-YYYY' para 'YYYY-MM' (formato esperado pelo SGE)."""
    partes = competencia.split("-")
    if len(partes) == 2 and len(partes[0]) == 2 and len(partes[1]) == 4:
        return f"{partes[1]}-{partes[0]}"
    return competencia


def _listar_registros_controle(
    empresa_codigo: str,
    competencia: str,
    codigo_documento: str,
    *,
    limite: int,
    url: str,
    key: str,
) -> list[dict]:
    """GET /controle - registros de controle da empresa/competencia/documento.

    Retorna lista vazia tambem quando a consulta falha: para os fluxos, "nao
    consegui perguntar ao SGE" tem o mesmo desfecho de "nao ha registro" —
    nenhuma baixa e feita e o arquivo fica onde esta.
    """
    params = {
        "empresa_codigo": empresa_codigo,
        "competencia": _converter_competencia(competencia),
        "codigo_documento": codigo_documento,
        "limit": limite,
    }
    headers = {
        "Authorization": f"Bearer {key}",
    }

    try:
        logger.info(
            "Consultando controle no SGE: empresa=%s competencia=%s cod_doc=%s",
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
                "Falha ao consultar controle no SGE: status=%s body=%s",
                response.status_code,
                resposta_texto,
            )
            return []

        registros = response.json().get("data", [])
        return registros if isinstance(registros, list) else []
    except Exception:
        logger.exception(
            "Erro ao consultar controle no SGE: empresa=%s",
            empresa_codigo,
        )
        return []


def buscar_id_empresa_supabase(
    empresa_codigo: str,
    competencia: str,
    codigo_documento: str,
    *,
    url: str = SUPABASE_CONTROLE_GET_URL,
    key: str = SUPABASE_CONTROLE_KEY,
) -> str | None:
    """GET /controle - busca id_empresa (UUID) pelo codigo da empresa."""
    registros = _listar_registros_controle(
        empresa_codigo,
        competencia,
        codigo_documento,
        limite=1,
        url=url,
        key=key,
    )

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


def _chave_texto(texto) -> str:
    """Chave de comparacao: maiuscula, sem acento e sem nao-alfanumerico.

    Torna o match insensivel a espacos e pontuacao, resolvendo casos como
    "C6BANK" (nome do arquivo) x "BANCO C6 BANK" (descricao da instancia) e
    "Nao Aplicavel" x "Não Aplicável" (status do SGE).
    """
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", str(texto).upper())
        if not unicodedata.combining(c)
    )
    return re.sub(r"[^A-Z0-9]", "", sem_acento)


def status_ja_baixado(status) -> bool:
    """True quando o status do SGE ja representa baixa (ou decisao) gravada."""
    return _chave_texto(status) in STATUS_JA_BAIXADO


def _agencia_da_descricao(desc: str) -> str:
    """Retorna o trecho de agencia da descricao da instancia.

    Cobre os formatos gravados no SGE: "... - A:0001-9 - C: 1265692-5",
    "...- A:RDC FLEX- C:1926-7" e o estilo do nome do arquivo
    "INFRENDTRI ITAU_CIAL AG 4372 CC 13288-8". Usa a ULTIMA ocorrencia de "A:"
    antes da conta porque a descricao pode vir prefixada por texto livre
    ("Extratos Bancarios - Aplicacoes Financeiras - PDF/OFX -
    EXTAPL-SICOOB-A:RDC AUT-C:1926-7"). Retorna "" quando nao traz agencia.
    """
    cabeca, separador, _ = desc.rpartition("C:")
    if not separador:
        cabeca = desc
    _, separador, agencia = cabeca.rpartition("A:")
    if separador:
        return agencia

    encontrado = re.search(
        r"(?:^|[^A-Z0-9])AG[\s:_-]+([0-9][0-9.\s-]*)", desc.upper()
    )
    return encontrado.group(1).strip() if encontrado else ""


def _conta_da_descricao(desc: str) -> str:
    """Digitos da conta na descricao da instancia (sem zeros a esquerda).

    Dois formatos convivem no SGE: "- C: 1265692-5" (extratos bancarios) e
    "CC 13288-8", que espelha o nome do arquivo (caso do INFRENDTRI). Sem o
    segundo formato a instancia so casaria pelo fallback "unica do banco", que
    falha assim que a empresa tem mais de uma conta no mesmo banco.
    Retorna "" quando a descricao nao traz conta.
    """
    if "C:" in desc:
        return re.sub(r"[^0-9]", "", desc.split("C:")[-1]).lstrip("0")

    encontrado = re.search(
        r"(?:^|[^A-Z0-9])CC[\s:_-]+([0-9][0-9.\s-]*)", desc.upper()
    )
    if encontrado:
        return re.sub(r"[^0-9]", "", encontrado.group(1)).lstrip("0")
    return ""


def _chave_agencia(valor: str) -> str:
    """Chave de agencia: alfanumerica e sem zeros a esquerda ('0001-9' -> '19')."""
    return _chave_texto(valor).lstrip("0")


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
    banco_key = _chave_texto(banco)
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
        if banco_key not in _chave_texto(desc):
            continue

        # Sem conta no arquivo, toda instancia do banco e candidata.
        if not conta_clean:
            candidatos.append((codigo, desc))
            continue

        # Conta da instancia (ignorando zeros a esquerda), se houver.
        desc_conta_clean = _conta_da_descricao(desc)

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


@dataclass(frozen=True)
class SituacaoControle:
    """Situacao do registro de controle do SGE para um documento.

    ``cadastrado`` responde "existe registro para baixar?"; ``ja_baixado``
    responde "esse registro ja tem baixa?" — os fluxos precisam das duas antes
    de chamar ``baixar_controle_supabase``.
    """

    cadastrado: bool
    id_empresa: str | None = None
    status_envio: str = ""
    nome_arquivo: str = ""
    data_recebimento: str = ""
    instancia_descricao: str = ""

    @property
    def ja_baixado(self) -> bool:
        return status_ja_baixado(self.status_envio)

    def resumo(self) -> str:
        """Descricao curta da baixa existente, para log e notificacao."""
        partes = [f"status '{self.status_envio}'"]
        if self.nome_arquivo:
            partes.append(f"arquivo '{self.nome_arquivo}'")
        if self.data_recebimento:
            partes.append(f"recebido em {self.data_recebimento}")
        if self.instancia_descricao:
            partes.append(f"instancia '{self.instancia_descricao}'")
        return ", ".join(partes)


def _registro_da_instancia(
    registros: list[dict], banco: str, agencia: str, conta: str
) -> dict | None:
    """Escolhe, entre os registros de controle, o que corresponde ao arquivo.

    Documento sem instancia (FECFIN e afins) tem registro unico, com
    ``instancia_id`` nulo. Com instancia (extratos), o casamento reusa a mesma
    logica banco+agencia+conta da baixa, sobre a descricao da instancia que a
    propria API devolve. Retorna None quando nao da para afirmar qual registro
    e o do arquivo — adivinhar aqui levaria a checar a baixa da conta errada.
    """
    if not banco and not conta:
        sem_instancia = [r for r in registros if not r.get("instancia_id")]
        if len(sem_instancia) == 1:
            return sem_instancia[0]
        return registros[0] if len(registros) == 1 else None

    id_alvo = _match_instancia(
        [
            {"codigo": r.get("id"), "descricao": r.get("instancia_descricao") or ""}
            for r in registros
            if r.get("id")
        ],
        banco,
        conta,
        agencia,
    )
    if not id_alvo:
        return None
    return next((r for r in registros if str(r.get("id")) == str(id_alvo)), None)


def consultar_situacao_controle(
    empresa_codigo: str,
    competencia: str,
    codigo_documento: str,
    *,
    banco: str = "",
    agencia: str = "",
    conta: str = "",
    url: str = SUPABASE_CONTROLE_GET_URL,
    key: str = SUPABASE_CONTROLE_KEY,
) -> SituacaoControle:
    """GET /controle - diz se o documento esta cadastrado e se ja foi baixado.

    Uma consulta so responde as duas perguntas que os fluxos fazem antes da
    baixa. A segunda existe porque ``POST /controle/baixa`` sobrescreve o
    registro sem olhar o status: sem esta checagem, reprocessar um arquivo
    apagaria a baixa anterior (nome do arquivo, quantidade, local e data).
    """
    registros = _listar_registros_controle(
        empresa_codigo,
        competencia,
        codigo_documento,
        limite=LIMITE_REGISTROS_CONTROLE,
        url=url,
        key=key,
    )

    # O filtro de codigo_documento da API e "contem": consultar MEDPRUD tambem
    # traz as linhas de COMPMEDPRUD. So o codigo exato interessa.
    alvo = str(codigo_documento).strip().upper()
    registros = [
        r
        for r in registros
        if str(r.get("codigo_documento") or "").strip().upper() == alvo
    ]

    if not registros:
        logger.warning(
            "Nenhum registro encontrado no SGE: empresa=%s competencia=%s cod_doc=%s",
            empresa_codigo,
            competencia,
            codigo_documento,
        )
        return SituacaoControle(cadastrado=False)

    id_empresa = next(
        (str(r["id_empresa"]) for r in registros if r.get("id_empresa")), None
    )
    if not id_empresa:
        logger.warning(
            "Registro encontrado mas id_empresa ausente: empresa=%s competencia=%s",
            empresa_codigo,
            competencia,
        )
        return SituacaoControle(cadastrado=False)

    registro = _registro_da_instancia(registros, banco, agencia, conta)
    if registro is None:
        # Instancia indeterminada por aqui: so trata como baixado se TODAS as
        # linhas do documento ja estiverem baixadas — qualquer que seja a certa,
        # a baixa sobrescreveria uma delas. Havendo alguma pendente, segue e
        # deixa o proprio POST /controle/baixa resolver a instancia.
        baixados = [r for r in registros if status_ja_baixado(r.get("status_envio"))]
        if len(baixados) != len(registros):
            return SituacaoControle(cadastrado=True, id_empresa=id_empresa)
        logger.info(
            "Instancia nao identificada para empresa=%s cod_doc=%s banco=%s conta=%s, "
            "mas todos os %s registro(s) da competencia ja estao baixados",
            empresa_codigo,
            codigo_documento,
            banco or "-",
            conta or "-",
            len(registros),
        )
        registro = baixados[0]

    return SituacaoControle(
        cadastrado=True,
        id_empresa=id_empresa,
        status_envio=str(registro.get("status_envio") or ""),
        nome_arquivo=str(registro.get("nome_arquivo") or ""),
        data_recebimento=str(registro.get("data_recebimento") or ""),
        instancia_descricao=str(registro.get("instancia_descricao") or ""),
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
