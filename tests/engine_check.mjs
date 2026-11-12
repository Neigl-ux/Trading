// Tests für app/engine.js: Datenstatus, Handelszeiten, Top/Flop und Screener.
import assert from "node:assert/strict";
import { marketOpen, dataStatus, pushQuote, topFlop, screen, perfOf, EMPTY_FILTER } from "../app/engine.js";

// Handelszeiten (Do 12.11.2026)
assert.equal(marketOpen("xetra", new Date("2026-11-12T10:00:00Z")), true);   // 11:00 Berlin
assert.equal(marketOpen("xetra", new Date("2026-11-12T17:00:00Z")), false);  // 18:00 Berlin
assert.equal(marketOpen("tradegate", new Date("2026-11-12T20:30:00Z")), true); // 21:30 Berlin
assert.equal(marketOpen("us", new Date("2026-11-12T15:00:00Z")), true);      // 10:00 New York
assert.equal(marketOpen("us", new Date("2026-11-26T15:00:00Z")), false);     // Thanksgiving
assert.equal(marketOpen("xetra", new Date("2026-12-24T10:00:00Z")), false);  // Heiligabend
assert.equal(marketOpen("xetra", new Date("2026-11-14T10:00:00Z")), false);  // Samstag
assert.equal(marketOpen("crypto", new Date("2026-11-14T03:00:00Z")), true);

// Datenstatus
const sap = { id: "SAP", kind: "aktie", market: "xetra", last: 200, last_ts: 1, last_source: "tradegate" };
const t0 = Date.parse("2026-11-12T10:00:00Z") / 1000;
assert.equal(dataStatus(sap, t0).code, "delayed");
assert.equal(dataStatus(sap, Date.parse("2026-11-14T10:00:00Z") / 1000).code, "closed");
assert.equal(dataStatus({ id: "X", market: "xetra", last: null }, t0).code, "none");
pushQuote("SAP", { price: 201, source: "tradegate", open: true });
assert.equal(dataStatus(sap).code, "live");
pushQuote("SAP", { price: 201, source: "tradegate", open: false }); // Abfrage außerhalb der Handelszeit ist nie „live“
assert.notEqual(dataStatus(sap).code, "live");

// perfOf: Live-Kurs gegen gespeicherten 24-Std.-Vergleichskurs
const e = { id: "A", kind: "aktie", last: 110, metrics: { perf: { "24h": 0.1 }, abs: { "24h": 10 }, avg_turnover_20d: 1e7 } };
assert.ok(Math.abs(perfOf(e, "24h").pct - 0.1) < 1e-12);
assert.ok(Math.abs(perfOf(e, "24h", 121).pct - 0.21) < 1e-9);
assert.equal(perfOf(e, "5m"), null); // fehlt -> null, nicht 0

// Top/Flop: Sortierung, Duplikate, Liquidität, Plausibilität
const mk = (id, kind, pct, extra = {}) => ({ id, kind, isin: extra.isin, last: 100 * (1 + pct), prev_close: 100, metrics: { avg_turnover_20d: extra.turn ?? 1e7 } });
const list = [
  mk("A", "aktie", 0.05), mk("B", "aktie", 0.12), mk("C", "aktie", -0.03), mk("D", "aktie", -0.09),
  mk("E", "aktie", 0.5), // unplausibel
  mk("F", "aktie", 0.2, { turn: 1000 }), // illiquide
  mk("G", "krypto", 0.3, { turn: 2e6 }),
  mk("B2", "aktie", 0.12, { isin: "X1" }), mk("B3", "aktie", 0.11, { isin: "X1" }), // gleiche ISIN -> einmal
  { id: "N", kind: "aktie", last: 5, prev_close: null, metrics: {} },
];
const tf = topFlop(list, { cats: new Set(["aktie", "krypto"]) });
// Live-Kurs verschiebt die Tagesveränderung
assert.ok(Math.abs(topFlop([mk("L", "aktie", 0.01)], { priceOf: () => 110 }).top[0].pct - 0.1) < 1e-12);
assert.ok(Math.abs(topFlop([mk("L", "aktie", 0.01)], { priceOf: () => 110 }).top[0].abs - 10) < 1e-9);
assert.deepEqual(tf.top.map((r) => r.e.id), ["G", "B", "B2", "A"]);
assert.deepEqual(tf.flop.map((r) => r.e.id), ["D", "C"]);
assert.equal(tf.excluded.implausible, 1);
assert.equal(tf.excluded.illiquid, 1);
assert.equal(tf.excluded.nodata, 1);
assert.deepEqual(topFlop(list, { cats: new Set(["krypto"]) }).top.map((r) => r.e.id), ["G"]);
assert.ok(topFlop(list, { liquidOnly: false }).top.some((r) => r.e.id === "F"));

// Screener: Kombinationen, fehlende Werte nie als 0
const S = (id, kind, o) => ({ id, kind, last: o.last ?? 100, metrics: { perf: { "24h": o.p24 }, abs: {}, rel_volume: o.rv, turnover_24h: o.turn,
  ind: { rsi14: o.rsi, ema50: o.ema50, ema20: 90, ema200: o.ema200, cross: o.cross ?? null, macd: o.macd, macd_signal: 0 } } });
const uni = [
  S("ok", "aktie", { p24: 0.03, rv: 1.8, rsi: 60, ema50: 95, macd: 1 }),
  S("lowrsi", "aktie", { p24: 0.03, rv: 1.8, rsi: 40, ema50: 95, macd: 1 }),
  S("below", "aktie", { p24: 0.03, rv: 1.8, rsi: 60, ema50: 105, macd: 1 }),
  S("norv", "aktie", { p24: 0.03, rsi: 60, ema50: 95, macd: 1 }),
  S("coin", "krypto", { p24: 0.05, rv: 3, rsi: 65, ema50: 50, macd: 1 }),
  S("flat", "aktie", { p24: 0.0, rv: 1.8, rsi: 60, ema50: 95, macd: 1 }),
];
const f = { ...EMPTY_FILTER, perfMin: 2, rsiMin: 50, rsiMax: 70, relVolMin: 1.5, signals: ["above_ema50"] };
let r = screen(uni, f, { cats: new Set(["aktie"]) });
assert.deepEqual(r.rows.map((x) => x.e.id), ["ok"]);
assert.equal(r.missing, 1); // „norv“ hat kein relatives Volumen
r = screen(uni, f, { cats: new Set(["aktie", "krypto"]) });
assert.deepEqual(r.rows.map((x) => x.e.id), ["coin", "ok"]);
// RSI unter 30 findet nichts, und „flat“ (0 %) zählt bei perfMin 0 mit
assert.equal(screen(uni, { ...EMPTY_FILTER, rsiMax: 30 }).rows.length, 0);
assert.ok(screen(uni, { ...EMPTY_FILTER, perfMin: 0 }).rows.some((x) => x.e.id === "flat"));
// MACD bearish und Death Cross: keiner erfüllt
assert.equal(screen(uni, { ...EMPTY_FILTER, signals: ["macd_bear"] }).rows.length, 0);
assert.equal(screen(uni, { ...EMPTY_FILTER, signals: ["golden_cross"] }).rows.length, 0);
// Sortierung nach RSI aufsteigend
assert.deepEqual(screen(uni, { ...EMPTY_FILTER, sort: "rsi", dir: "asc" }).rows.map((x) => x.e.id)[0], "lowrsi");
// Ohne Filter: alle
assert.equal(screen(uni, EMPTY_FILTER).rows.length, uni.length);
console.log("engine ok");
