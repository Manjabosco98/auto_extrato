"""
Dá baixa no SGE para registros "Não Baixados" da validação.

Lê o relatório validacao_historico_sge.xlsx (aba "Não Baixados"), re-deriva
banco/agência/conta a partir do NOME DO ARQUIVO (a coluna "Banco" do histórico
vem poluída: "INTER SM", "LANCBAN", conta embutida), detecta extratos SEM
MOVIMENTAÇÃO ([SEM MOV]/SM -> quantidade 1, pois só há o PDF) e chama o cliente
de produção `baixar_controle_supabase`, que trata o 400 (exige instância) fazendo
o match por banco+conta e reporta 404/409 de forma amigável.

Todas as baixas usam status "Enviado" (inclusive SEM MOVIMENTAÇÃO).

Uso:
    python scripts/baixar_nao_baixados.py            # DRY-RUN (nao escreve nada)
    python scripts/baixar_nao_baixados.py --exec     # executa a baixa no SGE
"""

import argparse
import logging
import re
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app.supabase.supabase_api import baixar_controle_supabase
from src.services.conversao import extrair_banco_nome_arquivo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

RELATORIO = Path.cwd() / "validacao_historico_sge.xlsx"


def banco_do_arquivo(nome_arquivo: str) -> str:
    """Banco canônico a partir do nome, removendo o sufixo ' SM'."""
    banco = extrair_banco_nome_arquivo(str(nome_arquivo))
    return re.sub(r"\s+SM$", "", banco).strip()


def eh_sem_movimentacao(nome_arquivo: str, banco_hist: str) -> bool:
    """Detecta extrato SEM MOVIMENTAÇÃO (só o PDF -> quantidade 1 na baixa)."""
    alvo = f"{nome_arquivo} {banco_hist}".upper()
    if "[SEM MOV]" in alvo:
        return True
    # segmento SM isolado: "_SM_", " SM_", " SM " ou terminando em " SM"
    return bool(re.search(r"(?:^|[ _])SM(?:[ _]|$)", alvo))


def extrair_agencia_conta(nome_arquivo: str) -> tuple[str, str]:
    """Extrai agência e conta do nome do arquivo.

    Padrões:
      0526_EXTBAN ITAU 98313-2_SILVA.pdf              -> ag="", conta="98313-2"
      0526_EXTBAN_CORA_WFORTE_AG 0001_CC 43613177.pdf -> ag="0001", conta="43613177"
      0526_LANCBAN_INTER_ACAO_AG 0001-9_CC 5561082-0  -> ag="0001-9", conta="5561082-0"
    """
    stem = Path(nome_arquivo).stem
    partes = stem.split("_")

    agencia = ""
    conta = ""
    for i, parte in enumerate(partes):
        p = parte.strip()
        # Rótulo e valor no mesmo segmento: "AG 0001-9" / "CC 5561082-0"
        m_ag = re.match(r"^AG\s+(.+)$", p, re.IGNORECASE)
        m_cc = re.match(r"^CC\s+(.+)$", p, re.IGNORECASE)
        if m_ag:
            agencia = m_ag.group(1).strip()
        elif m_cc:
            conta = m_cc.group(1).strip()
        # Rótulo sozinho, valor no próximo segmento: "AG"_"0001"
        elif p.upper() == "AG" and i + 1 < len(partes):
            agencia = partes[i + 1].strip()
        elif p.upper() == "CC" and i + 1 < len(partes):
            conta = partes[i + 1].strip()

    if agencia or conta:
        return agencia, conta

    # Padrão antigo: número embutido no segmento do banco ("ITAU 98313-2")
    if len(partes) >= 2:
        match = re.search(r"(\d[\d\-]+\d)", partes[1])
        if match:
            return "", match.group(1)

    return "", ""


def competencia_yyyy_mm(valor) -> str:
    """Normaliza competência para 'YYYY-MM' (aceito pelo cliente de produção)."""
    if pd.isna(valor) or valor is None:
        return ""
    texto = str(valor).strip()
    m = re.match(r"^(\d{4})-(\d{2})$", texto)
    if m:
        return texto
    m = re.match(r"^(\d{2})-(\d{4})$", texto)
    if m:
        return f"{m.group(2)}-{m.group(1)}"
    if len(texto) >= 7 and texto[4] == "-":
        return texto[:7]
    return ""


def data_recebimento_de_valor(valor) -> str:
    """Extrai 'YYYY-MM-DD' a partir de 'YYYY-MM-DD HH:MM:SS'."""
    if pd.isna(valor) or valor is None:
        return ""
    texto = str(valor).strip()
    return texto[:10] if len(texto) >= 10 else ""


