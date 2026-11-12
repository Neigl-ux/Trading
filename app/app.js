import { deriveKey, exportKey, importKey, openSealed } from "./crypto.js";
import { candleLabel, tickLabel } from "./format.js";
import { fetchQuote, tradegateOpen } from "./tradegate.js";
import { VIEWS, STEP, TZ as PERF_TZ, computeAll, formatPct, pctClass, periodStart, berlinParts, berlinToUtc } from "./perf.js";
import { CryptoFeed, binanceKlines, mergeRows } from "./feeds.js";

const KEY_STORE = "ta.key.v1";
const TZ = "Europe/Berlin";
const MARKET_TITLE = { crypto: "Krypto", xetra: "Deutsche Börse (Xetra)" };

const state = { key: null, raw: null, index: null, entry: null, data: null, viewKey: "1d", interval: "1d", rows: [], hovering: false, chart: null, candles: null, line: null, volume: null, ws: null, timeIndex: new Map() };
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
  updateFreshness();
  buildFilterStrip();
  renderList();
  renderStatus();
  startAutoRefresh();
}

function updateFreshness() {
  const age = Date.now() / 1000 - state.index.updated;
  const fresh = $("freshness");
  fresh.textContent = `Daten von ${fmtTime(state.index.updated, age > 20 * 3600)} Uhr`;
  fresh.style.color = age > 45 * 60 ? "var(--mark)" : "";
  fresh.title = age > 45 * 60 ? "Der letzte Sammel-Lauf liegt länger zurück. Prüfe den Status." : "";
}

// Neue Daten holen, solange die App offen ist (der Sammler läuft alle 15 Minuten)
let refreshTimer = null;
function startAutoRefresh() {
  clearInterval(refreshTimer);
  refreshTimer = setInterval(() => { if (!document.hidden) refreshIndex(); }, 5 * 60 * 1000);
}

async function refreshIndex() {
  try {
    const sealed = await fetchJSON("data/index.json");
    if (sealed.enc && state.raw && sealed.kdf && sealed.kdf.salt !== state.raw.kdf.salt) return location.reload();
    const index = sealed.enc ? await openSealed(state.key, sealed) : sealed.data;
    state.raw = sealed;
    if (index.updated !== state.index.updated) {
      state.index = index;
      files.clear();
      renderList();
      renderStatus();
      if (!$("view-chart").hidden && state.entry) {
        const fresh = index.instruments.find((e) => e.id === state.entry.id);
        if (fresh) {
          await reloadExport(fresh);
        }
      }
    }
  } catch {
    // Netz weg: beim nächsten Mal erneut versuchen
  }
  updateFreshness();
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
const KIND_LABEL = { aktie: "Aktie", etf: "ETF", index: "Index", krypto: "Krypto", devisen: "Devisen" };
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
  ["Devisen", (e) => e.kind === "devisen"],
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
  const timing = e.kind === "krypto" ? "live im Chart" : e.delay_minutes ? `${e.delay_minutes} Min verzögert` : "Stand letzter Lauf";
  return e.converted_from ? `${kind}, in € umgerechnet` : `${kind}, ${timing}`;
}

