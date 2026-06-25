#!/bin/bash

# Bootstrap do certificado Let's Encrypt para o AutoExtrato.
#
# Resolve o problema do "ovo e a galinha": o nginx precisa de um certificado
# para subir com a config HTTPS (ssl.conf), mas o certbot precisa do nginx no ar
# para validar o dominio. A solucao e criar um certificado dummy temporario,
# subir o nginx, pedir o certificado real ao Let's Encrypt e recarregar o nginx.
#
# Execute uma unica vez, na primeira configuracao do HTTPS:
#   cd /opt/autoextrato && ./deploy/scripts/init-letsencrypt.sh

set -e

DOMAIN="autoextrato.sgecont.com.br"
EMAIL="${LETSENCRYPT_EMAIL:-auto.sgecont@gmail.com}"
STAGING=0   # 1 = usa ambiente de teste do Let's Encrypt (sem limite de rate)

COMPOSE="docker-compose"
if ! command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker compose"
fi

CERT_PATH="/etc/letsencrypt/live/$DOMAIN"

if [ ! -e "./deploy/nginx/ssl.conf" ]; then
  echo "ERRO: execute este script a partir de /opt/autoextrato (raiz do projeto)."
  exit 1
fi

echo "### Criando certificado dummy temporario para $DOMAIN ..."
$COMPOSE run --rm --entrypoint "\
  sh -c 'mkdir -p $CERT_PATH && \
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout $CERT_PATH/privkey.pem \
    -out $CERT_PATH/fullchain.pem \
    -subj /CN=localhost'" certbot

echo "### Subindo nginx com o certificado dummy ..."
$COMPOSE up -d --force-recreate nginx
sleep 5

echo "### Removendo certificado dummy ..."
$COMPOSE run --rm --entrypoint "\
  sh -c 'rm -rf /etc/letsencrypt/live/$DOMAIN && \
  rm -rf /etc/letsencrypt/archive/$DOMAIN && \
  rm -rf /etc/letsencrypt/renewal/$DOMAIN.conf'" certbot

echo "### Solicitando certificado real ao Let's Encrypt ..."
STAGING_ARG=""
if [ "$STAGING" != "0" ]; then STAGING_ARG="--staging"; fi

$COMPOSE run --rm --entrypoint "\
  certbot certonly --webroot --webroot-path=/var/www/certbot \
    $STAGING_ARG \
    --email $EMAIL \
    -d $DOMAIN \
    --agree-tos \
    --no-eff-email \
    --force-renewal" certbot

echo "### Recarregando nginx com o certificado real ..."
$COMPOSE exec nginx nginx -s reload || $COMPOSE up -d --force-recreate nginx

echo ""
echo "### Concluido. Teste: https://$DOMAIN/api/health"
