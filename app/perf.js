// Prozentuale Kursveränderung je Zeitintervall.
//
// Regel (aus dem Auftrag): Veränderung = (aktueller Kurs / Referenzkurs - 1) × 100.
// Referenzkurs = Schlusskurs der letzten vollständig abgeschlossenen Kerze vor
// Beginn des Zeitraums.
//   1m … 12h  rollierend: Beginn = jetzt minus Intervall
//   1D/1W/1M  Kalender (deutsche Zeit): Tagesbeginn, Montag 0 Uhr, Monatserster 0 Uhr
//   6 Mon … 10 Jahre  rollierend in Kalendermonaten
// Bei Börsen, die gerade geschlossen sind, ist "jetzt" der Zeitpunkt des letzten Kurses.

export const TZ = "Europe/Berlin";

export const STEP = { "1s": 1, "5s": 5, "15s": 15, "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "12h": 43200, "1d": 86400, "1w": 604800, "1M": 2678400 };

// Jede Ansicht zeigt genau ihren Zeitraum (perf), in feineren Kerzen (candle):
// 1m = letzte Minute in Sekundenkerzen, 1D = heutiger Tag in 5-Minuten-Kerzen usw.
// „interval“ ist die frühere Kerzenlänge und wird nur noch als Ersatz genutzt.
export const VIEWS = [
  { key: "1m", label: "1m", candle: "1s", interval: "1m", window: 3 * 3600, perf: { rolling: 60 } },
  { key: "5m", label: "5m", candle: "5s", interval: "5m", window: 86400, perf: { rolling: 300 } },
  { key: "15m", label: "15m", candle: "15s", interval: "15m", window: 3 * 86400, perf: { rolling: 900 } },
  { key: "30m", label: "30m", candle: "1m", interval: "30m", window: 7 * 86400, perf: { rolling: 1800 } },
  { key: "1h", label: "1h", candle: "1m", interval: "1h", window: 14 * 86400, perf: { rolling: 3600 } },
  { key: "4h", label: "4h", candle: "5m", interval: "4h", window: 60 * 86400, perf: { rolling: 14400 } },
  { key: "12h", label: "12h", candle: "15m", interval: "12h", window: 180 * 86400, perf: { rolling: 43200 } },
  { key: "1D", label: "1D", candle: "5m", interval: "1d", window: 365 * 86400, perf: { calendar: "day" } },
  { key: "1W", label: "1W", candle: "1h", interval: "1w", window: 5 * 365 * 86400, perf: { calendar: "week" } },
  { key: "1M", label: "1M", candle: "4h", interval: "1M", window: 10 * 365 * 86400, perf: { calendar: "month" } },
  { key: "6M", label: "6 Mon", candle: "1d", interval: "1d", window: 183 * 86400, perf: { months: 6 }, range: true },
  { key: "1J", label: "1 Jahr", candle: "1d", interval: "1d", window: 366 * 86400, perf: { months: 12 }, range: true },
  { key: "3J", label: "3 Jahre", candle: "1w", interval: "1w", window: 3 * 366 * 86400, perf: { months: 36 }, range: true },
  { key: "5J", label: "5 Jahre", candle: "1w", interval: "1w", window: 5 * 366 * 86400, perf: { months: 60 }, range: true },
  { key: "10J", label: "10 Jahre", candle: "1M", interval: "1M", window: 10 * 366 * 86400, perf: { months: 120 }, range: true },
];

// ---------- deutsche Zeit ----------
const parts = new Intl.DateTimeFormat("en-US", {
  timeZone: TZ, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", weekday: "short",
});
const WD = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 7 };

export function berlinParts(sec) {
  const p = Object.fromEntries(parts.formatToParts(new Date(sec * 1000)).map((x) => [x.type, x.value]));
  return { y: +p.year, mo: +p.month, d: +p.day, h: +p.hour, mi: +p.minute, s: +p.second, wd: WD[p.weekday] };
}

function offsetAt(sec) {
  const p = berlinParts(sec);
  return Date.UTC(p.y, p.mo - 1, p.d, p.h, p.mi, p.s) / 1000 - sec;
}

