# Como Adicionar um Novo Layout FECFIN

Este guia explica como adicionar um novo layout de processamento para
arquivos FECFIN (Excel) no sistema AutoExtrato.

---

## Visão Geral

Arquivos FECFIN são planilhas Excel com movimentações financeiras que
chegam na pasta EXT do Google Drive. O sistema identifica o layout
correto e gera arquivos de lançamentos contábeis (`[LANC]`).

O fluxo FECFIN é **independente** do fluxo de conversão (extratos PDF).
Ele possui seu próprio endpoint: `POST /api/fecfin/executar`.

### Endpoint

```
POST /api/fecfin/executar
```

- Retorna **202** com `{"message": "Fluxo FECFIN iniciado"}` em sucesso
- Retorna **409** com `{"message": "Fluxo FECFIN ja esta em execucao"}` se já estiver rodando

### Estrutura de arquivos

```
src/schemas/fecfin/
├── __init__.py          # importa handlers (dispara @register)
├── base.py              # FecfinHandler — classe base abstrata
├── registry.py          # register() + dispatch_fecwin()
├── conta_azul.py        # Layout: Conta Azul (1 aba)
└── multibanco.py        # Layout: CEMAF multi-banco (4 abas)

src/services/fecfin.py   # Serviço独立 (executar_fecfin)
src/api/endpoints/fecfin.py  # Endpoint da API
```

---

## Passo a Passo

### 1. Criar o arquivo do handler

Crie um novo arquivo em `src/schemas/fecfin/`, por exemplo `meu_layout.py`:

```python
import logging
import pandas as pd

from src.schemas.fecfin.base import FecfinHandler
from src.schemas.fecfin.registry import register


logger = logging.getLogger(__name__)


@register
class MeuLayout(FecfinHandler):
    """Descrição curta do layout."""

    bank = "MeuLayout"

    def matches(self, xls: pd.ExcelFile) -> bool:
        """Verifica se o arquivo Excel corresponde a este layout.

        Exemplos de verificação:
        - Verificar colunas da primeira aba
        - Verificar nomes das abas
        - Verificar conteúdo de células específicas
        """
        try:
            df = pd.read_excel(xls, sheet_name=0, nrows=0)
            colunas = set(str(c).strip() for c in df.columns)
            return {"Coluna A", "Coluna B"}.issubset(colunas)
        except Exception:
            return False

    def parse(
        self, xls: pd.ExcelFile, file_stem: str
    ) -> list[tuple[str, pd.DataFrame]]:
        """Extrai dados do Excel e retorna lista de (banco, DataFrame).

        Cada tupla representa um banco encontrado no arquivo.
        O DataFrame DEVE ter as colunas: DATA, DESCRIÇÃO, VALOR, TIPO.

        Args:
            xls: Arquivo Excel aberto (pd.ExcelFile)
            file_stem: Nome do arquivo sem extensão (ex: "0426_FECFIN_TESTE")

        Returns:
            Lista de tuplas (nome_banco, DataFrame)
        """
        df = pd.read_excel(xls, sheet_name=0)

        # --- Transforme os dados aqui ---
        # Exemplo: renomear colunas, criar TIPO, etc.

        df = df.rename(columns={"Data": "DATA", "Descricao": "DESCRIÇÃO", "Valor": "VALOR"})

        df["TIPO"] = df["VALOR"].apply(lambda v: "C" if v > 0 else "D")
        df["VALOR"] = df["VALOR"].abs()
        df["DESCRIÇÃO"] = df["DESCRIÇÃO"].str.upper()

        # Selecionar apenas as colunas de saída
        df = df[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]

        # Retornar lista com tuplas (banco, df)
        return [("NomeDoBanco", df)]
```

### 2. Registrar o handler

O decorator `@register` já adiciona o handler automaticamente. Basta
importar o arquivo no `__init__.py`:

Edite `src/schemas/fecfin/__init__.py` e adicione o import:

```python
from src.schemas.fecfin import conta_azul, multibanco, meu_layout  # noqa: F401
from src.schemas.fecfin.registry import FecfinLayoutNotRecognized, dispatch_fecwin  # noqa: F401
```

### 3. Escrever testes

Adicione testes em `tests/test_fecfin_service.py`:

```python
class MeuLayoutTest(unittest.TestCase):
    def test_matches_detecta_layout(self):
        # Criar Excel de teste e verificar se matches() retorna True
        ...

    def test_parse_retorna_dataframe_correto(self):
        # Verificar colunas, tipos, valores
        ...

    def test_parse_multiplos_bancos(self):
        # Se o layout suporta múltiplos bancos
        ...
```

