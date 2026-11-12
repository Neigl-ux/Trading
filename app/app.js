import { deriveKey, exportKey, importKey, openSealed } from "./crypto.js";

const KEY_STORE = "ta.key.v1";
const TZ = "Europe/Berlin";
const LIVE_INTERVALS = new Set(["1m", "5m", "15m", "30m", "1h"]);
const SECONDS = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600 };
const MARKET_TITLE = { crypto: "Krypto", xetra: "Deutsche Börse (Xetra)" };

const state = { key: null, raw: null, index: null, entry: null, data: null, viewKey: "1d", chart: null, candles: null, volume: null, ws: null };
const files = new Map();
const $ = (id) => document.getElementById(id);

// ---------- Formatierung ----------
const nf = (max) => new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: max });
function fmtPrice(x, unit = "€") {
  if (x == null) return "–";
  return (Math.abs(x) >= 1 ? nf(2) : nf(6)).format(x) + " " + unit;
}
const unitOf = (e) => (e && e.unit) || "€";
function fmtPct(x) {
  if (x == null) return "";
  return (x > 0 ? "+" : "") + new Intl.NumberFormat("de-DE", { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(x);
}
function trendClass(x) { return x == null || Math.abs(x) < 1e-6 ? "flat" : x > 0 ? "up" : "down"; }
function fmtDate(sec) {
  return new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeZone: TZ }).format(new Date(sec * 1000));
}
function fmtTime(sec, withDate = false) {
  const opts = withDate ? { dateStyle: "short", timeStyle: "short", timeZone: TZ } : { timeStyle: "short", timeZone: TZ };
  return new Intl.DateTimeFormat("de-DE", opts).format(new Date(sec * 1000));
}

// Der Chart rechnet in UTC. Damit Achse und Kerzen deutsche Uhrzeit zeigen,
// werden die Zeitstempel um den Berliner Versatz verschoben.
const offsetCache = new Map();
const tzParts = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
function berlinOffset(sec) {
  const hourKey = Math.floor(sec / 3600);
  if (offsetCache.has(hourKey)) return offsetCache.get(hourKey);
  const p = Object.fromEntries(tzParts.formatToParts(new Date(sec * 1000)).map((x) => [x.type, x.value]));
  const local = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) / 1000;
  const off = local - sec;
  offsetCache.set(hourKey, off);
  return off;
}
const shift = (sec) => sec + berlinOffset(sec);

// ---------- Laden ----------
async function fetchJSON(path) {
  const res = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

async function boot() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  let sealed;
  try {
    sealed = await fetchJSON("data/index.json");
  } catch {
    showLock("Es sind noch keine Daten veröffentlicht. Starte auf GitHub den Ablauf „Daten sammeln“ und lade die Seite danach neu.");
    return;
  }
  state.raw = sealed;
  if (!sealed.enc) {
    state.index = sealed.data;
    return showMain();
  }
  try {
    const stored = JSON.parse(localStorage.getItem(KEY_STORE) || "null");
    if (stored && stored.salt === sealed.kdf.salt) {
      state.key = await importKey(stored.key);
      state.index = await openSealed(state.key, sealed);
      return showMain();
    }
  } catch {
    try { localStorage.removeItem(KEY_STORE); } catch {}
  }
  showLock();
}

function showLock(message = "") {
  $("lock").hidden = false;
  $("main").hidden = true;
  $("lock-error").textContent = message;
  $("pass").focus();
}

$("lock-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const btn = $("unlock-btn");
  if (!state.raw) return;
  btn.disabled = true;
  btn.textContent = "Entsperre …";
  $("lock-error").textContent = "";
  try {
    const key = await deriveKey($("pass").value, state.raw.kdf);
    state.index = await openSealed(key, state.raw);
    state.key = key;
    if ($("remember").checked) {
      try { localStorage.setItem(KEY_STORE, JSON.stringify({ salt: state.raw.kdf.salt, key: await exportKey(key) })); } catch {}
    }
    $("pass").value = "";
    showMain();
  } catch {
    $("lock-error").textContent = "Das Passwort passt nicht. Bitte prüfe Groß- und Kleinschreibung und versuche es erneut.";
  } finally {
    btn.disabled = false;
    btn.textContent = "Entsperren";
  }
});

async function loadInstrument(entry) {
  if (!files.has(entry.file)) files.set(entry.file, openSealed(state.key, await fetchJSON(`data/${entry.file}`)));
  return files.get(entry.file);
}

// ---------- Hauptansicht ----------
function showMain() {
  $("lock").hidden = true;
  $("main").hidden = false;
  const age = Date.now() / 1000 - state.index.updated;
  const fresh = $("freshness");
  fresh.textContent = `Daten von ${fmtTime(state.index.updated, age > 20 * 3600)} Uhr`;
  fresh.style.color = age > 45 * 60 ? "var(--mark)" : "";
  fresh.title = age > 45 * 60 ? "Der letzte Sammel-Lauf liegt länger zurück. Prüfe den Status." : "";
  renderList();
  renderStatus();
}

