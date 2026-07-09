"""Fallback de extração via IA (OpenAI) para extratos ilegíveis.

Usado quando o ``PDFExtractor`` não consegue extrair texto do PDF (scan/imagem,
fontes ``cid:``, página em branco) ou quando nenhum parser de banco reconhece o
layout. O PDF é enviado como file input à Responses API da OpenAI, que valida se
é um extrato bancário e devolve as movimentações estruturadas em JSON. A saída é
normalizada para o mesmo contrato dos parsers de banco: colunas
``DATA, DESCRIÇÃO, VALOR, TIPO``.

Garantias de completude (nenhuma movimentação perdida):

* PDFs longos são divididos em lotes de páginas (``IA_PAGINAS_POR_LOTE``)
  para a resposta nunca estourar o limite de saída do modelo;
* respostas truncadas em ``max_output_tokens`` são detectadas e o lote é
  redividido;
* o modelo autodeclara ``total_movimentacoes`` e, se a contagem divergir da
  lista retornada, a chamada é refeita e vence a resposta mais completa;
* saída JSON forçada via ``text.format=json_object``.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

# PDFs com mais páginas que isso são processados em lotes; respostas muito
# longas numa chamada única são a principal causa de movimentações perdidas.
PAGINAS_POR_LOTE = max(
    1, int(os.getenv("IA_PAGINAS_POR_LOTE") or os.getenv("GEMINI_PAGINAS_POR_LOTE") or "4")
)
MAX_OUTPUT_TOKENS = int(
    os.getenv("IA_MAX_OUTPUT_TOKENS") or os.getenv("GEMINI_MAX_OUTPUT_TOKENS") or "65536"
)

COLUNAS_CONTRATO = ["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]

REGRAS = """REGRAS IMPORTANTES:

1. Retorne somente um JSON válido, sem explicações, sem markdown e sem texto antes ou depois.
2. Não retorne amostra nem resumo. Extraia TODAS as movimentações financeiras encontradas no PDF, de todas as páginas, da primeira à última linha. Nunca pare no meio da lista.
3. Não invente informações.
4. Se algum campo não for encontrado no PDF, retorne null.
5. Preserve os dados exatamente como aparecem no extrato sempre que possível.
6. Datas devem ser retornadas no formato YYYY-MM-DD quando for possível identificar o ano.
7. Se a data da movimentação aparecer sem ano, use o ano identificado no período do extrato.
8. Valores devem ser retornados como número decimal, usando ponto como separador decimal.
   Exemplo: R$ 1.234,56 deve virar 1234.56.
9. O campo tipo deve ser:

   * "C" para crédito/entrada/recebimento;
   * "D" para débito/saída/pagamento;
   * null se não for possível identificar.
10. Não misture informações de uma movimentação com outra.
11. Se uma descrição estiver quebrada em várias linhas, una as linhas que pertencem à mesma movimentação.
12. Ignore linhas de cabeçalho, rodapé, saldo anterior, saldo final, totalizadores, mensagens institucionais e propagandas.
13. Não considere saldos como movimentações financeiras, a menos que o PDF indique claramente que é uma transação.
14. Mantenha a ordem original das movimentações conforme aparecem no extrato.
15. Se a mesma movimentação (mesma data, mesma descrição e mesmo valor) aparecer mais de uma vez no extrato, retorne todas as ocorrências. NÃO remova o que parecer duplicado.
16. Ao final, conte as movimentações extraídas e preencha o campo total_movimentacoes com esse número. Antes de responder, releia o documento página por página e confirme que nenhuma movimentação ficou de fora; se encontrar alguma faltando, adicione-a na posição correta."""

ESTRUTURA_MOVIMENTACAO = """{
"data": null,
"descricao": null,
"documento": null,
"valor": null,
"tipo": null,
"saldo_apos_movimentacao": null,
"categoria_original": null
}"""

PROMPT = (
    """Leia o PDF enviado e primeiro valide se o documento é um extrato bancário.

Um documento deve ser considerado extrato bancário somente se apresentar evidências claras de extrato, como:

* nome de banco ou instituição financeira;
* dados de conta, agência, titular ou cliente;
* período do extrato, competência ou intervalo de datas;
* lista de movimentações financeiras;
* saldos, lançamentos, créditos, débitos, pagamentos, transferências, PIX, tarifas ou depósitos.

