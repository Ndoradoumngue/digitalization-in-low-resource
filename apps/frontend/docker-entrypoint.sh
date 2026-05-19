#!/bin/sh
# Selects the nginx configuration based on HTTPS_MODE, then starts nginx.
#
# HTTPS_MODE=self_signed (default)
#   Certificate is baked into the image at /etc/nginx/ssl/cert.pem + key.pem
#   No extra setup required. Accept the browser warning once.
#
# HTTPS_MODE=letsencrypt
#   Requires LETSENCRYPT_DOMAIN to be set.
#   First run: starts HTTP-only so certbot can complete the ACME challenge.
#   Subsequent runs (cert found): starts with full HTTPS configuration.
set -e

HTTPS_MODE="${HTTPS_MODE:-self_signed}"
LETSENCRYPT_DOMAIN="${LETSENCRYPT_DOMAIN:-}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"
CONF=/etc/nginx/conf.d/default.conf

case "$HTTPS_MODE" in

  self_signed)
    echo "[entrypoint] HTTPS_MODE=self_signed — using certificate baked into image."
    cp /etc/nginx/templates/self_signed.conf "$CONF"
    ;;

  letsencrypt)
    if [ -z "$LETSENCRYPT_DOMAIN" ]; then
      echo "[entrypoint] ERROR: LETSENCRYPT_DOMAIN must be set when HTTPS_MODE=letsencrypt" >&2
      exit 1
    fi

    CERT="/etc/letsencrypt/live/${LETSENCRYPT_DOMAIN}/fullchain.pem"

    if [ -f "$CERT" ]; then
      echo "[entrypoint] Certificate found for ${LETSENCRYPT_DOMAIN} — starting with HTTPS."
      LETSENCRYPT_DOMAIN="$LETSENCRYPT_DOMAIN" \
        envsubst '${LETSENCRYPT_DOMAIN}' \
        < /etc/nginx/templates/letsencrypt.conf \
        > "$CONF"
    else
      echo "[entrypoint] No certificate yet — starting HTTP-only for ACME challenge."
      echo "[entrypoint] Once the stack is running, issue a certificate:"
      echo "[entrypoint]   docker compose exec frontend certbot --nginx \\"
      echo "[entrypoint]     -d ${LETSENCRYPT_DOMAIN} --email ${LETSENCRYPT_EMAIL:-<your-email>} \\"
      echo "[entrypoint]     --agree-tos --non-interactive"
      echo "[entrypoint] Then restart the frontend: docker compose restart frontend"
      LETSENCRYPT_DOMAIN="$LETSENCRYPT_DOMAIN" \
        envsubst '${LETSENCRYPT_DOMAIN}' \
        < /etc/nginx/templates/letsencrypt_pending.conf \
        > "$CONF"
    fi
    ;;

  *)
    echo "[entrypoint] Unknown HTTPS_MODE: ${HTTPS_MODE}" >&2
    echo "[entrypoint] Valid values: self_signed, letsencrypt" >&2
    exit 1
    ;;

esac

exec nginx -g 'daemon off;'
