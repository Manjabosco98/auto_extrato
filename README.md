# AutoExtrato

Projeto para converter extratos bancarios em arquivos de lancamento e planilhas totalizadas, usando arquivos PDF armazenados no Google Drive.

## Requisitos

- Python 3.14 ou superior
- `uv`
- `openpyxl` para preencher a planilha de lancamento sem depender do Excel instalado
- Credenciais do Google Drive dentro da pasta `credentials/`

## Instalar Dependencias

```powershell
uv sync
```

## Executar a API

```powershell
uv run uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```

Depois de iniciar o servidor, acesse:

- Documentacao interativa: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

## Rota de Conversao

A rota abaixo foi criada para ser chamada pelo botao "executar conversao" da plataforma:

```http
POST /api/conversao/executar
```

URL local completa:

```text
http://127.0.0.1:8000/api/conversao/executar
```

Quando a conversao e iniciada, a API responde imediatamente:

```json
{
  "message": "Conversão iniciada"
}
```

Se ja existir uma conversao em andamento, a API bloqueia uma segunda execucao e retorna status `409`:

```json
{
  "message": "Conversão já está em execução"
}
```

## Fluxo de Conversao

O endpoint chama a funcao `executar_conversao()` em `src/services/conversao.py`.

O fluxo:

1. Autentica no Google Drive.
2. Recupera ou cria as pastas `00_INVALIDOS` e `00_CONVERTIDOS`.
3. Lista PDFs na pasta configurada.
4. Processa apenas arquivos com `EXT` no nome.
5. Extrai o texto do PDF.
6. Identifica o layout bancario.
7. Gera o arquivo de lancamento `.xls` usando `openpyxl`.
8. Gera a planilha totalizada `.xlsx`.
9. Envia os arquivos convertidos para o Google Drive.
10. Move o PDF original para a pasta final ou para invalidos.
11. Limpa os arquivos temporarios locais.

## Logs

Os logs da conversao sao gravados no console e no arquivo:

```text
logs/conversao.log
```

O log registra o processo desde o inicio ate a conclusao, incluindo arquivos processados, arquivos ignorados, PDFs invalidos, uploads e erros com stack trace.

## Executar Conversao Sem API

Tambem e possivel executar o fluxo direto pelo modulo de servico:

```powershell
uv run python -m src.services.conversao
```

## Testes

```powershell
uv run python -m unittest discover -s tests
```

Os testes do endpoint usam mock da funcao de conversao para nao acessar Google Drive durante a suite.