const rowEls = new Map();
function renderList() {
  rowEls.clear();
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
  startListLive();
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
  rowEls.set(e.id, { entry: e, item, px: row.querySelector(".px"), chg, sub: row.querySelector(".sub") });
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
    stopListLive();
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

// ---------- Chartansicht ----------
// Alles läuft live weiter, ohne Tippen und ohne Neuladen:
//  Krypto:  Binance-WebSocket (Kerzen 1m/1h/1D/1W + gewähltes Intervall + 24-Std-Ticker),
//           Ersatz Kraken, automatische Wiederverbindung mit Lückenfüllung.
//  Aktien/ETFs: Tradegate alle 10 Sekunden (Schalter, standardmäßig an),
//           dazu die gesammelten Yahoo-Kerzen, die alle 5 Minuten nachgeladen werden.
const VIEW_STORE = "ta.view.v2";
const VIEW_BY_KEY = Object.fromEntries(VIEWS.map((v) => [v.key, v]));
let viewKey = (() => { try { const k = localStorage.getItem(VIEW_STORE); return VIEW_BY_KEY[k] ? k : "1D"; } catch { return "1D"; } })();
const PERF_ORDER = ["1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w", "1M"];

const asset = {
  entry: null, gen: 0, src: {}, ticks: [], price: null, asOf: null, stats: null,
  feed: null, rtTimer: null, rtAbort: null, status: "", source: "",
};
let perfQueued = false;

const isCrypto = (e) => Boolean(e && e.live && e.live.binance);
const krakenPair = (e) => (e.id || "").replace("-", "/");

function closeAsset() {
  asset.gen += 1;
  if (asset.feed) asset.feed.stop();
  asset.feed = null;
  stopTradegate();
}
function stopLive() { closeAsset(); }

async function openChart(entry) {
  stopListLive();
  closeAsset();
  const gen = asset.gen;
  state.entry = asset.entry = entry;
  Object.assign(asset, { src: {}, ticks: isCrypto(entry) ? [] : storedTicks(entry.id), price: null, asOf: null, stats: null, status: "", source: "" });
  $("view-markets").hidden = true;
  $("view-chart").hidden = false;
  $("q-name").textContent = entry.name;
  $("rt-wrap").hidden = !rtAvailable(entry) || isCrypto(entry);
  $("rt-switch").checked = rtWanted();
  $("rt-list-switch").checked = rtWanted();
  paintStar($("q-star"), entry.id);
  $("q-price").textContent = fmtPrice(entry.last, unitOf(entry));
  $("q-change").textContent = "";
  $("q-stats").replaceChildren();
  $("rt-info").textContent = "";
  $("q-meta").textContent = "Lade Kursverlauf …";
  buildStrip();
  try {
    state.data = await loadInstrument(entry);
  } catch {
    $("q-meta").textContent = "Die Kursdaten konnten nicht geladen werden. Prüfe die Internetverbindung.";
    return;
  }
  if (gen !== asset.gen) return;
  loadExportSources();
  if (isCrypto(entry)) {
    await loadCryptoHistory(gen);
    if (gen !== asset.gen) return;
    startCryptoFeed();
  } else {
    priceFromRows();
    if (rtAvailable(entry) && rtWanted()) startTradegate();
  }
  await selectView(viewKey);
  updateHeader();
}

/** Gesammelte Kerzen aus dem Export als Quellen übernehmen (Binance-Daten haben Vorrang). */
function loadExportSources() {
  for (const [interval, rows] of Object.entries(state.data.series || {})) {
    if (!STEP[interval] || (asset.src[interval] && asset.src[interval].live)) continue;
    asset.src[interval] = { name: `export-${interval}`, rows: rows.slice(), step: STEP[interval], live: false };
  }
}

async function reloadExport(fresh) {
  state.entry = asset.entry = fresh;
  state.data = await loadInstrument(fresh);
  loadExportSources();
  if (!isCrypto(fresh) && !rtActive()) priceFromRows();
  showRows();
  updateHeader();
  schedulePerf();
}

async function loadCryptoHistory(gen) {
  const sym = asset.entry.live.binance;
  const wanted = ["1m", "1h", "1d", "1w"];
  const results = await Promise.allSettled(wanted.map((i) => binanceKlines(sym, i, 1000)));
  if (gen !== asset.gen) return;
  results.forEach((r, k) => {
    if (r.status === "fulfilled" && r.value.length) {
      asset.src[wanted[k]] = { name: `binance-${wanted[k]}`, rows: r.value, step: STEP[wanted[k]], live: true };
    }
  });
  const m1 = asset.src["1m"];
  if (m1 && m1.live) {
    const last = m1.rows[m1.rows.length - 1];
    asset.price = last[4];
    asset.asOf = Date.now() / 1000;
  } else {
    priceFromRows();
  }
}

function startCryptoFeed() {
  const e = asset.entry;
  const gen = asset.gen;
  asset.feed = new CryptoFeed({
    binance: e.live.binance,
    kraken: krakenPair(e),
    intervals: feedIntervals(),
    onKline: (interval, row) => { if (gen === asset.gen) onKline(interval, row); },
    onTicker: (t) => { if (gen === asset.gen) onTicker(t); },
    onStatus: (text, source) => { if (gen === asset.gen) { asset.status = text; asset.source = source; updateMeta(); } },
    onResync: () => { if (gen === asset.gen) resyncCrypto(gen); },
  });
  asset.feed.start();
}

function feedIntervals() {
  const sel = VIEW_BY_KEY[viewKey].interval;
  return [...new Set(["1m", "1h", "1d", "1w", sel])];
}

/** Nach einem Verbindungsabbruch die Lücke per REST schließen. */
async function resyncCrypto(gen) {
  const sym = asset.entry.live.binance;
  for (const i of feedIntervals()) {
    try {
      const rows = await binanceKlines(sym, i, 200);
      if (gen !== asset.gen) return;
      const src = asset.src[i];
      if (src) src.rows = mergeRows(src.rows, rows);
      else asset.src[i] = { name: `binance-${i}`, rows, step: STEP[i], live: true };
    } catch {}
  }
  showRows();
  schedulePerf();
}

function onKline(interval, row) {
  let src = asset.src[interval];
  if (!src || !src.live) {
    if (!src) return;
  }
  const rows = src.rows;
  const last = rows[rows.length - 1];
  if (last && last[0] === row[0]) rows[rows.length - 1] = row;
  else if (!last || row[0] > last[0]) rows.push(row);
  else return; // ältere Kerze: ignorieren, keine Doppelten
  if (interval === state.interval) pushToChart(row);
  if (interval === "1m" && asset.price == null) { asset.price = row[4]; asset.asOf = Date.now() / 1000; }
  schedulePerf();
}

function onTicker(t) {
  asset.price = t.price;
  asset.asOf = t.time;
  asset.stats = { kind: "24h", high: t.high, low: t.low, volume: t.volume };
  addTick(t.time, t.price);
  updateHeader();
  schedulePerf();
}

function addTick(time, price) {
  recordTick(asset.entry.id, time, price);
  asset.ticks = tickStore.get(asset.entry.id) || [];
}

// Live-Kurse merken (auch aus der Liste), damit 1m bis 1h beim Öffnen schon berechenbar sind.
// Gespeichert werden die letzten 3 Stunden je Wert, nur auf diesem Gerät.
const TICK_STORE = "ta.ticks.v1";
const tickStore = (() => {
  try { return new Map(Object.entries(JSON.parse(localStorage.getItem(TICK_STORE) || "{}"))); } catch { return new Map(); }
})();
let tickSaveQueued = false;
function recordTick(id, time, price) {
  if (price == null) return;
  const ticks = tickStore.get(id) || [];
  if (ticks.length && ticks[ticks.length - 1][0] >= time) return;
  ticks.push([Math.round(time), price]);
  const cutoff = time - 3 * 3600;
  while (ticks.length && ticks[0][0] < cutoff) ticks.shift();
  tickStore.set(id, ticks);
  if (!tickSaveQueued) {
    tickSaveQueued = true;
    setTimeout(() => {
      tickSaveQueued = false;
      try { localStorage.setItem(TICK_STORE, JSON.stringify(Object.fromEntries(tickStore))); } catch {}
    }, 5000);
  }
}
function storedTicks(id) {
  const ticks = tickStore.get(id) || [];
  const cutoff = Date.now() / 1000 - 3 * 3600;
  return ticks.filter((t) => t[0] >= cutoff);
}

/** Ohne Live-Kurs: letzter Schlusskurs der feinsten Reihe, Zeitpunkt = Kerzenende. */
function priceFromRows() {
  for (const i of PERF_ORDER) {
    const s = asset.src[i];
    if (s && s.rows.length) {
      const last = s.rows[s.rows.length - 1];
      asset.price = last[4];
      asset.asOf = Math.min(last[0] + s.step, Date.now() / 1000);
      return;
    }
  }
}

function perfSources() {
  const list = [];
  if (asset.ticks.length) list.push({ name: "live", ticks: asset.ticks });
  for (const i of PERF_ORDER) {
    const s = asset.src[i];
    if (s && s.rows.length) list.push({ name: s.name, rows: s.rows, step: s.step, closedUntil: s.live ? asset.asOf : undefined });
  }
  return list;
}

function schedulePerf() {
  if (perfQueued) return;
  perfQueued = true;
  setTimeout(() => { perfQueued = false; updatePerf(); }, 300);
}

let lastPerf = {};
function updatePerf() {
  if (asset.price == null || asset.asOf == null) return;
  lastPerf = computeAll(perfSources(), asset.price, asset.asOf);
  for (const b of document.querySelectorAll("#tf-strip button")) {
    const p = lastPerf[b.dataset.key];
    const span = b.querySelector(".tf-pct");
    span.textContent = formatPct(p && p.pct);
    span.className = "tf-pct " + pctClass(p && p.pct);
    b.title = p && p.ref ? `Vergleich mit ${fmtPrice(p.ref.price, unitOf(asset.entry))} (${fmtTime(p.ref.time, true)} Uhr)` : "Für diesen Zeitraum liegen noch keine passenden Daten vor";
  }
  updateHeader();
}

function buildStrip() {
  const strip = $("tf-strip");
  strip.replaceChildren();
  for (const v of VIEWS) {
    if (v.key === "6M") {
      const sep = document.createElement("span");
      sep.className = "tf-sep";
      sep.setAttribute("aria-hidden", "true");
      strip.append(sep);
    }
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.key = v.key;
    b.innerHTML = `<span class="tf-label"></span><span class="tf-pct flat">–</span>`;
    b.querySelector(".tf-label").textContent = v.label;
    b.setAttribute("aria-pressed", String(v.key === viewKey));
    b.addEventListener("click", () => selectView(v.key));
    strip.append(b);
  }
}

async function selectView(key) {
  viewKey = key;
  try { localStorage.setItem(VIEW_STORE, key); } catch {}
  const gen = asset.gen;
  document.querySelectorAll("#tf-strip button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.key === key)));
  document.querySelector(`#tf-strip button[data-key="${key}"]`)?.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });

  const view = VIEW_BY_KEY[key];
  let interval = view.interval;
  let note = "";
  if (isCrypto(asset.entry)) {
    if (!asset.src[interval] || !asset.src[interval].live) {
      try {
        const rows = await binanceKlines(asset.entry.live.binance, interval, 1000);
        if (gen !== asset.gen) return;
        asset.src[interval] = { name: `binance-${interval}`, rows, step: STEP[interval], live: true };
      } catch {
        note = "Binance ist gerade nicht erreichbar. Angezeigt werden die zuletzt gesammelten Kerzen.";
      }
    }
    if (asset.feed) asset.feed.setIntervals(feedIntervals());
  }
  if (!asset.src[interval] || !asset.src[interval].rows.length) {
    if (interval === "1m") {
      interval = "5m";
      note = "Für Aktien und ETFs gibt es kostenlos keine 1-Minuten-Kerzen. Angezeigt werden 5-Minuten-Kerzen. Die 1m-Veränderung oben entsteht aus den Live-Kursen, sobald eine Minute Echtzeit vorliegt.";
    }
  }
  state.interval = interval;
  $("chart-note").textContent = note;
  showRows(view);
  schedulePerf();
}

