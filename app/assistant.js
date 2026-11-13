// Interner Assistent: beantwortet Fragen ohne KI, ohne Konto, ohne Kosten.
// Er versteht Fragen über Schlüsselwörter und antwortet aus
//   1. den Daten der App (Kurse, Ratgeber, Portfolio, Weltlage, Rückblick-Tests) und
//   2. einer eingebauten Wissensbasis (Begriffe, Strategien, Steuern, Bedienung der App).
// Alles läuft auf dem Gerät; nichts verlässt das Handy.

export const norm = (t) => String(t || "").toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
  .normalize("NFD").replace(/[̀-ͯ]/g, "");
const words = (t) => norm(t).split(/[^a-z0-9]+/).filter(Boolean);

// ---------- Wissensbasis ----------
// keys: Wörter oder Wortteile (normalisiert), die auf das Thema hinweisen. Das erste ist der Hauptbegriff.
export const KB = [
  { keys: ["etf", "indexfonds", "exchange traded"], title: "ETF", text: "Ein **ETF** (börsengehandelter Indexfonds) bildet einen Index nach, z. B. den MSCI World mit rund 1.300 Firmen. Du kaufst mit einem Anteil die ganze Mischung. Vorteile: breite Streuung, niedrige Kosten (oft 0,1–0,3 % pro Jahr), jederzeit handelbar. Das Geld ist Sondervermögen: Geht der Anbieter pleite, bleibt es dir." },
  { keys: ["msci world", "welt etf", "weltportfolio", "all world", "ftse all"], title: "Welt-ETF", text: "Ein **Welt-ETF** (MSCI World, FTSE All-World, MSCI ACWI) streut über Industrieländer, beim All-World/ACWI auch Schwellenländer. Etwa 60–70 % stecken in US-Firmen. Im Rückblick-Test der App war ein schlichter Welt-ETF-Sparplan die Strategie, die keine Punktzahl schlagen konnte: 2010–2026 rund +12 % pro Jahr, größter Rückgang −34 % (Corona 2020)." },
  { keys: ["sparplan", "monatlich sparen", "dauerauftrag"], title: "Sparplan", text: "Ein **Sparplan** kauft jeden Monat automatisch für einen festen Betrag. Du kaufst dadurch mal teuer, mal günstig, ohne den richtigen Zeitpunkt suchen zu müssen. Wichtig sind niedrige Kosten pro Ausführung und Durchhalten in Krisen: Wer bei −30 % weiterspart, kauft billig ein." },
  { keys: ["cost average", "durchschnittskosten", "cost-average"], title: "Cost-Average-Effekt", text: "Beim **Cost-Average-Effekt** kaufst du mit festem Betrag bei niedrigen Kursen mehr Anteile, bei hohen weniger. Er senkt das Risiko eines schlechten Einstiegszeitpunkts. Mehr Rendite als eine Einmalanlage bringt er im Schnitt nicht, weil Märkte öfter steigen als fallen; sein Wert ist die Ruhe." },
  { keys: ["einmalanlage", "sofort anlegen", "alles auf einmal", "timing", "market timing"], title: "Einmalanlage oder warten?", text: "Im Rückblick-Test der App lag **sofort anlegen** im Mittel vorn: Geld, das auf einen Rücksetzer wartet, verpasst meistens mehr, als es später günstiger kauft. Gestaffelte Käufe kosten wenig (1–2 % auf 10 Jahre) und dämpfen dafür starke Depot-Rückgänge." },
  { keys: ["pyramide", "nachkauf", "staffel", "rucksetzer", "ruecksetzer", "sale", "dip"], title: "Deine Nachkauf-Pyramide", text: "Deine **Pyramide** nutzt die Reserve von 1.000 € für Einbrüche bei ETFs: −10 % vom 12-Monats-Hoch → 100 €, −15 % → 150 €, −20 % → 200 €, −25 % → 250 €, −30 % → 300 €. Jede Stufe einmal pro Rückgang, erst nach neuem Hoch wieder frei. Im Test (Welt-ETF, 10 Jahre) brachte sie rund 1.500 € mehr als Geld, das nur herumliegt, aber etwa 500 € weniger als sofort anzulegen. Dafür fiel dein Gesamtvermögen über 5 Jahre nur in 10 % statt 40 % der Fälle um mehr als 30 %." },
  { keys: ["reserve", "notgroschen", "notreserve", "tagesgeld"], title: "Reserve und Notgroschen", text: "Halte einen **Notgroschen** (oft 3 Monatsausgaben) getrennt von der Nachkauf-Reserve auf dem Tagesgeld. Wer in einer Krise verkaufen muss, weil er das Geld braucht, verliert mehr als mit jeder schlechten Strategie. Die 1.000 € Reserve sind nur für Nachkäufe gedacht." },
  { keys: ["drawdown", "rueckgang", "einbruch", "crash", "maximaler verlust"], title: "Rückgang (Drawdown)", text: "Der **Rückgang** (Drawdown) misst, wie weit ein Kurs unter seinem bisherigen Hoch liegt. Die App misst ihn vom höchsten Kurs der letzten 12 Monate. Welt-ETFs fielen 2008/09 um rund die Hälfte, 2020 um gut ein Drittel, 2022 um rund ein Fünftel; erholt haben sie sich jedes Mal, aber unterschiedlich schnell." },
  { keys: ["volatilitaet", "schwankung", "volatil"], title: "Volatilität", text: "**Volatilität** ist die typische Schwankung pro Jahr. In den Daten der App: breite ETFs etwa 15–17 %, Einzelaktien im Median 29 %, Nasdaq 100 rund 19 %, Krypto oft 50–80 %. Je höher, desto häufiger und tiefer die Rückgänge." },
  { keys: ["diversifikation", "streuung", "streuen", "klumpen", "klumpenrisiko"], title: "Streuung und Klumpenrisiko", text: "**Streuung** heißt: nicht alles auf einen Wert, eine Branche, ein Land oder eine Währung setzen. Ein **Klumpenrisiko** entsteht, wenn eine einzelne Aktie mehr als etwa 20–25 % des Depots ausmacht. Ein Welt-ETF ist von sich aus breit gestreut; zusätzliche ETFs mit denselben Firmen streuen kaum mehr." },
  { keys: ["rebalancing", "umschichten", "gewichtung"], title: "Rebalancing", text: "Beim **Rebalancing** stellst du die Zielaufteilung wieder her (z. B. 70 % Welt, 30 % Nasdaq), wenn sie durch Kursbewegungen verrutscht ist. Am einfachsten ohne Verkauf: neue Sparraten in den Teil lenken, der zu klein geworden ist." },
  { keys: ["thesaurierend", "ausschuettend", "dividende", "ausschuettung"], title: "Thesaurierend oder ausschüttend", text: "**Thesaurierende** ETFs legen Dividenden automatisch wieder an, **ausschüttende** zahlen sie aufs Konto. Für langfristigen Vermögensaufbau ist thesaurierend bequemer; steuerlich fällt bei thesaurierenden jährlich eine kleine **Vorabpauschale** an, die über den Freistellungsauftrag meist steuerfrei bleibt." },
  { keys: ["steuer", "abgeltung", "sparerpauschbetrag", "freistellungsauftrag", "freibetrag", "teilfreistellung", "vorabpauschale"], title: "Steuern auf Kapitalerträge", text: "Gewinne und Dividenden kosten in Deutschland **25 % Abgeltungsteuer** plus Solidaritätszuschlag (und ggf. Kirchensteuer). Steuerfrei sind **1.000 € pro Jahr** (Sparer-Pauschbetrag, 2.000 € für Verheiratete); dafür bei der Bank einen **Freistellungsauftrag** einrichten. Bei Aktien-ETFs sind 30 % der Erträge steuerfrei (**Teilfreistellung**). Für Details: Steuerberater oder Lohnsteuerhilfeverein." },
  { keys: ["ter", "kosten", "gebuehr", "ordergebuehr", "laufende kosten"], title: "Kosten", text: "Achte auf drei Kosten: **laufende Kosten** des ETFs (TER, z. B. 0,2 % pro Jahr), **Kaufkosten** pro Ausführung (bei vielen Sparplänen 0 €) und den **Spread** (Unterschied zwischen Kauf- und Verkaufskurs). Kosten sind sichere Verluste; alles, was du hier sparst, ist Rendite ohne Risiko." },
  { keys: ["spread", "geldkurs", "briefkurs", "bid", "ask"], title: "Spread", text: "Der **Spread** ist der Abstand zwischen Kaufkurs (Brief) und Verkaufskurs (Geld). Bei großen ETFs und Aktien sind es oft nur 0,05–0,1 %, bei kleinen Werten, nachts oder bei Krypto-Randwerten deutlich mehr. Darum lieber zur Hauptbörsenzeit handeln." },
  { keys: ["limit", "limitorder", "market order", "marketorder", "stop loss", "stop-loss", "stopp", "orderart"], title: "Orderarten", text: "**Market-Order:** sofort zum nächsten Kurs. **Limit-Order:** nur zum Preis oder besser. **Stop-Loss:** wird zur Market-Order, wenn ein Kurs unterschritten wird; schützt vor großen Verlusten, kann aber bei kurzen Ausschlägen unnötig auslösen. Für langfristige ETF-Käufe reicht meist eine Limit-Order knapp über dem aktuellen Briefkurs." },
  { keys: ["tradegate", "xetra", "boerse", "boersenplatz", "handelszeit"], title: "Börsenplätze", text: "**Xetra** (9–17:30 Uhr) ist die Hauptbörse für deutsche Werte und ETFs. **Tradegate** handelt 8–22 Uhr (die App fragt ab 7:30 Uhr ab) und zeigt die Live-Kurse in der App. US-Börsen handeln 15:30–22 Uhr deutscher Zeit." },
  { keys: ["kgv", "kurs gewinn", "bewertung", "pe ratio"], title: "Kurs-Gewinn-Verhältnis", text: "Das **KGV** setzt den Aktienkurs ins Verhältnis zum Gewinn je Aktie. Ein KGV von 20 heißt: Du zahlst das 20-Fache des Jahresgewinns. Hohe KGVs spiegeln hohe Wachstumserwartungen; enttäuschen sie, fällt der Kurs oft stark. Kostenlose Bewertungsdaten gibt es in der App nur für US-Aktien (Finnhub)." },
  { keys: ["rsi", "relative staerke"], title: "RSI", text: "Der **RSI** (Relative-Stärke-Index, 0–100) vergleicht Gewinn- und Verlusttage der letzten 14 Tage. Über 70 gilt als „überkauft“, unter 30 als „überverkauft“. Bei starken Trends bleibt er lange extrem; als alleiniges Kaufsignal taugt er wenig. In der App: Chart → Indikatoren → RSI." },
  { keys: ["macd"], title: "MACD", text: "Der **MACD** ist die Differenz zweier gleitender Durchschnitte (12 und 26 Tage) mit einer Signallinie (9 Tage). Kreuzt er die Signallinie nach oben, gilt das als Aufwärtssignal, nach unten als Abwärtssignal. Er reagiert verzögert und liefert in Seitwärtsphasen viele Fehlsignale." },
  { keys: ["ema", "sma", "gleitender durchschnitt", "200 tage", "200-tage", "golden cross", "death cross"], title: "Gleitende Durchschnitte", text: "Ein **gleitender Durchschnitt** glättet den Kurs (SMA = einfach, EMA = jüngere Tage stärker gewichtet). Viele achten auf die **200-Tage-Linie**: Kurs darüber = langfristiger Aufwärtstrend. **Golden Cross**: 50-Tage-Linie kreuzt die 200-Tage-Linie nach oben, **Death Cross** nach unten. Der Ratgeber nutzt diese Trends mit 35 % Gewicht." },
  { keys: ["bollinger"], title: "Bollinger-Bänder", text: "**Bollinger-Bänder** liegen zwei Standardabweichungen über und unter dem 20-Tage-Durchschnitt. Ein Kurs außerhalb der Bänder ist ungewöhnlich weit gelaufen; enge Bänder kündigen oft eine größere Bewegung an, sagen aber nicht, in welche Richtung." },
  { keys: ["vwap", "atr", "adx"], title: "VWAP, ATR, ADX", text: "**VWAP**: Durchschnittspreis des Tages, gewichtet nach Umsatz; Profis nutzen ihn als fairen Tagespreis. **ATR**: durchschnittliche Tagesspanne, gut für Stop-Abstände. **ADX**: Stärke eines Trends (über 25 = deutlicher Trend), unabhängig von der Richtung." },
  { keys: ["hebel", "knock out", "knockout", "optionsschein", "cfd", "zertifikat", "leverage", "3x", "short"], title: "Hebelprodukte", text: "**Hebelprodukte** (Knock-outs, Optionsscheine, CFDs, 3x-ETFs) vervielfachen Gewinne und Verluste; Knock-outs können wertlos verfallen. Gehebelte ETFs verlieren in Seitwärtsphasen zusätzlich an Wert. Für deine Ziele (5–10 Jahre, max. 30 % Rückgang) passen sie nicht; ausprobieren lässt sich so etwas später im Simulator mit Spielgeld." },
  { keys: ["daytrading", "trading", "scalping", "kurzfristig"], title: "Daytrading", text: "**Daytrading** heißt: Positionen innerhalb eines Tages eröffnen und schließen. Studien zu Privatanlegern zeigen, dass die große Mehrheit dabei nach Kosten verliert. Der geplante Simulator der App (Phase 7) lässt dich Strategien mit Spielgeld testen, ohne echtes Geld zu riskieren." },
  { keys: ["krypto", "bitcoin", "btc", "ethereum", "coin"], title: "Krypto", text: "**Kryptowährungen** schwanken sehr stark (Rückgänge von 70–80 % kamen mehrfach vor) und haben keine Erträge wie Gewinne oder Zinsen. Du hast entschieden, Krypto nicht im Sparplan zu haben; der Ratgeber bewertet sie nur zur Information und gibt ihnen immer Risikostufe 5." },
  { keys: ["inflation", "zinsen", "leitzins", "ezb", "fed"], title: "Zinsen und Inflation", text: "Steigen die **Zinsen**, fallen oft Aktienkurse (besonders Wachstumswerte), weil sichere Anlagen attraktiver werden und künftige Gewinne weniger wert sind (2022). Sinkende Zinsen wirken umgekehrt. Gegen **Inflation** schützen auf lange Sicht vor allem Sachwerte wie Aktien; Bargeld verliert real an Wert." },
  { keys: ["anleihe", "bond", "rente", "staatsanleihe"], title: "Anleihen", text: "**Anleihen** sind Kredite an Staaten oder Firmen mit festem Zins. Sie schwanken weniger als Aktien und dämpfen ein Depot; 2022 fielen aber auch Anleihen deutlich, als die Zinsen schnell stiegen. Bei deinem Horizont von 5–10 Jahren und 30 % Risikotoleranz ist ein reines Aktien-ETF-Depot üblich." },
  { keys: ["gold", "rohstoff", "silber", "oel"], title: "Gold und Rohstoffe", text: "**Gold** bringt keine Erträge, gilt aber als Krisenschutz und schwankt oft anders als Aktien. Rohstoff-ETCs sind Schuldverschreibungen, meist mit physischer Hinterlegung. Als kleine Beimischung (5–10 %) kann Gold Rückgänge dämpfen; Rendite-Treiber ist es langfristig nicht." },
  { keys: ["nasdaq", "s&p", "sp500", "s p 500", "usa", "us aktien", "technologie", "tech"], title: "USA und Technologie", text: "Der **S&P 500** (500 große US-Firmen) und der **Nasdaq 100** (100 größte Nicht-Finanzwerte der Nasdaq, viel Technologie) liefen 2010–2026 stärker als die Welt: Nasdaq rund +19 % pro Jahr gegen +12 % beim Welt-ETF. Das war eine außergewöhnlich gute Phase; nach 2000 verlor der Nasdaq 100 rund 80 % und brauchte etwa 15 Jahre bis zum alten Hoch." },
  { keys: ["schwellenland", "emerging", "em etf", "china", "indien"], title: "Schwellenländer", text: "**Schwellenländer-ETFs** (MSCI Emerging Markets) enthalten China, Indien, Taiwan, Korea u. a. Sie bringen zusätzliche Streuung, schwankten aber stärker und liefen 2010–2026 schwächer als die Industrieländer. Ein FTSE All-World oder MSCI ACWI enthält sie bereits (rund 10 %)." },
  { keys: ["punktzahl", "score", "bewertung ratgeber", "einstufung", "interessant", "vorsicht"], title: "Die Punktzahl des Ratgebers", text: "Die **Punktzahl** (0–100) setzt sich aus Trend (35 %), Risiko (25 %), Qualität (15 %, nur US-Aktien), Handelbarkeit (10 %) und Passung (15 %) zusammen. Ab 65 = interessant, 40–64 = neutral, unter 40 = Vorsicht. Ehrlich: Im Rückblick-Test fand sie **keine Gewinner**, trennte aber das **Risiko** gut. Lies sie als Warnsignal, nicht als Kaufsignal." },
  { keys: ["rueckblick", "backtest", "test", "historisch"], title: "Rückblick-Tests", text: "Die App spielt Regeln an echten Kursen der Vergangenheit durch, als hättest du sie an jedem Monatsanfang begonnen. Ergebnisse: (1) Sofort anlegen schlägt Warten im Mittel. (2) Deine Pyramide kostet wenig Rendite und dämpft Rückgänge. (3) Die Punktzahl findet keine Gewinner, warnt aber vor Risiko. (4) Schlechter Nachrichten-Tonfall ging tieferen Rückgängen in den 3 Monaten danach voraus (schwacher Zusammenhang). Grenzen: keine Gebühren und Steuern, verschwundene Firmen fehlen, die Zukunft kann anders sein." },
  { keys: ["nachrichten", "news", "tonfall", "gdelt", "weltlage"], title: "Nachrichten in der App", text: "Die App liest weltweite Nachrichten über **GDELT** (kostenlos, 65 Sprachen, Archiv seit 2013) und bei US-Aktien über **Finnhub**. Sie zeigt Warnhinweise (Gewinnwarnung, Klage, Sanktionen …) und die **Weltlage**. In die Punktzahl fließen Nachrichten erst, wenn sie sich im Test als hilfreich erweisen." },
  { keys: ["push", "benachrichtigung", "ntfy", "radar", "liveradar", "alarm"], title: "Push und Liveradar", text: "Push-Nachrichten kommen kostenlos über die App **ntfy**. Der Ratgeber meldet Lagebericht und Nachkauf-Stufen. Das **Liveradar** (starke Tagesbewegungen, schnelle Sprünge, Nachkauf-Stufen) läuft auf deinem kostenlosen Server, auch wenn die App zu ist; Anleitung in docs/SERVER.md. Zusätzliche Werte: Einstellungen → „Kennungen für das Radar kopieren“." },
  { keys: ["live", "verzoegert", "echtzeit", "schlusskurs", "keine daten"], title: "Datenstatus", text: "**LIVE**: Kurs ist frisch (Tradegate, Binance, Kraken, Finnhub). **VERZÖGERT**: Börse offen, aber nur ein Kurs von Yahoo (etwa 15 Minuten alt), z. B. bei Indizes und Rohstoffen. **SCHLUSSKURS**: Börse geschlossen. Beim Öffnen der App fragt sie alle Tradegate-Werte selbst ab; nach etwa 15 Sekunden steht alles auf LIVE." },
  { keys: ["portfolio importieren", "csv", "import", "position hinzufuegen", "depot eintragen"], title: "Portfolio eintragen", text: "Im Reiter **Portfolio** kannst du Positionen einzeln anlegen oder eine CSV-Datei laden. Sie braucht eine Kopfzeile mit **ISIN** und **Stück** (oder Anzahl/Menge/Bestand), optional **Kaufkurs** (Einstand/Durchschnitt). Die Daten bleiben verschlüsselt auf dem Gerät." },
  { keys: ["screener", "filter", "suchen"], title: "Screener", text: "Der **Screener** (Märkte → Screener) filtert alle 1.300 Werte nach Kategorie, Veränderung in einem Zeitfenster, Umsatz und Signalen wie „Kurs über EMA 200“ oder „Golden Cross“. Filter lassen sich speichern; Ergebnisse als Tabelle oder Liste." },
  { keys: ["watchlist", "favorit", "stern"], title: "Watchlist", text: "Tippe im Chart auf den **Stern**, um einen Wert auf deine Watchlist zu setzen. Die Watchlist findest du unter Märkte → Filter „Watchlist“ (am PC in der Seitenleiste)." },
  { keys: ["simulator", "spielgeld", "paper trading"], title: "Simulator", text: "Der **Simulator** (Phase 7) wird Daytrading mit Spielgeld ermöglichen, strikt getrennt vom echten Depot. Echtes Trading gibt es nur nach deiner ausdrücklichen Freigabe (Phase 8)." },
  { keys: ["kostenlos", "kosten app", "bezahlen", "abo"], title: "Kosten des Projekts", text: "Das ganze Projekt ist **kostenlos**: GitHub (Sammeln, Webseite), Yahoo/Tradegate/Binance/Kraken (Kurse), Finnhub (kostenloser Schlüssel), GDELT (Nachrichten), ntfy (Push) und der Oracle-Server im „Always Free“-Rahmen. Dieser Assistent läuft ohne KI und ohne Konto direkt auf deinem Handy." },
];

