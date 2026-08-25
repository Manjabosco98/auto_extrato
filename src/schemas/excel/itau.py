import logging
import re
import unicodedata

import pandas as pd

from src.schemas.excel.base import ExcelBankHandler
from src.schemas.excel.registry import register


logger = logging.getLogger(__name__)

# O cabeçalho fica na linha 9 do arquivo de referência, mas o bloco de
# identificação acima dele (Atualização/Nome/Agência/Conta/Periodo) varia
# de conta para conta — então a linha é localizada pelo conteúdo.
_LINHAS_BUSCA_CABECALHO = 25

_COLUNAS_CABECALHO = (
    "DATA",
    "LANCAMENTO",
    "RAZAO SOCIAL",
    "CPF/CNPJ",
    "VALOR (R$)",
    "SALDO (R$)",
)

# Descarta SALDO ANTERIOR e SALDO TOTAL DISPONIVEL DIA sem derrubar
# lançamentos reais que só mencionem "saldo" no meio da descrição.
_PADRAO_SALDO = re.compile(r"^\s*SALDO\b")

_COLUNAS_SAIDA = ["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]


def _vazio() -> pd.DataFrame:
    return pd.DataFrame(columns=_COLUNAS_SAIDA)


def _normalizar(valor) -> str:
    """Normaliza um rótulo do Excel para comparação.

    Remove acentos e NBSP: arquivos copiados/renomeados no Drive trazem
    separadores visualmente idênticos a espaços, e nem todo export do Itaú
    acentua os rótulos.
    """
    if pd.isna(valor):
        return ""

    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.split()).upper()


def _localizar_cabecalho(bruto: pd.DataFrame) -> int | None:
    """Retorna o índice da linha de cabeçalho do extrato, ou None."""
    for i in range(min(len(bruto), _LINHAS_BUSCA_CABECALHO)):
        rotulos = {_normalizar(valor) for valor in bruto.iloc[i]}
        if all(coluna in rotulos for coluna in _COLUNAS_CABECALHO):
            return i

    return None


def _posicoes_colunas(linha) -> dict[str, int]:
    posicoes: dict[str, int] = {}

    for posicao, valor in enumerate(linha):
        rotulo = _normalizar(valor)
        if rotulo in _COLUNAS_CABECALHO and rotulo not in posicoes:
            posicoes[rotulo] = posicao

    return posicoes


def _texto(valor) -> str:
    if pd.isna(valor):
        return ""
    return str(valor).strip()


def _montar_descricao(lancamento, razao_social, documento) -> str:
    """Junta só as partes preenchidas de Lançamento/Razão Social/CPF-CNPJ.

    Concatenar tudo e depois remover o texto "nan" (como fazia o protótipo)
    corrompe descrições reais: aplicado antes do upper(), "Fernando" vira
    "Ferdo".
    """
    partes = [
        parte
        for parte in (_texto(lancamento), _texto(razao_social), _texto(documento))
        if parte
    ]
    return re.sub(r"\s+", " ", " ".join(partes)).strip().upper()


def _formatar_data(valor) -> str:
    if pd.isna(valor):
        return ""

    convertido = pd.to_datetime(valor, errors="coerce", dayfirst=True)
    if pd.isna(convertido):
        return _texto(valor)

    return convertido.strftime("%d/%m/%Y")


@register
class ItauExcel(ExcelBankHandler):
    """Extrato do Itaú exportado em Excel.

    Uma única aba (``Lançamentos``), com um bloco de identificação da conta
    no topo e o cabeçalho ``Data | Lançamento | Razão Social | CPF/CNPJ |
    Valor (R$) | Saldo (R$)`` mais abaixo.

    As linhas de saldo (``SALDO ANTERIOR`` e ``SALDO TOTAL DISPONÍVEL DIA``)
    vêm sem ``Valor (R$)``, só com ``Saldo (R$)``, e são descartadas.
    """

    bank = "Itau"

    def matches(self, xls: pd.ExcelFile, file_stem: str = "") -> bool:
        try:
            bruto = pd.read_excel(
                xls,
                sheet_name=0,
                header=None,
                nrows=_LINHAS_BUSCA_CABECALHO,
            )
        except Exception:
            return False

        return _localizar_cabecalho(bruto) is not None

    def parse(self, xls: pd.ExcelFile, file_stem: str = "") -> pd.DataFrame:
        bruto = pd.read_excel(xls, sheet_name=0, header=None)

        linha_cabecalho = _localizar_cabecalho(bruto)
        if linha_cabecalho is None:
            logger.warning("Cabecalho do extrato Itau nao encontrado em: %s", file_stem)
            return _vazio()

        posicoes = _posicoes_colunas(bruto.iloc[linha_cabecalho])
        corpo = bruto.iloc[linha_cabecalho + 1:].reset_index(drop=True)

        if corpo.empty:
            return _vazio()

        valor = pd.to_numeric(corpo.iloc[:, posicoes["VALOR (R$)"]], errors="coerce")

        df = pd.DataFrame(
            {
                "DATA": corpo.iloc[:, posicoes["DATA"]].apply(_formatar_data),
                "DESCRIÇÃO": [
                    _montar_descricao(lancamento, razao_social, documento)
                    for lancamento, razao_social, documento in zip(
                        corpo.iloc[:, posicoes["LANCAMENTO"]],
                        corpo.iloc[:, posicoes["RAZAO SOCIAL"]],
                        corpo.iloc[:, posicoes["CPF/CNPJ"]],
                    )
                ],
                "VALOR": valor,
            }
        )

        # Sem valor não há lançamento: é linha de saldo ou sobra de formatação.
        df = df[df["VALOR"].notna()]
        df = df[df["DESCRIÇÃO"] != ""]
        df = df[~df["DESCRIÇÃO"].str.match(_PADRAO_SALDO)]

        if df.empty:
            return _vazio()

        df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
        df["VALOR"] = df["VALOR"].abs()

        return df[_COLUNAS_SAIDA].reset_index(drop=True)
