#!/bin/bash

# Script de setup do Droplet para AutoExtrato
# Execute como root no Droplet novo

set -e

DOMAIN="autoextrato.sgecont.com.br"
EMAIL="auto.sgecont@gmail.com"  # Email usado no Let's Encrypt

echo "=== Atualizando sistema ==="
apt update && apt upgrade -y

echo "=== Instalando Docker ==="
apt install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo "=== Instalando Docker Compose ==="
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

echo "=== Habilitando Docker ==="
systemctl enable docker
systemctl start docker

echo "=== Criando diretorios ==="
mkdir -p /opt/autoextrato
mkdir -p /opt/autoextrato/credentials
mkdir -p /opt/autoextrato/logs
mkdir -p /opt/autoextrato/temp
mkdir -p /opt/autoextrato/data

echo "=== Configurando firewall ==="
ufw allow OpenSSH
ufw allow 80
ufw allow 443
ufw --force enable

echo ""
echo "=== Setup concluido ==="
echo ""
echo "Proximos passos:"
echo "1. Copie os arquivos do projeto para /opt/autoextrato/"
echo "2. Configure o arquivo .env em /opt/autoextrato/"
echo "3. Copie os arquivos de credenciais para /opt/autoextrato/credentials/"
echo "4. Suba a aplicacao (build local):"
echo "   cd /opt/autoextrato && docker-compose up -d --build"
echo "5. Emita o certificado HTTPS (executa uma unica vez):"
echo "   ./deploy/scripts/init-letsencrypt.sh"
echo "6. Valide: https://$DOMAIN/api/health"
