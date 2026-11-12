# Trading-Agent

Privates Projekt mit zwei Bereichen in einer Handy-App:

- **Ratgeber**: Einschätzungen zu Aktien, ETFs und Krypto, nur zur Information. Kein Zugriff auf ein Depot.
- **Simulator**: handelt selbstständig mit Spielgeld, um zu prüfen, ob die Modelle funktionieren. Kein echter Broker.

Keine Anlageberatung. Es werden keine echten Orders ausgeführt.

## Stand: Phase 2 (Dateninfrastruktur)

| Teil | Was er tut |
| --- | --- |
| Datensammler | holt alle 15 Minuten Kurse: Krypto (Kraken, Binance), Xetra (Yahoo, 15 Min verzögert) |
| Qualitätsprüfung | entfernt fehlerhafte Kerzen, meldet Lücken, Kurssprünge und veraltete Daten |
| Speicher | SQLite-Datei (`data/market.sqlite`), auf GitHub im Actions-Cache |
| Export | verschlüsselte Daten (AES-256-GCM, Passwort nur bei dir) für die App |
| App | `app/`: installierbare Web-App mit 14 Zeitansichten, Krypto live |

## Ordner

```
app/                     Handy-App (wird auf GitHub Pages veröffentlicht)
config/settings.yaml     Märkte, Quellen, Historie
config/instruments.yaml  Beobachtungsliste (keine persönlichen Depotwerte!)
src/tradingagent/        Python-Code
tests/                   automatische Tests
.github/workflows/       Zeitplan: Daten sammeln alle 15 Minuten, Tests
deploy/oracle/           Umzug auf einen eigenen Server
```

## Befehle

```
PYTHONPATH=src python -m tradingagent collect   # sammeln + exportieren
PYTHONPATH=src python -m tradingagent status    # was ist gespeichert?
PYTHONPATH=src python -m pytest -q              # Tests
```

Das Passwort für die Verschlüsselung kommt aus der Umgebungsvariable `APP_PASSPHRASE`
(auf GitHub als Secret). Es steht nie im Code.

## Umzug auf Oracle Cloud

Auf einem frischen Ubuntu-Server:

```
curl -fsSL https://raw.githubusercontent.com/<NAME>/<PROJEKT>/main/deploy/oracle/install.sh | bash -s -- https://github.com/<NAME>/<PROJEKT>.git
```

Danach läuft der Sammler alle 5 Minuten per systemd. Auf dem Server kann in
`config/settings.yaml` Binance vor Kraken gesetzt werden (Frankfurt ist nicht gesperrt).

## Bekannte Grenzen

- Xetra-Kurse kostenlos nur 15 Minuten verzögert; 1-Minuten-Kerzen gibt es nur für Krypto.
- Yahoo Finance ist eine inoffizielle Quelle und kann jederzeit ausfallen.
- Kraken liefert je Intervall nur die letzten 720 Kerzen; ältere Krypto-Tageskerzen kommen von Yahoo.
- GitHub startet Zeitpläne teils mit einigen Minuten Verspätung.
- Ist der Actions-Cache weg (z. B. nach 7 Tagen ohne Lauf), lädt der nächste Lauf die Historie neu.
