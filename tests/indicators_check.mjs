// Indikatoren gegen unabhängig nachgerechnete Werte prüfen.
import assert from "node:assert/strict";
import { sma, ema, bollinger, vwap, rsi, macd, atr, adx } from "../app/indicators.js";

const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps * Math.max(1, Math.abs(b)), `${a} != ${b}`);
let seed = 7;
const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
const rows = [];
let p = 100;
for (let i = 0; i < 300; i++) {
  const o = p; p = p * (1 + (rnd() - 0.5) * 0.04);
  const h = Math.max(o, p) * (1 + rnd() * 0.01), l = Math.min(o, p) * (1 - rnd() * 0.01);
  rows.push([1700000000 + i * 3600, o, h, l, p, 100 + rnd() * 50]);
}
const c = rows.map((r) => r[4]);

// SMA
const s20 = sma(rows, 20);
assert.equal(s20[18], null);
near(s20[50], c.slice(31, 51).reduce((a, b) => a + b) / 20);

// EMA: Start mit SMA, dann Rekursion
const e9 = ema(rows, 9);
let ref = c.slice(0, 9).reduce((a, b) => a + b) / 9;
near(e9[8], ref);
for (let i = 9; i <= 120; i++) ref = c[i] * 0.2 + ref * 0.8;
near(e9[120], ref);

// Bollinger: Mitte = SMA, Abstand = 2 Standardabweichungen (Grundgesamtheit)
const b = bollinger(rows);
const win = c.slice(81, 101);
const m = win.reduce((a, x) => a + x) / 20;
const sd = Math.sqrt(win.reduce((a, x) => a + (x - m) ** 2, 0) / 20);
near(b.mid[100], m); near(b.up[100], m + 2 * sd); near(b.low[100], m - 2 * sd);

// VWAP: neu je Tag
const day = (t) => Math.floor(t / 86400);
const vw = vwap(rows, day);
const d0 = rows.filter((r) => day(r[0]) === day(rows[30][0]) && r[0] <= rows[30][0]);
near(vw[30], d0.reduce((a, r) => a + (r[2] + r[3] + r[4]) / 3 * r[5], 0) / d0.reduce((a, r) => a + r[5], 0));

// RSI: Wilder mit Startdurchschnitt
const r = rsi(rows);
let g = 0, lo = 0;
for (let i = 1; i <= 14; i++) { const d = c[i] - c[i - 1]; g += Math.max(d, 0); lo += Math.max(-d, 0); }
g /= 14; lo /= 14;
for (let i = 15; i <= 200; i++) { const d = c[i] - c[i - 1]; g = (g * 13 + Math.max(d, 0)) / 14; lo = (lo * 13 + Math.max(-d, 0)) / 14; }
near(r[200], 100 - 100 / (1 + g / lo));
assert.ok(r.slice(14).every((x) => x >= 0 && x <= 100));

// MACD: Linie = EMA12 − EMA26, Histogramm = Linie − Signal
const md = macd(rows);
const e12 = ema(rows, 12), e26 = ema(rows, 26);
near(md.line[150], e12[150] - e26[150]);
near(md.hist[150], md.line[150] - md.signal[150]);
assert.equal(md.signal[25 + 7], null);
assert.notEqual(md.signal[25 + 8], null);

// ATR: Wilder
const a = atr(rows);
const tr = (i) => Math.max(rows[i][2] - rows[i][3], Math.abs(rows[i][2] - rows[i - 1][4]), Math.abs(rows[i][3] - rows[i - 1][4]));
let at = 0; for (let i = 1; i <= 14; i++) at += tr(i); at /= 14;
for (let i = 15; i <= 100; i++) at = (at * 13 + tr(i)) / 14;
near(a[100], at);

// ADX: Wertebereich und starker Trend
const ax = adx(rows);
assert.ok(ax.adx.filter((x) => x != null).every((x) => x >= 0 && x <= 100));
const trend = Array.from({ length: 80 }, (_, i) => [i, 100 + i, 101 + i, 99.5 + i, 100.8 + i, 10]);
const at2 = adx(trend);
assert.ok(at2.adx[79] > 50 && at2.pdi[79] > at2.mdi[79]);
console.log("indicators ok");
