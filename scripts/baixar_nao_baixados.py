"""
Dá baixa no SGE para registros "Não Baixados" da validação.

Lê o relatório validacao_historico_sge.xlsx, agrupa por empresa+competência+banco
e chama a API de baixa para cada conversão única.
"""

import logging
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app.supabase.supabase_api import (
    SUPABASE_BAIXA_URL,
    SUPABASE_CONTROLE_GET_URL,
    SUPABASE_CONTROLE_KEY,
    _converter_competencia,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

RELATORIO = Path.cwd() / "validacao_historico_sge.xlsx"


def consultar_instancias(empresa_codigo: str, competencia: str) -> list[dict]:
    """Consulta instâncias disponíveis para uma empresa+competência no SGE.

    Faz POST para /controle/baixa com dados mínimos para forçar erro 400
    e extrair a lista 'instancias_disponiveis' da resposta.
    """
    headers = {
        "Authorization": f"Bearer {SUPABASE_CONTROLE_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "empresa_codigo": empresa_codigo,
        "codigo_documento": "EXTBAN",
        "competencia": _converter_competencia(competencia),
        "banco": "_TESTE_",
    }
    try:
        r = requests.post(SUPABASE_BAIXA_URL, json=payload, headers=headers, timeout=30)
        if r.status_code == 400:
            data = r.json()
            # Estrutura: {"error": {"message": "...", "details": {"instancias_disponiveis": [...]}}}
            error = data.get("error", data)
            details = error.get("details", {})
            return details.get("instancias_disponiveis", [])
        return []
    except Exception:
        return []


def match_instancia(instancias: list[dict], banco: str, conta: str) -> dict | None:
    """Encontra a instância que bate com banco e conta.

    Cada instância tem: {"codigo": "3", "descricao": "EXTBAN-BANCO SICOOB - A:5004-0 - C: 1084669-7"}
    """
    banco_upper = banco.upper().strip()
    conta_clean = conta.replace("-", "").replace(" ", "").strip()

    for inst in instancias:
        codigo = str(inst.get("codigo", "")).strip()
        desc = str(inst.get("descricao", "")).upper()

        # Verificar se o banco está na descrição
        if banco_upper not in desc:
            continue

        # Se temos conta, verificar se bate
        if conta_clean:
            # Extrair conta da descrição (após "C:")
            if "C:" in desc:
                desc_conta = desc.split("C:")[-1].strip()
                desc_conta_clean = re.sub(r"[^0-9]", "", desc_conta)
                if desc_conta_clean and desc_conta_clean == conta_clean:
                    return inst
        else:
            # Sem conta, retornar primeira instância deste banco
            return inst

    return None


def baixar_com_instancia(
    empresa_codigo: str,
    competencia: str,
    codigo_documento: str,
    banco: str,
    instancia_codigo: str,
    nome_arquivo: str,
    local_arquivo: str,
    quantidade_arquivos: int,
    status: str = "Enviado",
    data_recebimento: str | None = None,
) -> tuple[bool, str | None]:
    """Chama POST /controle/baixa com instancia_codigo."""
    payload = {
        "empresa_codigo": empresa_codigo,
        "codigo_documento": codigo_documento,
        "competencia": _converter_competencia(competencia),
        "banco": banco,
        "instancia_codigo": instancia_codigo,
        "nome_arquivo": nome_arquivo,
        "local_arquivo": local_arquivo,
        "quantidade_arquivos": quantidade_arquivos,
        "status_envio": status,
        "data_recebimento": data_recebimento or "",
    }
    headers = {
        "Authorization": f"Bearer {SUPABASE_CONTROLE_KEY}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(SUPABASE_BAIXA_URL, json=payload, headers=headers, timeout=30)
        texto = (r.text or "")[:500]
        if r.status_code == 404:
            return False, f"Empresa {empresa_codigo} não cadastrada no SGE"
        if r.status_code < 200 or r.status_code >= 300:
            return False, f"Status {r.status_code}: {texto}"
        return True, None
    except Exception as e:
        return False, str(e)


def extrair_agencia_conta(nome_arquivo: str) -> tuple[str, str]:
    """Tenta extrair agência e conta do nome do arquivo.

    Padrões suportados:
      0526_EXTBAN ITAU 98313-2_SILVA.pdf          → ag="", conta="98313-2"
      0526_EXTBAN_CORA_WFORTE_AG 0001_CC 43613177.pdf → ag="0001", conta="43613177"
      0526_EXTBAN SICOOB_SILVA.pdf                → ag="", conta="" (sem info)
    """
    stem = Path(nome_arquivo).stem
    partes = stem.split("_")

    # Padrão novo: _AG XXX_CC YYYYY
    agencia = ""
    conta = ""
    for i, parte in enumerate(partes):
        parte_upper = parte.strip().upper()
        if parte_upper == "AG" and i + 1 < len(partes):
            agencia = partes[i + 1].strip()
        elif parte_upper == "CC" and i + 1 < len(partes):
            conta = partes[i + 1].strip()

    if agencia or conta:
        return agencia, conta

    # Padrão antigo: banco_contaEmpresa ou banco contaEmpresa
    # Ex: "ITAU 98313-2_SILVA" → partes[-2] = "ITAU 98313-2", partes[-1] = "SILVA"
    # O número depois do banco pode ser a conta
    if len(partes) >= 3:
        banco_parte = partes[2] if len(partes) > 2 else ""
        # Verificar se há número no nome do banco (ex: "ITAU 98313-2")
        match = re.search(r"(\d[\d\-]+\d)", banco_parte)
        if match:
            conta = match.group(1)
            return "", conta

    return "", ""


def competencia_de_data(valor) -> str:
    """Converte competência para formato 'MM-YYYY' esperado pelo SGE.

    Aceita: '2026-06', '2026-06-13 11:45:12', '06-2026', etc.
    """
    if pd.isna(valor) or valor is None:
        return ""
    texto = str(valor).strip()
    # Formato YYYY-MM (já vem da planilha)
    match = re.match(r"^(\d{4})-(\d{2})$", texto)
    if match:
        return f"{match.group(2)}-{match.group(1)}"
    # Formato YYYY-MM-DD ...
    if len(texto) >= 10:
        partes = texto[:10].split("-")
        if len(partes) >= 2:
            return f"{partes[1]}-{partes[0]}"
    return ""


def data_recebimento_de_valor(valor) -> str:
    """Extrai 'YYYY-MM-DD' a partir de 'YYYY-MM-DD HH:MM:SS'."""
    if pd.isna(valor) or valor is None:
        return ""
    texto = str(valor).strip()
    if len(texto) >= 10:
        return texto[:10]
    return ""


def main() -> None:
    t0 = time.perf_counter()

    # 1. Ler relatório
    if not RELATORIO.exists():
        logger.error("Relatório não encontrado: %s", RELATORIO)
        return

    df = pd.read_excel(RELATORIO, sheet_name="Não Baixados")
    logger.info("Registros Não Baixados: %s", len(df))

    # Normalizar colunas
    df.columns = [str(c).strip() for c in df.columns]

    # 2. Agrupar por (empresa_id, competência, banco)
    grupos = df.groupby(["Empresa ID", "Competência", "Banco"])
    chaves = list(grupos.groups.keys())
    logger.info("Grupos únicos (empresa+comp+banco): %s", len(chaves))

    # 3. Cache de instâncias por empresa+competência
    cache_instancias: dict[tuple[str, str], list[dict]] = {}

    # 4. Processar cada grupo
    sucesso = 0
    pulados = 0
    erros = 0
    resultados = []

    for idx, (emp_id, comp, banco) in enumerate(chaves, 1):
        grupo = grupos.get_group((emp_id, comp, banco))

        # Pegar primeira linha para dados gerais
        primeira = grupo.iloc[0]
        data_receb = data_recebimento_de_valor(primeira.get("Data/Hora Movimento"))

        # Pegar o PDF (primeiro arquivo do grupo)
        arquivos = grupo["Arquivo Histórico"].tolist()
        pdf_nome = None
        for arq in arquivos:
            if str(arq).lower().endswith(".pdf"):
                pdf_nome = str(arq).strip()
                break
        if not pdf_nome and arquivos:
            pdf_nome = str(arquivos[0]).strip()

        # Extrair conta do nome
        _, conta = extrair_agencia_conta(pdf_nome or "")

        # Converter competência para formato MM-YYYY
        emp_str = str(int(emp_id)) if isinstance(emp_id, float) else str(emp_id)
        comp_sg = competencia_de_data(comp) if "-" in str(comp) else str(comp)

        # Consultar instâncias (com cache)
        cache_key = (emp_str, comp_sg)
        if cache_key not in cache_instancias:
            cache_instancias[cache_key] = consultar_instancias(emp_str, comp_sg)
        instancias = cache_instancias[cache_key]

        # Encontrar instância que bate com banco+conta
        inst_match = match_instancia(instancias, banco, conta)

        if not inst_match:
            logger.warning(
                "[%s/%s] PULADO - instância não encontrada: emp=%s comp=%s banco=%s conta=%s",
                idx, len(chaves), emp_str, comp_sg, banco, conta,
            )
            pulados += 1
            resultados.append({
                "empresa_id": emp_str,
                "competencia": comp,
                "banco": banco,
                "arquivo": pdf_nome,
                "status": "Pulado",
                "detalhe": f"Instância não encontrada para banco={banco} conta={conta}",
            })
            continue

        inst_codigo = str(inst_match.get("codigo", "")).strip()

        logger.info(
            "[%s/%s] Baixando: emp=%s comp=%s banco=%s inst=%s arquivo=%s",
            idx, len(chaves), emp_str, comp_sg, banco, inst_codigo, pdf_nome,
        )

        ok, detalhe = baixar_com_instancia(
            empresa_codigo=emp_str,
            competencia=comp_sg,
            codigo_documento="EXTBAN",
            banco=banco,
            instancia_codigo=inst_codigo,
            nome_arquivo=pdf_nome or "",
            local_arquivo="Google Drive",
            quantidade_arquivos=3,
            status="Enviado",
            data_recebimento=data_receb,
        )

        if ok:
            sucesso += 1
            resultados.append({
                "empresa_id": emp_str,
                "competencia": comp,
                "banco": banco,
                "arquivo": pdf_nome,
                "status": "Sucesso",
                "detalhe": "",
            })
        else:
            erros += 1
            resultados.append({
                "empresa_id": emp_str,
                "competencia": comp,
                "banco": banco,
                "arquivo": pdf_nome,
                "status": "Erro",
                "detalhe": detalhe or "Erro desconhecido",
            })

        time.sleep(0.2)  # rate limit

    elapsed = time.perf_counter() - t0

    # 4. Resumo
    print("\n=== RESUMO DA BAIXA ===")
    print(f"  Sucesso: {sucesso}")
    print(f"  Pulados (sem ag/cc): {pulados}")
    print(f"  Erros: {erros}")
    print(f"  Total: {len(chaves)}")
    print(f"  Tempo: {elapsed:.1f}s")

    if erros > 0:
        print("\n--- Detalhes dos erros ---")
        for r in resultados:
            if r["status"] == "Erro":
                print(f"  emp={r['empresa_id']} comp={r['competencia']} banco={r['banco']}: {r['detalhe']}")

    # 5. Salvar log
    log_path = Path.cwd() / "log_baixa_nao_baixados.xlsx"
    df_log = pd.DataFrame(resultados)
    df_log.to_excel(log_path, index=False)
    print(f"\nLog salvo em: {log_path}")


if __name__ == "__main__":
    main()