function renderList() {
  const list = $("market-list");
  list.replaceChildren();
  for (const market of ["xetra", "crypto"]) {
    const entries = state.index.instruments.filter((i) => i.market === market);
    if (!entries.length) continue;
    const h = document.createElement("h3");
    h.className = "group-title";
    h.textContent = MARKET_TITLE[market] || market;
    list.append(h);
    for (const e of entries) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "row";
      row.innerHTML = `<span class="name"></span><span class="px"></span><span class="sub"></span><span class="chg"></span>`;
      row.querySelector(".name").textContent = e.name;
      row.querySelector(".px").textContent = fmtPrice(e.last, unitOf(e));
      row.querySelector(".sub").textContent = e.delay_minutes ? `${e.delay_minutes} Min verzögert` : "Live in der Detailansicht";
      const chg = row.querySelector(".chg");
      chg.textContent = e.change_1d == null ? "" : `${fmtPct(e.change_1d)} heute`;
      chg.classList.add(trendClass(e.change_1d));
      row.addEventListener("click", () => openChart(e));
      list.append(row);
    }
  }
}

function renderStatus() {
  const s = state.index.status || {};
  const dl = $("status-list");
  dl.replaceChildren();
  const items = [
    ["Letzter Lauf", s.started ? fmtTime(Date.parse(s.started) / 1000, true) + " Uhr" : "–"],
    ["Abrufe", s.fetches ?? "–"],
    ["Fehlgeschlagen", s.failed ?? "–"],
    ["Neue Kerzen", s.new_bars == null ? "–" : new Intl.NumberFormat("de-DE").format(s.new_bars)],
  ];
  for (const [k, v] of items) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    dl.append(dt, dd);
  }
  const ul = $("status-warnings");
  ul.replaceChildren();
  const notes = [
    ...(s.errors || []).map((e) => ({ where: `${e.instrument}, ${e.interval}`, text: `Abruf fehlgeschlagen: ${e.errors.join("; ")}` })),
    ...(s.warnings || []).map((w) => ({ where: `${w.instrument}, ${w.interval}`, text: w.detail })),
  ];
  if (!notes.length) {
    const li = document.createElement("li"); li.textContent = "Keine Auffälligkeiten im letzten Lauf."; ul.append(li);
  }
  for (const n of notes) {
    const li = document.createElement("li");
    const w = document.createElement("span"); w.className = "where"; w.textContent = n.where + ": ";
    li.append(w, document.createTextNode(n.text));
    ul.append(li);
  }
}

// ---------- Tabs ----------
const TABS = { markets: "view-markets", advisor: "view-advisor", sim: "view-sim", status: "view-status" };
document.querySelectorAll(".tabbar button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".tabbar button").forEach((x) => x.classList.toggle("active", x === b));
    stopLive();
    $("view-chart").hidden = true;
    for (const [tab, id] of Object.entries(TABS)) $(id).hidden = tab !== b.dataset.tab;
  }),
);
$("back-btn").addEventListener("click", () => {
  stopLive();
  $("view-chart").hidden = true;
  $("view-markets").hidden = false;
});
$("logout").addEventListener("click", () => {
  try { localStorage.removeItem(KEY_STORE); } catch {}
  location.reload();
});

// ---------- Chart ----------
async function openChart(entry) {
  state.entry = entry;
  $("view-markets").hidden = true;
  $("view-chart").hidden = false;
  $("q-name").textContent = entry.name;
  $("q-price").textContent = fmtPrice(entry.last, unitOf(entry));
  $("q-change").textContent = "";
  $("q-meta").textContent = "Lade Kursverlauf …";
  buildStrip();
  try {
    state.data = await loadInstrument(entry);
  } catch {
    $("q-meta").textContent = "Die Kursdaten konnten nicht geladen werden. Prüfe die Internetverbindung.";
    return;
  }
  selectView(state.viewKey);
}

function buildStrip() {
  const strip = $("tf-strip");
  strip.replaceChildren();
  for (const v of state.index.views) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = v.label;
    b.dataset.key = v.key;
    b.setAttribute("aria-pressed", String(v.key === state.viewKey));
    b.addEventListener("click", () => selectView(v.key));
    strip.append(b);
  }
}

function ensureChart() {
  if (state.chart || !window.LightweightCharts) return state.chart;
  const css = getComputedStyle(document.documentElement);
  const c = (n) => css.getPropertyValue(n).trim();
  const el = $("chart");
  state.chart = LightweightCharts.createChart(el, {
    autoSize: true,
    layout: { background: { color: c("--night") }, textColor: c("--mute"), fontFamily: "Barlow, system-ui, sans-serif" },
    grid: { vertLines: { color: "rgba(36,67,106,.45)" }, horzLines: { color: "rgba(36,67,106,.45)" } },
    rightPriceScale: { borderColor: c("--line") },
    timeScale: { borderColor: c("--line"), rightOffset: 3 },
    crosshair: { mode: 0 },
    localization: { locale: "de-DE", priceFormatter: (p) => nf(Math.abs(p) >= 1 ? 2 : 6).format(p) },
  });
  state.candles = state.chart.addCandlestickSeries({
    upColor: c("--up"), downColor: c("--down"), borderVisible: false, wickUpColor: c("--up"), wickDownColor: c("--down"),
  });
  state.volume = state.chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", color: "rgba(141,162,191,.35)" });
  state.volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  return state.chart;
}

