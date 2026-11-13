# Eigener Server (Oracle Cloud, kostenlos)

Der Server macht drei Dinge, die GitHub allein nicht zuverlässig kann:

1. **Abruf rund um die Uhr:** Er startet alle 15 Minuten den Sammel-Lauf auf GitHub. GitHubs eigener Zeitplan lässt nachts viele Läufe ausfallen (in der Nacht zum 13.11. lief von 2 bis 8 Uhr nur ein einziger).
2. **Liveradar mit Push:** Er beobachtet jede Minute alle Kernwerte und schickt dir über ntfy eine Nachricht bei starken Bewegungen und erreichten Nachkauf-Stufen, auch wenn die App zu ist.
3. **Aktualisiert sich selbst** jeden Morgen um 5 Uhr.

Er braucht keine offenen Ports, nur ausgehende Verbindungen. Dein Portfolio kommt nie auf den Server.

## 1. Oracle-Konto anlegen (10 Minuten)

1. Auf **cloud.oracle.com** „Kostenlos starten“ wählen. Als Heimatregion **Germany Central (Frankfurt)** nehmen; sie lässt sich später nicht ändern.
2. Oracle verlangt eine Kreditkarte zur Prüfung. Solange du nur „Always Free“-Bausteine nutzt, wird nichts abgebucht.

## 2. Server anlegen

1. Im Menü **Compute → Instances → Create instance**.
2. **Image:** Canonical Ubuntu 24.04.
3. **Shape:** „Ampere“ → **VM.Standard.A1.Flex** mit **1 OCPU und 6 GB**. Das liegt sicher im kostenlosen Rahmen (derzeit 2 OCPU / 12 GB insgesamt). Meldet Oracle „Out of capacity“, später erneut versuchen oder **VM.Standard.E2.1.Micro** (AMD, ebenfalls kostenlos) nehmen.
4. **SSH-Schlüssel:** „Generate a key pair for me“ → **Save private key** (Datei gut aufbewahren).
5. **Create**. Nach etwa einer Minute steht die **Public IP address** auf der Seite.

## 3. Verbinden und einrichten

Unter Windows die **PowerShell** öffnen (auf dem Mac das Terminal) und mit dem Pfad zu deinem Schlüssel:

```
ssh -i C:\Pfad\zum\ssh-key.key ubuntu@DEINE-IP
```

Auf dem Server dann einmal:

```
curl -fsSL https://raw.githubusercontent.com/Neigl-ux/Trading/main/server/install.sh | sudo bash
```

## 4. Zugangsdaten eintragen

**GitHub-Token** (damit der Server den Sammel-Lauf starten darf):
github.com → Profilbild → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token.
Repository access: **Only select repositories → Trading**. Permissions → Repository → **Actions: Read and write**. Laufzeit z. B. 1 Jahr. Token kopieren.

Dann auf dem Server:

```
sudo nano /etc/trading-agent.env
```

`GH_TOKEN=` (Token einfügen), `NTFY_TOPIC=` (dein ntfy-Kanal), optional `RADAR_EXTRA=` mit zusätzlichen Kennungen aus der App (z. B. `NVDA,SAP,RHM`). Speichern mit Strg+O, Enter, Strg+X. Danach:

```
sudo systemctl restart ta-radar && sudo systemctl start ta-trigger
journalctl -u ta-trigger -n 5
```

Dort sollte „Sammel-Lauf gestartet.“ oder „Es läuft bereits ein Sammel-Lauf“ stehen.

## Was du bekommst

| Meldung | ETF / Index | Aktie | Krypto |
|---|---|---|---|
| Tagesbewegung | ±2 / 3 / 5 / 8 % | ±5 / 8 / 12 / 20 % | ±7 / 10 / 15 / 25 % in 24 h |
| Schnell in 15 Minuten | ±1,5 % | ±3 % | ±4 % |
| Nachkauf-Stufe | −10 / −15 / −20 / −25 / −30 % vom 12-Monats-Hoch | – | – |

Jede Stufe meldet sich pro Tag nur einmal, Nachkauf-Stufen einmal pro Rückgang.

## Nützliche Befehle

```
systemctl status ta-radar          # läuft das Radar?
journalctl -u ta-radar -f          # Meldungen live mitlesen
systemctl list-timers 'ta-*'       # wann der nächste Sammel-Lauf startet
sudo systemctl start ta-update     # sofort aktualisieren
```