/** Kerzen des gewählten Intervalls in den Chart (vollständig nur beim Wechsel). */
function showRows(view = VIEW_BY_KEY[viewKey]) {
  const src = asset.src[state.interval];
  const rows = src ? src.rows : [];
  state.rows = rows;
  if (!rows.length) $("chart-note").textContent = "Für dieses Intervall liegen noch keine Kerzen vor. Die Historie füllt sich mit jedem Lauf.";
  showLastInLegend();
  updateMeta();
  if (!ensureChart()) {
    $("chart-note").textContent = "Die Chart-Bibliothek konnte nicht geladen werden. Prüfe die Internetverbindung und lade neu.";
    return;
  }
  const intraday = STEP[state.interval] < 86400;
  state.chart.applyOptions({ timeScale: { timeVisible: intraday, secondsVisible: false } });
  state.timeIndex = new Map(rows.map((r, i) => [shift(r[0]), i]));
  state.candles.setData(rows.map(toCandle));
  state.line.setData(rows.map(toLine));
  state.volume.setData(rows.map(toVolume));
  if (rows.length) {
    const last = rows[rows.length - 1][0];
    const first = Math.max(rows[0][0], last - view.window);
    try { state.chart.timeScale().setVisibleRange({ from: shift(first), to: shift(last) }); }
    catch { state.chart.timeScale().fitContent(); }
  }
}

