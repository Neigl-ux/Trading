// Automatische Tests der Prozentberechnung (läuft mit Node, siehe test_js_perf.py)
import { VIEWS, STEP, periodStart, referencePrice, computeAll, formatPct, pctClass, berlinToUtc, berlinParts } from "../app/perf.js";

let failed = 0;
const eq = (name, a, b, tol = 1e-9) => {
  const ok = typeof a === "number" && typeof b === "number" ? Math.abs(a - b) <= tol : a === b;
  if (!ok) { failed++; console.log(`FEHLER ${name}: ${a} != ${b}`); }
};
const iso = (s) => Date.parse(s) / 1000;

// 1) Formel aus dem Auftrag: 90.000 -> 91.000 = +1,11 %
eq("Beispiel", formatPct((91000 / 90000 - 1) * 100), "+1,11 %");
eq("negativ", formatPct(-0.284), "-0,28 %");
eq("null", formatPct(0.001), "+0,00 %");
eq("Farbe neutral", pctClass(0.001), "flat");
eq("Farbe rot", pctClass(-0.3), "down");
eq("Farbe grün", pctClass(0.3), "up");
eq("leer", formatPct(null), "–");

// 2) Kalendergrenzen in deutscher Zeit (Winter UTC+1, Sommer UTC+2)
const asOf = iso("2026-11-12T19:30:00Z"); // Do 20:30 Berlin
eq("Tagesbeginn", periodStart({ calendar: "day" }, asOf), iso("2026-11-11T23:00:00Z"));
eq("Wochenbeginn Montag", periodStart({ calendar: "week" }, asOf), iso("2026-11-08T23:00:00Z"));
eq("Monatsbeginn", periodStart({ calendar: "month" }, asOf), iso("2026-10-31T23:00:00Z"));
eq("6 Monate zurück (Sommerzeit)", periodStart({ months: 6 }, asOf), iso("2026-05-12T18:30:00Z"));
eq("Montag selbst", periodStart({ calendar: "week" }, iso("2026-11-09T05:00:00Z")), iso("2026-11-08T23:00:00Z"));
eq("Sonntag", periodStart({ calendar: "week" }, iso("2026-11-15T20:00:00Z")), iso("2026-11-08T23:00:00Z"));
eq("Januar minus 1 Monat", periodStart({ months: 1 }, iso("2026-01-15T11:00:00Z")), iso("2025-12-15T11:00:00Z"));
eq("Sommer Tagesbeginn", periodStart({ calendar: "day" }, iso("2026-07-01T10:00:00Z")), iso("2026-06-30T22:00:00Z"));
eq("berlinToUtc Umstellung", berlinToUtc(2026, 3, 29, 12, 0), iso("2026-03-29T10:00:00Z"));
eq("Wochentag", berlinParts(asOf).wd, 4);

// 3) Referenzkurs = Schluss der letzten abgeschlossenen Kerze vor Beginn
const t0 = iso("2026-11-12T19:00:00Z");
const m1 = Array.from({ length: 31 }, (_, i) => [t0 + i * 60, 100 + i, 100 + i, 100 + i, 100 + i, 1]); // Schluss = 100+i
const src = [{ name: "1m", rows: m1, step: 60 }];
// jetzt = 19:30:20, Kurs 131. 1m-Beginn 19:29:20 -> letzte abgeschlossene Kerze endet 19:29 (Start 19:28, Schluss 128)
const now = t0 + 30 * 60 + 20;
let r = referencePrice(src, now - 60, 60);
eq("1m Referenz", r.price, 128);
r = referencePrice(src, now - 300, 300);
eq("5m Referenz", r.price, 124);
// 1m darf nicht aus 5-Minuten-Kerzen geschätzt werden
const m5 = [[t0, 1, 1, 1, 50, 1], [t0 + 300, 1, 1, 1, 60, 1]];
eq("1m ohne feine Daten", referencePrice([{ name: "5m", rows: m5, step: 300 }], t0 + 400, 60), null);
// Live-Ticks haben Vorrang, wenn sie vorne in der Liste stehen
r = referencePrice([{ name: "ticks", ticks: [[now - 70, 200], [now - 55, 201]] }, ...src], now - 60, 60);
eq("Ticks", r.price, 200);
// veraltete Quelle wird übersprungen: 1h-Kerzen enden vor T, 1d-Kerzen decken T
const h1 = [[t0 - 7200, 1, 1, 1, 10, 1]];
const d1 = [[iso("2026-11-10T23:00:00Z"), 1, 1, 1, 20, 1], [iso("2026-11-11T23:00:00Z"), 1, 1, 1, 21, 1]];
r = referencePrice([{ name: "1h", rows: h1, step: 3600 }, { name: "1d", rows: d1, step: 86400 }], iso("2026-11-11T23:00:00Z"));
eq("Fallback auf Tageskerzen", r.price, 20);

// 4) Alle Intervalle gleichzeitig, unabhängig vom gewählten Chart
const all = computeAll(src, 131, now, VIEWS.slice(0, 2));
eq("computeAll 1m", Math.round(all["1m"].pct * 1e6) / 1e6, Math.round((131 / 128 - 1) * 100 * 1e6) / 1e6);
eq("computeAll 5m", Math.round(all["5m"].pct * 1e6) / 1e6, Math.round((131 / 124 - 1) * 100 * 1e6) / 1e6);
eq("15 Zeitintervalle", VIEWS.length, 15);
eq("Stufe 1M", STEP["1M"] >= 28 * 86400, true);

console.log(failed ? `${failed} Fehler` : "alle Prüfungen bestanden");
process.exit(failed ? 1 : 0);
