#!/bin/bash

# Script de deploy do AutoExtrato
# Execute apos o setup inicial e apos cada atualizacao

set -e

echo "=== Fazendo pull da imagem mais recente ==="
cd /opt/autoextrato
docker-compose pull autoextrato

echo "=== Reiniciando servicos ==="
docker-compose up -d --force-recreate autoextrato

echo "=== Limpando imagens antigas ==="
docker image prune -f

echo ""
echo "=== Deploy concluido ==="
echo "Verificando logs:"
docker-compose logs --tail=20 autoextrato
