#!/usr/bin/env bash
#
# Despliegue del Portal NOTAMs Colombia en un servidor Ubuntu 22.04/24.04.
#
#   sudo bash deploy/install.sh
#
# Idempotente: se puede volver a ejecutar sobre una instalacion existente.

set -euo pipefail

APP_USER="${APP_USER:-ubuntu}"
APP_DIR="${APP_DIR:-/home/${APP_USER}/proyecto_notams}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "Ejecutar con sudo."; exit 1; }

log "1/7  Paquetes del sistema"
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv nginx sqlite3 logrotate curl unzip

log "2/7  Google Chrome (necesario para el extractor con Selenium)"
if ! command -v google-chrome >/dev/null; then
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/chrome.deb" \
        https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    apt-get install -y -qq "$tmp/chrome.deb"
    rm -rf "$tmp"
else
    echo "    Chrome ya instalado: $(google-chrome --version)"
fi

log "3/7  Codigo de la aplicacion en ${APP_DIR}"
mkdir -p "$APP_DIR/logs"
rsync -a --exclude '.git' --exclude 'logs' --exclude '.env' \
      "$REPO_DIR/" "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

log "4/7  Entorno virtual y dependencias"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/.env" ]]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo "    ATENCION: edita $APP_DIR/.env y pon tu GEMINI_API_KEY antes de continuar."
fi

log "5/7  Servicio systemd"
sed "s|/usr/bin/python3|$APP_DIR/.venv/bin/python3|; s|/home/ubuntu/proyecto_notams|$APP_DIR|g" \
    "$REPO_DIR/deploy/notams.service" > /etc/systemd/system/notams.service
systemctl daemon-reload
systemctl enable --now notams.service

log "6/7  nginx"
cp "$REPO_DIR/deploy/nginx-notams.conf" /etc/nginx/sites-available/notams
ln -sfn /etc/nginx/sites-available/notams /etc/nginx/sites-enabled/notams
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

log "7/7  Rotacion de logs y tarea programada"
sed "s|/home/ubuntu/proyecto_notams|$APP_DIR|g" \
    "$REPO_DIR/deploy/logrotate-notams" > /etc/logrotate.d/notams

# El extractor ya escribe su propio log rotativo, por eso el cron no redirige
# la salida a un archivo que crezca sin control.
CRON_LINE="*/15 * * * * cd $APP_DIR && $APP_DIR/.venv/bin/python3 $APP_DIR/extractor.py >/dev/null 2>&1"
( sudo -u "$APP_USER" crontab -l 2>/dev/null | grep -v 'extractor.py' ; echo "$CRON_LINE" ) \
    | sudo -u "$APP_USER" crontab -

log "Primera extraccion (puede tardar ~1 minuto)"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && .venv/bin/python3 extractor.py" || \
    echo "    La primera extraccion fallo; el cron reintentara en 15 minutos."

echo
echo "-------------------------------------------------------------"
echo " Instalacion terminada."
echo "   Estado:   systemctl status notams"
echo "   Salud:    curl -s localhost/health"
echo "   Logs:     journalctl -u notams -f"
echo "             tail -f $APP_DIR/logs/extractor.log"
echo "-------------------------------------------------------------"
