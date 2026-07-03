"""Fallback de extração via IA (Gemini) para extratos ilegíveis.

Usado quando o ``PDFExtractor`` não consegue extrair texto do PDF (scan/imagem,
fontes ``cid:``, página em branco) ou quando nenhum parser de banco reconhece o
layout. O PDF é enviado à API do Gemini, que valida se é um extrato bancário e
devolve as movimentações estruturadas em JSON. A saída é normalizada para o mesmo
contrato dos parsers de banco: colunas ``DATA, DESCRIÇÃO, VALOR, TIPO``.
"""

import json
import logging
import os

import pandas as pd

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

COLUNAS_CONTRATO = ["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]

PROMPT = """
Leia o PDF enviado e primeiro valide se o documento é um extrato bancário.

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

REGRAS IMPORTANTES:

1. Retorne somente um JSON válido, sem explicações, sem markdown e sem texto antes ou depois.
2. Não retorne amostra. Extraia todas as movimentações financeiras encontradas no PDF.
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
15. Antes de extrair movimentações, confirme que o documento possui características suficientes de extrato bancário.
16. Caso o documento seja boleto, nota fiscal, contrato, comprovante, recibo, relatório genérico, imagem sem dados bancários ou qualquer outro tipo de documento que não seja extrato bancário, retorne o JSON de documento inválido.
17. Se houver dúvida se o documento é ou não extrato bancário, considere inválido e informe em observacoes o motivo da dúvida.

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
{
"data": null,
"descricao": null,
"documento": null,
"valor": null,
"tipo": null,
"saldo_apos_movimentacao": null,
"categoria_original": null
}
],
"observacoes": []
}
"""


def _limpar_json_texto(texto: str) -> str:
    """Remove fences markdown (```json ... ```) que o modelo às vezes adiciona."""
    texto = (texto or "").strip()
    if texto.startswith("```"):
        texto = texto.split("\n", 1)[-1] if "\n" in texto else texto
        texto = texto.rsplit("```", 1)[0]
    return texto.strip()


def _montar_dataframe(movimentacoes: list[dict]) -> pd.DataFrame:
    """Normaliza as movimentações do Gemini para o contrato DATA/DESCRIÇÃO/VALOR/TIPO."""
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


def extrair_extrato_ia(caminho_pdf) -> pd.DataFrame | None:
    """Envia o PDF ao Gemini e retorna um DataFrame no contrato do fluxo.

    Retorna ``None`` (degradação segura) quando: não há chave de API configurada,
    o modelo indica que o documento não é um extrato bancário, ou ocorre qualquer
    erro de upload/API/parse. Nesses casos o chamador mantém o comportamento
    atual (mover para 00_INVALIDOS).
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("gemini_api_key")
    if not api_key:
        logger.warning(
            "GEMINI_API_KEY nao configurada; fallback via IA desabilitado para %s",
            caminho_pdf,
        )
        return None

    arquivo_upload = None
    client = None
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        logger.info("Enviando PDF para o Gemini (%s): %s", GEMINI_MODEL, caminho_pdf)
        arquivo_upload = client.files.upload(file=str(caminho_pdf))

        resposta = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[arquivo_upload, PROMPT],
        )

        dados = json.loads(_limpar_json_texto(resposta.text))

        if dados.get("documento_valido") is not True:
            logger.info(
                "Gemini nao reconheceu como extrato bancario: %s | mensagem=%s",
                caminho_pdf,
                dados.get("mensagem"),
            )
            return None

        movimentacoes = dados.get("movimentacoes") or []
        df = _montar_dataframe(movimentacoes)
        logger.info(
            "Gemini extraiu %s movimentacao(oes) de %s", len(df), caminho_pdf
        )
        return df

    except Exception:
        logger.exception("Falha ao extrair extrato via Gemini: %s", caminho_pdf)
        return None

    finally:
        if client is not None and arquivo_upload is not None:
            try:
                client.files.delete(name=arquivo_upload.name)
            except Exception:
                logger.warning(
                    "Nao foi possivel remover o arquivo enviado ao Gemini: %s",
                    getattr(arquivo_upload, "name", None),
                )