// ---------- Hilfen ----------
const STOP = new Set(["der", "die", "das", "und", "oder", "ist", "wie", "was", "wer", "wo", "wann", "warum", "ein", "eine", "einen", "mein", "meine", "meinen",
  "mir", "mich", "ich", "du", "es", "zu", "im", "in", "am", "an", "auf", "fuer", "mit", "von", "bei", "steht", "stehen", "aktuell", "heute", "gerade", "jetzt",
  "kurs", "aktie", "etf", "fonds", "bitte", "kannst", "sag", "zeig", "gibt", "gut", "lohnt", "sich", "soll", "sollte", "kaufen", "verkaufen", "nicht",
  "noch", "mehr", "viel", "sind", "hat", "haben", "den", "dem", "des", "als", "auch", "so", "ja", "nein", "welche", "welcher", "welches", "top", "flop",
  "world", "core", "msci", "ishares", "vanguard", "xtrackers", "amundi", "spdr", "invesco", "ucits", "acc", "dist", "index", "usd", "eur", "plc", "inc", "ag", "se", "sa", "nv"]);
const BRAND = new Set(["ishares", "vanguard", "xtrackers", "amundi", "spdr", "invesco", "lyxor", "wisdomtree", "vaneck", "core", "ucits", "etf", "acc", "dist",
  "thesaurierend", "ausschuettend", "the", "inc", "ag", "se", "plc", "corp", "co", "ltd", "sa", "nv", "class", "a", "b", "eur", "usd", "hedged", "c"]);