/** Laufende Kerze im Chart aktualisieren, ohne neu zu laden. */
function pushToChart(row) {
  if (!state.candles) return;
  const rows = state.rows;
  if (!state.timeIndex.has(shift(row[0]))) state.timeIndex.set(shift(row[0]), rows.length - 1);
  try {
    state.candles.update(toCandle(row));
    state.line.update(toLine(row));
    state.volume.update(toVolume(row));
  } catch {}
  if (!state.hovering) showLastInLegend();
}

const toCandle = (r) => ({ time: shift(r[0]), open: r[1], high: r[2], low: r[3], close: r[4] });
const toLine = (r) => ({ time: shift(r[0]), value: r[4] });
const toVolume = (r) => ({ time: shift(r[0]), value: r[5], color: r[4] >= r[1] ? "rgba(60,201,160,.35)" : "rgba(242,112,107,.35)" });

// ---------- Kopfzeile: Kurs, Veränderung heute, Hoch/Tief/Volumen ----------
function updateHeader() {
  const e = asset.entry;
  if (!e || asset.price == null) return;
  const unit = unitOf(e);
  $("q-price").textContent = fmtPrice(asset.price, unit);
  const day = lastPerf["1D"];
  const ch = $("q-change");
  if (day && day.ref) {
    const abs = asset.price - day.ref.price;
    ch.textContent = `${abs >= 0 ? "+" : "-"}${fmtPrice(Math.abs(abs), unit)} (${formatPct(day.pct)}) heute`;
    ch.className = "change " + pctClass(day.pct);
  } else {
    ch.textContent = "";
  }
  const stats = asset.stats || todayStats();
  const box = $("q-stats");
  box.replaceChildren();
  if (!stats) return;
  const label = stats.kind === "24h" ? ["24 Std Hoch", "24 Std Tief", "24 Std Volumen"] : ["Tageshoch", "Tagestief", "Volumen heute"];
  const vals = [fmtPrice(stats.high, unit), fmtPrice(stats.low, unit), fmtVolume(stats.volume, stats.volumeUnit)];
  label.forEach((l, i) => {
    const d = document.createElement("div");
    const dt = document.createElement("dt"); dt.textContent = l;
    const dd = document.createElement("dd"); dd.textContent = stats[["high", "low", "volume"][i]] == null ? "–" : vals[i];
    d.append(dt, dd);
    box.append(d);
  });
}