async function selectView(key) {
  state.viewKey = key;
  stopLive();
  document.querySelectorAll("#tf-strip button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.key === key)));
  const active = document.querySelector(`#tf-strip button[data-key="${key}"]`);
  active?.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });

  const view = state.index.views.find((v) => v.key === key);
  const entry = state.entry;
  let interval = view.interval;
  let note = "";
  let rows = null;
  let source = entry.delay_minutes ? `Yahoo Finance, ${entry.delay_minutes} Min verzögert` : "gesammelte Daten";

  const binance = entry.live && entry.live.binance;
  if (binance && LIVE_INTERVALS.has(interval)) {
    const limit = Math.min(1000, Math.ceil(view.lookback_s / SECONDS[interval]));
    try {
      rows = await fetchBinance(binance, interval, limit);
      source = "Binance, live";
    } catch {
      note = "Live-Daten von Binance nicht erreichbar, angezeigt werden die zuletzt gesammelten Kerzen.";
    }
  }
  if (!rows) {
    if (interval === "1m") {
      interval = "5m";
      note = entry.market === "xetra"
        ? "Für Xetra gibt es kostenlos keine 1-Minuten-Kerzen. Angezeigt werden 5-Minuten-Kerzen."
        : note || "Angezeigt werden 5-Minuten-Kerzen.";
    }
    const all = state.data.series[interval] || [];
    const end = all.length ? all[all.length - 1][0] : Date.now() / 1000;
    rows = all.filter((r) => r[0] >= end - view.lookback_s);
  }

  if (!rows.length) note = "Für diesen Zeitraum liegen noch keine Kerzen vor. Die Historie füllt sich mit jedem Lauf.";
  updateQuote(rows, SECONDS[interval] !== undefined, source);
  $("chart-note").textContent = note;
  if (!ensureChart()) {
    $("chart-note").textContent = "Die Chart-Bibliothek konnte nicht geladen werden. Prüfe die Internetverbindung und lade neu.";
    return;
  }
  const intraday = SECONDS[interval] !== undefined;
  state.chart.applyOptions({ timeScale: { timeVisible: intraday, secondsVisible: false } });
  state.candles.setData(rows.map(toCandle));
  state.volume.setData(rows.map(toVolume));
  state.chart.timeScale().fitContent();

  if (binance && source === "Binance, live") startLive(binance, interval);
}

const toCandle = (r) => ({ time: shift(r[0]), open: r[1], high: r[2], low: r[3], close: r[4] });
const toVolume = (r) => ({ time: shift(r[0]), value: r[5], color: r[4] >= r[1] ? "rgba(60,201,160,.35)" : "rgba(242,112,107,.35)" });

function updateQuote(rows, intraday, source) {
  if (!rows.length) { $("q-meta").textContent = `Quelle ${source}`; return; }
  const first = rows[0][1];
  const last = rows[rows.length - 1][4];
  const change = first ? last / first - 1 : null;
  const when = (sec) => (intraday ? fmtTime(sec, true) + " Uhr" : fmtDate(sec));
  $("q-price").textContent = fmtPrice(last, unitOf(state.entry));
  const ch = $("q-change");
  ch.textContent = change == null ? "" : `${fmtPct(change)} seit ${when(rows[0][0])}`;
  ch.className = "change " + trendClass(change);
  $("q-meta").textContent = `Letzte ${intraday ? "Kerze" : "abgeschlossene Kerze"} ${when(rows[rows.length - 1][0])}, Quelle ${source}`;
}

async function fetchBinance(symbol, interval, limit) {
  const url = `https://api.binance.com/api/v3/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Binance ${res.status}`);
  const data = await res.json();
  return data.map((k) => [Math.floor(k[0] / 1000), +k[1], +k[2], +k[3], +k[4], +k[5]]);
}

function startLive(symbol, interval) {
  try {
    const ws = new WebSocket(`wss://stream.binance.com:9443/ws/${symbol.toLowerCase()}@kline_${interval}`);
    ws.onmessage = (msg) => {
      const k = JSON.parse(msg.data).k;
      if (!k) return;
      const row = [Math.floor(k.t / 1000), +k.o, +k.h, +k.l, +k.c, +k.v];
      state.candles.update(toCandle(row));
      state.volume.update(toVolume(row));
      $("q-price").textContent = fmtPrice(row[4], unitOf(state.entry));
    };
    state.ws = ws;
  } catch {}
}

function stopLive() {
  if (state.ws) { try { state.ws.close(); } catch {} state.ws = null; }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopLive();
  else if (!$("view-chart").hidden && state.data) selectView(state.viewKey);
});

boot();
