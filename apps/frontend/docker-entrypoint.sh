#!/bin/sh
# Selects the nginx configuration based on HTTPS_MODE, then starts nginx.
#
# HTTPS_MODE=self_signed (default)
#   A certificate of this installation is generated at first start into
#   /etc/nginx/ssl (a volume - kept across restarts and image updates).
#   CN = SELF_SIGNED_CN (default: this host name). Accept the browser
#   warning once.
#
# DEFAULT_LANGUAGE (en|fr, optional) is written into index.html: the
# interface language until each person picks one.
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

case "${DEFAULT_LANGUAGE:-}" in
  en|fr|"") ;;
  *) echo "[entrypoint] WARNING: DEFAULT_LANGUAGE must be en or fr - ignored." >&2
     DEFAULT_LANGUAGE="" ;;
esac
sed -i "s|<meta name=\"sdai-default-language\" content=\"[a-z]*\"|<meta name=\"sdai-default-language\" content=\"${DEFAULT_LANGUAGE:-}\"|" \
  /usr/share/nginx/html/index.html

case "$HTTPS_MODE" in

  self_signed)
    if [ ! -s /etc/nginx/ssl/cert.pem ] || [ ! -s /etc/nginx/ssl/key.pem ]; then
      CN="${SELF_SIGNED_CN:-$(hostname)}"
      echo "[entrypoint] HTTPS_MODE=self_signed - generating this installation's certificate (CN=${CN})."
      mkdir -p /etc/nginx/ssl
      openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout /etc/nginx/ssl/key.pem -out /etc/nginx/ssl/cert.pem \
        -subj "/CN=${CN}/O=SDAI" -addext "subjectAltName=DNS:${CN}" 2>/dev/null
      chmod 600 /etc/nginx/ssl/key.pem
    else
      echo "[entrypoint] HTTPS_MODE=self_signed - using this installation's certificate."
    fi
    cp /etc/nginx/templates/self_signed.conf "$CONF"
    ;;

  letsencrypt)
    if [ -z "$LETSENCRYPT_DOMAIN" ]; then
      echo "[entrypoint] ERROR: LETSENCRYPT_DOMAIN must be set when HTTPS_MODE=letsencrypt" >&2
      exit 1
    fi

    CERT="/etc/letsencrypt/live/${LETSENCRYPT_DOMAIN}/fullchain.pem"

    if [ -f "$CERT" ]; then
      echo "[entrypoint] Certificate found for ${LETSENCRYPT_DOMAIN} - starting with HTTPS."
      LETSENCRYPT_DOMAIN="$LETSENCRYPT_DOMAIN" \
        envsubst '${LETSENCRYPT_DOMAIN}' \
        < /etc/nginx/templates/letsencrypt.conf \
        > "$CONF"
    else
      echo "[entrypoint] No certificate yet - starting HTTP-only for ACME challenge."
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
