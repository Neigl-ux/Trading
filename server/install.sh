#!/usr/bin/env bash
# Einrichtung des Trading-Agent-Servers (Ubuntu 22.04/24.04, ARM oder x86).
# Aufruf auf dem Server:  curl -fsSL https://raw.githubusercontent.com/Neigl-ux/Trading/main/server/install.sh | sudo bash
# Danach die Zugangsdaten eintragen:  sudo nano /etc/trading-agent.env   und   sudo systemctl restart ta-radar
set -euo pipefail
REPO_URL="https://github.com/Neigl-ux/Trading.git"
DIR=/opt/trading-agent
ENV=/etc/trading-agent.env

echo "== Pakete =="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3 python3-venv python3-pip tzdata >/dev/null
timedatectl set-timezone Europe/Berlin || true

# Kleine Server (z. B. VM.Standard.E2.1.Micro mit 1 GB): 2 GB Auslagerungsdatei, damit pip und pandas sicher laufen
mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [ "$mem_kb" -lt 1800000 ] && ! swapon --show | grep -q /swapfile; then
  echo "== Auslagerungsdatei (2 GB) =="
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== Benutzer und Programm =="
id -u trading >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/trading-agent --shell /usr/sbin/nologin trading
if [ -d "$DIR/.git" ]; then git -c safe.directory="$DIR" -C "$DIR" pull -q; else git clone -q "$REPO_URL" "$DIR"; fi
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
chown -R trading:trading "$DIR" /var/lib/trading-agent

if [ ! -f "$ENV" ]; then
  cat > "$ENV" <<'CONF'
# Zugangsdaten des Servers. Nur root kann diese Datei lesen.
# GitHub-Token (fein abgestuft, nur Repository „Trading“, Recht „Actions: Read and write“):
GH_TOKEN=
GH_REPO=Neigl-ux/Trading
# Geheimer ntfy-Kanal für Push-Nachrichten (derselbe wie das GitHub-Secret NTFY_TOPIC):
NTFY_TOPIC=
# Zusätzliche Werte fürs Liveradar (Kennungen aus der App, durch Komma getrennt), z. B. deine Watchlist:
RADAR_EXTRA=
CONF
  chmod 600 "$ENV"
fi

echo "== Dienste =="
install -m 644 "$DIR/server/ta-trigger.service" "$DIR/server/ta-trigger.timer" \
               "$DIR/server/ta-radar.service" "$DIR/server/ta-update.service" "$DIR/server/ta-update.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ta-trigger.timer ta-update.timer >/dev/null
systemctl enable ta-radar.service >/dev/null
systemctl restart ta-radar.service || true

echo
echo "Fertig. Jetzt die Zugangsdaten eintragen:"
echo "   sudo nano $ENV"
echo "und danach:"
echo "   sudo systemctl restart ta-radar && sudo systemctl start ta-trigger"
echo "Prüfen:  systemctl status ta-radar   |   journalctl -u ta-trigger -n 20"
