# Phase 6 – KI-Analyse: Prüfbericht

Stand 2026-11-14. Ausbaustufe 1. 218 Werte. 745.464 Datenzeilen. Prüfjahre 2016–2026.
Ziel: Wahrscheinlichkeit, dass ein Kauf zur Eröffnung des nächsten Handelstags nach 5 Handelstagen mindestens 2 % im Plus liegt.

## Prognosequalität (nur Prüfjahre)

| Modell | AUC | Brier | Brier Skill | Log Loss | ECE | Trefferquote | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|---|---|
| SVM | 0,600 | 0,198 | 0,021 | 0,583 | 0,019 | 71,3 % | 38,9 % | 3,7 % | 6,8 % |
| Entscheidungsbaum | 0,602 | 0,197 | 0,025 | 0,581 | 0,016 | 71,2 % | 41,3 % | 6,0 % | 10,4 % |
| Grundrate | 0,494 | 0,202 | 0,000 | 0,595 | 0,011 | 71,9 % | – | 0,0 % | – |

Brier Skill > 0 heißt: besser als die Grundrate (immer dieselbe Wahrscheinlichkeit). ECE = mittlere Abweichung zwischen Prognose und Eintritt.

## Handel mit den Regeln und Kosten aus Phase 4

| Strategie | pro Jahr | größter Rückgang | Sharpe | Sortino | Profit Factor | Trades | Treffer | Ø Haltedauer | Gebühren |
|---|---|---|---|---|---|---|---|---|---|
| SVM | -3,5 % | -33,3 % | -0,88 | -0,17 | 0,25 | 82 | 26,8 % | 5,8 Tage | 1,312 € |
| Entscheidungsbaum | -0,5 % | -4,9 % | -0,40 | -0,04 | 0,24 | 11 | 36,4 % | 6,1 Tage | 176 € |
| Beste Technik (Phase 5): Mehrheit aus allen sechs | -11,7 % | -74,9 % | -1,15 | -0,66 | 0,51 | 497 | 26,4 % | – | 7,952 € |
| MSCI World halten | +11,3 % | -33,6 % | 0,78 | 0,71 | – | 1 je Jahr | – | – | – |
| Zufällige Käufe (Median, beste 10 % ab) | -14,4 % (-13,6 %) | – | – | – | – | – | – | – | – |

## Zulassung für Phase 7

**SVM: Nicht freigegeben**

- ✗ Genug Testentscheidungen: 82 (Grenze ≥ 100 Trades)
- ✓ Trennschärfe (AUC): 0,600 (Grenze ≥ 0.52)
- ✓ Besser als die Grundrate (Brier Skill): 0,021 (Grenze > 0.0)
- ✓ Stabil über die Jahre: 0,909 (Grenze in ≥ 60% der Jahre besser als Grundrate)
- ✓ Kalibrierung (ECE): 0,019 (Grenze ≤ 0.05)
- ✗ Rendite nach Kosten über MSCI World: -0,035 (Grenze > 0.113)
- ✓ Besser als Zufall (beste 10 %): -0,035 (Grenze > -0.136)
- ✓ Größter Rückgang: -0,333 (Grenze höchstens −35%)
- ✗ Positive Jahre: 0,091 (Grenze ≥ 50%)

**Entscheidungsbaum: Nicht freigegeben**

- ✗ Genug Testentscheidungen: 11 (Grenze ≥ 100 Trades)
- ✓ Trennschärfe (AUC): 0,602 (Grenze ≥ 0.52)
- ✓ Besser als die Grundrate (Brier Skill): 0,025 (Grenze > 0.0)
- ✓ Stabil über die Jahre: 0,909 (Grenze in ≥ 60% der Jahre besser als Grundrate)
- ✓ Kalibrierung (ECE): 0,016 (Grenze ≤ 0.05)
- ✗ Rendite nach Kosten über MSCI World: -0,005 (Grenze > 0.113)
- ✓ Besser als Zufall (beste 10 %): -0,005 (Grenze > -0.136)
- ✓ Größter Rückgang: -0,049 (Grenze höchstens −35%)
- ✗ Positive Jahre: 0,000 (Grenze ≥ 50%)

## Gegenprobe auf Datenlecks

Baum mit zufällig vertauschter Zielgröße trainiert; ohne Leck liegt die AUC bei etwa 0,50.

- 2025: AUC 0,524
- 2026: AUC 0,487

Ergebnis: unauffällig

## Grenzen

- Nur Werte, die es heute gibt (Survivorship Bias): verschwundene Firmen fehlen, das schönt die Ergebnisse eher.
- Branchen-Zuordnung von heute wird auch für die Vergangenheit verwendet (Firmen können früher anders eingeordnet gewesen sein).
- Nachrichten-Signal: weltweite Tageslage aus dem GDELT-Archiv ab April 2013, um einen Tag verzögert; keine Meldungen zu einzelnen Firmen.
- Quartalszahlen fließen (noch) nicht als Eingangsgröße ein: die kostenlose Quelle reicht nicht verlässlich weit zurück.
- Tageskerzen von Yahoo (Dividenden eingerechnet), Ausführung zur Eröffnung des Folgetags mit geschätztem Spread und Schlupf.
- Das Kalibrierjahr dient zugleich der Auswahl der Einstellung; berichtet wird nur das danach folgende, unberührte Prüfjahr.
