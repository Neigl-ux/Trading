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
echo "iptables-persistent iptables-persistent/autosave_v4 boolean true" | debconf-set-selections
echo "iptables-persistent iptables-persistent/autosave_v6 boolean true" | debconf-set-selections
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3 python3-venv python3-pip tzdata iptables-persistent curl gpg >/dev/null
if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy >/dev/null 2>&1; then
  # Caddy aus der offiziellen Paketquelle, falls Ubuntu es nicht mitbringt
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy >/dev/null
fi
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
# Dein App-Passwort (dasselbe wie das GitHub-Secret APP_PASSPHRASE). Damit verschlüsselt der Server die Daten.
APP_PASSPHRASE=
CONF
  chmod 600 "$ENV"
fi

# Ältere Installationen: fehlende Zeilen ergänzen
grep -q '^APP_PASSPHRASE=' "$ENV" || printf '\n# Dein App-Passwort (dasselbe wie das GitHub-Secret APP_PASSPHRASE):\nAPP_PASSPHRASE=\n' >> "$ENV"

echo "== Verschlüsselung wie auf GitHub =="
SALT=$(curl -fsSL "https://neigl-ux.github.io/Trading/data/index.json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["kdf"]["salt"])' || true)
if [ -n "$SALT" ]; then
  if grep -q '^TA_EXPORT_SALT_B64=' "$ENV"; then sed -i "s|^TA_EXPORT_SALT_B64=.*|TA_EXPORT_SALT_B64=$SALT|" "$ENV"; else echo "TA_EXPORT_SALT_B64=$SALT" >> "$ENV"; fi
fi

echo "== Ordner =="
install -d -o trading -g trading -m 755 /var/lib/trading-agent /var/lib/trading-agent/site /var/lib/trading-agent/site/data
install -d -o trading -g trading -m 750 /var/lib/trading-agent/data

echo "== Webserver mit HTTPS (Caddy, Zertifikat von Let's Encrypt, kostenlos) =="
IP=$(curl -fsS https://api.ipify.org || true)
if [ -n "$IP" ]; then
  HOST="$(echo "$IP" | tr . -).sslip.io"
  cat > /etc/caddy/Caddyfile <<CADDY
$HOST {
	root * /var/lib/trading-agent/site
	file_server
	header {
		Access-Control-Allow-Origin "https://neigl-ux.github.io"
		Cache-Control "no-store"
		-Server
	}
}
CADDY
  # Ubuntu auf Oracle sperrt eingehende Verbindungen per iptables; Port 80/443 für HTTPS freigeben
  for port in 80 443; do
    iptables -C INPUT -p tcp --dport $port -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -m state --state NEW -p tcp --dport $port -j ACCEPT
  done
  netfilter-persistent save >/dev/null 2>&1 || true
  systemctl enable caddy >/dev/null 2>&1 || true
  systemctl restart caddy || true
fi

echo "== Dienste =="
install -m 644 "$DIR/server/ta-trigger.service" "$DIR/server/ta-trigger.timer" \
               "$DIR/server/ta-radar.service" "$DIR/server/ta-update.service" "$DIR/server/ta-update.timer" \
               "$DIR/server/ta-collect.service" "$DIR/server/ta-collect.timer" \
               "$DIR/server/ta-strategien.service" "$DIR/server/ta-strategien.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ta-trigger.timer ta-update.timer ta-collect.timer ta-strategien.timer >/dev/null
systemctl enable ta-radar.service >/dev/null
systemctl restart ta-radar.service || true

echo
if [ -n "${HOST:-}" ]; then
  echo "Deine Server-Adresse für die App (Einstellungen → Dein Server):"
  echo "   https://$HOST"
fi
echo
echo "Fertig. Falls noch nicht geschehen, die Zugangsdaten eintragen:"
echo "   sudo nano $ENV"
echo "und danach:"
echo "   sudo systemctl restart ta-radar && sudo systemctl start ta-collect"
echo "Prüfen:  systemctl status ta-radar   |   journalctl -u ta-trigger -n 20"
