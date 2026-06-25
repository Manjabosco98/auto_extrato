#!/bin/bash

# Script de deploy do AutoExtrato
# Execute apos o setup inicial e apos cada atualizacao

set -e

cd /opt/autoextrato

echo "=== Atualizando codigo (git pull) ==="
git pull --ff-only || echo "Aviso: git pull ignorado (repo nao versionado neste diretorio)"

echo "=== Rebuild e restart do servico (build local) ==="
docker-compose up -d --build autoextrato

echo "=== Limpando imagens antigas ==="
docker image prune -f

echo ""
echo "=== Deploy concluido ==="
echo "Verificando logs:"
docker-compose logs --tail=20 autoextrato
