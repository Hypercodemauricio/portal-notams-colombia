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

log "1/8  Paquetes del sistema"
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv nginx sqlite3 logrotate curl unzip

log "2/8  Memoria de intercambio"
# La e2-micro que entra en el nivel gratuito de Google Cloud tiene 1 GB de RAM.
# Chrome headless pide entre 300 y 500 MB y el extractor ademas abre con
# pdfplumber un PDF de 48 paginas. Sin swap eso termina en OOM: el kernel mata
# Chrome a mitad de la descarga, la extraccion falla y la base se queda vieja
# -y como el proceso muere sin escribir nada, en el log no queda ni un error
# claro, solo el intento cortado-. Con swap la extraccion va mas lenta pero
# termina. En una maquina con 2 GB o mas no se toca nada.
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
SWAP_MB=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
if (( MEM_MB < 2048 && SWAP_MB < 1024 )); then
    if [[ ! -f /swapfile ]]; then
        fallocate -l 2G /swapfile 2>/dev/null || \
            dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
        chmod 600 /swapfile
        mkswap -q /swapfile
    fi
    swapon /swapfile 2>/dev/null || true
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "    ${MEM_MB} MB de RAM: anadidos 2 GB de swap."
else
    echo "    ${MEM_MB} MB de RAM y ${SWAP_MB} MB de swap: no hace falta."
fi

log "3/8  Google Chrome (necesario para el extractor con Selenium)"
if ! command -v google-chrome >/dev/null; then
    # El alojamiento gratuito que sale a cuenta para esto es ARM (las Ampere
    # A1 de Oracle); Google Cloud y AWS son x86. Google publica las dos
    # arquitecturas, pero con nombres de paquete distintos, asi que fijar
    # amd64 hacia fallar la instalacion entera justo en el servidor mas
    # probable.
    ARQ="$(dpkg --print-architecture)"
    case "$ARQ" in
        amd64|arm64) ;;
        *) echo "    Chrome no tiene paquete para $ARQ."; exit 1 ;;
    esac
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/chrome.deb" \
        "https://dl.google.com/linux/direct/google-chrome-stable_current_${ARQ}.deb"
    apt-get install -y -qq "$tmp/chrome.deb"
    rm -rf "$tmp"
    echo "    Chrome instalado para $ARQ."
else
    echo "    Chrome ya instalado: $(google-chrome --version)"
fi

log "4/8  Codigo de la aplicacion en ${APP_DIR}"
mkdir -p "$APP_DIR/logs"
rsync -a --exclude '.git' --exclude 'logs' --exclude '.env' \
      "$REPO_DIR/" "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

log "5/8  Entorno virtual y dependencias"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/.env" ]]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo "    ATENCION: edita $APP_DIR/.env y pon tu GEMINI_API_KEY antes de continuar."
fi

log "6/8  Servicio systemd"
sed "s|/usr/bin/python3|$APP_DIR/.venv/bin/python3|; s|/home/ubuntu/proyecto_notams|$APP_DIR|g" \
    "$REPO_DIR/deploy/notams.service" > /etc/systemd/system/notams.service
systemctl daemon-reload
systemctl enable --now notams.service

log "7/8  nginx"
cp "$REPO_DIR/deploy/nginx-notams.conf" /etc/nginx/sites-available/notams
ln -sfn /etc/nginx/sites-available/notams /etc/nginx/sites-enabled/notams
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

log "8/8  Rotacion de logs y tarea programada"
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
