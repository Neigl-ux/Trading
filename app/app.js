import { deriveKey, exportKey, importKey, openSealed } from "./crypto.js";
import { candleLabel, tickLabel } from "./format.js";
import { fetchQuote, tradegateOpen } from "./tradegate.js";

const KEY_STORE = "ta.key.v1";
const TZ = "Europe/Berlin";
const LIVE_INTERVALS = new Set(["1m", "5m", "15m", "30m", "1h"]);
const SECONDS = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600 };
const MARKET_TITLE = { crypto: "Krypto", xetra: "Deutsche Börse (Xetra)" };

const state = { key: null, raw: null, index: null, entry: null, data: null, viewKey: "1d", interval: "1d", rows: [], hovering: false, chart: null, candles: null, volume: null, ws: null };
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
  buildFilterStrip();
  renderList();
  renderStatus();
}

// ---------- Favoriten (nur auf diesem Gerät) ----------
const FAV_STORE = "ta.favs.v1";
const FILTER_STORE = "ta.filter.v1";
function loadFavs() {
  try { return JSON.parse(localStorage.getItem(FAV_STORE) || "[]"); } catch { return []; }
}
let favs = loadFavs();
function isFav(id) { return favs.includes(id); }
function toggleFav(id) {
  favs = isFav(id) ? favs.filter((x) => x !== id) : [...favs, id];
  try { localStorage.setItem(FAV_STORE, JSON.stringify(favs)); } catch {}
}

// ---------- Filter und Suche ----------
const KIND_LABEL = { aktie: "Aktie", etf: "ETF", index: "Index", krypto: "Krypto" };
const MARKET_LABEL = { xetra: "Xetra", europe: "Europa", us: "USA", crypto: "Krypto" };
const FILTERS = [
  { key: "fav", label: "Favoriten", test: (e) => isFav(e.id) },
  { key: "all", label: "Alle", test: () => true },
  { key: "de", label: "Deutschland", test: (e) => e.kind === "aktie" && e.market === "xetra" },
  { key: "us", label: "USA", test: (e) => e.kind === "aktie" && e.market === "us" },
  { key: "eu", label: "Europa", test: (e) => e.kind === "aktie" && e.market === "europe" },
  { key: "etf", label: "ETFs", test: (e) => e.kind === "etf" },
  { key: "index", label: "Indizes", test: (e) => e.kind === "index" },
  { key: "krypto", label: "Krypto", test: (e) => e.kind === "krypto" },
];
const GROUPS = [
  ["Indizes", (e) => e.kind === "index"],
  ["ETFs und ETCs", (e) => e.kind === "etf"],
  ["Deutsche Aktien", (e) => e.kind === "aktie" && e.market === "xetra"],
  ["Europäische Aktien", (e) => e.kind === "aktie" && e.market === "europe"],
  ["US-Aktien", (e) => e.kind === "aktie" && e.market === "us"],
  ["Krypto", (e) => e.kind === "krypto"],
];
let filterKey = (() => {
  try { return localStorage.getItem(FILTER_STORE) || (favs.length ? "fav" : "all"); } catch { return "all"; }
})();

const norm = (t) => t.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/ß/g, "ss").replace(/[^a-z0-9]/g, "");

function buildFilterStrip() {
  const strip = $("filter-strip");
  strip.replaceChildren();
  for (const f of FILTERS) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = f.label;
    b.dataset.key = f.key;
    b.setAttribute("aria-pressed", String(f.key === filterKey));
    b.addEventListener("click", () => {
      filterKey = f.key;
      try { localStorage.setItem(FILTER_STORE, f.key); } catch {}
      $("search").value = "";
      strip.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      renderList();
    });
    strip.append(b);
  }
}

function subLine(e) {
  const kind = e.kind === "aktie" ? `Aktie ${MARKET_LABEL[e.market] || ""}`.trim() : KIND_LABEL[e.kind] || "";
  const timing = e.kind === "krypto" ? "live im Chart" : e.delay_minutes ? `${e.delay_minutes} Min verzögert` : "nahezu Echtzeit";
  return `${kind}, ${timing}`;
}

function renderList() {
  const list = $("market-list");
  const hint = $("list-hint");
  list.replaceChildren();
  const all = state.index.instruments;
  const q = norm($("search").value.trim());
  let items;
  if (q) {
    items = all.filter((e) => norm(`${e.name} ${e.id}`).includes(q));
    hint.textContent = items.length ? `${items.length} Treffer in allen Bereichen` : `Kein Wert passt zu „${$("search").value.trim()}“. Versuche den Firmennamen oder das Börsenkürzel.`;
  } else {
    const filter = FILTERS.find((f) => f.key === filterKey) || FILTERS[1];
    items = all.filter(filter.test);
    if (filter.key === "fav") {
      items.sort((a, b) => favs.indexOf(a.id) - favs.indexOf(b.id));
      hint.textContent = items.length ? "" : "Noch keine Favoriten. Tippe unter „Alle“ auf den Stern neben einem Wert, um ihn hier zu sammeln.";
    } else {
      hint.textContent = "";
    }
  }
  const grouped = !q && filterKey === "all";
  if (grouped) {
    for (const [title, test] of GROUPS) {
      const group = items.filter(test);
      if (!group.length) continue;
      const h = document.createElement("h3");
      h.className = "group-title";
      h.textContent = title;
      list.append(h, ...group.map(rowFor));
    }
  } else {
    list.append(...items.map(rowFor));
  }
}

