// Echtzeitkurse von Tradegate (inoffizielle, öffentliche Kursadresse der Börse).
// Die App fragt sie direkt vom Handy ab, nur solange ein Chart offen ist.
// Fällt die Adresse weg, läuft die App mit den verzögerten Yahoo-Daten weiter.

const URL = "https://www.tradegatebsx.com/refresh.php";
const TZ = "Europe/Berlin";

/** Tradegate liefert Preise mal als Zahl (212.75), mal als Text ("1 371,80"). */
export function parseNumber(v) {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v !== "string") return null;
  let t = v.replace(/[\s  ]/g, "");
  if (!t || t === "-") return null;
  if (t.includes(",")) t = t.replace(/\./g, "").replace(",", ".");
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** Wandelt eine Tradegate-Antwort in ein einheitliches Objekt um (oder null). */
export function parseQuote(json) {
  if (!json || typeof json !== "object") return null;
  const q = {
    last: parseNumber(json.last),
    bid: parseNumber(json.bid),
    ask: parseNumber(json.ask),
    high: parseNumber(json.high),
    low: parseNumber(json.low),
    prevClose: parseNumber(json.close),
    volume: parseNumber(json.stueck),
  };
  if (q.last == null && q.bid == null) return null;
  if (q.last == null) q.last = q.ask != null ? (q.bid + q.ask) / 2 : q.bid;
  return q;
}

export async function fetchQuote(isin, signal) {
  const res = await fetch(`${URL}?isin=${encodeURIComponent(isin)}&t=${Date.now()}`, { signal, cache: "no-store" });
  if (!res.ok) throw new Error(`Tradegate HTTP ${res.status}`);
  const q = parseQuote(await res.json());
  if (!q) throw new Error("Tradegate liefert für diesen Wert keinen Kurs");
  return q;
}

/** Handelszeit Tradegate: werktags 7:30 bis 22:00 Uhr. */
export function tradegateOpen(date = new Date()) {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(date).map((x) => [x.type, x.value]),
  );
  if (p.weekday === "Sat" || p.weekday === "Sun") return false;
  const minutes = +p.hour * 60 + +p.minute;
  return minutes >= 7 * 60 + 30 && minutes < 22 * 60;
}
