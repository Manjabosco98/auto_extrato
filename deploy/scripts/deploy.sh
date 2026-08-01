#!/bin/bash

# Script de deploy do AutoExtrato
# Execute apos o setup inicial e apos cada atualizacao

set -e

cd /opt/autoextrato

# O droplet roda Docker Compose v2 (plugin "docker compose"); o binario v1
# "docker-compose" nao existe mais la. Detecta o que estiver disponivel para o
# script funcionar nos dois casos.
if docker compose version >/dev/null 2>&1; then
    compose() { docker compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
    compose() { docker-compose "$@"; }
else
    echo "ERRO: nem 'docker compose' nem 'docker-compose' encontrados" >&2
    exit 1
fi

echo "=== Atualizando codigo (git pull) ==="
# Um pull que falha nao pode passar batido: seguir o deploy reconstroi a imagem
# com o codigo ANTIGO e o resultado parece um sucesso. A causa comum e arquivo
# de config modificado no droplet conflitando com o commit que esta chegando.
if [ -d .git ]; then
    if ! git pull --ff-only; then
        echo "ERRO: git pull falhou. Deploy abortado para nao subir codigo velho." >&2
        echo "Verifique 'git status' em /opt/autoextrato (alteracoes locais?)." >&2
        exit 1
    fi
else
    echo "Aviso: /opt/autoextrato nao e um repositorio git; pull ignorado."
fi

echo "=== Rebuild e restart do servico (build local) ==="
compose up -d --build autoextrato

echo "=== Limpando imagens antigas ==="
docker image prune -f

echo ""
echo "=== Deploy concluido ==="
echo "Verificando logs:"
compose logs --tail=20 autoextrato