def montar_plano(df: pd.DataFrame) -> list[dict]:
    """Agrupa por conversão única e re-deriva os dados para a baixa."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    plano: dict[tuple, dict] = {}
    for _, row in df.iterrows():
        emp = str(row.get("Empresa ID", "")).strip()
        if emp.endswith(".0"):
            emp = emp[:-2]
        arquivo = str(row.get("Arquivo Histórico", "")).strip()
        banco_hist = str(row.get("Banco", "")).strip()

        banco = banco_do_arquivo(arquivo)
        agencia, conta = extrair_agencia_conta(arquivo)
        comp = competencia_yyyy_mm(row.get("Competência"))
        sem_mov = eh_sem_movimentacao(arquivo, banco_hist)
        data_receb = data_recebimento_de_valor(row.get("Data/Hora Movimento"))

        chave = (emp, comp, banco, conta)
        # preferir um PDF como arquivo representativo do grupo
        if chave not in plano or (
            arquivo.lower().endswith(".pdf")
            and not plano[chave]["arquivo"].lower().endswith(".pdf")
        ):
            plano[chave] = {
                "empresa_id": emp,
                "competencia": comp,
                "banco": banco,
                "agencia": agencia,
                "conta": conta,
                "arquivo": arquivo,
                "sem_movimentacao": sem_mov,
                "status": "Enviado",
                "quantidade_arquivos": 1 if sem_mov else 3,
                "data_recebimento": data_receb,
            }
    return list(plano.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Baixa dos 'Não Baixados' no SGE")
    parser.add_argument(
        "--exec",
        action="store_true",
        help="Executa a baixa no SGE. Sem esta flag roda em DRY-RUN (nao escreve).",
    )
    args = parser.parse_args()
    executar = args.exec

    if not RELATORIO.exists():
        logger.error("Relatório não encontrado: %s", RELATORIO)
        return

    df = pd.read_excel(RELATORIO, sheet_name="Não Baixados")
    logger.info("Registros Não Baixados: %s", len(df))

    plano = montar_plano(df)
    logger.info("Grupos únicos (empresa+comp+banco+conta): %s", len(plano))

    modo = "EXECUÇÃO" if executar else "DRY-RUN"
    print(f"\n=== PLANO DE BAIXA ({modo}) ===")
    for p in plano:
        print(
            f"  emp={p['empresa_id']:<5} comp={p['competencia']:<8} "
            f"banco={p['banco']:<12} ag={p['agencia'] or '-':<8} conta={p['conta'] or '-':<12} "
            f"status={p['status']:<15} arq={p['arquivo'][:50]}"
        )

    if not executar:
        print(f"\nDRY-RUN: nada foi enviado ao SGE. Rode com --exec para executar {len(plano)} baixa(s).")
        return

    sucesso = 0
    erros = 0
    resultados = []
    for idx, p in enumerate(plano, 1):
        logger.info(
            "[%s/%s] Baixando: emp=%s comp=%s banco=%s conta=%s status=%s",
            idx, len(plano), p["empresa_id"], p["competencia"], p["banco"], p["conta"], p["status"],
        )
        ok, detalhe = baixar_controle_supabase(
            empresa_codigo=p["empresa_id"],
            codigo_documento="EXTBAN",
            competencia=p["competencia"],
            banco=p["banco"],
            agencia=p["agencia"],
            conta=p["conta"],
            nome_arquivo=p["arquivo"],
            local_arquivo="Google Drive",
            quantidade_arquivos=p["quantidade_arquivos"],
            status=p["status"],
            data_recebimento=p["data_recebimento"] or None,
        )
        p_resultado = {**p, "resultado": "Sucesso" if ok else "Erro", "detalhe": detalhe or ""}
        resultados.append(p_resultado)
        if ok:
            sucesso += 1
        else:
            erros += 1
            logger.warning("  FALHA: %s", detalhe)
        time.sleep(0.2)

    print("\n=== RESUMO DA BAIXA ===")
    print(f"  Sucesso: {sucesso}")
    print(f"  Erros: {erros}")
    print(f"  Total: {len(plano)}")

    if erros:
        print("\n--- Detalhes dos erros ---")
        for r in resultados:
            if r["resultado"] == "Erro":
                print(f"  emp={r['empresa_id']} banco={r['banco']} conta={r['conta'] or '-'}: {r['detalhe']}")

    log_path = Path.cwd() / "log_baixa_nao_baixados.xlsx"
    pd.DataFrame(resultados).to_excel(log_path, index=False)
    print(f"\nLog salvo em: {log_path}")


if __name__ == "__main__":
    main()