function rowFor(e) {
  const item = document.createElement("div");
  item.className = "item";
  const row = document.createElement("button");
  row.type = "button";
  row.className = "row";
  row.innerHTML = `<span class="name"></span><span class="px"></span><span class="sub"></span><span class="chg"></span>`;
  row.querySelector(".name").textContent = e.name;
  row.querySelector(".px").textContent = fmtPrice(e.last, unitOf(e));
  row.querySelector(".sub").textContent = subLine(e);
  const chg = row.querySelector(".chg");
  chg.textContent = e.change_1d == null ? "" : `${fmtPct(e.change_1d)} heute`;
  chg.classList.add(trendClass(e.change_1d));
  row.addEventListener("click", () => openChart(e));
  item.append(row, starButton(e.id, () => { if (filterKey === "fav" && !$("search").value) renderList(); }));
  return item;
}

function starButton(id, onChange) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "star";
  paintStar(b, id);
  b.addEventListener("click", () => { toggleFav(id); paintStar(b, id); onChange && onChange(); });
  return b;
}

function paintStar(b, id) {
  const on = isFav(id);
  b.textContent = on ? "★" : "☆";
  b.setAttribute("aria-pressed", String(on));
  b.setAttribute("aria-label", on ? "Aus Favoriten entfernen" : "Zu Favoriten hinzufügen");
}

$("search").addEventListener("input", () => renderList());

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
    if (b.dataset.tab === "markets") renderList();
  }),
);
$("back-btn").addEventListener("click", () => {
  stopLive();
  $("view-chart").hidden = true;
  $("view-markets").hidden = false;
  renderList();
});
$("q-star").addEventListener("click", () => {
  if (!state.entry) return;
  toggleFav(state.entry.id);
  paintStar($("q-star"), state.entry.id);
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
  $("rt-wrap").hidden = !rtAvailable(entry);
  $("rt-switch").checked = rtWanted();
  paintStar($("q-star"), entry.id);
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
    timeScale: { borderColor: c("--line"), rightOffset: 3, tickMarkFormatter: (t, type) => tickLabel(t, type) },
    crosshair: { mode: 0 },
    localization: {
      locale: "de-DE",
      priceFormatter: (p) => nf(Math.abs(p) >= 1 ? 2 : 6).format(p),
      timeFormatter: (t) => candleLabel(t, state.interval),
    },
  });
  state.candles = state.chart.addCandlestickSeries({
    upColor: c("--up"), downColor: c("--down"), borderVisible: false, wickUpColor: c("--up"), wickDownColor: c("--down"),
  });
  state.volume = state.chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", color: "rgba(141,162,191,.35)" });
  state.volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  state.chart.subscribeCrosshairMove((param) => {
    const bar = param.time !== undefined && param.seriesData ? param.seriesData.get(state.candles) : null;
    state.hovering = Boolean(bar);
    if (bar) renderLegend(param.time, bar.open, bar.high, bar.low, bar.close);
    else showLastInLegend();
  });
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
  state.interval = interval;
  state.rows = rows;
  showLastInLegend();
  updateQuote(rows, SECONDS[interval] !== undefined, source);
  $("chart-note").textContent = note;
  if (rtAvailable(entry) && rtWanted()) {
    startRealtime();
    if (SECONDS[interval]) $("chart-note").textContent = (note ? note + " " : "") + "Die Kerzen bis vor 15 Minuten kommen von Yahoo, die aktuelle Kerze baut sich aus den Tradegate-Kursen auf.";
  }
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

// Infozeile über dem Chart: Datum und Werte der berührten (oder neuesten) Kerze
function renderLegend(time, open, high, low, close) {
  const el = $("legend");
  const unit = unitOf(state.entry);
  const f = (x) => (Math.abs(x) >= 1 ? nf(2) : nf(6)).format(x);
  el.querySelector(".lg-date").textContent = candleLabel(time, state.interval);
  const vals = { open, high, low, close };
  for (const k of Object.keys(vals)) el.querySelector(`.lg-${k}`).textContent = f(vals[k]);
  el.querySelector(".lg-unit").textContent = unit === "€" ? "Werte in €" : `Werte in ${unit}`;
  el.querySelector(".lg-close").className = "lg-close " + trendClass(close - open);
  el.hidden = false;
}

