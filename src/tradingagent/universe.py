"""Erzeugt config/universe.yaml aus öffentlichen Verzeichnissen (keine erfundenen Werte).

Quellen:
- Xetra „All tradable instruments“ (Deutsche Börse, täglich aktualisiert): Aktien, ETFs, ETCs
  mit ISIN, WKN und Xetra-Kürzel. Yahoo-Symbol = Kürzel + „.DE“.
- Yahoo-Aktienscreener: größte US-Unternehmen nach Börsenwert.

Auswahl nach Liquidität: Für die Xetra-Kandidaten wird der durchschnittliche Tagesumsatz
(Kurs × Stückzahl, letzte 20 Handelstage) bei Yahoo ermittelt; genommen werden die
umsatzstärksten. Werte ohne Kurse bei Yahoo fallen heraus.

Dasselbe Kürzel an verschiedenen Börsen ist nicht dasselbe Instrument: Xetra-Werte bekommen
das Xetra-Kürzel, US-Werte das US-Kürzel; doppelte ISINs werden übersprungen.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd
import requests
import yaml

from .config import CONFIG_DIR, load_settings

UA = {"User-Agent": "Mozilla/5.0 (tradingagent; privates Forschungsprojekt)"}
XETRA_PAGE = "https://www.xetra.com/xetra-en/instruments/instruments"

# Zielgrößen (zusätzlich zu den handverlesenen Werten)
N_SHARES_XETRA = 260
N_ETF = 260
N_ETC = 40
N_US = 460

# Aktien aus diesen Xetra-Gruppen sind meist US-Werte in Euro; die kommen über die US-Börse
SKIP_GROUPS = {"NORDAMERIKA"}

_ABBR = {"ISHS": "iShares", "ISHSII": "iShares II", "ISHSIII": "iShares III", "ISHSIV": "iShares IV", "ISHSV": "iShares V",
         "ISHSVI": "iShares VI", "ISHSVII": "iShares VII", "XTR": "Xtrackers", "XTRACKERS": "Xtrackers", "VANG": "Vanguard",
         "VANGUARD": "Vanguard", "AMUN": "Amundi", "AMUNDI": "Amundi", "INV": "Invesco", "INVESCO": "Invesco",
         "SPDR": "SPDR", "WISDOMTREE": "WisdomTree", "WT": "WisdomTree", "LYX": "Lyxor", "HSBC": "HSBC", "UBS": "UBS",
         "DWS": "DWS", "L+G": "L&G", "VANECK": "VanEck", "GLOB": "Global", "INH": "", "INH.": "", "NA": "", "O.N.": "",
         "ON": "", "AG": "AG", "SE": "SE", "NV": "NV", "SA": "SA", "PLC": "plc", "INC": "Inc.", "EO": "", "DL": "",
         "-,01": "", "-,10": "", "ACC": "Acc", "DIS": "Dis", "UCITS": "UCITS", "ETF": "ETF", "ETC": "ETC", "MSCI": "MSCI",
         "S&P": "S&P", "USD": "USD", "EUR": "EUR", "ESG": "ESG", "DAX": "DAX", "MDAX": "MDAX", "SDAX": "SDAX", "EM": "EM",
         "US": "US", "UK": "UK", "AI": "AI", "STOXX": "Stoxx", "FTSE": "FTSE", "NASDAQ": "Nasdaq", "ST.": "", "VZ": "Vz.",
         "VORZ.": "Vz.", "STAMMAKT.": "", "NAM.-AKT.": "", "NAMENS-AKT.": "", "INHABER-AKT.": "", "KGAA": "KGaA"}


def pretty_name(raw: str) -> str:
    """Xetra-Namen sind gekürzt und in Großbuchstaben; lesbarer machen, ohne etwas zu erfinden."""
    out = []
    for word in str(raw).split():
        key = word.upper().strip(",")
        if key in _ABBR:
            if _ABBR[key]:
                out.append(_ABBR[key])
            continue
        parts = re.split(r"([\-.])", word)
        conv = []
        for p in parts:
            u = p.upper()
            if p in ("-", ".") or not p:
                conv.append(p)
            elif u in _ABBR:
                conv.append(_ABBR[u])
            elif any(ch.isdigit() for ch in p) or len(p) <= 3:
                conv.append(p)
            else:
                conv.append(p.capitalize())
        token = "".join(conv).strip("-.")
        if token:
            out.append(token)
    name = " ".join(out).replace(".", " ").replace("-", " ")
    name = re.sub(r"\s+", " ", name).strip(" -,")
    return name or str(raw)


def xetra_list() -> pd.DataFrame:
    page = requests.get(XETRA_PAGE, headers=UA, timeout=30)
    page.raise_for_status()
    m = re.search(r'href="([^"]*allTradableInstruments[^"]*\.csv)"', page.text)
    if not m:
        raise RuntimeError("Xetra-Instrumentenliste nicht gefunden (Seitenaufbau geändert?)")
    url = m.group(1) if m.group(1).startswith("http") else "https://www.xetra.com" + m.group(1)
    csv = requests.get(url, headers=UA, timeout=120)
    csv.raise_for_status()
    df = pd.read_csv(io.StringIO(csv.content.decode("utf-8", "replace")), sep=";", skiprows=2, dtype=str, on_bad_lines="skip")
    df = df[(df["Instrument Status"] == "Active") & (df["Currency"] == "EUR")]
    print(f"Xetra-Liste: {len(df)} aktive Euro-Instrumente ({url.rsplit('/', 1)[-1]})")
    return df


def turnover_rank(symbols: list[str]) -> dict[str, float]:
    """Durchschnittlicher Tagesumsatz in Euro der letzten 20 Handelstage je Yahoo-Symbol."""
    import yfinance as yf
    out: dict[str, float] = {}
    for i in range(0, len(symbols), 200):
        chunk = symbols[i:i + 200]
        try:
            data = yf.download(tickers=chunk, period="1mo", interval="1d", group_by="ticker", auto_adjust=False,
                               actions=False, threads=True, progress=False)
        except Exception as exc:  # einzelne Blöcke dürfen scheitern
            print(f"  Block {i}: {exc}")
            continue
        for sym in chunk:
            try:
                d = data[sym] if isinstance(data.columns, pd.MultiIndex) else data
                t = (d["Close"] * d["Volume"]).dropna()
                t = t[t > 0].tail(20)
                if len(t) >= 5:
                    out[sym] = float(t.mean())
            except (KeyError, TypeError):
                continue
        print(f"  Umsätze: {min(i + 200, len(symbols))}/{len(symbols)} geprüft, {len(out)} mit Kursen")
    return out


def us_largest(n: int) -> list[dict]:
    import yfinance as yf
    from yfinance import EquityQuery
    q = EquityQuery("and", [EquityQuery("eq", ["region", "us"]), EquityQuery("gt", ["intradaymarketcap", 2_000_000_000])])
    rows: list[dict] = []
    for offset in range(0, n + 250, 250):
        res = yf.screen(q, size=250, offset=offset, sortField="intradaymarketcap", sortAsc=False)
        quotes = res.get("quotes", [])
        rows += [x for x in quotes if x.get("quoteType") == "EQUITY" and x.get("currency") == "USD"]
        if len(quotes) < 250 or len(rows) >= n + 60:
            break
    print(f"US-Screener: {len(rows)} Unternehmen")
    return rows


def main() -> int:
    settings = load_settings()
    have_isin = {i.isin for i in settings.instruments if i.isin and i.tier == "core"}
    have_ids = {i.id for i in settings.instruments if i.tier == "core"}
    have_yahoo = {i.symbol_for("yahoo") for i in settings.instruments if i.tier == "core"}
    out: list[dict] = []
    used_ids = set(have_ids)

    def new_id(base: str) -> str:
        cand = re.sub(r"[^A-Za-z0-9.\-]", "", base) or "X"
        k, n = cand, 2
        while k in used_ids:
            k = f"{cand}-{n}"
            n += 1
        used_ids.add(k)
        return k

    # ---------- Xetra: Aktien, ETFs, ETCs ----------
    df = xetra_list()
    df = df[~df["ISIN"].isin(have_isin)]
    shares = df[(df["Instrument Type"] == "CS") & (~df["Product Assignment Group Description"].isin(SKIP_GROUPS))]
    etfs = df[df["Instrument Type"] == "ETF"]
    etcs = df[df["Instrument Type"] == "ETC"]
    # ETFs gibt es über 3000; vorab nur die als liquide eingestuften prüfen
    etfs = etfs[etfs["Regulatory Liquid Instrument"] == "Y"]
    cands = pd.concat([shares, etfs, etcs])
    cands = cands[cands["Mnemonic"].notna()]
    symbols = [f"{m}.DE" for m in cands["Mnemonic"]]
    symbols = [s for s in symbols if s not in have_yahoo]
    print(f"Prüfe Umsätze für {len(symbols)} Xetra-Kandidaten …")
    turn = turnover_rank(symbols)

    def pick(frame: pd.DataFrame, n: int, kind: str) -> None:
        f = frame.copy()
        f["yahoo"] = f["Mnemonic"] + ".DE"
        f["turn"] = f["yahoo"].map(turn)
        f = f[f["turn"].notna()].sort_values("turn", ascending=False).head(n)
        for _, r in f.iterrows():
            wkn = str(r.get("WKN") or "").lstrip("0")[-6:] or None
            out.append({"id": new_id(r["Mnemonic"]), "name": pretty_name(r["Instrument"]), "market": "xetra", "kind": kind,
                        "isin": r["ISIN"], "wkn": wkn, "yahoo": r["yahoo"]})
        print(f"  {kind}: {len(f)} übernommen")

    pick(shares, N_SHARES_XETRA, "aktie")
    pick(etfs, N_ETF, "etf")
    pick(etcs, N_ETC, "etf")

    # ---------- USA: größte Unternehmen ----------
    us = us_largest(N_US)
    added = 0
    for x in us:
        sym = x.get("symbol")
        if not sym or sym in have_yahoo or sym in used_ids or "." in sym and not sym.endswith((".A", ".B")):
            continue
        name = x.get("longName") or x.get("shortName") or sym
        out.append({"id": new_id(sym), "name": name, "market": "us", "kind": "aktie", "unit": "$", "yahoo": sym})
        added += 1
        if added >= N_US:
            break
    print(f"  US-Aktien: {added} übernommen")

    path = CONFIG_DIR / "universe.yaml"
    header = ("# Automatisch erzeugt mit `python -m tradingagent universe` (Ablauf „Verzeichnis aktualisieren“).\n"
              "# Nicht von Hand bearbeiten: eigene Werte gehören in instruments.yaml.\n"
              f"# Stand: {pd.Timestamp.now(tz='Europe/Berlin'):%d.%m.%Y %H:%M}, {len(out)} Werte.\n")
    body = yaml.safe_dump({"instruments": out}, allow_unicode=True, sort_keys=False, width=200)
    Path(path).write_text(header + body, encoding="utf-8")
    print(f"{len(out)} Werte nach {path} geschrieben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