/** Tageswerte aus den gesammelten Kerzen (ohne Live-Daten). */
function todayStats() {
  const src = asset.src["5m"] || asset.src["15m"];
  if (!src || !src.rows.length || asset.asOf == null) return null;
  const start = periodStart({ calendar: "day" }, asset.asOf);
  const today = src.rows.filter((r) => r[0] >= start);
  if (!today.length) return null;
  return {
    kind: "day",
    high: Math.max(...today.map((r) => r[2])),
    low: Math.min(...today.map((r) => r[3])),
    volume: today.reduce((a, r) => a + r[5], 0),
  };
}

function fmtVolume(v, unit = "") {
  if (v == null) return "–";
  const n = new Intl.NumberFormat("de-DE", { notation: "compact", maximumFractionDigits: 2 }).format(v);
  return unit ? `${n} ${unit}` : n;
}

function updateMeta() {
  const e = asset.entry;
  if (!e) return;
  let text;
  if (isCrypto(e)) {
    const src = asset.source === "kraken" ? "Kraken (Ersatz)" : "Binance";
    text = asset.status === "live" ? `Live über ${src}` : asset.status ? `${src}: ${asset.status}` : `Verbinde mit ${src} …`;
  } else if (rtActive()) {
    text = "Live über Tradegate, Kerzenhistorie von Yahoo";
  } else {
    text = e.delay_minutes ? `Yahoo Finance, ${e.delay_minutes} Min verzögert` : "Yahoo Finance, Stand letzter Lauf";
  }
  if (e.converted_from) text += ", von US-Dollar in Euro umgerechnet";
  $("q-meta").textContent = text;
}

// ---------- Echtzeit für Aktien und ETFs (Tradegate) ----------
const RT_STORE = "ta.realtime.v1";
const rt = { line: null, failures: 0 };
// Standard: an. Nur wer ausdrücklich ausschaltet, bekommt verzögerte Kurse.
function rtWanted() { try { return localStorage.getItem(RT_STORE) !== "0"; } catch { return true; } }
function rtAvailable(e) { return Boolean(e && e.isin && unitOf(e) === "€"); }
function rtActive() { return Boolean(asset.rtTimer || asset.rtAbort) && rtAvailable(asset.entry); }

function setRealtime(on) {
  try { localStorage.setItem(RT_STORE, on ? "1" : "0"); } catch {}
  $("rt-switch").checked = on;
  $("rt-list-switch").checked = on;
}
$("rt-switch").addEventListener("change", (ev) => {
  setRealtime(ev.target.checked);
  if (ev.target.checked) startTradegate();
  else { stopTradegate(); asset.ticks = []; asset.stats = null; priceFromRows(); updateMeta(); updateHeader(); schedulePerf(); }
});
$("rt-list-switch").addEventListener("change", (ev) => {
  setRealtime(ev.target.checked);
  if (ev.target.checked) startListLive();
  else renderList();
});
$("rt-list-switch").checked = rtWanted();