Se o PDF NÃO for um extrato bancário, retorne somente o JSON abaixo, sem explicações, sem markdown e sem texto antes ou depois:

{
"mensagem": "O PDF enviado não é um extrato bancário."
}

Caso o documento seja boleto, nota fiscal, contrato, comprovante, recibo, relatório genérico, imagem sem dados bancários ou qualquer outro tipo de documento que não seja extrato bancário, retorne o JSON de documento inválido. Se houver dúvida se o documento é ou não extrato bancário, considere inválido e informe em observacoes o motivo da dúvida.

Se o PDF for um extrato bancário, extraia as informações estruturadas abaixo.

O objetivo é identificar:

* banco;
* cliente/titular da conta;
* agência;
* número da conta;
* período de competência do extrato;
* data inicial do extrato;
* data final do extrato;
* todas as movimentações financeiras presentes no documento.

"""
    + REGRAS
    + """

ESTRUTURA JSON OBRIGATÓRIA PARA EXTRATO BANCÁRIO VÁLIDO:

{
"documento_valido": true,
"tipo_documento_identificado": "extrato_bancario",
"mensagem": null,
"banco": {
"nome": null,
"codigo": null
},
"cliente": {
"nome": null,
"documento": null
},
"conta": {
"agencia": null,
"numero_conta": null,
"tipo_conta": null
},
"extrato": {
"competencia": null,
"data_inicio": null,
"data_fim": null,
"moeda": "BRL"
},
"movimentacoes": [
"""
    + ESTRUTURA_MOVIMENTACAO
    + """
],
"total_movimentacoes": null,
"observacoes": []
}
"""
)


class _RespostaTruncada(Exception):
    """Resposta do modelo cortada pelo limite de tokens de saída."""


def _prompt_continuacao(contexto: dict | None) -> str:
    """Prompt para lotes de páginas intermediárias/finais de um extrato já validado."""
    contexto = contexto or {}
    return (
        "O PDF enviado contém páginas de continuação de um extrato bancário maior "
        "que já foi validado como legítimo. Estas páginas podem não ter cabeçalho "
        "com os dados do banco ou da conta; mesmo assim, trate o documento como "
        "extrato válido.\n\n"
        "Contexto do extrato completo:\n"
        f"* banco: {contexto.get('banco')}\n"
        f"* competência: {contexto.get('competencia')}\n"
        f"* data inicial: {contexto.get('data_inicio')}\n"
        f"* data final: {contexto.get('data_fim')}\n\n"
        "Extraia TODAS as movimentações financeiras presentes nestas páginas.\n\n"
        + REGRAS
        + "\n\nESTRUTURA JSON OBRIGATÓRIA:\n\n{\n\"documento_valido\": true,\n\"movimentacoes\": [\n"
        + ESTRUTURA_MOVIMENTACAO
        + "\n],\n\"total_movimentacoes\": null,\n\"observacoes\": []\n}\n"
    )


def _limpar_json_texto(texto: str) -> str:
    """Remove fences markdown (```json ... ```) que o modelo às vezes adiciona."""
    texto = (texto or "").strip()
    if texto.startswith("```"):
        texto = texto.split("\n", 1)[-1] if "\n" in texto else texto
        texto = texto.rsplit("```", 1)[0]
    return texto.strip()


def _montar_dataframe(movimentacoes: list[dict]) -> pd.DataFrame:
    """Normaliza as movimentações da IA para o contrato DATA/DESCRIÇÃO/VALOR/TIPO."""
    if not movimentacoes:
        return pd.DataFrame(columns=COLUNAS_CONTRATO)

    df = pd.DataFrame(movimentacoes)

    for coluna in ("categoria_original", "descricao", "documento"):
        if coluna not in df.columns:
            df[coluna] = None

    df["DESCRIÇÃO"] = df.apply(
        lambda x: f"{x['categoria_original']} {x['descricao']} {x['documento']}",
        axis=1,
    )
    df = df.rename(columns={"data": "DATA", "valor": "VALOR", "tipo": "TIPO"})
    df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]

    # remove prefixo repetido (ex.: "PIX PIX ..." -> "PIX ...")
    df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.replace(
        r"^(.+?)\s+\1(?=\s|$)", r"\1", regex=True
    )
    df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.strip().str.upper()
    df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.replace(r"NONE", "", regex=True)
    df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.replace(r"\s+", " ", regex=True).str.strip()

    df["DATA"] = pd.to_datetime(df["DATA"], errors="coerce").dt.strftime("%d/%m/%Y")

    df["VALOR"] = pd.to_numeric(df["VALOR"], errors="coerce").abs()

    df["TIPO"] = df["TIPO"].astype("string").str.upper().str.strip()
    df.loc[~df["TIPO"].isin(["C", "D"]), "TIPO"] = None

    return df[COLUNAS_CONTRATO]


def _chamar_openai(client, caminho_pdf, prompt: str) -> dict:
    """Envia o PDF como file input à Responses API e retorna o JSON parseado.

    Levanta ``_RespostaTruncada`` quando a resposta estoura ``MAX_OUTPUT_TOKENS``
    (JSON incompleto = movimentações perdidas; o chamador redivide o PDF).
    """
    with open(caminho_pdf, "rb") as arquivo:
        arquivo_upload = client.files.create(file=arquivo, purpose="user_data")
    try:
        resposta = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_file", "file_id": arquivo_upload.id},
                        {"type": "input_text", "text": prompt},
                    ],
                }
            ],
            text={"format": {"type": "json_object"}},
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    finally:
        try:
            client.files.delete(arquivo_upload.id)
        except Exception:
            logger.warning(
                "Nao foi possivel remover o arquivo enviado a OpenAI: %s",
                getattr(arquivo_upload, "id", None),
            )

    if getattr(resposta, "status", None) == "incomplete":
        detalhes = getattr(resposta, "incomplete_details", None)
        if getattr(detalhes, "reason", None) == "max_output_tokens":
            raise _RespostaTruncada(
                f"Resposta da OpenAI truncada em max_output_tokens para {caminho_pdf}"
            )

    return json.loads(_limpar_json_texto(resposta.output_text))


def _contagem_confere(dados: dict) -> bool:
    total = dados.get("total_movimentacoes")
    if not isinstance(total, int):
        return True
    return total == len(dados.get("movimentacoes") or [])


def _chamar_com_conferencia(client, caminho_pdf, prompt: str, rotulo: str) -> dict:
    """Chama a OpenAI e confere a contagem autodeclarada de movimentações.

    Se ``total_movimentacoes`` divergir da lista retornada, refaz a chamada uma
    vez e mantém a resposta com mais movimentações (proteção contra respostas
    incompletas).
    """
    dados = _chamar_openai(client, caminho_pdf, prompt)
    if _contagem_confere(dados):
        return dados

    logger.warning(
        "Contagem de movimentacoes divergente em %s (informado=%s, extraido=%s); refazendo chamada",
        rotulo,
        dados.get("total_movimentacoes"),
        len(dados.get("movimentacoes") or []),
    )
    try:
        nova = _chamar_openai(client, caminho_pdf, prompt)
    except _RespostaTruncada:
        raise
    except Exception:
        logger.exception(
            "Falha na segunda tentativa para %s; usando a primeira resposta", rotulo
        )
        return dados

    if _contagem_confere(nova):
        return nova
    return max(
        (dados, nova), key=lambda d: len(d.get("movimentacoes") or [])
    )


def _contar_paginas(caminho_pdf) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(caminho_pdf)).pages)
    except Exception:
        logger.warning("Nao foi possivel contar as paginas de %s", caminho_pdf)
        return None


def _dividir_pdf_em_lotes(caminho_pdf, paginas_por_lote: int, destino_dir) -> list[Path]:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(caminho_pdf))
    lotes: list[Path] = []
    for inicio in range(0, len(reader.pages), paginas_por_lote):
        writer = PdfWriter()
        for pagina in reader.pages[inicio : inicio + paginas_por_lote]:
            writer.add_page(pagina)
        caminho_lote = Path(destino_dir) / f"lote_{len(lotes) + 1:03d}.pdf"
        with open(caminho_lote, "wb") as arquivo:
            writer.write(arquivo)
        lotes.append(caminho_lote)
    return lotes


def _extrair_contexto(dados: dict) -> dict:
    extrato = dados.get("extrato") or {}
    banco = dados.get("banco") or {}
    return {
        "banco": banco.get("nome"),
        "competencia": extrato.get("competencia"),
        "data_inicio": extrato.get("data_inicio"),
        "data_fim": extrato.get("data_fim"),
    }


def _processar_em_lotes(
    client, caminho_pdf, paginas_por_lote: int, contexto: dict | None = None
) -> tuple[list[dict] | None, dict | None]:
    """Processa o PDF em lotes de páginas e concatena as movimentações na ordem.

    Retorna ``(movimentacoes, contexto)``; ``movimentacoes`` é ``None`` quando o
    primeiro lote indica que o documento não é um extrato bancário. Lotes com
    resposta truncada são redivididos pela metade até chegar a 1 página.
    """
    with tempfile.TemporaryDirectory(prefix="autoextrato_ia_") as destino:
        lotes = _dividir_pdf_em_lotes(caminho_pdf, paginas_por_lote, destino)
        logger.info(
            "Processando %s em %s lote(s) de ate %s pagina(s)",
            caminho_pdf,
            len(lotes),
            paginas_por_lote,
        )
        movimentacoes: list[dict] = []
        for indice, caminho_lote in enumerate(lotes, start=1):
            primeiro = contexto is None and indice == 1
            prompt = PROMPT if primeiro else _prompt_continuacao(contexto)
            rotulo = f"{caminho_pdf} (lote {indice}/{len(lotes)})"
            try:
                dados = _chamar_com_conferencia(client, caminho_lote, prompt, rotulo)
            except _RespostaTruncada:
                if paginas_por_lote <= 1:
                    raise
                logger.warning(
                    "Resposta truncada em %s; redividindo o lote", rotulo
                )
                parciais, contexto_lote = _processar_em_lotes(
                    client,
                    caminho_lote,
                    max(1, paginas_por_lote // 2),
                    contexto=contexto,
                )
                if parciais is None:
                    return None, None
                if contexto is None:
                    contexto = contexto_lote
                movimentacoes.extend(parciais)
                continue

            if primeiro:
                if dados.get("documento_valido") is not True:
                    logger.info(
                        "OpenAI nao reconheceu como extrato bancario: %s | mensagem=%s",
                        caminho_pdf,
                        dados.get("mensagem"),
                    )
                    return None, None
                contexto = _extrair_contexto(dados)

            movimentacoes.extend(dados.get("movimentacoes") or [])

        return movimentacoes, contexto


def _extrair_movimentacoes(client, caminho_pdf) -> list[dict] | None:
    total_paginas = _contar_paginas(caminho_pdf)

    if total_paginas and total_paginas > PAGINAS_POR_LOTE:
        movimentacoes, _ = _processar_em_lotes(client, caminho_pdf, PAGINAS_POR_LOTE)
        return movimentacoes

    try:
        dados = _chamar_com_conferencia(client, caminho_pdf, PROMPT, str(caminho_pdf))
    except _RespostaTruncada:
        if not total_paginas or total_paginas <= 1:
            raise
        logger.warning(
            "Resposta truncada para %s; reprocessando pagina a pagina", caminho_pdf
        )
        movimentacoes, _ = _processar_em_lotes(client, caminho_pdf, 1)
        return movimentacoes

    if dados.get("documento_valido") is not True:
        logger.info(
            "OpenAI nao reconheceu como extrato bancario: %s | mensagem=%s",
            caminho_pdf,
            dados.get("mensagem"),
        )
        return None

    return dados.get("movimentacoes") or []


def extrair_extrato_ia(caminho_pdf) -> pd.DataFrame | None:
    """Envia o PDF à OpenAI e retorna um DataFrame no contrato do fluxo.

    Retorna ``None`` (degradação segura) quando: não há chave de API configurada,
    o modelo indica que o documento não é um extrato bancário, ou ocorre qualquer
    erro de upload/API/parse. Nesses casos o chamador mantém o comportamento
    atual (mover para 00_INVALIDOS).
    """
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("openai_api_key")
    if not api_key:
        logger.warning(
            "OPENAI_API_KEY nao configurada; fallback via IA desabilitado para %s",
            caminho_pdf,
        )
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        logger.info("Enviando PDF para a OpenAI (%s): %s", OPENAI_MODEL, caminho_pdf)

        movimentacoes = _extrair_movimentacoes(client, caminho_pdf)
        if movimentacoes is None:
            return None

        df = _montar_dataframe(movimentacoes)
        logger.info(
            "OpenAI extraiu %s movimentacao(oes) de %s", len(df), caminho_pdf
        )
        return df

    except Exception:
        logger.exception("Falha ao extrair extrato via OpenAI: %s", caminho_pdf)
        return None
