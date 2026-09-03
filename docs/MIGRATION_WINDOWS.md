# Migração do AutoExtrato — DigitalOcean > Contabo (Windows 10)

Registro de operação. Migrou a API AutoExtrato de um Droplet DigitalOcean
(Docker/Linux) para uma VPS Contabo com **Windows 10**, rodando **nativamente**
(Python + Caddy), pois Docker/WSL2 não é viável nesta VPS.

## Contexto / decisão

- Origem: Droplet DigitalOcean `137.184.192.16` (Docker Compose: fastapi + nginx
  + certbot; domínio `autoextrato.sgecont.com.br`).
- Destino: VPS Contabo `169.58.84.88`, Windows 10.
- **Bloqueio confirmado**: a Contabo não expõe virtualização aninhada (SLAT).
  Provas: `SLAT(2ndLevelAddr)=False`, `wsl --import --version 2` falhou com
  "Habilite a Plataforma de Máquina Virtual / virtualização no BIOS", e o
  instalador do Docker Desktop sempre retornava `exit 1` (falha idêntica já
  registrada em 2026-07-29, antes desta migração).
- **Decisão**: rodar nativo. Python + Caddy (HTTPS automático), sem Docker.

## Ambiente de destino

- Windows 10 Enterprise LTSC, build 19044 (21H2)
- 12 GB RAM, 6 núcleos (AMD EPYC), ~259 GB livres
- Usuário: `sge` (admin); **SSH por chave** (senha desativada)
- Recursos `WSL` e `VirtualMachinePlatform`: **desativados** (removidos da
  tentativa inicial)

## Estrutura / caminhos (na VPS)

| Item | Caminho |
|---|---|
| Projeto (código) | `C:\apps\autoextrato` |
| Python (uv-managed) | Python 3.14.7 (`uv python install 3.14`) |
| Virtualenv | `C:\apps\autoextrato\.venv` |
| Ferramentas | `C:\apps\tools\uv.exe`, `caddy.exe`, `nssm\nssm-2.24\win64\nssm.exe` |
| Segredos | `C:\apps\autoextrato\.env` |
| Credenciais Google | `C:\apps\autoextrato\credentials\` |
| Dados | `C:\apps\autoextrato\data\` (template `Lancamentos_Contabeis.xlsm`) |
| Logs | `C:\apps\autoextrato\logs\` |
| Temp | `C:\apps\autoextrato\temp\` |
| Caddy | `C:\apps\caddy\Caddyfile`, logs `C:\apps\caddy\caddy.{out,err}.log` |

## Serviços do Windows (NSSM, auto-start)

1. **AutoExtrato** — uvicorn (FastAPI)
   - `C:\apps\autoextrato\.venv\Scripts\uvicorn.exe`
   - Params: `src.api.main:app --host 127.0.0.1 --port 8000`
   - AppDirectory: `C:\apps\autoextrato`
   - Logs: `logs\app_service.out.log` / `logs\app_service.err.log`
2. **Caddy** — proxy reverso + HTTPS automático
   - `C:\apps\tools\caddy.exe`
   - Params: `run --config C:\apps\caddy\Caddyfile --watch`
   - AppDirectory: `C:\apps\caddy`
   - Logs: `caddy\caddy.out.log` / `caddy\caddy.err.log`

`Caddyfile`:

```
{
    acme_ca https://acme-v02.api.letsencrypt.org/directory
}

autoextrato.sgecont.com.br {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy gerencia o certificado Let's Encrypt automaticamente.

## Firewall

- Windows: regras de entrada liberadas para TCP 80 e 443 ("AutoExtrato HTTP 80"
  / "AutoExtrato HTTPS 443"); SSH 22 já aberto.
- Contabo (cloud firewall): regras de aceitação TCP 22, 80, 443, 3389 — acima do
  `Block all traffic`; associadas à instância `vmi3469550`.

## Testes executados

- `uv sync --frozen --no-dev` -> OK (todas as wheels p/ Python 3.14 no Windows)
- Import do app -> OK (`AutoExtrato API`)
- uvicorn em `127.0.0.1:8000` -> `GET /api/health` = 200
- Serviços sobem no boot pós-reboot -> `AutoExtrato=Running`, `Caddy=Running`
- Caddy escutando `0.0.0.0:80` e `0.0.0.0:443`

## Cutover final (concluído)

- **DNS**: `autoextrato.sgecont.com.br` A -> `169.58.84.88` (Locaweb).
- **Let's Encrypt (produção)**: o resolver do LE ficou um tempo com o IP antigo
  (DO); contornado pelo nginx do DO repassando `/.well-known/acme-challenge/`
  para a VPS `169.58.84.88:80`. Certificado obtido e persistido.
- **Validação**: `https://autoextrato.sgecont.com.br/api/health` = 200,
  `/docs` = 200, `/openapi.json` = 200. Mesmos endpoints da API.
- **Serviços** Windows (auto-start) em `Running`: AutoExtrato e Caddy.
- **DigitalOcean** descomissionado (droplet removido; snapshot
  `autoextrato-final-antes_descom` mantido como backup).
- **Segurança**: SSH por chave configurado; `PasswordAuthentication no`;
  `DIGITALOCEAN_API_TOKEN` removido do `.env`.

## Manutenção

- Reiniciar app: `Restart-Service AutoExtrato`
- Reiniciar proxy: `Restart-Service Caddy`
- Logs do app: `C:\apps\autoextrato\logs\conversao.log`
- Conectar via SSH (Windows): `ssh -i %USERPROFILE%\.ssh\id_ed25519_autoextrato sge@169.58.84.88`
- Backup: copiar `.env`, `credentials/`, `data/` (contém segredos e estado).

## Pendência listada / recomendação

- Rotacionar a senha do usuário `sge` (foi compartilhada em texto durante a
  migração) mesmo com SSH por chave.
- Excluir o snapshot no DigitalOcean após confirmada a estabilidade.
