"""Diagnostica por que um arquivo FECFIN nao foi reconhecido/convertido.

Ferramenta somente leitura: nao acessa o Google Drive nem o SGE, nao grava
nada. Recebe um .xlsx local e mostra, na ordem em que o fluxo real decide:

1. o stem normalizado e o tipo identificado pelo nome;
2. as abas do arquivo (em repr, para expor NBSP e afins) e as abas bancarias;
3. quais handlers do registry casariam com o arquivo;
4. as primeiras linhas cruas de cada aba bancaria (para conferir o indice
   real do cabecalho); e
5. o resultado de cada processador, com o traceback completo da excecao que
   o fluxo em producao engole.

Uso:
    python scripts/diagnosticar_fecfin.py "C:/.../0726_FECFIN_AD 52.xlsx"
"""

import sys
import traceback
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.schemas.fecfin import registry  # noqa: E402
from src.schemas.fecfin.ce_part import (  # noqa: E402
    _identificar_bancos,
    _identificar_tipo_arquivo,
    _normalizar_identificador,
    _PROCESSADORES_ACR,
    _PROCESSADORES_AD_52,
    _PROCESSADORES_ADP_PART,
    _PROCESSADORES_ALOHA,
    _PROCESSADORES_CE_PART,
    _PROCESSADORES_CEMAF_60,
    _PROCESSADORES_CEMAF_PART,
)


_PROCESSADORES_POR_TIPO = {
    "CEMAF_PART": _PROCESSADORES_CEMAF_PART,
    "CEMAF_60": _PROCESSADORES_CEMAF_60,
    "ALOHA": _PROCESSADORES_ALOHA,
    "ADP_PART": _PROCESSADORES_ADP_PART,
    "ACR": _PROCESSADORES_ACR,
    "AD_52": _PROCESSADORES_AD_52,
    "CE_PART": _PROCESSADORES_CE_PART,
}

LINHAS_PREVIEW = 8


def _titulo(texto: str) -> None:
    print()
    print("=" * 78)
    print(texto)
    print("=" * 78)


def _diagnosticar_nome(file_stem: str) -> str | None:
    _titulo("1. NOME DO ARQUIVO")
    print(f"stem bruto      : {file_stem!r}")
    print(f"stem normalizado: {_normalizar_identificador(file_stem)!r}")
    tipo = _identificar_tipo_arquivo(file_stem)
    print(f"tipo identificado: {tipo!r}")
    if tipo is None:
        print(
            "  -> nenhum marcador conhecido (CE PART / CEMAF PART / CEMAF 60 / "
            "ADP PART / ALOHA / ACR / AD 52) foi encontrado no nome."
        )
    return tipo


def _diagnosticar_abas(xls: pd.ExcelFile, tipo: str | None) -> dict[str, str]:
    _titulo("2. ABAS DO ARQUIVO")
    for aba in xls.sheet_names:
        print(f"  {str(aba)!r:<40} -> {_normalizar_identificador(aba)!r}")

    if tipo is None:
        print("\n(sem tipo, _identificar_bancos nao se aplica)")
        return {}

    bancos = _identificar_bancos(xls, tipo)
    print(f"\nabas bancarias reconhecidas para {tipo}: {bancos}")
    if not bancos:
        print("  -> nenhuma aba casou com os bancos esperados deste layout.")
    return bancos


def _diagnosticar_handlers(xls: pd.ExcelFile, file_stem: str) -> None:
    _titulo("3. HANDLERS DO REGISTRY (ordem de dispatch)")
    for handler in registry._FECFIN_HANDLERS:
        nome = type(handler).__name__
        try:
            casou = handler.matches(xls, file_stem=file_stem)
        except Exception as erro:  # nao deveria acontecer, mas nao pode abortar
            print(f"  {nome:<12} ERRO em matches(): {erro!r}")
            continue
        print(f"  {nome:<12} matches={casou}")


def _preview_aba(xls: pd.ExcelFile, aba: str) -> None:
    bruto = pd.read_excel(xls, sheet_name=aba, header=None)
    print(f"\n--- aba {aba!r} — {len(bruto)} linhas x {len(bruto.columns)} colunas ---")
    for i in range(min(LINHAS_PREVIEW, len(bruto))):
        valores = [
            "" if pd.isna(v) else str(v).strip()
            for v in bruto.iloc[i].tolist()
        ]
        print(f"  iloc[{i}] {valores}")


def _diagnosticar_processadores(
    xls: pd.ExcelFile, tipo: str, bancos: dict[str, str]
) -> None:
    _titulo("4. LINHAS CRUAS DAS ABAS BANCARIAS (conferir indice do cabecalho)")
    for aba in bancos.values():
        _preview_aba(xls, aba)

    _titulo("5. EXECUCAO DOS PROCESSADORES")
    processadores = _PROCESSADORES_POR_TIPO.get(tipo, _PROCESSADORES_CE_PART)
    for banco, aba in bancos.items():
        processador = processadores.get(banco)
        print(f"\n--- {banco} (aba {aba!r}) ---")
        if processador is None:
            print("  SEM PROCESSADOR cadastrado para este banco neste tipo.")
            continue
        try:
            df = processador(xls, aba)
        except Exception:
            print("  EXCECAO (engolida em producao por ce_part.py:616):")
            print(traceback.format_exc())
            continue
        print(f"  OK — {len(df)} linha(s)")
        if df.empty:
            print("  DataFrame VAZIO: o fluxo descarta e reporta 'sem layout'.")
        else:
            print(df.head(5).to_string())


def diagnosticar(caminho: Path) -> None:
    print(f"Arquivo: {caminho}")
    file_stem = caminho.stem
    tipo = _diagnosticar_nome(file_stem)

    with pd.ExcelFile(caminho) as xls:
        bancos = _diagnosticar_abas(xls, tipo)
        _diagnosticar_handlers(xls, file_stem)
        if tipo and bancos:
            _diagnosticar_processadores(xls, tipo, bancos)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for argumento in sys.argv[1:]:
        caminho = Path(argumento)
        if not caminho.is_file():
            print(f"Arquivo nao encontrado: {caminho}")
            return 1
        diagnosticar(caminho)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
