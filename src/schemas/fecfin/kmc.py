import logging
import re

import pandas as pd

from src.schemas.fecfin.base import FecfinHandler
from src.schemas.fecfin.registry import register


logger = logging.getLogger(__name__)

# Os rótulos das colunas ficam na linha 1 (a linha 0 é vazia)
_LINHA_CABECALHO = 1

_REQUIRED_COLUMNS = {
    "OBRA",
    "DESCRIÇÃO",
    "NF",
    "CATEGORIA",
    "BANCO",
    "ENTRADA",
    "SAÍDA",
    "DATA",
}

# Abas de competência: 07-2026.  Exclui DRE-07-2026, Old10-2025, TESTE, etc.
_PADRAO_ABA = re.compile(r"^\d{2}-\d{4}$")

# Prefixo MMAA do nome do arquivo: 0726_FECFIN_KMC -> 07-2026
_PADRAO_STEM = re.compile(r"^(\d{2})(\d{2})")

_OBRA_VAZIA = {"", "-", "nan", "NAN"}


def _colunas_aba(xls: pd.ExcelFile, aba: str) -> set[str]:
    """Retorna os rótulos das colunas de uma aba (linha 1)."""
    df = pd.read_excel(xls, sheet_name=aba, header=None, nrows=_LINHA_CABECALHO + 1)
    if len(df) <= _LINHA_CABECALHO:
        return set()
    return {
        str(c).strip()
        for c in df.iloc[_LINHA_CABECALHO]
        if pd.notna(c)
    }


def _aba_valida(xls: pd.ExcelFile, aba: str) -> bool:
    try:
        return _REQUIRED_COLUMNS.issubset(_colunas_aba(xls, aba))
    except Exception:
        return False


def _resolver_aba(xls: pd.ExcelFile, file_stem: str) -> str | None:
    """Descobre a aba de competência a processar.

    Prioriza a competência do nome do arquivo (``0726`` -> ``07-2026``).
    Se ela não existir, usa a última aba ``MM-AAAA`` com as colunas exigidas.
    """
    try:
        abas = [str(aba) for aba in xls.sheet_names]

        match = _PADRAO_STEM.match(file_stem or "")
        if match:
            alvo = f"{match.group(1)}-20{match.group(2)}"
            if alvo in abas and _aba_valida(xls, alvo):
                return alvo

        candidatas = [aba for aba in abas if _PADRAO_ABA.match(aba.strip())]
        for aba in reversed(candidatas):
            if _aba_valida(xls, aba):
                return aba
    except Exception:
        return None

    return None


def _formatar_nf(valor) -> str:
    """Converte a NF para texto sem casas decimais (1043.0 -> 'NF 1043')."""
    if pd.isna(valor):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        texto = str(int(valor))
    else:
        texto = str(valor)
    digitos = re.sub(r"\D", "", texto)
    return f"NF {digitos}" if digitos else ""


def _montar_descricao(row) -> str:
    partes = []

    obra = str(row.get("OBRA", "")).strip()
    if obra not in _OBRA_VAZIA:
        partes.append(obra)

    nf = _formatar_nf(row.get("NF"))
    if nf:
        partes.append(nf)

    descricao = str(row.get("DESCRIÇÃO", "")).strip()
    if descricao and descricao.lower() != "nan":
        partes.append(descricao)

    texto = " ".join(partes).replace("|", " ")
    return re.sub(r"\s+", " ", texto).strip().upper()


@register
class Kmc(FecfinHandler):
    """Layout FECFIN — KMC / A. Monteiro Construtora.

    Planilha com uma aba por competência (``07-2026``), cabeçalho na
    linha 1 e colunas OBRA, DESCRIÇÃO, NF, CATEGORIA, BANCO, ENTRADA,
    SAÍDA, DATA.  A aba processada é a da competência do nome do
    arquivo (``0726_FECFIN_KMC`` -> ``07-2026``).

    Linhas sem ``BANCO`` (saldo anterior, cartão de crédito e totais
    do DRE) são descartadas.  A coluna ``DATA`` vem com células
    mescladas por dia, então precisa de forward-fill.
    """

    bank = "Kmc"

    def matches(self, xls: pd.ExcelFile, file_stem: str = "") -> bool:
        return _resolver_aba(xls, file_stem) is not None

    def parse(
        self, xls: pd.ExcelFile, file_stem: str
    ) -> list[tuple[str, pd.DataFrame]]:
        aba = _resolver_aba(xls, file_stem)
        if aba is None:
            logger.warning("Nenhuma aba de competência encontrada em: %s", file_stem)
            return []

        logger.info("Layout KMC — processando aba %s", aba)

        df = pd.read_excel(xls, sheet_name=aba, header=_LINHA_CABECALHO)
        df.columns = [str(c).strip() for c in df.columns]
        df = df[["OBRA", "DESCRIÇÃO", "NF", "CATEGORIA", "BANCO", "ENTRADA", "SAÍDA", "DATA"]].copy()

        # Datas vêm mescladas por dia — preencher antes de qualquer filtro
        df["DATA"] = pd.to_datetime(df["DATA"], errors="coerce").ffill()

        # Manter apenas as linhas bancárias
        banco_texto = df["BANCO"].astype(str).str.strip()
        df = df[df["BANCO"].notna() & (banco_texto != "") & (banco_texto.str.lower() != "nan")]

        entrada = pd.to_numeric(df["ENTRADA"], errors="coerce")
        saida = pd.to_numeric(df["SAÍDA"], errors="coerce")
        df = df[entrada.notna() | saida.notna()]
        entrada = entrada.loc[df.index]
        saida = saida.loc[df.index]

        df["VALOR"] = entrada.where(entrada.notna(), -saida.fillna(0))
        df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
        df["VALOR"] = df["VALOR"].abs()

        df["DESCRIÇÃO"] = df.apply(_montar_descricao, axis=1)

        df = df.dropna(subset=["DATA"])
        df["DATA"] = df["DATA"].dt.strftime("%d/%m/%Y")

        resultado: list[tuple[str, pd.DataFrame]] = []

        for banco, grupo in df.groupby("BANCO"):
            banco_nome = str(banco).split(" - ")[0].strip().upper()
            df_banco = grupo[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]].reset_index(drop=True)
            resultado.append((banco_nome, df_banco))

        return resultado
