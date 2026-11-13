// Gemeinsame Market Data Engine der App.
// Jeder Kurs, egal aus welcher Quelle, landet hier als einheitlicher Datenpunkt:
// { id, kind, venue, price, time, source, live, updated }.
// Liste, Top/Flop, Screener und Chart lesen den Datenstatus nur von hier.

// ---------- Handelszeiten und Feiertage ----------
// Feiertage, an denen die Börse ganz geschlossen ist (Quelle: Börsenkalender von Xetra und NYSE).
// Verkürzte Handelstage sind nicht berücksichtigt.
const HOLIDAYS = {
  xetra: ["2026-01-01", "2026-04-03", "2026-04-06", "2026-05-01", "2026-12-24", "2026-12-25", "2026-12-31",
    "2027-01-01", "2027-03-26", "2027-03-29", "2027-12-24", "2027-12-31"],
  us: ["2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"],
};
HOLIDAYS.europe = HOLIDAYS.xetra;
HOLIDAYS.tradegate = HOLIDAYS.xetra;

// Markt -> [Zeitzone, Öffnung, Schluss in Minuten, nur werktags]
export const HOURS = {
  crypto: null, // rund um die Uhr
  xetra: ["Europe/Berlin", 9 * 60, 17 * 60 + 30, true],
  europe: ["Europe/Berlin", 9 * 60, 17 * 60 + 30, true],
  tradegate: ["Europe/Berlin", 8 * 60, 22 * 60, true],
  us: ["America/New_York", 9 * 60 + 30, 16 * 60, true],
  uk: ["Europe/London", 8 * 60, 16 * 60 + 30, true],
  japan: ["Asia/Tokyo", 9 * 60, 15 * 60 + 30, true],
  hongkong: ["Asia/Hong_Kong", 9 * 60 + 30, 16 * 60, true],
  fx: ["UTC", 0, 24 * 60, true],
  futures: ["America/New_York", 0, 24 * 60, true],
};

function localParts(date, tz) {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", weekday: "short", hourCycle: "h23" })
      .formatToParts(date).map((x) => [x.type, x.value]),
  );
  return { day: `${p.year}-${p.month}-${p.day}`, wd: p.weekday, min: Number(p.hour) * 60 + Number(p.minute) };
}

export function marketOpen(market, date = new Date()) {
  if (!(market in HOURS)) return false;
  const h = HOURS[market];
  if (!h) return true;
  const [tz, open, close, weekdays] = h;
  const p = localParts(date, tz);
  if (weekdays && (p.wd === "Sat" || p.wd === "Sun")) return false;
  if ((HOLIDAYS[market] || []).includes(p.day)) return false;
  return p.min >= open && p.min < close;
}

// ---------- Datenpunkte ----------
const points = new Map();
const listeners = new Set();

/** Neuer Live-Kurs aus einer Quelle (Tradegate, Binance, Finnhub). */
export function pushQuote(id, { price, time, source, venue, open = true }) {
  if (price == null || !(price > 0)) return;
  points.set(id, { id, price, time: time || Date.now() / 1000, source, venue, live: open, updated: Date.now() / 1000 });
  for (const fn of listeners) fn(id);
}
export function quoteOf(id) { return points.get(id) || null; }
export function onQuote(fn) { listeners.add(fn); return () => listeners.delete(fn); }

/** Wo kommt der Kurs gerade her? Für Markt-Öffnung und Beschriftung. */
export function venueMarket(e) {
  if (e.market === "crypto") return "crypto";
  if (e.last_source === "tradegate") return "tradegate";
  return e.market;
}

export const LIVE_MAX_AGE = 90; // Sekunden: älter gilt nicht mehr als live (Streams)
export const LIVE_MAX_AGE_POLL = 300; // abgefragte Börsenkurse (Tradegate) werden reihum erneuert

/**
 * Datenstatus eines Instruments:
 * live      – Kurs aus einem Live-Strom, höchstens 90 s alt, Börse offen
 * delayed   – Börse offen, aber nur verzögerte Daten (z. B. Yahoo 15 Min)
 * closed    – Börse geschlossen: letzter Schlusskurs
 * none      – kein Kurs vorhanden
 */
export function dataStatus(e, now = Date.now() / 1000) {
  const q = points.get(e.id);
  if (q && q.live && now - q.updated <= (q.source === "tradegate" ? LIVE_MAX_AGE_POLL : LIVE_MAX_AGE)) return { code: "live", time: q.time, price: q.price, source: q.source };
  const price = q ? q.price : e.last;
  const time = q ? q.time : e.last_ts;
  if (price == null) return { code: "none", time: null, price: null };
  const open = marketOpen(venueMarket(e), new Date(now * 1000));
  return { code: open ? "delayed" : "closed", time, price, source: q ? q.source : e.last_source };
}