function stopTradegate() {
  clearInterval(asset.rtTimer);
  asset.rtTimer = null;
  if (asset.rtAbort) asset.rtAbort.abort();
  asset.rtAbort = null;
  if (rt.line && state.candles) { try { state.candles.removePriceLine(rt.line.c); state.line.removePriceLine(rt.line.l); } catch {} }
  rt.line = null;
  $("rt-info").textContent = "";
}

function startTradegate() {
  stopTradegate();
  if (!rtAvailable(asset.entry) || isCrypto(asset.entry) || $("view-chart").hidden) return;
  rt.failures = 0;
  pollTradegate();
  // Tradegate aktualisiert alle 10 Sekunden; außerhalb der Handelszeit genügt eine Abfrage pro Minute
  asset.rtTimer = setInterval(pollTradegate, tradegateOpen() ? 10_000 : 60_000);
  updateMeta();
}

async function pollTradegate() {
  const entry = asset.entry;
  if (asset.rtAbort) asset.rtAbort.abort();
  const ctrl = new AbortController();
  asset.rtAbort = ctrl;
  try {
    const q = await fetchQuote(entry.isin, ctrl.signal);
    if (entry !== asset.entry) return;
    rt.failures = 0;
    applyQuote(q);
  } catch (err) {
    if (err.name === "AbortError") return;
    rt.failures += 1;
    $("rt-info").textContent = "Tradegate liefert gerade keinen Kurs für diesen Wert. Angezeigt werden die verzögerten Daten.";
    if (rt.failures >= 3) { clearInterval(asset.rtTimer); asset.rtTimer = null; updateMeta(); }
  } finally {
    if (asset.rtAbort === ctrl) asset.rtAbort = null;
  }
}

function lastTradegateClose(now) {
  // Ende der letzten Tradegate-Handelszeit (werktags 22:00 Uhr)
  let d = now;
  for (let k = 0; k < 8; k++, d -= 86400) {
    const p = berlinParts(d);
    if (p.wd <= 5) {
      const close = berlinToUtc(p.y, p.mo, p.d, 22, 0);
      if (close <= now) return close;
    }
  }
  return now;
}

function applyQuote(q) {
  const unit = unitOf(asset.entry);
  const now = Date.now() / 1000;
  const open = tradegateOpen();
  asset.price = q.last;
  asset.asOf = open ? now : lastTradegateClose(now);
  asset.stats = { kind: "day", high: q.high, low: q.low, volume: q.volume, volumeUnit: "Stück" };
  if (open) addTick(now, q.last);
  const time = new Intl.DateTimeFormat("de-DE", { timeStyle: "medium", timeZone: TZ }).format(new Date());
  const spread = q.bid != null && q.ask != null ? `Geld ${fmtPrice(q.bid, unit)}, Brief ${fmtPrice(q.ask, unit)}` : "";
  $("rt-info").textContent = `Tradegate, Stand ${time} Uhr${spread ? ", " + spread : ""}${open ? "" : " (Börse geschlossen, letzter Kurs)"}`;
  updateMeta();
  updateHeader();
  schedulePerf();
  if (!state.candles) return;
  const opts = { price: q.last, color: "#f2b544", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Live" };
  if (rt.line) { rt.line.c.applyOptions({ price: q.last }); rt.line.l.applyOptions({ price: q.last }); }
  else rt.line = { c: state.candles.createPriceLine(opts), l: state.line.createPriceLine(opts) };
  if (open) updateLiveCandle(q.last, now);
}

// Die laufende Kerze des gewählten Intervalls wächst mit jedem Live-Kurs.
function updateLiveCandle(price, now) {
  const step = STEP[state.interval];
  const rows = state.rows;
  if (!step || !rows) return;
  const last = rows[rows.length - 1];
  let row;
  if (last && now < last[0] + step) {
    row = [last[0], last[1], Math.max(last[2], price), Math.min(last[3], price), price, last[5]];
    rows[rows.length - 1] = row;
  } else if (step <= 3600) {
    const bucket = Math.floor(now / step) * step;
    if (last && bucket <= last[0]) return;
    row = [bucket, price, price, price, price, 0];
    rows.push(row);
  } else return;
  pushToChart(row);
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
    crosshair: { mode: 1 },
    // Wischen mit einem Finger zeigt Werte (eigene Steuerung unten), zwei Finger zoomen
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: false, vertTouchDrag: false },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
    localization: {
      locale: "de-DE",
      priceFormatter: (p) => nf(Math.abs(p) >= 1 ? 2 : 6).format(p),
      timeFormatter: (t) => candleLabel(t, state.interval),
    },
  });
  state.candles = state.chart.addCandlestickSeries({
    upColor: c("--up"), downColor: c("--down"), borderVisible: false, wickUpColor: c("--up"), wickDownColor: c("--down"),
  });
  state.line = state.chart.addAreaSeries({
    lineColor: "#cfe0f5", lineWidth: 2, topColor: "rgba(207,224,245,.16)", bottomColor: "rgba(207,224,245,0)",
    crosshairMarkerBackgroundColor: c("--mark"), crosshairMarkerBorderColor: c("--night"), crosshairMarkerRadius: 5,
  });
  applyChartType();
  state.volume = state.chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", color: "rgba(141,162,191,.35)", lastValueVisible: false, priceLineVisible: false });
  state.volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  state.chart.subscribeCrosshairMove((param) => {
    if (touching) return; // Fingersteuerung setzt die Infozeile selbst
    const i = param.time !== undefined ? state.timeIndex.get(param.time) : undefined;
    state.hovering = i !== undefined;
    if (i !== undefined) renderLegendRow(i);
    else showLastInLegend();
  });
  attachTouch(el);
  return state.chart;
}

