"""Prüft die Asset-Datenbank gegen die echten Quellen.

Für jedes Instrument:
- Liefert die Kursquelle (Yahoo bzw. Kraken) aktuelle Tageskerzen?
- Kennt Tradegate die ISIN, und passt der Tradegate-Kurs zum Kurs der
  Heimatbörse (höchstens 8 % Abweichung)? So fällt auf, wenn eine ISIN zu einem
  anderen Unternehmen gehört.

Ausgabe: eine Zeile je Problem mit „PROBLEM“ am Anfang, am Ende eine Übersicht.
Der Befehl ändert nichts am Datenbestand.
"""

from __future__ import annotations

import pandas as pd

from .config import load_settings
from .data.providers import ProviderError, build_providers
from .quotes import fetch_quotes


def main() -> int:
    settings = load_settings()
    now = pd.Timestamp.now(tz="UTC")
    start = now - pd.Timedelta(days=12)
    providers = build_providers(["yahoo", "kraken"])
    yahoo, kraken = providers["yahoo"], providers["kraken"]
    insts = [i for i in settings.instruments if settings.markets[i.market].enabled]

    ysyms = [i.symbol_for("yahoo") for i in insts if i.symbol_for("yahoo")]
    yahoo.prefetch(ysyms, "1d", start, now)

    price: dict[str, float] = {}
    problems: list[str] = []
    for inst in insts:
        got = None
        for name, prov in (("yahoo", yahoo), ("kraken", kraken)):
            sym = inst.symbol_for(name)
            if not sym:
                continue
            try:
                df = prov.fetch(sym, "1d", start, now)
            except ProviderError as exc:
                problems.append(f"PROBLEM {inst.id}: {name} {sym}: {exc}")
                continue
            if df.empty:
                problems.append(f"PROBLEM {inst.id}: {name} {sym}: keine Kerzen")
                continue
            age = (now - df.index[-1]).days
            if age > 6:
                problems.append(f"PROBLEM {inst.id}: {name} {sym}: letzte Kerze {age} Tage alt")
            got = got or float(df["close"].iloc[-1])
        if got is None:
            problems.append(f"PROBLEM {inst.id}: keine Quelle liefert Kurse")
        else:
            price[inst.id] = got

    rate = price.get("EURUSD")
    quotes, q_errors = fetch_quotes(settings, now)
    for inst in insts:
        if not inst.isin:
            continue
        q = quotes.get(inst.id)
        if q is None:
            problems.append(f"PROBLEM {inst.id}: Tradegate kennt ISIN {inst.isin} nicht")
            continue
        ref = price.get(inst.id)
        if ref is None:
            continue
        if inst.unit == "$":
            if not rate:
                continue
            ref = ref / rate
        dev = q.last / ref - 1
        flag = "PROBLEM" if abs(dev) > 0.08 else "ok     "
        line = f"{flag} {inst.id}: Tradegate {q.last:.4g} € vs Heimatbörse {ref:.4g} € ({dev:+.1%})"
        if flag == "PROBLEM":
            problems.append(line + " – ISIN gehört vermutlich zu einem anderen Wert")
        else:
            print(line)

    print("\n".join(problems) if problems else "Keine Probleme gefunden.")
    print(f"\n{len(insts)} Instrumente geprüft, {len(price)} mit Kursen, {len(quotes)} mit Tradegate-Kurs, "
          f"{len(problems)} Probleme, {len(q_errors)} Tradegate-Fehler.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