export const STATUS_LABEL = { live: "LIVE", delayed: "VERZÖGERT", closed: "LETZTER SCHLUSSKURS", none: "KEINE DATEN" };

// ---------- Kategorien ----------
export const CATEGORIES = [
  { key: "aktie", label: "Aktien" },
  { key: "etf", label: "ETFs" },
  { key: "krypto", label: "Kryptowährungen" },
  { key: "index", label: "Indizes" },
  { key: "devisen", label: "Forex" },
  { key: "rohstoff", label: "Rohstoffe" },
];

// ---------- 24-Stunden-Werte mit Live-Kurs ----------
/** Veränderung über ein Zeitfenster; mit Live-Kurs aus dem gespeicherten Vergleichskurs neu gerechnet. */
export function perfOf(e, win, price = null) {
  const m = e.metrics || {};
  const p = m.perf && m.perf[win];
  if (p == null || e.last == null) return null;
  if (price == null || price === e.last) return { pct: p, abs: m.abs ? m.abs[win] : null, ref: e.last / (1 + p) };
  const ref = e.last / (1 + p);
  return { pct: price / ref - 1, abs: price - ref, ref };
}

/** Veränderung heute: gegenüber dem Schlusskurs vor Tagesbeginn (wie „heute“ in der Liste und 1D im Chart). */
export function dayPerf(e, price = null) {
  const p = price ?? e.last;
  if (p == null || !e.prev_close) return null;
  return { pct: p / e.prev_close - 1, abs: p - e.prev_close, ref: e.prev_close };
}

// ---------- Top/Flop ----------
const MIN_TURNOVER = { aktie: 500_000, etf: 250_000, krypto: 100_000 };
const MAX_MOVE = { krypto: 0.8 };

/**
 * Tagesgewinner und -verlierer (Veränderung heute in Prozent).
 * Ausgeschlossen werden: Werte ohne Vortagesschluss, illiquide Werte (wenn gewünscht)
 * und unplausible Sprünge (> 40 %, bei Krypto > 80 %), die meist Datenfehler sind.
 */
export function topFlop(entries, { cats, liquidOnly = true, limit = 10, priceOf = () => null } = {}) {
  const seen = new Set();
  const rows = [];
  const excluded = { nodata: 0, illiquid: 0, implausible: 0 };
  for (const e of entries) {
    if (cats && !cats.has(e.kind)) continue;
    const key = e.isin || e.id;
    if (seen.has(key)) continue;
    seen.add(key);
    const live = priceOf(e);
    const perf = dayPerf(e, live);
    if (!perf) { excluded.nodata += 1; continue; }
    const m = e.metrics || {};
    const min = MIN_TURNOVER[e.kind];
    if (liquidOnly && min && (m.avg_turnover_20d == null || m.avg_turnover_20d < min)) { excluded.illiquid += 1; continue; }
    if (Math.abs(perf.pct) > (MAX_MOVE[e.kind] || 0.4)) { excluded.implausible += 1; continue; }
    rows.push({ e, pct: perf.pct, abs: perf.abs, price: live ?? e.last });
  }
  const top = rows.filter((r) => r.pct > 0).sort((a, b) => b.pct - a.pct).slice(0, limit);
  const flop = rows.filter((r) => r.pct < 0).sort((a, b) => a.pct - b.pct).slice(0, limit);
  return { top, flop, excluded, considered: rows.length };
}

// ---------- Screener ----------
export const WINDOWS = ["5m", "15m", "1h", "4h", "24h", "7d", "30d"];
export const SIGNALS = {
  above_ema20: { label: "Kurs über EMA 20", need: (m) => m.ind && m.ind.ema20, test: (m, p) => p > m.ind.ema20 },
  above_ema50: { label: "Kurs über EMA 50", need: (m) => m.ind && m.ind.ema50, test: (m, p) => p > m.ind.ema50 },
  above_ema200: { label: "Kurs über EMA 200", need: (m) => m.ind && m.ind.ema200, test: (m, p) => p > m.ind.ema200 },
  golden_cross: { label: "Golden Cross (letzte 10 Tage)", need: (m) => m.ind && "cross" in m.ind, test: (m) => m.ind.cross === "golden" },
  death_cross: { label: "Death Cross (letzte 10 Tage)", need: (m) => m.ind && "cross" in m.ind, test: (m) => m.ind.cross === "death" },
  macd_bull: { label: "MACD bullish (über Signallinie)", need: (m) => m.ind && m.ind.macd != null, test: (m) => m.ind.macd > m.ind.macd_signal },
  macd_bear: { label: "MACD bearish (unter Signallinie)", need: (m) => m.ind && m.ind.macd != null, test: (m) => m.ind.macd < m.ind.macd_signal },
  boll_up: { label: "Ausbruch über oberes Bollinger-Band", need: (m) => m.ind && m.ind.boll_up != null, test: (m, p) => p > m.ind.boll_up },
  boll_down: { label: "Ausbruch unter unteres Bollinger-Band", need: (m) => m.ind && m.ind.boll_low != null, test: (m, p) => p < m.ind.boll_low },
  new_high: { label: "Neues 24-Stunden-Hoch", need: (m) => m.high_24h != null, test: (m, p) => p >= m.high_24h },
  new_low: { label: "Neues 24-Stunden-Tief", need: (m) => m.low_24h != null, test: (m, p) => p <= m.low_24h },
  vol_breakout: { label: "Volumen-Ausbruch (mind. doppelter Durchschnitt)", need: (m) => m.rel_volume != null, test: (m) => m.rel_volume >= 2 },
};