// ---------- Darstellung: Kerzen oder Linie ----------
const TYPE_STORE = "ta.charttype.v1";
let chartType = (() => { try { return localStorage.getItem(TYPE_STORE) || "candles"; } catch { return "candles"; } })();
function activeSeries() { return chartType === "line" ? state.line : state.candles; }
function applyChartType() {
  document.querySelectorAll("#chart-type button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.type === chartType)));
  if (!state.candles) return;
  state.candles.applyOptions({ visible: chartType === "candles" });
  state.line.applyOptions({ visible: chartType === "line" });
}
document.querySelectorAll("#chart-type button").forEach((b) =>
  b.addEventListener("click", () => {
    chartType = b.dataset.type;
    try { localStorage.setItem(TYPE_STORE, chartType); } catch {}
    applyChartType();
  }),
);
applyChartType();

function rowAtTime(t) {
  const i = state.timeIndex.get(t);
  return i === undefined ? null : state.rows[i];
}

// ---------- Werte unter dem Finger ----------
// Ein Finger wischt nach links oder rechts: Fadenkreuz und Infozeile folgen
// dem Finger. Loslassen zeigt wieder die neueste Kerze. Zwei Finger zoomen.
let touching = false;
function attachTouch(el) {
  const show = (ev) => {
    if (ev.touches.length !== 1 || !state.rows.length) return;
    const rect = el.getBoundingClientRect();
    const x = ev.touches[0].clientX - rect.left;
    const logical = state.chart.timeScale().coordinateToLogical(x);
    if (logical === null) return;
    const i = Math.max(0, Math.min(state.rows.length - 1, Math.round(logical)));
    const row = state.rows[i];
    touching = true;
    state.hovering = true;
    try { state.chart.setCrosshairPosition(row[4], shift(row[0]), activeSeries()); } catch {}
    renderLegendRow(i);
  };
  const end = () => {
    if (!touching) return;
    touching = false;
    state.hovering = false;
    try { state.chart.clearCrosshairPosition(); } catch {}
    showLastInLegend();
  };
  el.addEventListener("touchstart", show, { passive: true });
  el.addEventListener("touchmove", show, { passive: true });
  el.addEventListener("touchend", end, { passive: true });
  el.addEventListener("touchcancel", end, { passive: true });
}


// Infozeile über dem Chart: Datum, OHLC, Volumen und Veränderung zur Vorkerze
function renderLegendRow(i) {
  const rows = state.rows;
  const r = rows[i];
  if (!r) return;
  const el = $("legend");
  const f = (x) => (Math.abs(x) >= 1 ? nf(2) : nf(6)).format(x);
  el.querySelector(".lg-date").textContent = candleLabel(shift(r[0]), state.interval);
  el.querySelector(".lg-open").textContent = f(r[1]);
  el.querySelector(".lg-high").textContent = f(r[2]);
  el.querySelector(".lg-low").textContent = f(r[3]);
  const close = el.querySelector(".lg-close");
  close.textContent = f(r[4]);
  close.className = "lg-close " + trendClass(r[4] - r[1]);
  el.querySelector(".lg-vol").textContent = fmtVolume(r[5]);
  const prev = rows[i - 1];
  const pct = prev && prev[4] ? (r[4] / prev[4] - 1) * 100 : null;
  const chg = el.querySelector(".lg-prev");
  chg.textContent = formatPct(pct);
  chg.className = "lg-prev " + pctClass(pct);
  const unit = unitOf(state.entry);
  el.querySelector(".lg-unit").textContent = unit === "€" ? "Werte in €" : `Werte in ${unit}`;
  el.hidden = false;
}

function showLastInLegend() {
  const rows = state.rows || [];
  if (!rows.length) { $("legend").hidden = true; return; }
  renderLegendRow(rows.length - 1);
}


// ---------- Echtzeit in der Liste ----------
// Favoriten und Suchtreffer (bei kurzen Listen alle Einträge) werden live
// aktualisiert: Euro-Werte über Tradegate alle 20 Sekunden, Krypto über Binance.
const listLive = { timer: null, ws: null, run: 0 };
function stopListLive() {
  clearInterval(listLive.timer);
  listLive.timer = null;
  listLive.run += 1;
  if (listLive.ws) { try { listLive.ws.close(); } catch {} listLive.ws = null; }
}

function startListLive() {
  stopListLive();
  if (!rtWanted() || $("view-markets").hidden || document.hidden) return;
  const shown = [...rowEls.values()].map((r) => r.entry);
  const many = shown.length > 20;
  const pick = (e) => !many || isFav(e.id);
  const tg = shown.filter((e) => rtAvailable(e) && pick(e)).slice(0, 25);
  const cr = shown.filter((e) => e.live && e.live.binance && pick(e));
  if (tg.length) {
    const run = listLive.run;
    pollList(tg, run);
    if (tradegateOpen()) listLive.timer = setInterval(() => pollList(tg, run), 20_000);
  }
  if (cr.length) openCryptoStream(cr);
}

async function pollList(entries, run) {
  for (let i = 0; i < entries.length; i += 4) {
    if (run !== listLive.run) return;
    await Promise.all(entries.slice(i, i + 4).map(async (e) => {
      try {
        const q = await fetchQuote(e.isin);
        if (tradegateOpen()) recordTick(e.id, Date.now() / 1000, q.last);
        if (run === listLive.run) paintLive(e.id, q.last, q.prevClose ? q.last / q.prevClose - 1 : null, "heute");
      } catch {}
    }));
  }
}

function openCryptoStream(entries) {
  const bySymbol = new Map(entries.map((e) => [e.live.binance.toUpperCase(), e.id]));
  const streams = [...bySymbol.keys()].map((s) => `${s.toLowerCase()}@miniTicker`).join("/");
  try {
    const ws = new WebSocket(`wss://stream.binance.com:443/stream?streams=${streams}`);
    ws.onmessage = (msg) => {
      const d = JSON.parse(msg.data).data;
      const id = d && bySymbol.get(d.s);
      if (!id) return;
      const close = +d.c, open = +d.o;
      paintLive(id, close, open ? close / open - 1 : null, "24 Std");
    };
    listLive.ws = ws;
  } catch {}
}

function paintLive(id, price, change, label) {
  const r = rowEls.get(id);
  if (!r || price == null) return;
  r.px.textContent = fmtPrice(price, unitOf(r.entry));
  r.chg.textContent = change == null ? "" : `${fmtPct(change)} ${label}`;
  r.chg.className = "chg " + trendClass(change);
  r.item.classList.add("is-live");
  r.sub.textContent = `${subLine(r.entry).split(",")[0]}, live`;
}


document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.index) { refreshIndex(); startListLive(); }
  if (document.hidden) { stopListLive(); pauseAsset(); }
  else if (!$("view-chart").hidden && asset.entry) resumeAsset();
});

function pauseAsset() {
  if (asset.feed) { asset.feed.stop(); asset.paused = true; }
  if (asset.rtTimer) { stopTradegate(); asset.rtPaused = true; }
}
function resumeAsset() {
  if (asset.paused && isCrypto(asset.entry)) { asset.paused = false; startCryptoFeed(); resyncCrypto(asset.gen); }
  if (asset.rtPaused) { asset.rtPaused = false; startTradegate(); }
}

boot();
