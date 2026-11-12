# Datenquellen je Anlageklasse

Stand: 12.11.2026. Alle Quellen sind kostenlos. Kostenpflichtige Datenabos werden nicht genutzt.

| Anlageklasse | Anbieter | Aktualisierung | Kosten | Einschränkungen | Datenqualität |
|---|---|---|---|---|---|
| Kryptowährungen | Binance-WebSocket (Euro- und USDT-Paare), Kraken (Kerzen, WebSocket v2 als Ersatz und für Werte ohne Binance-Paar) | laufend, etwa jede Sekunde; die laufende Kerze wächst mit jedem Kurs | kostenlos | Paare in Euro. Bei „$“ zeigt die App den Binance-USDT-Kurs, USDT ist nicht exakt US-Dollar | börsenplatzbezogene Echtzeitdaten (Binance bzw. Kraken) |
| Deutsche und europäische Aktien, ETFs | Tradegate (inoffizielle Kursabfrage), Yahoo Finance (Kerzen) | Tradegate: sichtbare Werte und offener Chart alle 2 s, alle übrigen reihum; Tradegate selbst aktualisiert etwa alle 10 s | kostenlos | Live-Kurs stammt von Tradegate, nicht von Xetra oder der Heimatbörse. Kerzen 15 Minuten verzögert | börsenplatzbezogene Echtzeitdaten (Tradegate) und verzögerte Daten (Yahoo) |
| US-Aktien und US-ETFs | Finnhub-WebSocket (eigener Schlüssel), Tradegate, Yahoo Finance | Finnhub laufend während der US-Handelszeit | kostenlos | Finnhub kostenlos: nur US-Börsen, höchstens 50 Werte gleichzeitig | Echtzeit-Handelsdaten der US-Börsen |
| Indizes | Yahoo Finance | alle 15 Minuten | kostenlos | europäische und asiatische Indizes 15 Minuten verzögert; reine Referenzwerte, nicht handelbar | verzögerte Daten |
| Rohstoffe | Yahoo Finance (Terminkontrakte CME/ICE) | alle 15 Minuten | kostenlos | etwa 10 Minuten verzögert; Sprünge beim Wechsel in den nächsten Kontrakt | verzögerte Daten |
| Devisen | Yahoo Finance | alle 15 Minuten | kostenlos | Referenzkurs, kein Handelskurs einer Bank | nahezu aktuell |

## Geprüft, aber nicht genutzt

- **Alpaca:** Der kostenlose Tarif liefert in Echtzeit nur die Börse IEX, die wenige Prozent des US-Handels abdeckt, für höchstens 30 Werte. Er braucht Schlüssel und Geheimnis im Browser. Finnhub deckt dasselbe besser ab.
- **Xetra-Echtzeit, Finnhub international:** nur kostenpflichtig.
- **Google Finance, Nasdaq.com:** keine nutzbare Schnittstelle für Apps.

## Datenstatus in der App

- **LIVE:** Kurs aus einem Live-Strom oder einer Live-Abfrage, höchstens 90 Sekunden alt, Börse geöffnet.
- **VERZÖGERT:** Börse geöffnet, aber nur verzögerte Daten.
- **LETZTER SCHLUSSKURS:** Börse geschlossen (Wochenende, Feiertag, außerhalb der Handelszeit).
- **KEINE DATEN:** kein Kurs vorhanden.

Außerhalb der Handelszeit erzeugt die App keine Live-Bewegung. Feiertage sind für Xetra, Tradegate, Euronext und NYSE/Nasdaq für 2026 und 2027 hinterlegt.

## Prüfung der Asset-Datenbank

Der Ablauf „Asset-Datenbank prüfen“ (`python -m tradingagent verify`) kontrolliert jedes Instrument:

- Liefert Yahoo bzw. Kraken Kurse?
- Kennt Tradegate die ISIN?
- Weicht der Tradegate-Kurs höchstens 8 % vom Kurs der Heimatbörse ab? Ist die Abweichung größer, gehört die ISIN vermutlich zu einem anderen Wert.

Letzte Prüfung: 288 Instrumente, alle mit Kursen. Bei zwei Werten kannte Tradegate die ISIN nicht (Unilever, SQQQ); diese laufen ohne Tradegate.