/** Deutsche Ortszeit (Jahr, Monat 1-12, Tag, Std, Min) -> Unix-Sekunden. */
export function berlinToUtc(y, mo, d, h = 0, mi = 0) {
  const naive = Date.UTC(y, mo - 1, d, h, mi) / 1000; // Monats-/Tagesüberlauf rechnet Date selbst
  let utc = naive - offsetAt(naive);
  utc = naive - offsetAt(utc); // zweiter Schritt für Tage mit Zeitumstellung
  return utc;
}

/** Beginn des Zeitraums, auf den sich die Veränderung bezieht. */
export function periodStart(perf, asOf) {
  if (perf.rolling) return asOf - perf.rolling;
  const p = berlinParts(asOf);
  if (perf.calendar === "day") return berlinToUtc(p.y, p.mo, p.d);
  if (perf.calendar === "week") return berlinToUtc(p.y, p.mo, p.d - (p.wd - 1));
  if (perf.calendar === "month") return berlinToUtc(p.y, p.mo, 1);
  if (perf.months) return berlinToUtc(p.y, p.mo - perf.months, p.d, p.h, p.mi);
  throw new Error("unbekannter Zeitraum");
}

// ---------- Referenzkurs ----------
// Eine Quelle ist { rows: [[start, o, h, l, c, v], …] aufsteigend, step: Sekunden }
// oder { ticks: [[zeit, kurs], …] } für einzelne Live-Kurse.

function lastCompleted(rows, step, T) {
  // letzte Kerze, deren Ende (Start + Länge, höchstens Start der nächsten) <= T ist
  let lo = 0, hi = rows.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const next = mid + 1 < rows.length ? rows[mid + 1][0] : Infinity;
    const end = Math.min(rows[mid][0] + step, next);
    if (end <= T) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return found;
}

/**
 * Schlusskurs der letzten abgeschlossenen Kerze vor T aus der feinsten
 * passenden Quelle. maxStep: gröbste erlaubte Kerzenlänge (damit z. B. die
 * 1-Minuten-Veränderung nicht aus 5-Minuten-Kerzen geschätzt wird).
 */
export function referencePrice(sources, T, maxStep = Infinity) {
  for (const src of sources) {
    if (src.ticks) {
      const t = src.ticks;
      if (!t.length || t[0][0] > T) continue;
      let i = t.length - 1;
      while (i >= 0 && t[i][0] > T) i--;
      if (i >= 0) return { price: t[i][1], time: t[i][0], source: src.name };
      continue;
    }
    if (!src.rows || !src.rows.length || src.step > maxStep) continue;
    const i = lastCompleted(src.rows, src.step, T);
    if (i < 0) continue;
    // Die Quelle muss bis an T heranreichen, sonst ist sie veraltet
    const coversT = src.rows[src.rows.length - 1][0] + src.step >= T || src.closedUntil >= T;
    if (!coversT) continue;
    return { price: src.rows[i][4], time: src.rows[i][0] + src.step, source: src.name };
  }
  return null;
}

export function percentChange(price, ref) {
  if (price == null || ref == null || !ref) return null;
  return (price / ref - 1) * 100;
}

/** Alle Zeitintervalle auf einmal: { key: { pct, ref, start } }. */
export function computeAll(sources, price, asOf, views = VIEWS) {
  const out = {};
  for (const v of views) {
    const start = periodStart(v.perf, asOf);
    const maxStep = v.perf.rolling ? v.perf.rolling : Infinity;
    const ref = referencePrice(sources, start, maxStep);
    out[v.key] = { pct: ref ? percentChange(price, ref.price) : null, ref, start };
  }
  return out;
}

/** Anzeige: Vorzeichen, zwei Nachkommastellen, deutsches Komma. */
export function formatPct(pct) {
  if (pct == null || !Number.isFinite(pct)) return "–";
  const r = Math.round(pct * 100) / 100;
  const sign = r < 0 ? "-" : "+";
  return `${sign}${Math.abs(r).toFixed(2).replace(".", ",")} %`;
}

export function pctClass(pct) {
  if (pct == null || !Number.isFinite(pct)) return "flat";
  const r = Math.round(pct * 100) / 100;
  return r > 0 ? "up" : r < 0 ? "down" : "flat";
}