function showLastInLegend() {
  const r = state.rows[state.rows.length - 1];
  if (!r) { $("legend").hidden = true; return; }
  renderLegend(shift(r[0]), r[1], r[2], r[3], r[4]);
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
      const rows = state.rows;
      if (rows.length && rows[rows.length - 1][0] === row[0]) rows[rows.length - 1] = row;
      else rows.push(row);
      if (!state.hovering) showLastInLegend();
      $("q-price").textContent = fmtPrice(row[4], unitOf(state.entry));
    };
    state.ws = ws;
  } catch {}
}

function stopLive() {
  if (state.ws) { try { state.ws.close(); } catch {} state.ws = null; }
  stopRealtime();
}

// ---------- Echtzeit von Tradegate (umschaltbar) ----------
const RT_STORE = "ta.realtime.v1";
const rt = { timer: null, abort: null, line: null, failures: 0 };
function rtWanted() { try { return localStorage.getItem(RT_STORE) === "1"; } catch { return false; } }
function rtAvailable(e) { return Boolean(e && e.isin && unitOf(e) === "€"); }

$("rt-switch").addEventListener("change", (ev) => {
  try { localStorage.setItem(RT_STORE, ev.target.checked ? "1" : "0"); } catch {}
  if (ev.target.checked) startRealtime();
  else { stopRealtime(); selectView(state.viewKey); }
});

function stopRealtime() {
  clearInterval(rt.timer);
  rt.timer = null;
  if (rt.abort) rt.abort.abort();
  rt.abort = null;
  if (rt.line && state.candles) { try { state.candles.removePriceLine(rt.line); } catch {} }
  rt.line = null;
  $("rt-info").textContent = "";
}

function startRealtime() {
  stopRealtime();
  if (!rtAvailable(state.entry) || !rtWanted() || $("view-chart").hidden) return;
  rt.failures = 0;
  pollRealtime();
  if (tradegateOpen()) rt.timer = setInterval(pollRealtime, 10_000); // Tradegate aktualisiert alle 10 Sekunden
}

async function pollRealtime() {
  const entry = state.entry;
  if (rt.abort) rt.abort.abort();
  rt.abort = new AbortController();
  try {
    const q = await fetchQuote(entry.isin, rt.abort.signal);
    if (entry !== state.entry) return;
    rt.failures = 0;
    applyQuote(q);
  } catch (err) {
    if (err.name === "AbortError") return;
    rt.failures += 1;
    $("rt-info").textContent = "Tradegate liefert gerade keinen Kurs für diesen Wert. Angezeigt werden die verzögerten Daten.";
    if (rt.failures >= 3) { clearInterval(rt.timer); rt.timer = null; }
  }
}

function applyQuote(q) {
  const unit = unitOf(state.entry);
  $("q-price").textContent = fmtPrice(q.last, unit);
  if (q.prevClose) {
    const change = q.last / q.prevClose - 1;
    const ch = $("q-change");
    ch.textContent = `${fmtPct(change)} heute`;
    ch.className = "change " + trendClass(change);
  }
  const time = new Intl.DateTimeFormat("de-DE", { timeStyle: "medium", timeZone: TZ }).format(new Date());
  const spread = q.bid != null && q.ask != null ? `, Geld ${fmtPrice(q.bid, unit)}, Brief ${fmtPrice(q.ask, unit)}` : "";
  const closed = tradegateOpen() ? "" : " (Börse geschlossen, letzter Kurs)";
  $("rt-info").textContent = `Echtzeit Tradegate, Stand ${time} Uhr${spread}${closed}`;
  if (!state.candles) return;
  if (rt.line) rt.line.applyOptions({ price: q.last });
  else rt.line = state.candles.createPriceLine({ price: q.last, color: "#f2b544", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Live" });
  updateLiveCandle(q.last);
}

// Bei Minuten- und Stundenansichten wächst die aktuelle Kerze mit jedem Abruf.
function updateLiveCandle(price) {
  const step = SECONDS[state.interval];
  if (!step || !tradegateOpen()) return;
  const bucket = Math.floor(Date.now() / 1000 / step) * step;
  const rows = state.rows;
  const lastRow = rows[rows.length - 1];
  let row;
  if (lastRow && lastRow[0] === bucket) {
    row = [bucket, lastRow[1], Math.max(lastRow[2], price), Math.min(lastRow[3], price), price, lastRow[5]];
    rows[rows.length - 1] = row;
  } else if (!lastRow || lastRow[0] < bucket) {
    row = [bucket, price, price, price, price, 0];
    rows.push(row);
  } else return;
  try { state.candles.update(toCandle(row)); } catch {}
  if (!state.hovering) showLastInLegend();
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopLive();
  else if (!$("view-chart").hidden && state.data) selectView(state.viewKey);
});

boot();
