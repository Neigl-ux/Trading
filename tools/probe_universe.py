"""Erkundet öffentliche Instrumentenverzeichnisse (läuft auf GitHub, ändert nichts)."""
import io, re, sys, time
import requests
import pandas as pd

UA = {"User-Agent": "Mozilla/5.0 (tradingagent research)"}

def show(title, fn):
    print(f"\n===== {title} =====")
    try:
        fn()
    except Exception as exc:
        print("FEHLER:", type(exc).__name__, exc)

def xetra():
    pages = ["https://www.xetra.com/xetra-en/instruments/instruments",
             "https://www.xetra.com/xetra-de/instrumente/alle-handelbaren-instrumente"]
    url = None
    for p in pages:
        r = requests.get(p, headers=UA, timeout=20)
        print(p, r.status_code, len(r.text))
        m = re.findall(r'href="([^"]*allTradableInstruments[^"]*\.csv)"', r.text)
        print("Treffer:", m[:3])
        if m:
            url = m[0] if m[0].startswith("http") else "https://www.xetra.com" + m[0]
            break
    if not url:
        return
    r = requests.get(url, headers=UA, timeout=60)
    print("CSV", url, r.status_code, len(r.content))
    text = r.content.decode("utf-8", "replace")
    lines = text.splitlines()
    for l in lines[:6]:
        print(l[:300])
    df = pd.read_csv(io.StringIO(text), sep=";", skiprows=2, dtype=str, on_bad_lines="skip")
    print(df.columns.tolist())
    print(len(df))
    for col in df.columns:
        if df[col].nunique() < 40:
            print(col, df[col].value_counts().head(15).to_dict())

def yscreen():
    import yfinance as yf
    print("yfinance", yf.__version__)
    from yfinance import EquityQuery
    q = EquityQuery("and", [EquityQuery("eq", ["region", "us"]), EquityQuery("gt", ["intradaymarketcap", 5_000_000_000])])
    res = yf.screen(q, size=250, sortField="intradaymarketcap", sortAsc=False)
    quotes = res.get("quotes", [])
    print("US-Treffer", res.get("total"), len(quotes))
    for x in quotes[:5]:
        print({k: x.get(k) for k in ("symbol", "longName", "exchange", "marketCap", "currency", "quoteType")})
    q2 = EquityQuery("and", [EquityQuery("eq", ["region", "de"]), EquityQuery("gt", ["intradaymarketcap", 500_000_000])])
    res2 = yf.screen(q2, size=250, sortField="intradaymarketcap", sortAsc=False)
    print("DE-Treffer", res2.get("total"), len(res2.get("quotes", [])))
    for x in res2.get("quotes", [])[:5]:
        print({k: x.get(k) for k in ("symbol", "longName", "exchange", "marketCap", "currency")})

def nasdaq():
    for u in ["https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"]:
        r = requests.get(u, headers=UA, timeout=30)
        print(u, r.status_code, len(r.text))
        print("\n".join(r.text.splitlines()[:3]))

def tradegate_list():
    for u in ["https://www.tradegatebsx.com/index.php", "https://www.tradegate.de/"]:
        r = requests.get(u, headers=UA, timeout=20)
        print(u, r.status_code, len(r.text), re.findall(r'href="([^"]*(?:csv|Instrument|instrument)[^"]*)"', r.text)[:10])

show("Xetra", xetra)
show("Yahoo-Screener", yscreen)
show("Nasdaq Trader", nasdaq)
show("Tradegate", tradegate_list)
