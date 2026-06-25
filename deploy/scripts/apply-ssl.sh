#!/bin/bash
set -e
cd /opt/autoextrato

echo "=== 1. Fechando porta 8000 (expose em vez de ports) ==="
sed -i 's/ports:/expose:/' docker-compose.yml
sed -i 's/"8000:8000"/"8000"/' docker-compose.yml

echo "=== 2. Habilitando ssl.conf ==="
sed -i 's|# - ./deploy/nginx/ssl.conf|  - ./deploy/nginx/ssl.conf|' docker-compose.yml

echo "=== 3. Atualizando nginx HTTP (redirect para HTTPS) ==="
cat > deploy/nginx/autoextrato.conf << 'EOF'
server {
    listen 80;
    server_name autoextrato.sgecont.com.br;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}
EOF

echo "=== 4. Configurando nginx SSL ==="
cat > deploy/nginx/ssl.conf << 'EOF'
server {
    listen 443 ssl http2;
    server_name autoextrato.sgecont.com.br;
    ssl_certificate /etc/letsencrypt/live/autoextrato.sgecont.com.br/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/autoextrato.sgecont.com.br/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / {
        proxy_pass http://autoextrato:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
EOF

echo "=== 5. Gerando certificado dummy temporario ==="
mkdir -p /etc/letsencrypt/live/autoextrato.sgecont.com.br
openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
  -keyout /etc/letsencrypt/live/autoextrato.sgecont.com.br/privkey.pem \
  -out /etc/letsencrypt/live/autoextrato.sgecont.com.br/fullchain.pem \
  -subj /CN=localhost 2>/dev/null

echo "=== 6. Subindo nginx com certificado dummy ==="
docker-compose up -d --force-recreate nginx
sleep 5

echo "=== 7. Removendo certificado dummy ==="
rm -rf /etc/letsencrypt/live/autoextrato.sgecont.com.br
rm -rf /etc/letsencrypt/archive/autoextrato.sgecont.com.br
rm -rf /etc/letsencrypt/renewal/autoextrato.sgecont.com.br.conf

echo "=== 8. Emitindo certificado real Let's Encrypt ==="
docker-compose run --rm --entrypoint \
  "certbot certonly --webroot --webroot-path=/var/www/certbot \
    --email auto.sgecont@gmail.com \
    -d autoextrato.sgecont.com.br \
    --agree-tos --no-eff-email --force-renewal" certbot

echo "=== 9. Recarregando backend e nginx ==="
docker-compose up -d --force-recreate autoextrato
docker-compose exec nginx nginx -s reload 2>/dev/null || docker-compose up -d --force-recreate nginx

echo ""
echo "=== CONCLUIDO ==="
echo "Teste: https://autoextrato.sgecont.com.br/api/health"