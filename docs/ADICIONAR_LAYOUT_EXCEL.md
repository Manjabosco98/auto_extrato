# Como Adicionar um Novo Layout de Extrato em Excel

Este guia explica como adicionar o layout de um extrato **bancário**
exportado em Excel (`.xlsx` / `.xls`) ao fluxo de conversão.

---

## Visão Geral

Alguns bancos disponibilizam o extrato em Excel em vez de PDF. Esses
arquivos chegam na pasta EXT do Google Drive com o mesmo padrão de nome
dos PDFs (`MMAA_EXTBAN_BANCO_..._EMPRESA_AG x_CC y.xlsx`) e passam pelo
mesmo pipeline: planilha EXT tratada (totalizada), planilha de lançamento
`.xlsm`, baixa no SGE, histórico e movimentação para a pasta da empresa.

Não confundir com o **fluxo FECFIN** (`docs/ADICIONAR_LAYOUT_FECFIN.md`),
que também lê Excel mas processa planilhas de fechamento financeiro do
cliente, tem outro padrão de nome (`MMAA_FECFIN_CLIENTE`) e outro
endpoint.

| | Extrato em Excel (este guia) | FECFIN |
|---|---|---|
| Nome do arquivo | `MMAA_EXTBAN_...` | `MMAA_FECFIN_...` |
| Endpoint | `POST /api/conversao/executar` | `POST /api/fecfin/executar` |
| Handlers | `src/schemas/excel/` | `src/schemas/fecfin/` |
| `parse` retorna | um `DataFrame` (uma conta) | `list[(banco, DataFrame)]` |
| Saída | `.xlsx` tratado + `.xlsm` de lançamento | `[LANC] ....xlsm` |

O arquivo original é preservado na pasta da empresa como
`{nome}_ORIGINAL{ext}`, já que a planilha tratada é gravada com o mesmo
nome do arquivo de origem.

### Estrutura de arquivos

```
src/schemas/excel/
├── __init__.py          # importa handlers (dispara @register)
├── base.py              # ExcelBankHandler — classe base abstrata
├── registry.py          # register() + dispatch_excel()
└── itau.py              # Layout: Itaú (cabeçalho localizado por conteúdo)

src/services/conversao.py   # laço de Excel da pasta EXT
```

---

## Passo a Passo

### 1. Criar o arquivo do handler

Crie um novo arquivo em `src/schemas/excel/`, por exemplo `meu_banco.py`:

```python
import logging

import pandas as pd

from src.schemas.excel.base import ExcelBankHandler
from src.schemas.excel.registry import register


logger = logging.getLogger(__name__)


@register
class MeuBanco(ExcelBankHandler):
    """Descrição curta do layout."""

    bank = "MeuBanco"

    def matches(self, xls: pd.ExcelFile, file_stem: str = "") -> bool:
        """Reconhece o layout pelo CONTEÚDO da planilha.

        Não use o nome do arquivo para escolher o layout: o segmento do
        banco no nome é digitado à mão e serve só para a baixa no SGE.
        `matches` nunca deve levantar exceção.
        """
        try:
            df = pd.read_excel(xls, sheet_name=0, nrows=0)
            colunas = {str(c).strip() for c in df.columns}
            return {"Data", "Histórico", "Valor"}.issubset(colunas)
        except Exception:
            return False

    def parse(self, xls: pd.ExcelFile, file_stem: str = "") -> pd.DataFrame:
        df = pd.read_excel(xls, sheet_name=0)

        # --- Transforme os dados aqui ---
        df = df.rename(columns={"Data": "DATA", "Histórico": "DESCRIÇÃO", "Valor": "VALOR"})

        df["VALOR"] = pd.to_numeric(df["VALOR"], errors="coerce")
        df = df[df["VALOR"].notna()]
        df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
        df["VALOR"] = df["VALOR"].abs()
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].astype(str).str.strip().str.upper()

        return df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]].reset_index(drop=True)
```

### 2. Registrar o handler

O decorator `@register` cuida do registro; basta importar o módulo em
`src/schemas/excel/__init__.py`:

```python
from src.schemas.excel import itau, meu_banco  # noqa: F401 — dispara @register dos handlers
from src.schemas.excel.registry import ExcelLayoutNotRecognized, dispatch_excel  # noqa: F401
```

### 3. Escrever testes

Crie `tests/test_excel_meu_banco.py` no molde de `tests/test_excel_itau.py`:
monte a planilha em memória com `io.BytesIO` + `pd.ExcelWriter` (não há
diretório de fixtures no projeto) e cubra `matches` (inclusive negativo,
com outro layout), as colunas de saída, o descarte de linhas de saldo e a
classificação `C`/`D`.

Para o caminho ponta a ponta, `tests/test_conversao_password.py` tem
`test_excel_itau_com_movimentacao_converte_e_preserva_original` como
referência — o `FakeDrive` aceita `file_contents={file_id: bytes}` para
entregar um Excel de verdade no `download`.

### 4. Rodar os testes

```bash
uv run python -m unittest discover -s tests
```

---

## Estrutura do DataFrame de Saída

Todo handler DEVE retornar um DataFrame com exatamente estas colunas
(é o contrato validado por `validar_dataframe_extrato`):

| Coluna      | Tipo  | Descrição                          |
|-------------|-------|------------------------------------|
| `DATA`      | str   | Data da movimentação (dd/mm/aaaa)  |
| `DESCRIÇÃO` | str   | Descrição da movimentação (UPPER)  |
| `VALOR`     | float | Valor absoluto (sempre positivo)   |
| `TIPO`      | str   | `"C"` (crédito) ou `"D"` (débito)  |

Quando não houver lançamentos, retorne um DataFrame **vazio com essas
colunas**: o fluxo trata isso como extrato sem movimentação e arquiva o
arquivo na pasta da empresa.

---

## Armadilhas conhecidas

- **Não fixe o índice da linha de cabeçalho.** O bloco de identificação
  da conta acima do cabeçalho varia entre contas do mesmo banco. Localize
  a linha pelo conteúdo, como `_localizar_cabecalho` em
  `src/schemas/excel/itau.py`.
- **Não monte a descrição com `f-string` + `replace("nan", "")`.**
  Aplicado antes do `.upper()`, isso transforma `"Fernando"` em `"Ferdo"`.
  Junte apenas as partes que passam no `pd.notna`.
- **`fillna("")` antes de `astype(str)`** se for concatenar colunas: no
  pandas 3.0 o `astype(str)` mantém `NaN` como float e o join estoura.
- **Linhas de saldo não têm valor.** No Itaú elas vêm com `Valor (R$)`
  vazio e só `Saldo (R$)` preenchido; descartar por `VALOR` nulo e por
  descrição começando com `SALDO` cobre os dois casos.
- **Sem fallback de IA.** Diferente dos PDFs, um Excel sem layout
  reconhecido não vai para `00_INVALIDOS`: fica na EXT e aparece na
  notificação como alerta.
