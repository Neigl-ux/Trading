// Prüfung mit echten Marktdaten (läuft auf GitHub, siehe .github/workflows/live-check.yml).
// Holt echte Kerzen und den aktuellen Kurs, rechnet alle Zeitintervalle mit dem
// App-Code (app/perf.js) und kontrolliert jedes Ergebnis mit einer unabhängigen,
// einfachen Nachrechnung.
import { VIEWS, STEP, computeAll, periodStart, formatPct } from "../app/perf.js";

const KRAKEN = "https://api.kraken.com/0/public";
const pairs = [["BTC/EUR", "XBTEUR"], ["ETH/EUR", "ETHEUR"]];
const minutes = { "1m": 1, "1h": 60, "1d": 1440, "1w": 10080 };

async function json(url) {
  const r = await fetch(url, { headers: { "User-Agent": "tradingagent-check" } });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

// Unabhängige Nachrechnung: lineare Suche über alle Kerzen der feinsten Reihe, die T abdeckt
function naiveRef(sourcesByStep, T, maxStep) {
  for (const { rows, step } of sourcesByStep) {
    if (step > maxStep) continue;
    if (rows[rows.length - 1][0] + step < T) continue; // deckt T nicht ab
    let best = null;
    for (const r of rows) if (r[0] + step <= T) best = r;
    if (best) return best[4];
  }
  return null;
}

let failed = 0;
for (const [label, pair] of pairs) {
  const sources = [];
  for (const [iv, m] of Object.entries(minutes)) {
    const d = await json(`${KRAKEN}/OHLC?pair=${pair}&interval=${m}`);
    if (d.error && d.error.length) throw new Error(d.error.join(","));
    const key = Object.keys(d.result).find((k) => k !== "last");
    const rows = d.result[key].map((r) => [r[0], +r[1], +r[2], +r[3], +r[4], +r[6]]);
    rows.pop(); // letzte Kerze ist noch offen
    sources.push({ name: `kraken-${iv}`, rows, step: STEP[iv] });
  }
  const t = await json(`${KRAKEN}/Ticker?pair=${pair}`);
  const tk = Object.values(t.result)[0];
  const price = +tk.c[0];
  const asOf = Date.now() / 1000;
  const res = computeAll(sources, price, asOf);
  console.log(`\n${label}: aktueller Kurs ${price} EUR (Kraken), Stand ${new Date(asOf * 1000).toISOString()}`);
  console.log(`  1m-Kerzen: ${sources[0].rows.length}, letzte Startzeit ${new Date(sources[0].rows.at(-1)[0] * 1000).toISOString()}`);
  for (const v of VIEWS) {
    const r = res[v.key];
    const T = periodStart(v.perf, asOf);
    const maxStep = v.perf.rolling ? v.perf.rolling : Infinity;
    const naive = naiveRef(sources, T, maxStep);
    const naivePct = naive ? (price / naive - 1) * 100 : null;
    const same = (r.pct == null && naivePct == null) || (r.pct != null && naivePct != null && Math.abs(r.pct - naivePct) < 1e-9);
    if (!same) failed++;
    const ref = r.ref ? `${r.ref.price} (${r.ref.source}, Kerzenende ${new Date(r.ref.time * 1000).toISOString().slice(0, 16)}Z)` : "keine Daten";
    console.log(`  ${same ? "OK " : "FEHLER"} ${v.key.padEnd(4)} ${formatPct(r.pct).padStart(10)}  Referenz ${ref}  Beginn ${new Date(T * 1000).toISOString().slice(0, 16)}Z`);
  }
  // Plausibilität: Krakens eigene 24-Std-Eröffnung gegen unsere 12h/1D-Werte nur anzeigen
  console.log(`  Kraken 24h: Eröffnung heute (UTC) ${tk.o}, Hoch ${tk.h[1]}, Tief ${tk.l[1]}, Volumen ${tk.v[1]}`);
}

// Binance aus den USA (GitHub) meist gesperrt; nur Erreichbarkeit melden
for (const host of ["https://api.binance.com", "https://data-api.binance.vision"]) {
  try {
    const r = await fetch(`${host}/api/v3/klines?symbol=BTCEUR&interval=1m&limit=2`);
    console.log(`\nBinance ${host}: HTTP ${r.status}`);
  } catch (e) { console.log(`\nBinance ${host}: ${e.message}`); }
}

console.log(failed ? `\n${failed} Abweichungen` : "\nAlle Werte stimmen mit der unabhängigen Nachrechnung überein.");
process.exit(failed ? 1 : 0);
