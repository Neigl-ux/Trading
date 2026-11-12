#!/usr/bin/env bash
# Umzug auf einen eigenen Server (z. B. Oracle Cloud "Always Free", Ubuntu).
# Aufruf auf dem Server:
#   curl -fsSL https://raw.githubusercontent.com/<DEIN-NAME>/<PROJEKT>/main/deploy/oracle/install.sh | bash -s -- https://github.com/<DEIN-NAME>/<PROJEKT>.git
#
# Danach läuft der Sammler per systemd-Zeitgeber alle 5 Minuten, ohne GitHub.
# Das Passwort wird einmalig abgefragt und nur auf dem Server gespeichert.
set -euo pipefail

REPO_URL="${1:?Bitte die Git-Adresse deines Projekts angeben}"
APP_DIR="/opt/tradingagent"
APP_USER="tradingagent"

echo "==> Pakete installieren"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git

echo "==> Benutzer und Verzeichnis anlegen"
id -u "$APP_USER" >/dev/null 2>&1 || sudo useradd --system --create-home --home-dir "$APP_DIR" "$APP_USER"
if [ ! -d "$APP_DIR/code/.git" ]; then
  sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR/code"
else
  sudo -u "$APP_USER" git -C "$APP_DIR/code" pull --ff-only
fi

echo "==> Python-Umgebung"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/code/requirements.txt"

ENV_FILE="/etc/tradingagent.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "==> App-Passwort festlegen (dasselbe wie in der App, mind. 12 Zeichen)"
  read -r -s -p "Passwort: " PASS </dev/tty; echo
  sudo install -m 600 -o root -g root /dev/null "$ENV_FILE"
  {
    echo "APP_PASSPHRASE=$PASS"
    echo "PYTHONPATH=$APP_DIR/code/src"
    echo "TA_STORAGE_PATH=$APP_DIR/data/market.sqlite"
    echo "TA_EXPORT_DIR=$APP_DIR/site/data"
  } | sudo tee "$ENV_FILE" >/dev/null
fi
sudo -u "$APP_USER" mkdir -p "$APP_DIR/data" "$APP_DIR/site"
sudo -u "$APP_USER" cp -r "$APP_DIR/code/app/." "$APP_DIR/site/"

echo "==> Zeitgeber einrichten"
sudo cp "$APP_DIR/code/deploy/oracle/tradingagent-collect.service" /etc/systemd/system/
sudo cp "$APP_DIR/code/deploy/oracle/tradingagent-collect.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tradingagent-collect.timer

echo "==> Erster Lauf"
sudo systemctl start tradingagent-collect.service || true
sudo journalctl -u tradingagent-collect.service -n 30 --no-pager

echo
echo "Fertig. Der Sammler läuft jetzt alle 5 Minuten."
echo "Webserver (HTTPS) für die App richten wir im nächsten Schritt mit Caddy ein."