### 4. Rodar os testes

```bash
uv run python -m pytest tests/test_fecfin_service.py -v
```

---

## Estrutura do DataFrame de Saída

Todo handler DEVE retornar um DataFrame com exatamente estas colunas:

| Coluna   | Tipo    | Descrição                           |
|----------|---------|-------------------------------------|
| `DATA`   | str     | Data da movimentação (dd/mm/aaaa)   |
| `DESCRIÇÃO` | str  | Descrição da movimentação (UPPER)   |
| `VALOR`  | float   | Valor absoluto (sempre positivo)    |
| `TIPO`   | str     | `"C"` (crédito) ou `"D"` (débito)   |

---

## Exemplo: Layout Conta Azul (referência)

Arquivo: `src/schemas/fecfin/conta_azul.py`

```python
@register
class ContaAzul(FecfinHandler):
    bank = "ContaAzul"

    def matches(self, xls: pd.ExcelFile) -> bool:
        # Verifica se a primeira aba tem as 4 colunas esperadas
        df = pd.read_excel(xls, sheet_name=0, nrows=0)
        colunas = set(str(c).strip() for c in df.columns)
        return {"Data movimento", "Descrição", "Valor (R$)", "Conta bancária"}.issubset(colunas)

    def parse(self, xls, file_stem):
        df = pd.read_excel(xls, sheet_name=0)
        # Concatena descrição + nome do fornecedor
        df["DESCRIÇÃO"] = df["Descrição"] + " " + df["Nome do fornecedor/cliente"]
        # ... transformações ...
        # Agrupa por banco e retorna 1 tupla por grupo
        for banco, grupo in df.groupby("Conta bancária"):
            resultados.append((banco, grupo[["DATA", "DESCRIÇÃO", "VALOR", "TIPO"]]))
        return resultados
```

---

## Exemplo: Layout Multi-Banco (referência)

Arquivo: `src/schemas/fecfin/multibanco.py`

Para layouts com múltiplas abas (uma por banco):

```python
@register
class MultiBanco(FecfinHandler):
    bank = "MultiBanco"

    def matches(self, xls):
        # Verifica se tem abas com nomes de bancos conhecidos
        for aba in xls.sheet_names:
            if "UNICRED" in str(aba).upper():
                return True
        return False

    def parse(self, xls, file_stem):
        resultados = []
        for aba in xls.sheet_names:
            if "UNICRED" in str(aba).upper():
                df = processar_unicred(xls, aba)
                resultados.append(("UNICRED", df))
        return resultados
```

---

## Como o Dispatch Funciona

1. O fluxo de conversão detecta um arquivo Excel com "FECFIN" no nome
2. Abre o arquivo com `pd.ExcelFile()`
3. Chama `dispatch_fecwin(xls, file_stem)`
4. O dispatch itera sobre todos os handlers registrados
5. Chama `matches(xls)` em cada um
6. O primeiro que retornar `True` é usado para `parse()`
7. Se nenhum retornar `True`, o arquivo fica na pasta EXT e notifica no chat

---

## Dicas

- **`matches()`** deve ser rápido e determinístico — não baixe o arquivo
  inteiro, verifique apenas estrutura (colunas, nomes de abas)
- **`parse()`** pode ser mais pesado — leia os dados e transforme
- Use `try/except` nos processadores individuais de aba para que um
  erro em uma aba não impeça as outras
- Filtrar linhas de "SALDO" é responsabilidade do handler
- O decorator `@register` é obrigatório — sem ele o handler não é
  detectado pelo dispatch
- Valores financeiros DEVEM ser absolutos (positivos), com `TIPO`
  indicando se é crédito ou débito

---

## Arquivos Relacionados

| Arquivo | Função |
|---------|--------|
| `src/schemas/fecfin/base.py` | Classe base `FecfinHandler` |
| `src/schemas/fecfin/registry.py` | `register()` + `dispatch_fecwin()` |
| `src/schemas/fecfin/__init__.py` | Importa todos os handlers |
| `src/schemas/__init__.py` | Expõe `dispatch_fecwin` publicamente |
| `src/services/fecfin.py` | Serviço FECFIN (`executar_fecfin`) |
| `src/api/endpoints/fecfin.py` | Endpoint `POST /api/fecfin/executar` |
| `src/api/router.py` | Registra o router FECFIN |
| `tests/test_fecfin_service.py` | Testes unitários dos handlers + endpoint |