export const SORTS = {
  perf: { label: "Performance", value: (r) => r.perf },
  turnover: { label: "Handelsvolumen (Umsatz 24 Std.)", value: (r) => r.m.turnover_24h },
  rel_volume: { label: "Relatives Volumen", value: (r) => r.m.rel_volume },
  rsi: { label: "RSI", value: (r) => r.m.ind && r.m.ind.rsi14 },
  mcap: { label: "Marktkapitalisierung (nicht verfügbar)", value: () => null },
  price: { label: "Preis", value: (r) => r.price },
  volatility: { label: "Volatilität (30 Tage)", value: (r) => r.m.ind && r.m.ind.volatility },
};

export const EMPTY_FILTER = {
  window: "24h", perfMin: null, perfMax: null,
  turnoverMin: null, turnoverMax: null, relVolMin: null, volRiseMin: null,
  priceMin: null, priceMax: null, liquidityMin: null,
  rsiMin: null, rsiMax: null, signals: [],
  sort: "perf", dir: "desc",
};

const num = (x) => (x === "" || x == null || Number.isNaN(Number(x)) ? null : Number(x));

/**
 * Wendet einen Filter an. Fehlende Kennzahlen gelten nie als 0:
 * ist ein Filter auf eine Kennzahl gesetzt, die fehlt, fällt der Wert heraus
 * und wird unter „ohne Daten“ gezählt.
 */
export function screen(entries, f, { cats, priceOf = () => null } = {}) {
  const out = [];
  let missing = 0;
  for (const e of entries) {
    if (cats && !cats.has(e.kind)) continue;
    const m = e.metrics || {};
    const price = priceOf(e) ?? e.last;
    const perfObj = perfOf(e, f.window, priceOf(e));
    const perf = perfObj ? perfObj.pct : null;
    let ok = true;
    let lacks = false;
    const need = (val, cond) => {
      if (!ok) return;
      if (val == null) { ok = false; lacks = true; return; }
      if (!cond(val)) ok = false;
    };
    const pMin = num(f.perfMin), pMax = num(f.perfMax);
    if (pMin != null) need(perf, (v) => v * 100 >= pMin);
    if (pMax != null) need(perf, (v) => v * 100 <= pMax);
    if (num(f.turnoverMin) != null) need(m.turnover_24h, (v) => v >= num(f.turnoverMin));
    if (num(f.turnoverMax) != null) need(m.turnover_24h, (v) => v <= num(f.turnoverMax));
    if (num(f.relVolMin) != null) need(m.rel_volume, (v) => v >= num(f.relVolMin));
    if (num(f.volRiseMin) != null) need(m.rel_volume, (v) => (v - 1) * 100 >= num(f.volRiseMin));
    if (num(f.priceMin) != null) need(price, (v) => v >= num(f.priceMin));
    if (num(f.priceMax) != null) need(price, (v) => v <= num(f.priceMax));
    if (num(f.liquidityMin) != null) need(m.avg_turnover_20d, (v) => v >= num(f.liquidityMin));
    const rsi = m.ind ? m.ind.rsi14 : null;
    if (num(f.rsiMin) != null) need(rsi, (v) => v >= num(f.rsiMin));
    if (num(f.rsiMax) != null) need(rsi, (v) => v <= num(f.rsiMax));
    for (const key of f.signals || []) {
      const s = SIGNALS[key];
      if (!s || !ok) continue;
      if (!s.need(m) || price == null) { ok = false; lacks = true; continue; }
      if (!s.test(m, price)) ok = false;
    }
    if (ok) out.push({ e, m, price, perf, abs: perfObj ? perfObj.abs : null });
    else if (lacks) missing += 1;
  }
  const sort = SORTS[f.sort] || SORTS.perf;
  const dir = f.dir === "asc" ? 1 : -1;
  // Werte ohne Sortierkennzahl stehen immer am Ende
  out.sort((a, b) => {
    const va = sort.value(a), vb = sort.value(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return (va - vb) * dir;
  });
  return { rows: out, missing };
}