const pct = (x, d = 1) => (x == null ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x * 100).toFixed(d).replace(".", ",")} %`);
const share = (x) => (x == null ? "–" : `${Math.round(x * 100)} %`);
const has = (q, ...keys) => keys.some((k) => q.includes(k));

/** Werte, die in der Frage genannt werden (Kürzel, Kennung, ISIN, Name). */
export function findInstruments(question, instruments, limit = 3) {
  const raw = String(question || "");
  const q = ` ${norm(raw).replace(/[^a-z0-9]+/g, " ")} `;
  const upper = new Set((raw.match(/\b[A-Z0-9][A-Z0-9.\-]{1,9}\b/g) || []).map((x) => x.toUpperCase()));
  const hits = [];
  for (const e of instruments) {
    let score = 0;
    const tick = String(e.ticker || "").toUpperCase().replace(/\.DE$|\.F$/, "");
    const id = String(e.id || "").toUpperCase();
    if (e.isin && raw.toUpperCase().includes(e.isin)) score = 100;
    else if (upper.has(id) || (tick && upper.has(tick))) score = 80;
    else {
      const n = norm(e.name).replace(/[^a-z0-9]+/g, " ").trim();
      const core = n.split(" ").filter((w) => w && !BRAND.has(w));
      const two = core.slice(0, 2).join(" ");
      if (n.length >= 4 && q.includes(` ${n} `)) score = 70;
      else if (core.length >= 2 && q.includes(` ${core.join(" ")} `)) score = 65;
      else if (core.length >= 2 && two.length >= 6 && q.includes(` ${two} `)) score = 55;
      else if (core[0] && core[0].length >= 4 && !STOP.has(core[0]) && q.includes(` ${core[0]} `)) score = 40 + Math.min(10, core[0].length);
      else if (id.length >= 3 && !STOP.has(id.toLowerCase()) && q.includes(` ${id.toLowerCase()} `)) score = 35;
    }
    if (score) hits.push({ e, score: score + (e.tier === "core" ? 5 : 0) });
  }
  hits.sort((a, b) => b.score - a.score);
  const out = [];
  const seen = new Set();
  for (const h of hits) {
    const key = h.e.isin || h.e.id;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(h.e);
    if (out.length >= limit) break;
  }
  return out;
}

/** Bestes Thema aus der Wissensbasis (oder null). */
export function searchKB(question) {
  const q = ` ${norm(question).replace(/[^a-z0-9&]+/g, " ")} `;
  let best = null;
  for (const item of KB) {
    let s = 0;
    for (const [i, k] of item.keys.entries()) {
      const nk = norm(k);
      if (q.includes(` ${nk} `) || (nk.length >= 6 && q.includes(nk))) s += i === 0 ? 3 : 2;
    }
    if (s && (!best || s > best.s)) best = { item, s };
  }
  return best ? best.item : null;
}

// ---------- Antworten aus den Daten der App ----------
function instrumentAnswer(e, ctx) {
  const p = ctx.price(e);
  const day = ctx.day(e);
  const v = ctx.adv && ctx.adv.values ? ctx.adv.values[e.id] : null;
  const lines = [`**${e.name}** (${e.ticker || e.id}, ${ctx.kindLabel(e.kind)})`];
  lines.push(`- Kurs: ${ctx.fmtPrice(e, p)} · ${ctx.status(e)}${day != null ? ` · heute ${pct(day, 2)}` : ""}`);
  const m = e.metrics || {};
  const perf = [["7d", "1 Woche"], ["30d", "1 Monat"]].filter(([k]) => m.perf && m.perf[k] != null).map(([k, l]) => `${l} ${pct(m.perf[k])}`);
  if (perf.length) lines.push(`- Entwicklung: ${perf.join(", ")}`);
  if (v) {
    lines.push(`- Ratgeber: **${Math.round(v.s)} Punkte** (${v.g}), Risiko ${v.r}/5, ${v.dd < 0.005 ? "am 12-Monats-Hoch" : `${pct(-v.dd)} unter dem 12-Monats-Hoch`}`);
    if (e.kind === "etf" && v.st) lines.push(`- Nachkauf-Stufe ${v.st} deiner Pyramide ist erreicht${v.n && v.n.n >= 2 ? " – aber erst die Negativ-Meldungen prüfen" : ""}.`);
    for (const w of (v.w || []).slice(0, 3)) lines.push(`- ${w}`);
    if (v.n && v.n.n >= 2) lines.push(`- ⚠ ${v.n.n} Negativ-Meldungen der letzten Tage (Details im Ratgeber).`);
  } else if (["index", "devisen", "rohstoff"].includes(e.kind)) {
    lines.push("- Indizes, Devisen und Rohstoff-Terminkontrakte bewertet der Ratgeber nicht (nicht direkt besparbar).");
  } else {
    lines.push("- Keine Ratgeber-Bewertung (zu wenig Kursverlauf).");
  }
  return lines.join("\n");
}

function compareAnswer(list, ctx) {
  const rows = list.map((e) => {
    const v = ctx.adv && ctx.adv.values ? ctx.adv.values[e.id] : null;
    const m = e.metrics || {};
    return `- **${e.name}**: heute ${pct(ctx.day(e), 2)}, 1 Monat ${pct(m.perf && m.perf["30d"])}${v ? `, ${Math.round(v.s)} Punkte (${v.g}), Risiko ${v.r}/5, ${pct(-v.dd)} vom Hoch` : ""}`;
  });
  const scored = list.map((e) => [e, ctx.adv && ctx.adv.values ? ctx.adv.values[e.id] : null]).filter(([, v]) => v);
  let tail = "";
  if (scored.length >= 2) {
    scored.sort((a, b) => a[1].r - b[1].r);
    tail = `\nRuhiger ist **${scored[0][0].name}** (Risiko ${scored[0][1].r}/5). Denk daran: Die Punktzahl hat im Rückblick keine Gewinner gefunden, sie zeigt vor allem das Risiko.`;
  }
  return `Vergleich:\n${rows.join("\n")}${tail}`;
}

function portfolioAnswer(ctx) {
  const pf = ctx.portfolio();
  if (!pf.rows.length) return "Dein Portfolio ist noch leer. Trage im Reiter **Portfolio** Positionen ein oder lade eine CSV-Datei, dann kann ich es auswerten.";
  const total = pf.total;
  const lines = [`Dein Portfolio: **${pf.rows.length} Positionen**, Wert ${ctx.fmtEur(total)}${pf.dayAbs != null ? `, heute ${pf.dayAbs >= 0 ? "+" : "−"}${ctx.fmtEur(Math.abs(pf.dayAbs))}` : ""}.`];
  const sorted = [...pf.rows].filter((r) => r.value).sort((a, b) => b.value - a.value);
  for (const r of sorted.slice(0, 6)) {
    const v = ctx.adv && ctx.adv.values && r.e ? ctx.adv.values[r.e.id] : null;
    lines.push(`- ${r.name}: ${share(r.value / total)} des Depots${r.cost ? `, ${pct(r.value / r.cost - 1)} seit Kauf` : ""}${v ? `, ${Math.round(v.s)} Punkte` : ""}`);
  }
  {
    const tips = improvements(pf, ctx);
    lines.push("", tips.length ? "**Was mir auffällt:**" : "Mir fällt nichts Kritisches auf: breit genug gestreut, keine großen Klumpen.");
    for (const t of tips) lines.push(`- ${t}`);
  }
  return lines.join("\n");
}

export function improvements(pf, ctx) {
  const tips = [];
  const total = pf.total || 0;
  if (!total) return tips;
  const rows = pf.rows.filter((r) => r.value);
  const part = (f) => rows.filter(f).reduce((a, r) => a + r.value, 0) / total;
  const big = [...rows].sort((a, b) => b.value - a.value)[0];
  if (big && big.e && big.e.kind !== "etf" && big.value / total > 0.25) tips.push(`**Klumpenrisiko:** ${big.name} macht ${share(big.value / total)} aus. Über 20–25 % in einer Einzelaktie kann ein einziger Absturz dein Ziel (max. 30 % Rückgang) sprengen.`);
  const etf = part((r) => r.e && r.e.kind === "etf");
  if (etf < 0.5) tips.push(`Nur ${share(etf)} stecken in ETFs. Für 5–10 Jahre mit max. 30 % Rückgang ist ein breiter ETF-Kern (70–80 %) die Basis, die im Rückblick-Test nicht zu schlagen war.`);
  const world = rows.some((r) => /world|all-world|acwi|all world/i.test(r.name));
  if (!world) tips.push("Es ist kein Welt-ETF im Depot. Ein MSCI World oder FTSE All-World als Kern streut über rund 1.300 bis 4.000 Firmen.");
  const crypto = part((r) => r.e && r.e.kind === "krypto");
  if (crypto > 0.1) tips.push(`Krypto macht ${share(crypto)} aus. Krypto fiel schon mehrfach um 70–80 %; mehr als 5–10 % passen kaum zu deiner Risikogrenze.`);
  const caution = part((r) => r.e && ctx.adv && ctx.adv.values && ctx.adv.values[r.e.id] && ctx.adv.values[r.e.id].g === "vorsicht");
  if (caution > 0.25) tips.push(`${share(caution)} des Depots stehen im Ratgeber auf „Vorsicht“. Das sagt keine Verluste voraus, aber höhere Schwankung (im Test: mehr als doppelt so oft Einbrüche über 30 %).`);
  const small = rows.filter((r) => r.value / total < 0.02).length;
  if (small >= 3) tips.push(`${small} Positionen sind kleiner als 2 % des Depots. Sie bewegen das Ergebnis kaum, kosten aber Überblick und ggf. Gebühren.`);
  const usd = part((r) => r.e && (r.e.unit === "$" || r.e.unit === "USD"));
  if (usd > 0.6) tips.push(`${share(usd)} notieren in US-Dollar. Neben dem Kurs schwankt dann auch der Wechselkurs.`);
  const losers = rows.filter((r) => r.cost && r.value / r.cost - 1 < -0.3 && r.e && r.e.kind !== "etf");
  if (losers.length) tips.push(`${losers.map((r) => r.name).join(", ")} ${losers.length > 1 ? "liegen" : "liegt"} über 30 % im Minus. Prüfe, ob sich an der Firma etwas Grundlegendes geändert hat (Ratgeber → Nachrichten), statt nur auf Erholung zu hoffen.`);
  return tips;
}

function moversAnswer(ctx, down) {
  const m = ctx.movers();
  const list = down ? m.flop : m.top;
  if (!list.length) return "Gerade liegen keine Tagesdaten vor (z. B. vor Handelsbeginn).";
  return `${down ? "Größte Verlierer" : "Größte Gewinner"} heute (Aktien, ETFs, Krypto mit genug Umsatz):\n${list.slice(0, 6).map((r) => `- ${r.e.name}: ${pct(r.pct, 2)}`).join("\n")}`;
}

function planAnswer(ctx) {
  const d = ctx.adv;
  if (!d || !d.plan || !d.plan.items) return "Der Sparplan-Vorschlag kommt mit dem nächsten Ratgeber-Lauf.";
  const items = d.plan.items.map((x) => `- ${ctx.name(x.id)}: **${x.eur} €** (${x.role})`).join("\n");
  const why = d.plan.mode === "einfach" ? "\nWarum nur ein ETF? Im Rückblick-Test hat die Punktzahl den schlichten Welt-ETF nicht geschlagen; einfacher war besser. Kern und Beimischungen stehen als Alternative im Ratgeber." : "";
  return `Vorschlag für deine 120 € im Monat:\n${items}${why}\nOb die ING den Wert als Sparplan anbietet und was er kostet, prüfst du bitte dort.`;
}

function stagesAnswer(ctx, e) {
  const d = ctx.adv;
  const kb = KB.find((k) => k.title === "Deine Nachkauf-Pyramide").text;
  if (!d || !d.values) return kb;
  const ids = e ? [e.id] : [...new Set([...(d.plan && d.plan.items || []), ...(d.plan && d.plan.alternative || [])].map((x) => x.id))];
  const lines = ids.map((id) => d.values[id] ? `- ${ctx.name(id)}: ${pct(-d.values[id].dd)} vom 12-Monats-Hoch → ${d.values[id].st ? `**Stufe ${d.values[id].st} erreicht**` : `noch keine Stufe (Stufe 1 bei −10 %)`}` : null).filter(Boolean);
  return `${lines.length ? `Stand jetzt:\n${lines.join("\n")}\n\n` : ""}${kb}`;
}

function worldAnswer(ctx) {
  const w = ctx.adv && ctx.adv.world;
  if (!w) return KB.find((k) => k.title === "Nachrichten in der App").text;
  const lines = [`Weltlage: **${w.level || "noch keine Einschätzung"}**.`];
  const a = w.archive;
  if (a && a.z) {
    const word = (z) => (z == null ? "–" : z >= 1.5 ? "deutlich schlechter als üblich" : z >= 0.5 ? "schlechter als üblich" : z <= -0.5 ? "besser als üblich" : "wie üblich");
    for (const [k, z] of Object.entries(a.z)) lines.push(`- ${a.names[k] || k}: ${word(z)}`);
  }
  const nt = ctx.adv.news_test;
  if (nt && nt.rows && nt.rows.length) {
    const r = nt.rows.find((x) => /MSCI/.test(x.label)) || nt.rows[0];
    lines.push(`\nIm Rückblick fiel der ${r.label} nach Monaten mit sehr schlechtem Tonfall in den 3 Monaten danach im Schnitt um ${pct(-r.dd3_bad)} (nach guten: ${pct(-r.dd3_good)}). Der Zusammenhang ist schwach; er fließt noch nicht in die Punktzahl ein.`);
  }
  return lines.join("\n");
}

function appIdeas(ctx) {
  const s = ctx.setup();
  const todo = [];
  if (!s.portfolio) todo.push("Portfolio eintragen (Reiter Portfolio, CSV-Import), damit Ratgeber und Assistent dein Depot einordnen können.");
  if (!s.finnhub) todo.push("Kostenlosen Finnhub-Schlüssel eintragen (Einstellungen): US-Aktien live und Unternehmensnachrichten.");
  todo.push("Server einrichten (docs/SERVER.md): Abruf rund um die Uhr und Liveradar mit Push, auch wenn die App zu ist.");
  const ideas = [
    "Pyramide mit Nachrichten-Filter testen: bei sehr schlechter Weltlage auf tiefere Stufen warten.",
    "Sparplan-Tagebuch: jeden ausgeführten Kauf eintragen und die Entwicklung gegen den reinen Welt-ETF zeigen.",
    "Ziel-Rechner: Wie viel wird aus 120 € im Monat in 5/10/20 Jahren (mit guten und schlechten Verläufen aus der Vergangenheit)?",
    "Phase 7: Daytrading-Simulator mit Spielgeld.",
  ];
  return `**Als Nächstes für dich:**\n${todo.map((t) => `- ${t}`).join("\n")}\n\n**Ideen für die App** (sag Bescheid, dann kommt es auf die Liste):\n${ideas.map((t) => `- ${t}`).join("\n")}`;
}

export const SUGGESTIONS = [
  "Wie steht mein Portfolio?",
  "Was würdest du an meinem Portfolio verbessern?",
  "Wie steht der MSCI World?",
  "Ist eine Nachkauf-Stufe erreicht?",
  "Was sagt die Weltlage?",
  "Was ist ein Golden Cross?",
  "Wer sind die Gewinner heute?",
  "Wie viel Steuern zahle ich auf Gewinne?",
];

/** Hauptfunktion: Frage → { text, actions } */
export function answer(question, ctx) {
  const q = norm(question);
  const found = findInstruments(question, ctx.instruments);
  const actions = found.map((e) => ({ label: `${e.name}: Chart öffnen`, id: e.id }));
  const done = (text, extra = []) => ({ text, actions: [...actions, ...extra] });

  if (/^(hallo|hi|hey|moin|servus|guten (morgen|tag|abend)|na)\b/.test(q) && q.length < 25)
    return done("Hallo! Ich bin dein Assistent in der App. Ich antworte ohne KI und ohne Internet-Dienst, nur aus den Daten der App und meinem eingebauten Wissen. Frag mich z. B. nach einem Wert, deinem Portfolio, der Weltlage oder einem Begriff.");
  if (has(q, "was kannst du", "hilfe", "wie funktionierst du", "wer bist du"))
    return done("Ich kann:\n- **Werte** nachschlagen: Kurs, Status, Entwicklung, Ratgeber-Punkte (z. B. „Wie steht SAP?“)\n- zwei Werte **vergleichen** („Vergleiche MSCI World und Nasdaq“)\n- dein **Portfolio** auswerten und **Verbesserungen** vorschlagen\n- **Sparplan**, **Nachkauf-Stufen**, **Weltlage**, **Gewinner/Verlierer** des Tages zeigen\n- **Begriffe** erklären (RSI, ETF, Steuern, Orderarten …)\n- Ideen für die **App** sammeln\nIch rechne alles auf deinem Handy; es kostet nichts.");

  const wantsCompare = has(q, "vergleich", " vs ", "versus", "gegenueber", "oder lieber", "besser als");
  if (found.length >= 2 && wantsCompare) return done(compareAnswer(found, ctx));
  if (has(q, "pyramide", "nachkauf", "stufe", "reserve", "nachkaufen")) return done(stagesAnswer(ctx, found.find((e) => e.kind === "etf")));
  if (has(q, "app") && has(q, "verbesser", "vorschlag", "idee", "fehlt", "wuensch", "feature", "funktion"))
    return done(appIdeas(ctx));
  if (has(q, "portfolio", "depot", "meine positionen", "mein geld")) return done(portfolioAnswer(ctx));
  if (has(q, "sparplan") && !has(q, "was ist", "was bedeutet", "erklaer")) return done(planAnswer(ctx));
  if (!found.length && has(q, "weltlage", "nachrichtenlage", "lage der welt", "nachrichten heute", "news heute")) return done(worldAnswer(ctx));
  if (!found.length && has(q, "gewinner", "steiger", "top heute", "beste heute", "am meisten gestiegen")) return done(moversAnswer(ctx, false));
  if (!found.length && has(q, "verlierer", "faller", "flop", "schlechteste heute", "am meisten gefallen", "eingebrochen")) return done(moversAnswer(ctx, true));

  const explain = has(q, "was ist", "was sind", "was bedeutet", "erklaer", "wie funktioniert", "was heisst", "definition", "unterschied");
  const kb = searchKB(question);
  if (kb && (explain || !found.length)) return done(kb.text);
  if (found.length) return done(found.slice(0, 2).map((e) => instrumentAnswer(e, ctx)).join("\n\n"));
  if (kb) return done(kb.text);
  return done("Dazu habe ich keine Antwort. Ich verstehe Fragen zu Werten der App (Name, Kürzel oder ISIN), zu deinem Portfolio, Sparplan, Nachkauf-Stufen, Weltlage, Gewinnern/Verlierern und erkläre Begriffe rund ums Anlegen. Probier eine der Vorschläge unten.");
}
