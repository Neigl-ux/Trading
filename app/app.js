import { deriveKey, exportKey, importKey, openSealed, sealText, openText } from "./crypto.js";
import { FinnhubStream, usMarketOpen } from "./finnhub.js";
import { CATEGORIES, EMPTY_FILTER, SIGNALS, SORTS, STATUS_LABEL, WINDOWS, dataStatus, dayPerf, onQuote, perfOf, pushQuote, quoteOf, screen, topFlop } from "./engine.js";
import { candleLabel, tickLabel } from "./format.js";
import { fetchQuote, tradegateOpen } from "./tradegate.js";
import { VIEWS, STEP, TZ as PERF_TZ, computeAll, formatPct, pctClass, periodStart, berlinParts, berlinToUtc } from "./perf.js";
import { CryptoFeed, binanceKlines, mergeRows } from "./feeds.js";
import { OVERLAYS, PANES, overlayLines, rsi as rsiCalc, macd as macdCalc, atr as atrCalc, adx as adxCalc } from "./indicators.js";

const KEY_STORE = "ta.key.v1";
const TZ = "Europe/Berlin";
const MARKET_TITLE = { crypto: "Krypto", xetra: "Deutsche Börse (Xetra)" };

const state = { key: null, raw: null, index: null, entry: null, data: null, viewKey: "1d", interval: "1d", rows: [], hovering: false, chart: null, candles: null, line: null, volume: null, ws: null, timeIndex: new Map() };
const files = new Map();
const $ = (id) => document.getElementById(id);

// ---------- Formatierung ----------
const nf = (max) => new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: max });
// Anzeigewährung: intern ist alles in Euro, auf Wunsch zeigt die App Dollar (Kurs EUR/USD aus dem letzten Lauf)
const CUR_STORE = "ta.currency";
let currency = (() => { try { return localStorage.getItem(CUR_STORE) === "$" ? "$" : "€"; } catch { return "€"; } })();
function usdRate() {
  const fx = state.index && state.index.instruments.find((e) => e.id === "EURUSD");
  return fx && fx.last > 0 ? fx.last : null;
}
// Faktor für einen Wert mit Einheit unit; null, wenn nichts umzurechnen ist
function curFactor(unit) {
  return unit === "€" && currency === "$" && usdRate() ? usdRate() : 1;
}
const shownUnit = (unit) => (curFactor(unit) !== 1 ? "$" : unit);
const numFmt = (x) => (Math.abs(x) >= 1 ? nf(2) : nf(6)).format(x);
function fmtPrice(x, unit = "€") {
  if (x == null) return "–";
  return numFmt(x * curFactor(unit)) + " " + shownUnit(unit);
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
    state.index = applyVenue(sealed.data);
    return showMain();
  }
  try {
    const stored = JSON.parse(localStorage.getItem(KEY_STORE) || "null");
    if (stored && stored.salt === sealed.kdf.salt) {
      state.key = await importKey(stored.key);
      state.index = applyVenue(await openSealed(state.key, sealed));
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
    state.index = applyVenue(await openSealed(key, state.raw));
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
  renderStatus();
  startAutoRefresh();
  // Nach dem Neuladen (z. B. Wischen nach unten) dort weitermachen, wo man war
  const r = routeNow();
  if (!(history.state && history.state.app)) {
    if (r.startsWith("chart/")) {
      history.replaceState({ route: "markets", app: true, depth: 0 }, "", "#markets");
      history.pushState({ route: r, app: true, depth: 1 }, "", "#" + r);
    } else {
      history.replaceState({ route: r, app: true, depth: 0 }, "", "#" + r);
    }
  }
  applyRoute(r);
  if (r === "markets") {
    const y = Number(sessionStorage.getItem(SCROLL_STORE) || 0);
    if (y) requestAnimationFrame(() => window.scrollTo(0, y));
  }
  loadFinnhubKey().then(startFinnhub);
}

function updateFreshness() {
  const age = Date.now() / 1000 - state.index.updated;
  const fresh = $("freshness");
  fresh.textContent = `Daten von ${fmtTime(state.index.updated, age > 20 * 3600)} Uhr`;
  fresh.style.color = age > 45 * 60 ? "var(--warn)" : "";
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
      state.index = applyVenue(index);
      files.clear();
      renderCurrentSub();
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
const KIND_LABEL = { aktie: "Aktie", etf: "ETF", index: "Index", krypto: "Krypto", devisen: "Devisen", rohstoff: "Rohstoff" };
const MARKET_LABEL = { xetra: "Xetra", europe: "Europa", us: "USA", crypto: "Krypto", uk: "London", japan: "Japan", hongkong: "Hongkong", fx: "Devisen", futures: "Termin" };
const FILTERS = [
  { key: "fav", label: "Favoriten", test: (e) => isFav(e.id) },
  { key: "all", label: "Alle", test: () => true },
  { key: "de", label: "Deutschland", test: (e) => e.kind === "aktie" && e.market === "xetra" },
  { key: "us", label: "USA", test: (e) => e.kind === "aktie" && e.market === "us" },
  { key: "eu", label: "Europa", test: (e) => e.kind === "aktie" && e.market === "europe" },
  { key: "etf", label: "ETFs", test: (e) => e.kind === "etf" },
  { key: "index", label: "Indizes", test: (e) => e.kind === "index" },
  { key: "krypto", label: "Krypto", test: (e) => e.kind === "krypto" },
  { key: "rohstoff", label: "Rohstoffe", test: (e) => e.kind === "rohstoff" },
  { key: "devisen", label: "Devisen", test: (e) => e.kind === "devisen" },
];
const GROUPS = [
  ["Indizes", (e) => e.kind === "index"],
  ["ETFs und ETCs", (e) => e.kind === "etf"],
  ["Deutsche Aktien", (e) => e.kind === "aktie" && e.market === "xetra"],
  ["Europäische Aktien", (e) => e.kind === "aktie" && e.market === "europe"],
  ["US-Aktien", (e) => e.kind === "aktie" && e.market === "us"],
  ["Krypto", (e) => e.kind === "krypto"],
  ["Rohstoffe", (e) => e.kind === "rohstoff"],
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
    items = all.filter((e) => norm(`${e.name} ${e.id} ${e.ticker || ""} ${e.isin || ""} ${e.wkn || ""}`).includes(q));
    hint.textContent = items.length ? `${items.length} Treffer in allen Bereichen` : `Kein Wert passt zu „${$("search").value.trim()}“. Versuche den Firmennamen oder das Börsenkürzel.`;
  } else {
    const filter = FILTERS.find((f) => f.key === filterKey) || FILTERS[1];
    items = all.filter(filter.test);
    if (filter.key === "all") items = items.filter((e) => cats.has(e.kind));
    if (filter.key === "fav") {
      items.sort((a, b) => favs.indexOf(a.id) - favs.indexOf(b.id));
      hint.textContent = items.length ? "" : "Noch keine Favoriten. Tippe unter „Alle“ auf den Stern neben einem Wert, um ihn hier zu sammeln.";
    } else if (filter.key === "all" && !items.length) {
      hint.textContent = "Alle Kategorien sind ausgeblendet. Tippe oben auf eine Kategorie, um sie wieder einzublenden.";
    } else {
      hint.textContent = "";
    }
  }
  items = sortEntries(items);
  const grouped = !q && filterKey === "all";
  if (grouped) {
    for (const [title, test] of GROUPS) {
      const group = items.filter(test);
      if (!group.length) continue;
      // Gruppen lassen sich ein- und ausklappen (z. B. alle ETFs zuklappen); gemerkt auf diesem Gerät
      const open = !collapsed.has(title);
      const h = document.createElement("button");
      h.type = "button";
      h.className = "group-title";
      h.setAttribute("aria-expanded", String(open));
      h.innerHTML = '<span class="chev" aria-hidden="true"></span><span></span><span class="group-n"></span>';
      h.children[1].textContent = title;
      h.children[2].textContent = new Intl.NumberFormat("de-DE").format(group.length);
      h.addEventListener("click", () => {
        if (collapsed.has(title)) collapsed.delete(title); else collapsed.add(title);
        try { localStorage.setItem(GROUP_STORE, JSON.stringify([...collapsed])); } catch {}
        renderList();
      });
      list.append(h);
      // große Gruppen in Etappen zeichnen, damit die Liste flüssig bleibt
      if (open) list.append(...group.map(rowFor));
    }
  } else {
    list.append(...items.map(rowFor));
  }
  startListLive();
}

const GROUP_STORE = "ta.groups.collapsed.v1";
const SORT_STORE = "ta.sort.v1";
const collapsed = (() => { try { return new Set(JSON.parse(localStorage.getItem(GROUP_STORE) || "[]")); } catch { return new Set(); } })();
let sortKey = (() => { try { return localStorage.getItem(SORT_STORE) || "default"; } catch { return "default"; } })();
const SORT_FNS = {
  default: null,
  name: (a, b) => a.name.localeCompare(b.name, "de"),
  up: (a, b) => (dayChange(b) ?? -Infinity) - (dayChange(a) ?? -Infinity),
  down: (a, b) => (dayChange(a) ?? Infinity) - (dayChange(b) ?? Infinity),
  price: (a, b) => (curPriceOf(b) ?? -Infinity) - (curPriceOf(a) ?? -Infinity),
  turnover: (a, b) => (((b.metrics || {}).avg_turnover_20d) ?? -1) - (((a.metrics || {}).avg_turnover_20d) ?? -1),
};
function dayChange(e) { const p = dayPerf(e, livePrice(e)); return p ? p.pct : null; }
function sortEntries(items) { const fn = SORT_FNS[sortKey]; return fn ? items.slice().sort(fn) : items; }

function rowFor(e) {
  const item = document.createElement("div");
  item.className = "item";
  const row = document.createElement("button");
  row.type = "button";
  row.className = "row with-spark";
  row.innerHTML = `<span class="name"></span><span class="px"></span><span class="sub"></span><span class="chg"></span>`;
  const spark = sparkline(daySpark(e), trendClass(e.change_1d));
  row.prepend(spark);
  row.querySelector(".name").textContent = e.name;
  row.querySelector(".px").textContent = fmtPrice(e.last, unitOf(e));
  paintSub(row.querySelector(".sub"), e);
  const chg = row.querySelector(".chg");
  chg.textContent = e.change_1d == null ? "" : `${fmtPct(e.change_1d)} heute`;
  chg.classList.add(trendClass(e.change_1d));
  row.addEventListener("click", () => openChart(e));
  item.dataset.id = e.id;
  rowEls.set(e.id, { entry: e, item, px: row.querySelector(".px"), chg, sub: row.querySelector(".sub"), spark });
  const q = quoteOf(e.id);
  if (q) paintRow(e.id, q.price, q.live); // zuletzt empfangenen Live-Kurs gleich zeigen
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

// Suchen geht von überall: wer im Chart tippt, landet in der Trefferliste (Zurück führt wieder zum Chart)
$("search").addEventListener("input", () => {
  if ($("view-markets").hidden || $("sub-list").hidden) navigate("markets");
  else renderList();
});

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
const TABS = { home: "view-home", markets: "view-markets", portfolio: "view-portfolio", advisor: "view-advisor", sim: "view-sim", status: "view-status" };
// Jeder Wechsel steht im Browserverlauf: Zurück (Handy-Geste oder Pfeil am PC) geht einen Schritt
// zurück statt die App zu schließen, und Neuladen landet wieder auf derselben Seite.
const SCROLL_STORE = "ta.scroll";
history.scrollRestoration = "manual";
let listScroll = 0;
const routeNow = () => decodeURIComponent(location.hash.slice(1)) || "home";
function navigate(route, replace = false) {
  const depth = (history.state && history.state.depth) || 0;
  const url = "#" + route.split("/").map(encodeURIComponent).join("/");
  if (replace) history.replaceState({ route, app: true, depth }, "", url);
  else history.pushState({ route, app: true, depth: depth + 1 }, "", url);
  applyRoute(route);
}
function applyRoute(route) {
  if (!state.index) return;
  const [tab, ...rest] = route.split("/");
  if (tab === "chart") {
    const id = rest.join("/");
    const e = state.index.instruments.find((x) => x.id === id);
    if (e) {
      if ($("view-chart").hidden || !state.entry || state.entry.id !== id) showChart(e);
      return;
    }
    history.replaceState({ route: "home", app: true, depth: (history.state && history.state.depth) || 0 }, "", "#home");
    return showTab("home");
  }
  if (tab === "movers" || tab === "screener") return showTab("markets", tab, tab);
  if (tab === "watch") { setFilterKey("fav"); return showTab("markets", "list", "watch"); }
  showTab(TABS[tab] ? tab : "home");
}
window.addEventListener("popstate", () => applyRoute(routeNow()));
function setTabActive(tab) {
  document.querySelectorAll(".tabbar button").forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
}
function setFilterKey(key) {
  filterKey = key;
  try { localStorage.setItem(FILTER_STORE, key); } catch {}
  $("search").value = "";
  document.querySelectorAll("#filter-strip button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.key === key)));
}
function showTab(tab, sub = "list", active = tab) {
  const fromChart = !$("view-chart").hidden;
  showSub(tab === "markets" ? sub : "list");
  setTabActive(active);
  stopLive();
  stopListLive();
  $("view-chart").hidden = true;
  for (const [t, id] of Object.entries(TABS)) $(id).hidden = t !== tab;
  if (tab === "home") renderDash();
  if (tab === "portfolio") renderPortfolio();
  if (tab === "status") renderSettings();
  if (tab === "markets") {
    renderCatBars();
    if (sub === "movers") renderMovers();
    else if (sub === "screener") renderScreener();
    else renderList();
    if (fromChart) requestAnimationFrame(() => window.scrollTo(0, listScroll));
  } else {
    window.scrollTo(0, 0);
  }
}
document.querySelectorAll(".tabbar button").forEach((b) =>
  b.addEventListener("click", () => {
    if (routeNow() === b.dataset.tab) return applyRoute(b.dataset.tab);
    navigate(b.dataset.tab);
  }),
);
$("back-btn").addEventListener("click", () => {
  if (history.state && history.state.depth > 0) history.back();
  else navigate("markets", true);
});
let scrollQueued = false;
window.addEventListener("scroll", () => {
  if (scrollQueued || $("view-markets").hidden) return;
  scrollQueued = true;
  setTimeout(() => { scrollQueued = false; if (!$("view-markets").hidden) try { sessionStorage.setItem(SCROLL_STORE, String(Math.round(window.scrollY))); } catch {} }, 300);
}, { passive: true });
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

function openChart(entry) { navigate(`chart/${entry.id}`); }

async function showChart(entry) {
  if (!$("view-markets").hidden) listScroll = window.scrollY;
  for (const id of Object.values(TABS)) $(id).hidden = true;
  setTabActive("markets");
  window.scrollTo(0, 0);
  stopListLive();
  closeAsset();
  const gen = asset.gen;
  state.entry = asset.entry = entry;
  Object.assign(asset, { src: {}, ticks: isCrypto(entry) ? [] : storedTicks(entry.id), price: null, asOf: null, stats: null, status: "", source: "" });
  $("view-markets").hidden = true;
  $("view-chart").hidden = false;
  $("q-name").textContent = entry.name;
  $("rt-wrap").hidden = isCrypto(entry) || !(rtAvailable(entry) || (fhSymbol(entry) && fh.token));
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
    // Listenkurs (Tradegate beim letzten Lauf) ist neuer als die Yahoo-Kerzen: denselben Kurs zeigen
    if (entry.last != null && entry.last_ts && entry.last_ts >= (asset.asOf || 0)) {
      asset.price = entry.last;
      asset.asOf = entry.last_ts;
    }
    if (rtWanted() && (rtAvailable(entry) || fhSymbol(entry))) startTradegate();
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

const SUBSEC = { "1s": 1, "5s": 5, "15s": 15 };
const baseOf = (c) => (SUBSEC[c] ? "1s" : c);
function feedIntervals() {
  const sel = baseOf(VIEW_BY_KEY[viewKey].candle);
  return [...new Set(["1m", "1h", "1d", "1w", sel])];
}

/** Kerzen zu größeren Kerzen zusammenfassen (z. B. 1-Sekunden- zu 5-Sekunden-Kerzen). */
function aggregate(rows, step) {
  const out = [];
  for (const r of rows) {
    const t = Math.floor(r[0] / step) * step;
    const last = out[out.length - 1];
    if (last && last[0] === t) { last[2] = Math.max(last[2], r[2]); last[3] = Math.min(last[3], r[3]); last[4] = r[4]; last[5] += r[5] || 0; }
    else out.push([t, r[1], r[2], r[3], r[4], r[5] || 0]);
  }
  return out;
}
/** Kerzen aus einzelnen Live-Kursen bauen (Aktien: Tradegate/Finnhub). */
function tickCandles(ticks, step) {
  return aggregate(ticks.map(([t, p]) => [t, p, p, p, p, 0]), step);
}

/** Beginn und Ende des Zeitraums einer Ansicht, bezogen auf den aktuellen Kurszeitpunkt. */
function viewRange(view, rows) {
  const lastRow = rows[rows.length - 1];
  const lastEnd = lastRow ? lastRow[0] + (STEP[state.interval] || 60) : null;
  let end = asset.asOf || lastEnd || Date.now() / 1000;
  let start = periodStart(view.perf, end);
  // Liegt im Zeitraum keine Kerze (z. B. Börse heute noch zu), den letzten Zeitraum mit Daten zeigen
  if (lastRow && !rows.some((r) => r[0] >= start && r[0] < end)) {
    end = lastEnd;
    start = periodStart(view.perf, end);
  }
  return { start, end };
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
  if (interval === "1s") {
    for (const [iv, step] of [["5s", 5], ["15s", 15]]) {
      const d = asset.src[iv];
      if (!d || !d.live) continue;
      const t = Math.floor(row[0] / step) * step;
      const parts = rows.filter((r) => r[0] >= t && r[0] < t + step);
      const agg = aggregate(parts, step)[0];
      if (!agg) continue;
      const dl = d.rows[d.rows.length - 1];
      if (dl && dl[0] === t) d.rows[d.rows.length - 1] = agg;
      else if (!dl || t > dl[0]) d.rows.push(agg);
      if (iv === state.interval) pushToChart(agg);
    }
  }
  if (interval === "1m" && asset.price == null) { asset.price = row[4]; asset.asOf = Date.now() / 1000; }
  schedulePerf();
}

function onTicker(t) {
  pushQuote(asset.entry.id, { price: t.price, time: t.time, source: "binance", open: true });
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
  if (!state.hovering) showLastInLegend();
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
  let candle = view.candle;
  let note = "";
  if (isCrypto(asset.entry)) {
    const base = baseOf(candle);
    if (!asset.src[base] || !asset.src[base].live) {
      try {
        const rows = await binanceKlines(asset.entry.live.binance, base, 1000);
        if (gen !== asset.gen) return;
        asset.src[base] = { name: `binance-${base}`, rows, step: STEP[base], live: true };
      } catch {
        note = "Binance ist gerade nicht erreichbar. Angezeigt werden die zuletzt gesammelten Kerzen.";
      }
    }
    if (asset.feed) asset.feed.setIntervals(feedIntervals());
    if (SUBSEC[candle] && candle !== "1s" && asset.src["1s"]) {
      asset.src[candle] = { name: `binance-${candle}`, rows: aggregate(asset.src["1s"].rows, SUBSEC[candle]), step: SUBSEC[candle], live: true };
    }
  } else if (STEP[candle] < 300) {
    // Aktien: Kerzen unter 5 Minuten gibt es kostenlos nur aus den Live-Kursen dieser App
    const bucket = Math.max(5, STEP[candle]);
    const key2 = bucket === 5 ? "5s" : candle;
    const rows = tickCandles(asset.ticks || [], bucket);
    const span = view.perf.rolling || 3600;
    const covers = rows.length >= 2 && rows[0][0] <= (asset.asOf || Date.now() / 1000) - span * 0.5;
    if (covers && rtActive()) {
      asset.src[key2] = { name: `ticks-${key2}`, rows, step: bucket, live: false };
      candle = key2;
      note = "Kerzen aus den Live-Kursen von Tradegate bzw. der US-Börse, gesammelt seit die App offen ist.";
    } else {
      candle = "5m";
      note = "Für diesen kurzen Zeitraum liegen noch nicht genug Live-Kurse vor (sie werden gesammelt, solange die App offen und die Börse geöffnet ist). Angezeigt werden 5-Minuten-Kerzen.";
    }
  }
  // Ersatz, wenn die gewünschte Kerzenlänge fehlt: die nächstgröbere vorhandene
  if (!asset.src[candle] || !asset.src[candle].rows.length) {
    const order = ["1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w", "1M"];
    const alt = order.filter((i) => STEP[i] >= (STEP[candle] || 0)).find((i) => asset.src[i] && asset.src[i].rows.length);
    if (alt && alt !== candle) {
      if (!note) note = `Für diesen Wert gibt es keine ${candle}-Kerzen. Angezeigt werden ${alt}-Kerzen.`;
      candle = alt;
    }
  }
  state.interval = candle;
  $("chart-note").textContent = note;
  showRows(view);
  schedulePerf();
}

/** Kerzen des gewählten Intervalls in den Chart (vollständig nur beim Wechsel). */
// Der Chart zeigt deutsche Ortszeit. Bei der Zeitumstellung im Herbst gibt es eine Stunde
// doppelt; zwei Kerzen mit derselben Ortszeit verträgt der Chart nicht. Sie werden zu einer
// Kerze zusammengefasst (Eröffnung der ersten, Schluss der zweiten, Hoch/Tief/Volumen gemeinsam).
function mergeSameLocalTime(rows) {
  for (let i = rows.length - 1; i > 0; i--) {
    if (shift(rows[i][0]) > shift(rows[i - 1][0])) continue;
    const a = rows[i - 1], b = rows[i];
    rows.splice(i - 1, 2, [a[0], a[1], Math.max(a[2], b[2]), Math.min(a[3], b[3]), b[4], (a[5] || 0) + (b[5] || 0)]);
  }
  return rows;
}

function showRows(view = VIEW_BY_KEY[viewKey]) {
  const src = asset.src[state.interval];
  const rows = src ? mergeSameLocalTime(src.rows) : [];
  state.rows = rows;
  if (!rows.length) $("chart-note").textContent = "Für dieses Intervall liegen noch keine Kerzen vor. Die Historie füllt sich mit jedem Lauf.";
  showLastInLegend();
  updateMeta();
  if (!ensureChart()) {
    $("chart-note").textContent = "Die Chart-Bibliothek konnte nicht geladen werden. Prüfe die Internetverbindung und lade neu.";
    return;
  }
  const intraday = STEP[state.interval] < 86400;
  state.chart.applyOptions({ timeScale: { timeVisible: intraday, secondsVisible: STEP[state.interval] < 60 } });
  state.timeIndex = new Map(rows.map((r, i) => [shift(r[0]), i]));
  state.candles.setData(rows.map(toCandle));
  state.line.setData(rows.map(toLine));
  state.volume.setData(rows.map(toVolume));
  if (rows.length) {
    // genau der eingestellte Zeitraum: 1m = letzte Minute, 1D = heute, 1W = diese Woche …
    const { start, end } = viewRange(view, rows);
    const from = Math.max(rows[0][0], start);
    const lastStart = rows[rows.length - 1][0];
    const to = Math.max(from, Math.min(lastStart, end));
    try { state.chart.timeScale().setVisibleRange({ from: shift(from), to: shift(to) }); }
    catch { state.chart.timeScale().fitContent(); }
  }
  paintResolution();
  hookPaneSync();
  renderIndicators();
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
  scheduleIndicators();
  if (!state.hovering) showLastInLegend();
}

const toCandle = (r) => ({ time: shift(r[0]), open: r[1], high: r[2], low: r[3], close: r[4] });
const toLine = (r) => ({ time: shift(r[0]), value: r[4] });
const toVolume = (r) => ({ time: shift(r[0]), value: r[5], color: r[4] >= r[1] ? "rgba(22,199,132,.35)" : "rgba(234,57,67,.35)" });

// ---------- Kopfzeile: Kurs, Veränderung heute, Hoch/Tief/Volumen ----------
function updateHeader() {
  const e = asset.entry;
  if (!e || asset.price == null) return;
  const unit = unitOf(e);
  $("q-price").textContent = priceText(e, asset.price);
  const day = lastPerf["1D"];
  // Veränderung, Hoch, Tief und Volumen beziehen sich auf den gewählten Zeitraum (5m = letzte 5 Minuten …)
  const per = lastPerf[viewKey];
  const short = PERIOD_SHORT[viewKey] || "";
  const ch = $("q-change");
  if (per && per.ref) {
    const abs = asset.price - per.ref.price;
    ch.textContent = `${abs >= 0 ? "+" : "-"}${fmtPrice(Math.abs(abs), unit)} (${formatPct(per.pct)}) ${short}`;
    ch.className = "change " + pctClass(per.pct);
  } else if (day && day.ref) {
    const abs = asset.price - day.ref.price;
    ch.textContent = `${abs >= 0 ? "+" : "-"}${fmtPrice(Math.abs(abs), unit)} (${formatPct(day.pct)}) heute`;
    ch.className = "change " + pctClass(day.pct);
  } else {
    ch.textContent = "";
  }
  const ps = periodSummary();
  const stats = ps ? { high: ps.high, low: ps.low, volume: ps.volume } : null;
  const box = $("q-stats");
  box.replaceChildren();
  if (!stats) return;
  const label = [`Hoch ${short}`, `Tief ${short}`, `Volumen ${short}`];
  const vals = [fmtPrice(stats.high, unit), fmtPrice(stats.low, unit), fmtVolume(stats.volume)];
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
  } else if (fhFresh(e)) {
    text = "Live von der US-Börse über Finnhub, Kerzenhistorie von Yahoo";
  } else if (rtActive()) {
    text = "Live über Tradegate, Kerzenhistorie von Yahoo";
  } else {
    text = e.delay_minutes ? `Yahoo Finance, ${e.delay_minutes} Min verzögert` : "Yahoo Finance, Stand letzter Lauf";
  }
  if (e.converted_from) text += ", von US-Dollar in Euro umgerechnet";
  $("q-meta").textContent = text;
  paintBadge($("q-status"), e);
}

// ---------- Echtzeit für Aktien und ETFs (Tradegate) ----------
const RT_STORE = "ta.realtime.v1";
const rt = { line: null, failures: 0 };
// Standard: an. Nur wer ausdrücklich ausschaltet, bekommt verzögerte Kurse.
function rtWanted() { try { return localStorage.getItem(RT_STORE) !== "0"; } catch { return true; } }
function rtAvailable(e) { return Boolean(e && e.isin && unitOf(e) === "€") && venuePref() === "tradegate"; }
function rtActive() { return (Boolean(asset.rtTimer || asset.rtAbort) && rtAvailable(asset.entry)) || fhFresh(asset.entry); }

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
  else { stopAllLive(); renderCurrentSub(); }
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
  if (fh.chartSym) { fh.chartSym = null; updateFinnhubWant(); }
}

function startTradegate() {
  stopTradegate();
  if (isCrypto(asset.entry) || $("view-chart").hidden) return;
  fh.chartSym = fhSymbol(asset.entry);
  if (fh.chartSym) updateFinnhubWant();
  if (!rtAvailable(asset.entry)) { updateMeta(); return; }
  rt.failures = 0;
  pollTradegate();
  // Tradegate aktualisiert alle 10 Sekunden; außerhalb der Handelszeit genügt eine Abfrage pro Minute
  asset.rtTimer = setInterval(pollTradegate, tradegateOpen() ? LIVE_TICK_MS : 60_000);
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
  if (fhFresh(asset.entry)) {
    // Der Kurs kommt gerade direkt von der US-Börse; Tradegate liefert nur Hoch, Tief und Volumen in Euro
    asset.stats = { kind: "day", high: q.high, low: q.low, volume: q.volume, volumeUnit: "Stück" };
    updateHeader();
    return;
  }
  const open = tradegateOpen();
  pushQuote(asset.entry.id, { price: q.last, source: "tradegate", open, time: now });
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
  livePriceLine(q.last);
  if (open && state.candles) updateLiveCandle(q.last, now);
}

function livePriceLine(price) {
  if (!state.candles) return;
  const opts = { price, color: "#3b82f6", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Live" };
  if (rt.line) { rt.line.c.applyOptions({ price }); rt.line.l.applyOptions({ price }); }
  else rt.line = { c: state.candles.createPriceLine(opts), l: state.line.createPriceLine(opts) };
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
    layout: { background: { color: c("--panel") }, textColor: c("--mute"), fontFamily: "Inter, system-ui, sans-serif" },
    grid: { vertLines: { color: c("--grid") }, horzLines: { color: c("--grid") } },
    rightPriceScale: { borderColor: c("--line") },
    timeScale: { borderColor: c("--line"), rightOffset: 3, tickMarkFormatter: (t, type) => tickLabel(t, type) },
    crosshair: { mode: 1 },
    // Wischen mit einem Finger zeigt Werte (eigene Steuerung unten), zwei Finger zoomen
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: false, vertTouchDrag: false },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
    localization: {
      locale: "de-DE",
      priceFormatter: (p) => numFmt(p * curFactor(unitOf(state.entry))),
      timeFormatter: (t) => candleLabel(t, state.interval),
    },
  });
  state.candles = state.chart.addCandlestickSeries({
    upColor: c("--up"), downColor: c("--down"), borderVisible: false, wickUpColor: c("--up"), wickDownColor: c("--down"),
  });
  state.line = state.chart.addAreaSeries({
    lineColor: "#3b82f6", lineWidth: 2, topColor: "rgba(59,130,246,.22)", bottomColor: "rgba(59,130,246,0)",
    crosshairMarkerBackgroundColor: c("--accent"), crosshairMarkerBorderColor: c("--panel"), crosshairMarkerRadius: 5,
  });
  applyChartType();
  state.volume = state.chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", color: "rgba(133,146,166,.3)", lastValueVisible: false, priceLineVisible: false });
  state.volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  state.chart.timeScale().subscribeVisibleLogicalRangeChange(syncPan);
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
    paintResolution();
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
  const k = curFactor(unitOf(state.entry));
  const f = (x) => numFmt(x * k);
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
  chg.previousElementSibling.textContent = "zur Vorkerze";
  close.previousElementSibling.textContent = "Schluss";
  const unit = unitOf(state.entry);
  el.querySelector(".lg-unit").textContent = k !== 1
    ? `Werte in $, umgerechnet mit 1 € = ${nf(4).format(k)} $`
    : `Werte in ${unit}`;
  el.hidden = false;
}

// Ohne Finger/Maus über dem Chart: Werte des ganzen Zeitraums. Beim Darüberfahren: die einzelne Kerze.
const PERIOD_LONG = { "1m": "Letzte Minute", "5m": "Letzte 5 Minuten", "15m": "Letzte 15 Minuten", "30m": "Letzte 30 Minuten",
  "1h": "Letzte Stunde", "4h": "Letzte 4 Stunden", "12h": "Letzte 12 Stunden", "1D": "Heute", "1W": "Letzte 7 Tage",
  "1M": "Letzter Monat", "6M": "Letzte 6 Monate", "1J": "Letztes Jahr", "3J": "Letzte 3 Jahre", "5J": "Letzte 5 Jahre", "10J": "Letzte 10 Jahre" };
const PERIOD_SHORT = { "1m": "1 Min", "5m": "5 Min", "15m": "15 Min", "30m": "30 Min", "1h": "1 Std", "4h": "4 Std", "12h": "12 Std",
  "1D": "heute", "1W": "7 Tage", "1M": "1 Monat", "6M": "6 Monate", "1J": "1 Jahr", "3J": "3 Jahre", "5J": "5 Jahre", "10J": "10 Jahre" };

/** Eröffnung, Hoch, Tief, Schluss und Volumen über den gewählten Zeitraum. */
function periodSummary() {
  const rows = state.rows || [];
  if (!rows.length) return null;
  const view = VIEW_BY_KEY[viewKey];
  const { start, end } = viewRange(view, rows);
  let part = rows.filter((r) => r[0] >= start && r[0] < end);
  if (!part.length) part = rows.slice(-1);
  const live = asset.price != null && asset.asOf != null && asset.asOf >= part[part.length - 1][0];
  const close = live ? asset.price : part[part.length - 1][4];
  let high = Math.max(...part.map((r) => r[2])), low = Math.min(...part.map((r) => r[3]));
  if (live) { high = Math.max(high, close); low = Math.min(low, close); }
  const per = lastPerf[viewKey];
  return {
    start: part[0][0], end: live ? asset.asOf : part[part.length - 1][0] + (STEP[state.interval] || 0),
    open: part[0][1], high, low, close,
    volume: part.reduce((a, r) => a + (r[5] || 0), 0),
    pct: per && per.pct != null ? per.pct : (close / part[0][1] - 1) * 100,
  };
}

function showLastInLegend() {
  const rows = state.rows || [];
  if (!rows.length) { $("legend").hidden = true; return; }
  const ps = periodSummary();
  if (!ps) return renderLegendRow(rows.length - 1);
  const el = $("legend");
  const k = curFactor(unitOf(state.entry));
  const f = (x) => numFmt(x * k);
  const span = STEP[state.interval] >= 86400
    ? `${fmtDate(ps.start)} – ${fmtDate(ps.end)}`
    : `${fmtTime(ps.start, true)} – ${fmtTime(ps.end)} Uhr`;
  el.querySelector(".lg-date").textContent = `${PERIOD_LONG[viewKey] || ""}: ${span}`;
  el.querySelector(".lg-open").textContent = f(ps.open);
  el.querySelector(".lg-high").textContent = f(ps.high);
  el.querySelector(".lg-low").textContent = f(ps.low);
  const close = el.querySelector(".lg-close");
  close.textContent = f(ps.close);
  close.className = "lg-close " + trendClass(ps.close - ps.open);
  close.previousElementSibling.textContent = "Aktuell";
  el.querySelector(".lg-vol").textContent = fmtVolume(ps.volume);
  const chg = el.querySelector(".lg-prev");
  chg.textContent = formatPct(ps.pct);
  chg.className = "lg-prev " + pctClass(ps.pct);
  chg.previousElementSibling.textContent = "im Zeitraum";
  const unit = unitOf(state.entry);
  el.querySelector(".lg-unit").textContent = k !== 1 ? `Werte in $, umgerechnet mit 1 € = ${nf(4).format(k)} $` : `Werte in ${unit}`;
  el.hidden = false;
}


// ---------- Echtzeit in der Liste ----------
// Alle Zeilen, die gerade auf dem Bildschirm stehen, werden live aktualisiert:
// Euro-Werte über Tradegate alle 15 Sekunden, Krypto über den Binance-Strom.
// „Heute“ vergleicht mit demselben Vortageskurs wie die 1D-Anzeige im Chart.
// Live-Kurse für ALLE Werte, solange die App offen ist (nicht nur die sichtbaren):
// - Tradegate (Aktien, ETFs): alle 2 Sekunden die sichtbaren Werte und der offene Chart,
//   dazu reihum 4 weitere Werte, sodass jeder Wert regelmäßig dran ist. Tradegate selbst
//   aktualisiert seine Kurse etwa alle 10 Sekunden.
// - Krypto: Binance-Strom (jede Sekunde) für alle Paare, Kraken-Strom für Werte ohne Binance-Paar.
// - US-Aktien und US-ETFs: Finnhub-Strom (bis zu 50 Werte gleichzeitig, sichtbare zuerst).
// „Heute“ vergleicht mit demselben Vortageskurs wie die 1D-Anzeige im Chart.
const LIVE_TICK_MS = 2000;
const listLive = { timer: null, ws: null, kws: null, run: 0, visible: new Set(), observer: null, rot: 0, busy: false, usd: new Map() };

// Nur die Beobachtung der gerade gezeichneten Zeilen endet; die Live-Abfragen laufen weiter.
function stopListLive() {
  if (listLive.observer) { listLive.observer.disconnect(); listLive.observer = null; }
  listLive.visible.clear();
}

function stopAllLive() {
  stopListLive();
  clearInterval(listLive.timer);
  listLive.timer = null;
  listLive.run += 1;
  for (const k of ["ws", "kws"]) {
    if (listLive[k]) { try { listLive[k].onclose = null; listLive[k].close(); } catch {} listLive[k] = null; }
  }
  if (fh.listSyms.size) { fh.listSyms = new Set(); updateFinnhubWant(); }
}

function startListLive() {
  stopListLive();
  if (!rtWanted() || document.hidden || !state.index) return;
  // sichtbare Zeilen beobachten: sie kommen bei jeder Runde dran
  if ("IntersectionObserver" in window) {
    listLive.observer = new IntersectionObserver((items) => {
      for (const it of items) {
        const id = it.target.dataset.id;
        if (it.isIntersecting) listLive.visible.add(id);
        else listLive.visible.delete(id);
      }
      listFinnhubSymbols();
    }, { rootMargin: "80px 0px" });
    for (const r of rowEls.values()) listLive.observer.observe(r.item);
  }
  ensureAllLive();
  listFinnhubSymbols();
}

function ensureAllLive() {
  if (listLive.timer || !rtWanted() || document.hidden || !state.index) return;
  listLive.run += 1;
  const run = listLive.run;
  listLive.timer = setInterval(() => liveTick(run), LIVE_TICK_MS);
  liveTick(run);
  const all = state.index.instruments;
  const binance = all.filter(isCrypto);
  if (binance.length) openCryptoStream(binance, run);
  const kraken = all.filter((e) => e.kind === "krypto" && !isCrypto(e));
  if (kraken.length) openKrakenStream(kraken, run);
}

const tgEntries = () => state.index.instruments.filter((e) => rtAvailable(e) && !isCrypto(e));

async function liveTick(run) {
  if (listLive.busy || run !== listLive.run || document.hidden) return;
  listLive.busy = true;
  try {
    const all = tgEntries();
    const pri = [];
    for (const id of listLive.visible) {
      const r = rowEls.get(id);
      if (r && rtAvailable(r.entry) && !isCrypto(r.entry) && !fhFresh(r.entry)) pri.push(r.entry);
    }
    const chosen = new Map(pri.slice(0, 10).map((e) => [e.id, e]));
    // reihum weitere Werte, auch unsichtbare
    for (let k = 0; k < all.length && chosen.size < pri.slice(0, 10).length + 4; k++) {
      const e = all[listLive.rot % all.length];
      listLive.rot += 1;
      if (!chosen.has(e.id) && !fhFresh(e)) chosen.set(e.id, e);
    }
    const list = [...chosen.values()];
    for (let i = 0; i < list.length; i += 4) {
      if (run !== listLive.run) return;
      await Promise.all(list.slice(i, i + 4).map((e) => pollOne(e, run)));
    }
  } finally {
    listLive.busy = false;
  }
}

async function pollOne(e, run) {
  try {
    const q = await fetchQuote(e.isin);
    if (fhFresh(e)) return; // US-Börse ist gerade die Quelle
    if (tradegateOpen()) recordTick(e.id, Date.now() / 1000, q.last);
    if (run === listLive.run) paintLive(e.id, q.last, { source: "tradegate", open: tradegateOpen() });
  } catch {}
}

// Binance: Euro-Paare für die Kurse, dazu die USDT-Paare für die Dollar-Anzeige
// (so steht bei „$“ derselbe Kurs wie auf Binance, ohne Umweg über den Wechselkurs).
function openCryptoStream(entries, run) {
  const bySymbol = new Map();
  for (const e of entries) {
    const sym = e.live.binance.toUpperCase();
    bySymbol.set(sym, { id: e.id, usd: false });
    if (sym.endsWith("EUR")) bySymbol.set(sym.slice(0, -3) + "USDT", { id: e.id, usd: true });
  }
  const streams = [...bySymbol.keys()].map((s) => `${s.toLowerCase()}@miniTicker`).join("/");
  let ws;
  try { ws = new WebSocket(`wss://stream.binance.com:443/stream?streams=${streams}`); } catch { return; }
  ws.onmessage = (msg) => {
    let d;
    try { d = JSON.parse(msg.data).data; } catch { return; }
    const hit = d && bySymbol.get(d.s);
    if (!hit) return;
    if (hit.usd) { listLive.usd.set(hit.id, { price: +d.c, at: Date.now() / 1000 }); refreshUsdShown(hit.id); }
    else paintLive(hit.id, +d.c, { source: "binance", open: true });
  };
  ws.onclose = () => { if (run === listLive.run) setTimeout(() => { if (run === listLive.run) openCryptoStream(entries, run); }, 3000); };
  listLive.ws = ws;
}

// Kraken (WebSocket v2, öffentlich): für Kryptowährungen ohne Binance-Euro-Paar
function openKrakenStream(entries, run) {
  const bySymbol = new Map(entries.map((e) => [krakenPair(e), e.id]));
  let ws;
  try { ws = new WebSocket("wss://ws.kraken.com/v2"); } catch { return; }
  ws.onopen = () => ws.send(JSON.stringify({ method: "subscribe", params: { channel: "ticker", symbol: [...bySymbol.keys()] } }));
  ws.onmessage = (msg) => {
    let d;
    try { d = JSON.parse(msg.data); } catch { return; }
    if (d.channel !== "ticker" || !Array.isArray(d.data)) return;
    for (const t of d.data) {
      const id = bySymbol.get(t.symbol);
      if (id && t.last > 0) paintLive(id, +t.last, { source: "kraken", open: true });
    }
  };
  ws.onclose = () => { if (run === listLive.run) setTimeout(() => { if (run === listLive.run) openKrakenStream(entries, run); }, 3000); };
  listLive.kws = ws;
}

/** Dollar-Kurs direkt vom USDT-Paar, wenn „$“ gewählt ist und der Kurs frisch ist. */
function usdtPrice(e) {
  if (currency !== "$" || !e || e.kind !== "krypto") return null;
  const q = listLive.usd.get(e.id);
  return q && Date.now() / 1000 - q.at < 90 ? q.price : null;
}
/** Kurs für die Anzeige: bei Krypto in „$“ der echte USDT-Kurs, sonst umgerechnet. */
function priceText(e, eur) {
  const usd = usdtPrice(e);
  return usd != null ? `${numFmt(usd)} $` : fmtPrice(eur, unitOf(e));
}
function refreshUsdShown(id) {
  if (currency !== "$") return;
  const r = rowEls.get(id);
  if (r && r.px) r.px.textContent = priceText(r.entry, (quoteOf(id) || {}).price ?? r.entry.last);
  if (asset.entry && asset.entry.id === id && !$("view-chart").hidden) $("q-price").textContent = priceText(asset.entry, asset.price);
}

function paintLive(id, price, { source = "tradegate", open = true } = {}) {
  if (price == null) return;
  pushQuote(id, { price, source, open, time: Date.now() / 1000 });
  paintRow(id, price, open);
}

function paintRow(id, price, open) {
  const r = rowEls.get(id);
  if (!r) return;
  if (r.rank) { paintSub(r.sub, r.entry); return; } // Ranglisten ordnen sich selbst neu (siehe onQuote)
  const e = r.entry;
  const change = e.prev_close ? price / e.prev_close - 1 : null;
  r.px.textContent = priceText(e, price);
  r.chg.textContent = change == null ? "" : `${fmtPct(change)} heute`;
  r.chg.className = "chg " + trendClass(change);
  if (r.spark) r.spark.replaceWith(r.spark = sparkline(daySpark(e, price), trendClass(change)));
  r.item.classList.toggle("is-live", open);
  paintSub(r.sub, e);
}


document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.index) { refreshIndex(); startListLive(); }
  if (document.hidden) { stopAllLive(); pauseAsset(); }
  else if (!$("view-chart").hidden && asset.entry) resumeAsset();
  updateFinnhubWant();
});

function pauseAsset() {
  if (asset.feed) { asset.feed.stop(); asset.paused = true; }
  if (asset.rtTimer || fh.chartSym) { stopTradegate(); asset.rtPaused = true; }
}
function resumeAsset() {
  if (asset.paused && isCrypto(asset.entry)) { asset.paused = false; startCryptoFeed(); resyncCrypto(asset.gen); }
  if (asset.rtPaused) { asset.rtPaused = false; startTradegate(); }
}

boot();

// ---------- Währung umschalten ----------
function paintCurrency() {
  for (const b of document.querySelectorAll(".cur-toggle button")) b.setAttribute("aria-pressed", String(b.dataset.cur === currency));
}
function setCurrency(cur) {
  if (cur === currency) return;
  if (cur === "$" && !usdRate()) { $("list-hint").textContent = "Der Dollarkurs ist gerade nicht verfügbar. Die Werte bleiben in Euro."; return; }
  currency = cur;
  try { localStorage.setItem(CUR_STORE, cur); } catch {}
  paintCurrency();
  if (state.index) renderCurrentSub();
  if (state.entry && !$("view-chart").hidden) {
    try {
      updateHeader(); updatePerf(); updateMeta();
      if (state.chart) state.chart.applyOptions({ localization: { priceFormatter: (p) => numFmt(p * curFactor(unitOf(state.entry))) } });
      if (state.rows.length) renderLegendRow(state.rows.length - 1);
    } catch (err) { console.warn(err); }
  }
}
for (const b of document.querySelectorAll(".cur-toggle button")) b.addEventListener("click", () => setCurrency(b.dataset.cur));
paintCurrency();

// ---------- Finnhub: US-Aktien live von der US-Börse ----------
// Der Schlüssel liegt nur auf diesem Gerät, verschlüsselt mit dem App-Passwort.
const FH_STORE = "ta.finnhub.v1";
const fh = { token: null, stream: null, status: "", msg: "", last: new Map(), listSyms: new Set(), chartSym: null, dirty: new Set(), flush: null };
function fhSymbol(e) { return (e && e.live && e.live.finnhub) || null; }
function fhFresh(e) {
  const sym = fhSymbol(e);
  const t = sym && fh.token && fh.last.get(sym);
  return Boolean(t && Date.now() / 1000 - t.recv < 90);
}

async function loadFinnhubKey() {
  try {
    const box = JSON.parse(localStorage.getItem(FH_STORE) || "null");
    if (!box) return null;
    if (box.plain) return box.plain;
    return state.key ? await openText(state.key, box) : null;
  } catch {
    return null;
  }
}
async function saveFinnhubKey(token) {
  const box = state.key ? await sealText(state.key, token) : { plain: token };
  localStorage.setItem(FH_STORE, JSON.stringify(box));
}

function startFinnhub(token) {
  if (fh.stream) fh.stream.stop();
  fh.stream = null;
  fh.token = token || null;
  fh.status = "";
  fh.last.clear();
  if (fh.token) {
    fh.stream = new FinnhubStream({
      token: fh.token,
      onTrade: onFinnhubTrade,
      onStatus: (st, msg) => { fh.status = st; fh.msg = msg || ""; paintFinnhubState(); },
    });
    updateFinnhubWant();
  }
  paintFinnhubState();
}

function updateFinnhubWant() {
  if (!fh.stream) return;
  const want = new Set();
  if (!document.hidden && usMarketOpen()) {
    if (fh.chartSym) want.add(fh.chartSym);
    for (const sym of fh.listSyms) want.add(sym);
  }
  fh.stream.want(want);
  paintFinnhubState();
}
setInterval(updateFinnhubWant, 60_000); // Börsenbeginn und -schluss mitbekommen

function listFinnhubSymbols() {
  // sichtbare US-Werte zuerst, dann alle übrigen bis zur Grenze von 50 Werten
  const syms = new Set();
  for (const id of listLive.visible) {
    const r = rowEls.get(id);
    const sym = r && fhSymbol(r.entry);
    if (sym) syms.add(sym);
  }
  if (state.index && rtWanted()) {
    for (const e of state.index.instruments) {
      if (syms.size >= 49) break; // ein Platz bleibt für den offenen Chart
      const sym = fhSymbol(e);
      if (sym) syms.add(sym);
    }
  }
  fh.listSyms = syms;
  updateFinnhubWant();
}

function onFinnhubTrade(sym, usd, time) {
  const rate = usdRate();
  if (!rate || !(usd > 0)) return;
  fh.last.set(sym, { eur: usd / rate, usd, time: Math.floor(time), recv: Date.now() / 1000 });
  fh.dirty.add(sym);
  // höchstens einmal pro Sekunde zeichnen
  if (!fh.flush) fh.flush = setTimeout(flushFinnhub, 1000);
}

function flushFinnhub() {
  fh.flush = null;
  if (!state.index) return;
  for (const sym of fh.dirty) {
    const t = fh.last.get(sym);
    for (const e of state.index.instruments) {
      if (fhSymbol(e) !== sym) continue;
      recordTick(e.id, t.time, t.eur);
      if (rowEls.has(e.id)) paintLive(e.id, t.eur, { source: "finnhub", open: true });
      else pushQuote(e.id, { price: t.eur, source: "finnhub", open: true, time: t.time });
      if (asset.entry && asset.entry.id === e.id && !$("view-chart").hidden && fh.chartSym === sym) applyFinnhub(t);
    }
  }
  fh.dirty.clear();
}

function applyFinnhub(t) {
  pushQuote(asset.entry.id, { price: t.eur, time: t.time, source: "finnhub", open: true });
  asset.price = t.eur;
  asset.asOf = t.time;
  asset.ticks = tickStore.get(asset.entry.id) || [];
  const time = new Intl.DateTimeFormat("de-DE", { timeStyle: "medium", timeZone: TZ }).format(new Date(t.time * 1000));
  $("rt-info").textContent = `US-Börse über Finnhub, Stand ${time} Uhr, ${nf(2).format(t.usd)} $ umgerechnet mit 1 € = ${nf(4).format(usdRate())} $`;
  updateMeta();
  updateHeader();
  schedulePerf();
  livePriceLine(t.eur);
  if (state.candles) updateLiveCandle(t.eur, t.time);
}

function paintFinnhubState() {
  const el = $("fh-state");
  if (!el) return;
  $("fh-remove").hidden = !fh.token;
  let text;
  if (!fh.token) text = "Kein Schlüssel hinterlegt. US-Aktien laufen live über Tradegate (in Euro).";
  else if (fh.status === "rejected") text = "Finnhub nimmt die Verbindung nicht an. Prüfe, ob der Schlüssel stimmt, und speichere ihn neu.";
  else if (fh.status === "error") text = `Finnhub meldet: ${fh.msg}`;
  else if (!usMarketOpen()) text = "Schlüssel gespeichert. Die US-Börse ist gerade geschlossen (Haupthandel meist 15:30 bis 22:00 Uhr). Bis dahin zeigt die App Tradegate.";
  else if (fh.status === "live") text = "Verbunden. US-Aktien kommen live von der US-Börse.";
  else if (fh.status === "reconnect") text = "Verbindung unterbrochen, verbinde neu …";
  else text = "Schlüssel gespeichert. Die Verbindung startet, sobald du eine US-Aktie siehst.";
  el.textContent = text;
}

$("fh-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const token = $("fh-key").value.trim();
  if (!token) return;
  const el = $("fh-state");
  el.textContent = "Prüfe den Schlüssel …";
  let note = "";
  try {
    const res = await fetch(`https://finnhub.io/api/v1/quote?symbol=AAPL&token=${encodeURIComponent(token)}`, { cache: "no-store" });
    if (res.status === 401 || res.status === 403) {
      el.textContent = "Finnhub lehnt diesen Schlüssel ab. Kopiere ihn noch einmal vollständig aus deinem Finnhub-Konto.";
      return;
    }
    const q = await res.json();
    if (q && q.c) note = ` Test: Apple zuletzt ${nf(2).format(q.c)} $.`;
  } catch {
    note = " (Der Test war von hier aus nicht möglich, der Schlüssel ist trotzdem gespeichert.)";
  }
  try {
    await saveFinnhubKey(token);
  } catch {
    el.textContent = "Der Schlüssel konnte auf diesem Gerät nicht gespeichert werden.";
    return;
  }
  $("fh-key").value = "";
  startFinnhub(token);
  el.textContent += note;
});
$("fh-remove").addEventListener("click", () => {
  try { localStorage.removeItem(FH_STORE); } catch {}
  startFinnhub(null);
});

// ---------- Datenstatus (LIVE / VERZÖGERT / LETZTER SCHLUSSKURS / KEINE DATEN) ----------
const STATUS_CLASS = { live: "st-live", delayed: "st-delayed", closed: "st-closed", none: "st-none" };
function statusTime(st) {
  if (!st.time) return "";
  const age = Date.now() / 1000 - st.time;
  return age > 20 * 3600 ? fmtTime(st.time, true) : fmtTime(st.time);
}
function paintBadge(el, e) {
  if (!el || !e) return;
  const st = dataStatus(e);
  el.className = `badge ${STATUS_CLASS[st.code]}`;
  const when = statusTime(st);
  // in Listen kurz („SCHLUSSKURS“), im Chart ausführlich („LETZTER SCHLUSSKURS“)
  const label = el.dataset.long ? STATUS_LABEL[st.code] : ({ closed: "SCHLUSSKURS" }[st.code] || STATUS_LABEL[st.code]);
  el.textContent = when ? `${label} ${when}` : label;
  el.title = `${STATUS_LABEL[st.code]}${when ? ", Stand " + when + " Uhr" : ""}`;
  if (st.code === "delayed" && e.delay_minutes) el.title += ` (Kursverlauf ${e.delay_minutes} Minuten verzögert)`;
}
function venueLabel(e) {
  const st = dataStatus(e);
  if (st.code === "live" && st.source === "finnhub") return "US-Börse";
  if (st.source === "tradegate") return "Tradegate";
  if (st.source === "binance") return "Binance";
  return e.venue || MARKET_LABEL[e.market] || "";
}
function paintSub(el, e) {
  const kind = e.kind === "aktie" ? `Aktie ${MARKET_LABEL[e.market] || ""}`.trim() : KIND_LABEL[e.kind] || "";
  const badge = document.createElement("span");
  paintBadge(badge, e);
  const venue = venueLabel(e);
  el.replaceChildren(document.createTextNode(venue && venue !== kind ? `${kind}, ${venue} ` : `${kind} `), badge);
}
// Kennzeichnung regelmäßig neu bewerten: ein Kurs ohne Nachschub ist nach 90 Sekunden nicht mehr „live“
setInterval(() => {
  if (document.hidden) return;
  for (const r of rowEls.values()) if (r.sub) paintSub(r.sub, r.entry);
  if (asset.entry && !$("view-chart").hidden) paintBadge($("q-status"), asset.entry);
}, 15_000);

// ---------- Kategorien: zentraler Filter für Liste, Top/Flop und Screener ----------
const CAT_STORE = "ta.cats.v1";
const cats = (() => {
  try {
    const saved = JSON.parse(localStorage.getItem(CAT_STORE) || "null");
    if (Array.isArray(saved)) return new Set(saved);
  } catch {}
  return new Set(CATEGORIES.map((c) => c.key));
})();
function saveCats() { try { localStorage.setItem(CAT_STORE, JSON.stringify([...cats])); } catch {} }
function setCat(key, on) {
  if (key === "all") CATEGORIES.forEach((c) => cats.add(c.key));
  else if (on) cats.add(key);
  else cats.delete(key);
  saveCats();
  renderCatBars();
  paintOptsSummary();
  // nur neu zeichnen, nichts neu laden
  if (!$("sub-movers").hidden) renderMovers();
  else if (!$("sub-screener").hidden) renderResults();
  else renderList();
}
function renderCatBars() {
  if (!state.index) return;
  const counts = new Map();
  for (const e of state.index.instruments) counts.set(e.kind, (counts.get(e.kind) || 0) + 1);
  const all = CATEGORIES.every((c) => cats.has(c.key));
  for (const bar of document.querySelectorAll("[data-cat-bar]")) {
    const chips = [];
    const allChip = document.createElement("button");
    allChip.type = "button";
    allChip.className = "chip";
    allChip.setAttribute("aria-pressed", String(all));
    allChip.textContent = `Alle (${new Intl.NumberFormat("de-DE").format(state.index.instruments.length)})`;
    allChip.addEventListener("click", () => setCat("all", true));
    chips.push(allChip);
    for (const c of CATEGORIES) {
      const n = counts.get(c.key) || 0;
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.setAttribute("aria-pressed", String(cats.has(c.key)));
      b.textContent = `${c.label} (${new Intl.NumberFormat("de-DE").format(n)})`;
      b.disabled = n === 0;
      b.addEventListener("click", () => setCat(c.key, !cats.has(c.key)));
      chips.push(b);
    }
    bar.replaceChildren(...chips);
  }
}

// ---------- Unteransichten der Marktübersicht ----------
const SUBS = { list: "sub-list", movers: "sub-movers", screener: "sub-screener" };
function showSub(sub) {
  for (const [k, id] of Object.entries(SUBS)) $(id).hidden = k !== sub;
  for (const b of document.querySelectorAll("#sub-nav button")) b.setAttribute("aria-pressed", String(b.dataset.sub === sub));
}
for (const b of document.querySelectorAll("#sub-nav button")) {
  b.addEventListener("click", () => {
    const sub = b.dataset.sub;
    const route = sub === "list" ? "markets" : sub;
    if (routeNow() === route) return;
    navigate(route);
  });
}

// Mini-Verlauf (Sparkline) als SVG
function sparkline(values, cls) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 64 22");
  svg.setAttribute("class", `spark ${cls}`);
  svg.setAttribute("aria-hidden", "true");
  if (!values || values.length < 2) return svg;
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = hi - lo || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1) * 62 + 1).toFixed(1)},${(20 - (v - lo) / span * 18).toFixed(1)}`);
  const line = document.createElementNS(ns, "polyline");
  line.setAttribute("points", pts.join(" "));
  svg.append(line);
  return svg;
}

const livePrice = (e) => {
  const q = quoteOf(e.id);
  return q && q.live ? q.price : null;
};
const fmtMoneyCompact = (v) => (v == null ? "–" : `${new Intl.NumberFormat("de-DE", { notation: "compact", maximumFractionDigits: 1 }).format(v * curFactor("€"))} ${shownUnit("€")}`);

// ---------- Top & Flop ----------
function rankRow(r, win = "24h") {
  const e = r.e;
  const item = document.createElement("div");
  item.className = "item rank";
  item.dataset.id = e.id;
  const row = document.createElement("button");
  row.type = "button";
  row.className = "row rank-row";
  const cls = trendClass(r.pct);
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = e.name;
  const tick = document.createElement("span");
  tick.className = "ticker";
  tick.textContent = ` ${e.ticker || e.id}`;
  name.append(tick);
  const px = document.createElement("span");
  px.className = "px";
  px.textContent = priceText(e, r.price);
  const sub = document.createElement("span");
  sub.className = "sub";
  paintSub(sub, e);
  const m = e.metrics || {};
  const vol = document.createElement("span");
  vol.className = "vol";
  vol.textContent = m.turnover_24h != null ? `Umsatz 24 Std. ${fmtMoneyCompact(m.turnover_24h)}` : "Umsatz: nicht verfügbar";
  const chg = document.createElement("span");
  chg.className = `chg ${cls}`;
  const absTxt = r.abs == null ? "" : `${r.abs >= 0 ? "+" : "-"}${fmtPrice(Math.abs(r.abs), unitOf(e))}  `;
  chg.textContent = `${absTxt}${formatPct(r.pct * 100)}`;
  row.append(sparkline(daySpark(e, r.price), cls), name, px, sub, chg, vol);
  row.addEventListener("click", () => openChart(e));
  item.append(row);
  rowEls.set(e.id, { entry: e, item, rank: true, sub });
  return item;
}

let moversQueued = false;
function renderMovers({ soft = false } = {}) {
  if (!state.index || $("sub-movers").hidden) return;
  if (!soft) stopListLive();
  rowEls.clear();
  const res = topFlop(state.index.instruments, { cats, liquidOnly: $("movers-liquid").checked, priceOf: livePrice });
  const fill = (el, rows, empty) => {
    el.replaceChildren(...rows.map((r) => rankRow(r)));
    if (!rows.length) { const p = document.createElement("p"); p.className = "list-hint"; p.textContent = empty; el.append(p); }
  };
  fill($("movers-top"), res.top, "Kein Wert im Plus in den gewählten Kategorien.");
  fill($("movers-flop"), res.flop, "Kein Wert im Minus in den gewählten Kategorien.");
  const x = res.excluded;
  const parts = [`${res.considered} Werte verglichen, jeweils mit dem Schlusskurs des Vortags (Veränderung heute in ${shownUnit("€")} und Prozent).`];
  if (x.nodata) parts.push(`${x.nodata} ohne Vortagesschluss`);
  if (x.illiquid) parts.push(`${x.illiquid} wegen zu geringem Handel ausgeblendet`);
  if (x.implausible) parts.push(`${x.implausible} mit unplausiblem Sprung ausgeblendet`);
  $("movers-note").textContent = parts.join(" ") + (parts.length > 1 ? "." : "");
  // weich: Live-Abfragen laufen weiter, nur neue Zeilen werden zusätzlich beobachtet
  if (soft && listLive.observer) for (const r of rowEls.values()) listLive.observer.observe(r.item);
  else startListLive();
}
$("movers-liquid").addEventListener("change", renderMovers);
// Neue Live-Kurse: Rangliste höchstens alle 3 Sekunden neu ordnen
onQuote(() => {
  if (moversQueued || $("sub-movers").hidden || $("view-markets").hidden) return;
  moversQueued = true;
  setTimeout(() => { moversQueued = false; renderMovers({ soft: true }); }, 3000);
});

// ---------- Screener ----------
const SCR_STORE = "ta.screener.v1";
const SCR_SAVED = "ta.screener.saved.v1";
const SCR_LAYOUT = "ta.screener.layout.v1";
let scr = (() => { try { return { ...EMPTY_FILTER, ...JSON.parse(localStorage.getItem(SCR_STORE) || "{}") }; } catch { return { ...EMPTY_FILTER }; } })();
let scrLayout = (() => { try { return localStorage.getItem(SCR_LAYOUT) || "list"; } catch { return "list"; } })();
const savedFilters = () => { try { return JSON.parse(localStorage.getItem(SCR_SAVED) || "{}"); } catch { return {}; } };
const WIN_LABEL = { "5m": "5 Minuten", "15m": "15 Minuten", "1h": "1 Stunde", "4h": "4 Stunden", "24h": "24 Stunden", "7d": "7 Tage", "30d": "30 Tage" };

function numField(name, label, { step = "any", placeholder = "" } = {}) {
  return `<label class="fld"><span>${label}</span><input name="${name}" type="number" inputmode="decimal" step="${step}" placeholder="${placeholder}"></label>`;
}

function buildScreenerForm() {
  const form = $("screener-form");
  if (form.dataset.built) return;
  form.dataset.built = "1";
  const winOpts = WINDOWS.map((w) => `<option value="${w}">${WIN_LABEL[w]}</option>`).join("");
  const sortOpts = Object.entries(SORTS).map(([k, s]) => `<option value="${k}"${k === "mcap" ? " disabled" : ""}>${s.label}</option>`).join("");
  const sig = Object.entries(SIGNALS).map(([k, s]) => `<label class="check-line"><input type="checkbox" name="sig" value="${k}"> ${s.label}</label>`).join("");
  form.innerHTML = `
    <details open><summary>Performance</summary>
      <div class="fld-row">
        <label class="fld"><span>Zeitraum</span><select name="window">${winOpts}</select></label>
        ${numField("perfMin", "mindestens %", { placeholder: "z. B. 3" })}
        ${numField("perfMax", "höchstens %")}
      </div>
    </details>
    <details><summary>Volumen und Liquidität</summary>
      <div class="fld-row">
        ${numField("turnoverMin", "Umsatz 24 Std. ab €")}
        ${numField("turnoverMax", "Umsatz 24 Std. bis €")}
        ${numField("relVolMin", "Relatives Volumen ab", { placeholder: "z. B. 1,5" })}
        ${numField("volRiseMin", "Volumenanstieg ab %")}
        ${numField("liquidityMin", "Ø Tagesumsatz (20 T.) ab €")}
      </div>
    </details>
    <details><summary>Preis</summary>
      <div class="fld-row">
        ${numField("priceMin", "Kurs ab €")}
        ${numField("priceMax", "Kurs bis €")}
        <p class="small fld-note">Marktkapitalisierung: keine kostenlose Quelle verfügbar, daher nicht filterbar.</p>
      </div>
    </details>
    <details><summary>Technische Signale (Tageskerzen)</summary>
      <div class="fld-row">
        ${numField("rsiMin", "RSI ab", { step: "1" })}
        ${numField("rsiMax", "RSI bis", { step: "1" })}
        <div class="presets"><button type="button" data-rsi="0,30">RSI unter 30</button><button type="button" data-rsi="70,100">RSI über 70</button></div>
      </div>
      <div class="sig-list">${sig}</div>
    </details>
    <div class="fld-row sort-row">
      <label class="fld"><span>Sortieren nach</span><select name="sort">${sortOpts}</select></label>
      <label class="fld"><span>Reihenfolge</span><select name="dir"><option value="desc">absteigend</option><option value="asc">aufsteigend</option></select></label>
    </div>
    <div class="scr-actions">
      <button type="button" id="scr-reset" class="secondary">Zurücksetzen</button>
      <input id="scr-name" type="text" placeholder="Name für diesen Filter" aria-label="Name für diesen Filter">
      <button type="button" id="scr-save">Speichern</button>
    </div>
    <div class="scr-actions">
      <select id="scr-saved" aria-label="Gespeicherte Filter"></select>
      <button type="button" id="scr-load" class="secondary">Laden</button>
      <button type="button" id="scr-delete" class="secondary">Löschen</button>
    </div>`;
  form.addEventListener("input", readScreenerForm);
  form.addEventListener("change", readScreenerForm);
  form.addEventListener("submit", (ev) => ev.preventDefault());
  form.querySelectorAll("[data-rsi]").forEach((b) => b.addEventListener("click", () => {
    const [lo, hi] = b.dataset.rsi.split(",");
    form.rsiMin.value = lo === "0" ? "" : lo;
    form.rsiMax.value = hi === "100" ? "" : hi;
    readScreenerForm();
  }));
  $("scr-reset").addEventListener("click", () => { scr = { ...EMPTY_FILTER }; fillScreenerForm(); readScreenerForm(); });
  $("scr-save").addEventListener("click", () => {
    const name = $("scr-name").value.trim();
    if (!name) { $("scr-name").focus(); return; }
    const all = savedFilters();
    all[name] = scr;
    try { localStorage.setItem(SCR_SAVED, JSON.stringify(all)); } catch {}
    $("scr-name").value = "";
    fillSaved(name);
  });
  $("scr-load").addEventListener("click", () => {
    const f = savedFilters()[$("scr-saved").value];
    if (!f) return;
    scr = { ...EMPTY_FILTER, ...f };
    fillScreenerForm();
    readScreenerForm();
  });
  $("scr-delete").addEventListener("click", () => {
    const all = savedFilters();
    delete all[$("scr-saved").value];
    try { localStorage.setItem(SCR_SAVED, JSON.stringify(all)); } catch {}
    fillSaved();
  });
  for (const b of document.querySelectorAll("#sub-screener [data-layout]")) {
    b.addEventListener("click", () => {
      scrLayout = b.dataset.layout;
      try { localStorage.setItem(SCR_LAYOUT, scrLayout); } catch {}
      renderResults();
    });
  }
  fillScreenerForm();
  fillSaved();
}

function fillSaved(select) {
  const names = Object.keys(savedFilters()).sort((a, b) => a.localeCompare(b, "de"));
  const sel = $("scr-saved");
  sel.replaceChildren(...(names.length ? names : ["Noch keine gespeicherten Filter"]).map((n) => {
    const o = document.createElement("option");
    o.value = names.length ? n : "";
    o.textContent = n;
    return o;
  }));
  sel.disabled = !names.length;
  $("scr-load").disabled = $("scr-delete").disabled = !names.length;
  if (select) sel.value = select;
}

function fillScreenerForm() {
  const form = $("screener-form");
  for (const el of form.elements) {
    if (!el.name || el.name === "sig") continue;
    if (el.name in scr) el.value = scr[el.name] ?? "";
  }
  for (const el of form.querySelectorAll("input[name=sig]")) el.checked = scr.signals.includes(el.value);
}

const SCR_PAGE = 50;
let scrPage = 0;
function readScreenerForm() {
  scrPage = 0;
  const form = $("screener-form");
  const next = { ...EMPTY_FILTER };
  for (const el of form.elements) {
    if (!el.name || el.name === "sig" || !(el.name in next)) continue;
    next[el.name] = el.type === "number" ? (el.value === "" ? null : Number(el.value)) : el.value;
  }
  next.signals = [...form.querySelectorAll("input[name=sig]:checked")].map((el) => el.value);
  // Kurs- und Umsatzgrenzen gibt man in der Anzeigewährung ein; intern wird in Euro gerechnet
  const k = curFactor("€");
  for (const key of ["priceMin", "priceMax", "turnoverMin", "turnoverMax", "liquidityMin"]) if (next[key] != null) next[key] = next[key] / k;
  scr = next;
  try { localStorage.setItem(SCR_STORE, JSON.stringify(scr)); } catch {}
  renderResults();
}

function renderScreener() {
  buildScreenerForm();
  renderResults();
}

function renderResults() {
  if (!state.index || $("sub-screener").hidden) return;
  stopListLive();
  rowEls.clear();
  for (const b of document.querySelectorAll("#sub-screener [data-layout]")) b.setAttribute("aria-pressed", String(b.dataset.layout === scrLayout));
  const { rows, missing } = screen(state.index.instruments, scr, { cats, priceOf: livePrice });
  const n = new Intl.NumberFormat("de-DE").format(rows.length);
  $("screener-count").textContent = `${n} Treffer` + (missing ? `, ${missing} Werte ohne ausreichende Daten für die gewählten Kriterien` : "");
  const box = $("screener-results");
  const pages = Math.max(1, Math.ceil(rows.length / SCR_PAGE));
  scrPage = Math.min(scrPage, pages - 1);
  const shown = rows.slice(scrPage * SCR_PAGE, (scrPage + 1) * SCR_PAGE);
  if (scrLayout === "table") {
    const tbl = document.createElement("table");
    tbl.className = "scr-table";
    tbl.innerHTML = `<thead><tr><th>Wert</th><th>Kurs</th><th>${WIN_LABEL[scr.window]}</th><th>Umsatz 24 Std.</th><th>Rel. Vol.</th><th>RSI</th><th>Volatilität</th><th>Status</th></tr></thead>`;
    const body = document.createElement("tbody");
    for (const r of shown) {
      const tr = document.createElement("tr");
      const ind = r.m.ind || {};
      const cells = [
        `${r.e.name}`, fmtPrice(r.price, unitOf(r.e)), r.perf == null ? "–" : formatPct(r.perf * 100),
        fmtMoneyCompact(r.m.turnover_24h), r.m.rel_volume == null ? "–" : nf(2).format(r.m.rel_volume),
        ind.rsi14 == null ? "–" : new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format(ind.rsi14), ind.volatility == null ? "–" : formatPct(ind.volatility * 100).replace("+", ""),
      ];
      cells.forEach((c, i) => {
        const td = document.createElement("td");
        td.textContent = c;
        if (i === 2) td.className = pctClass(r.perf == null ? null : r.perf * 100);
        tr.append(td);
      });
      const td = document.createElement("td");
      const badge = document.createElement("span");
      paintBadge(badge, r.e);
      td.append(badge);
      tr.append(td);
      tr.tabIndex = 0;
      tr.addEventListener("click", () => openChart(r.e));
      tr.addEventListener("keydown", (ev) => { if (ev.key === "Enter") openChart(r.e); });
      body.append(tr);
    }
    tbl.append(body);
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    wrap.append(tbl);
    box.replaceChildren(wrap);
  } else {
    box.replaceChildren(...shown.map((r) => rankRow({ e: r.e, pct: r.perf, abs: r.abs, price: r.price })));
  }
  if (pages > 1) {
    const nav = document.createElement("div");
    nav.className = "pager";
    const btn = (label, page, disabled) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.disabled = disabled;
      b.addEventListener("click", () => { scrPage = page; renderResults(); $("screener-count").scrollIntoView({ block: "start" }); });
      return b;
    };
    const info = document.createElement("span");
    info.textContent = `${scrPage * SCR_PAGE + 1}–${scrPage * SCR_PAGE + shown.length} von ${n}`;
    nav.append(btn("‹ Zurück", scrPage - 1, scrPage === 0), info, btn("Weiter ›", scrPage + 1, scrPage >= pages - 1));
    box.append(nav);
  }
  startListLive();
}

// ---------- Datenquellen (Status) ----------
const SOURCES = [
  ["Kryptowährungen", "Binance (Live-Strom, öffentlich), Kraken (Kerzen und Ersatz-Strom)", "laufend, Kerze wächst mit jedem Handel", "kostenlos", "Paare in Euro; Werte ohne Binance-Paar nur im 15-Minuten-Takt"],
  ["Deutsche und europäische Aktien, ETFs", "Tradegate (Live-Kurs), Yahoo Finance (Kursverlauf)", "Tradegate alle 10–15 s, Kerzen alle 15 Min", "kostenlos", "Live-Kurs ist Tradegate, nicht Xetra/Paris; Kerzen der Heimatbörse 15 Min verzögert"],
  ["US-Aktien und US-ETFs", "Finnhub (Live-Strom, eigener Schlüssel), Tradegate, Yahoo Finance", "Finnhub laufend während 15:30–22:00 Uhr", "kostenlos", "Finnhub kostenlos: nur US-Börsen, höchstens 50 Werte gleichzeitig"],
  ["Indizes", "Yahoo Finance", "alle 15 Minuten", "kostenlos", "15 Min verzögert (US-Indizes nahezu aktuell); reine Referenzwerte, nicht handelbar"],
  ["Rohstoffe", "Yahoo Finance (Terminkontrakte CME/ICE)", "alle 15 Minuten", "kostenlos", "ca. 10 Min verzögert; Sprünge beim Kontraktwechsel möglich"],
  ["Devisen", "Yahoo Finance", "alle 15 Minuten", "kostenlos", "Referenzkurs, kein Handelskurs einer Bank"],
  ["Geprüft, nicht genutzt", "Alpaca (kostenlos nur Börse IEX, wenige Prozent des US-Handels), Xetra-Echtzeit und Finnhub international (kostenpflichtig)", "", "", ""],
];
function renderSources() {
  const tbl = document.createElement("table");
  tbl.className = "scr-table src-table";
  tbl.innerHTML = "<thead><tr><th>Anlageklasse</th><th>Anbieter</th><th>Aktualisierung</th><th>Kosten</th><th>Einschränkungen</th></tr></thead>";
  const body = document.createElement("tbody");
  for (const row of SOURCES) {
    const tr = document.createElement("tr");
    for (const c of row) { const td = document.createElement("td"); td.textContent = c; tr.append(td); }
    body.append(tr);
  }
  tbl.append(body);
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  wrap.append(tbl);
  $("sources").replaceChildren(wrap);
}
renderSources();

function renderCurrentSub() {
  renderCatBars();
  if (!$("sub-movers").hidden) renderMovers();
  else if (!$("sub-screener").hidden) renderResults();
  else renderList();
}

// ---------- Chart verschieben: Schieberegler unter dem Chart ----------
// Auf dem Handy ist Wischen mit einem Finger für die Werte-Anzeige reserviert. Der Regler
// verschiebt den sichtbaren Ausschnitt, ohne den Zoom zu ändern; die Pfeile springen um eine halbe Breite.
let panBusy = false;
function panGeometry() {
  if (!state.chart || !state.rows.length) return null;
  const r = state.chart.timeScale().getVisibleLogicalRange();
  if (!r) return null;
  const width = r.to - r.from;
  const n = state.rows.length;
  const min = 0, max = Math.max(0, n - 1 - width + 3); // +3: Abstand rechts wie rightOffset
  return { r, width, min, max };
}
function syncPan() {
  if (panBusy) return;
  const g = panGeometry();
  const pan = $("pan");
  if (!g || g.max <= g.min) { pan.disabled = true; pan.value = 1000; return; }
  pan.disabled = false;
  pan.value = String(Math.round(Math.min(1, Math.max(0, (g.r.from - g.min) / (g.max - g.min))) * 1000));
}
function panTo(fraction) {
  const g = panGeometry();
  if (!g) return;
  const from = g.min + Math.min(1, Math.max(0, fraction)) * (g.max - g.min);
  panBusy = true;
  state.chart.timeScale().setVisibleLogicalRange({ from, to: from + g.width });
  panBusy = false;
}
$("pan").addEventListener("input", (ev) => panTo(Number(ev.target.value) / 1000));
for (const [id, dir] of [["pan-left", -1], ["pan-right", 1]]) {
  $(id).addEventListener("click", () => {
    const g = panGeometry();
    if (!g || g.max <= g.min) return;
    const from = Math.min(g.max, Math.max(g.min, g.r.from + dir * g.width / 2));
    state.chart.timeScale().setVisibleLogicalRange({ from, to: from + g.width });
  });
}

// =====================================================================
// Startseite (Dashboard): nur Darstellung, alle Daten und Funktionen von oben
// =====================================================================
const DASH_TICKER = ["SPX", "NDX", "DAX", "BTC-EUR", "ETH-EUR"];
const DEFAULT_WATCH = ["BTC-EUR", "ETH-EUR", "SOL-EUR", "XRP-EUR", "DOGE-EUR", "TSLA", "AAPL", "NVDA", "MSFT"];
const DASH_STORE = "ta.dash.v1";
const OV_TABS = [
  { key: "index", label: "Indizes", filter: "index" },
  { key: "aktie", label: "Aktien", filter: "all" },
  { key: "etf", label: "ETFs", filter: "etf" },
  { key: "krypto", label: "Krypto", filter: "krypto" },
  { key: "rohstoff", label: "Rohstoffe", filter: "rohstoff" },
  { key: "devisen", label: "Forex", filter: "devisen" },
];
// Zeiträume wie im großen Chart: genau dieser Zeitraum, in passenden Kerzen aus den gesammelten Daten
const DC_VIEWS = [["1h", "5m"], ["4h", "5m"], ["12h", "15m"], ["1D", "5m"], ["1W", "1h"], ["1M", "4h"], ["1J", "1d"]];
const dash = (() => {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(DASH_STORE) || "{}"); } catch {}
  return { id: saved.id || "BTC-EUR", iv: DC_VIEWS.some(([k]) => k === saved.iv) ? saved.iv : "1D", ov: saved.ov || "index", chart: null, candles: null, vol: null, rows: [], gen: 0, queued: false, last: null };
})();
const saveDash = () => { try { localStorage.setItem(DASH_STORE, JSON.stringify({ id: dash.id, iv: dash.iv, ov: dash.ov })); } catch {} };
const homeVisible = () => state.index && !$("view-home").hidden && !$("main").hidden;
const byId = (id) => state.index.instruments.find((x) => x.id === id);
const curPriceOf = (e) => { const q = quoteOf(e.id); return q ? q.price : e.last; };
function pct24(e) {
  const p = dayPerf(e, livePrice(e));
  return p ? p.pct : e.change_1d ?? null;
}
function shortSym(e) {
  if (e.kind === "krypto") return e.id.split("-")[0];
  if (e.kind === "index") return e.id;
  return (e.ticker || e.id).replace(/^\^/, "").replace(/=.*$/, "").replace(/\.[A-Z]{1,3}$/, "");
}
function avatar(e, el = document.createElement("span")) {
  const sym = shortSym(e);
  let h = 0;
  for (const ch of sym) h = (h * 31 + ch.charCodeAt(0)) % 360;
  el.className = "avatar";
  el.style.setProperty("--hue", h);
  el.textContent = sym.slice(0, 2);
  el.setAttribute("aria-hidden", "true");
  return el;
}
const pctText = (x) => (x == null ? "–" : formatPct(x * 100));

function renderDash() {
  if (!state.index) return;
  renderTicker();
  renderWatch();
  renderDashMovers();
  renderDashScreener();
  renderOverview();
  renderDashChart();
  ensureAllLive();
}

function renderTicker() {
  const box = $("dash-ticker");
  box.replaceChildren(...DASH_TICKER.map(byId).filter(Boolean).map((e) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tick-card";
    const pct = pct24(e);
    const cls = trendClass(pct);
    const info = document.createElement("div");
    info.innerHTML = `<span class="tc-name"></span><span class="tc-price"></span><span class="tc-chg ${cls}"></span>`;
    info.querySelector(".tc-name").textContent = e.kind === "krypto" ? shortSym(e) : e.name;
    info.querySelector(".tc-price").textContent = e.kind === "index" ? numFmt(curPriceOf(e)) : priceText(e, curPriceOf(e));
    info.querySelector(".tc-chg").textContent = pctText(pct);
    b.append(info, sparkline((e.metrics || {}).spark, cls));
    b.addEventListener("click", () => setDashAsset(e.id));
    return b;
  }));
}

function renderWatch() {
  const ids = favs.length ? favs : DEFAULT_WATCH;
  const rows = ids.map(byId).filter(Boolean).slice(0, 12).map((e) => miniRow(e, () => setDashAsset(e.id)));
  $("dash-watch").replaceChildren(...rows);
  if (!rows.length) $("dash-watch").textContent = "Noch keine Favoriten.";
}

function miniRow(e, onClick, { spark = false } = {}) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "mini-row" + (spark ? " with-spark" : "");
  const pct = pct24(e);
  const sym = document.createElement("span");
  sym.className = "mr-sym";
  const label = document.createElement("span");
  label.textContent = spark ? e.name : shortSym(e);
  sym.append(avatar(e), label);
  const px = document.createElement("span");
  px.className = "mr-px";
  px.textContent = e.kind === "index" ? numFmt(curPriceOf(e)) : priceText(e, curPriceOf(e));
  const chg = document.createElement("span");
  chg.className = `mr-chg ${trendClass(pct)}`;
  chg.textContent = pctText(pct);
  b.append(sym, px, chg);
  if (spark) b.append(sparkline((e.metrics || {}).spark, trendClass(pct)));
  b.addEventListener("click", onClick);
  return b;
}

function renderDashMovers() {
  const res = topFlop(state.index.instruments, { cats, liquidOnly: true, limit: 5, priceOf: livePrice });
  const fill = (el, rows, empty) => {
    el.replaceChildren(...rows.map((r, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "rank-mini";
      const cls = trendClass(r.pct);
      const n = document.createElement("span");
      n.className = "rm-n";
      n.textContent = String(i + 1);
      const sym = document.createElement("span");
      sym.className = "mr-sym";
      const t = document.createElement("span");
      t.textContent = shortSym(r.e);
      t.title = r.e.name;
      sym.append(avatar(r.e), t);
      const chg = document.createElement("span");
      chg.className = `mr-chg ${cls}`;
      chg.textContent = pctText(r.pct);
      b.append(n, sym, sparkline(daySpark(r.e, r.price), cls), chg);
      b.addEventListener("click", () => openChart(r.e));
      return b;
    }));
    if (!rows.length) el.textContent = empty;
  };
  fill($("dash-top"), res.top, "Kein Wert im Plus.");
  fill($("dash-flop"), res.flop, "Kein Wert im Minus.");
}

// Kurz-Screener: setzt dieselben Filter wie der große Screener
function dashFilter() {
  const f = { ...EMPTY_FILTER, window: "24h" };
  const perf = $("ds-perf").value;
  if (perf !== "") { if (Number(perf) < 0) f.perfMax = Number(perf); else f.perfMin = Number(perf); }
  if ($("ds-vol").value !== "") f.turnoverMin = Number($("ds-vol").value);
  if ($("ds-rsi").value !== "") {
    const [lo, hi] = $("ds-rsi").value.split(",").map(Number);
    if (lo > 0) f.rsiMin = lo;
    if (hi < 100) f.rsiMax = hi;
  }
  return f;
}
function renderDashScreener() {
  const box = $("ds-cats");
  if (!box.dataset.built) {
    box.dataset.built = "1";
    for (const c of CATEGORIES) {
      const l = document.createElement("label");
      l.className = "check-line";
      l.innerHTML = `<input type="checkbox" value="${c.key}"> <span></span>`;
      l.querySelector("span").textContent = c.key === "krypto" ? "Krypto" : c.label;
      l.querySelector("input").addEventListener("change", (ev) => setCat(c.key, ev.target.checked));
      box.append(l);
    }
    for (const id of ["ds-perf", "ds-vol", "ds-rsi"]) $(id).addEventListener("change", updateDsCount);
    $("ds-go").addEventListener("click", () => {
      scr = dashFilter();
      try { localStorage.setItem(SCR_STORE, JSON.stringify(scr)); } catch {}
      if ($("screener-form").dataset.built) fillScreenerForm();
      navigate("screener");
    });
    $("ds-reset").addEventListener("click", () => {
      for (const id of ["ds-perf", "ds-vol", "ds-rsi"]) $(id).value = "";
      setCat("all", true);
    });
    $("ds-saved").addEventListener("change", (ev) => {
      const f = savedFilters()[ev.target.value];
      if (!f) return;
      scr = { ...EMPTY_FILTER, ...f };
      try { localStorage.setItem(SCR_STORE, JSON.stringify(scr)); } catch {}
      if ($("screener-form").dataset.built) fillScreenerForm();
      navigate("screener");
    });
  }
  for (const input of box.querySelectorAll("input")) input.checked = cats.has(input.value);
  const names = Object.keys(savedFilters()).sort((a, b) => a.localeCompare(b, "de"));
  const sel = $("ds-saved");
  sel.replaceChildren(...[names.length ? "Gespeicherte Filter" : "Keine gespeicherten Filter", ...names].map((n, i) => {
    const o = document.createElement("option");
    o.value = i === 0 ? "" : n;
    o.textContent = n;
    return o;
  }));
  sel.disabled = !names.length;
  updateDsCount();
}
function updateDsCount() {
  const { rows } = screen(state.index.instruments, dashFilter(), { cats, priceOf: livePrice });
  $("ds-go").textContent = `Suchen (${new Intl.NumberFormat("de-DE").format(rows.length)} Treffer)`;
}

function renderOverview() {
  const tabs = $("ov-tabs");
  if (!tabs.childElementCount) {
    for (const t of OV_TABS) {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.key = t.key;
      b.textContent = t.label;
      b.addEventListener("click", () => { dash.ov = t.key; saveDash(); renderOverview(); });
      tabs.append(b);
    }
    $("ov-all").addEventListener("click", () => {
      const t = OV_TABS.find((x) => x.key === dash.ov) || OV_TABS[0];
      setFilterKey(t.filter);
      navigate("markets");
    });
  }
  for (const b of tabs.children) b.setAttribute("aria-pressed", String(b.dataset.key === dash.ov));
  let items = state.index.instruments.filter((e) => e.kind === dash.ov);
  // am meisten gehandelte zuerst (Indizes in der Reihenfolge der Liste)
  if (dash.ov !== "index") items = items.slice().sort((a, b) => ((b.metrics || {}).avg_turnover_20d || 0) - ((a.metrics || {}).avg_turnover_20d || 0));
  $("ov-list").replaceChildren(...items.slice(0, 8).map((e) => miniRow(e, () => openChart(e), { spark: true })));
}

// ---------- Chart auf der Startseite ----------
function setDashAsset(id) {
  dash.id = id;
  saveDash();
  renderDashChart();
  if (window.innerWidth < 1100) document.querySelector(".dash-chart-card").scrollIntoView({ behavior: "smooth", block: "start" });
}
function dashEntry() { return byId(dash.id) || byId("BTC-EUR") || state.index.instruments[0]; }

async function renderDashChart() {
  const e = dashEntry();
  if (!e || typeof LightweightCharts === "undefined") return;
  const gen = ++dash.gen;
  // Kerzenlängen-Leiste
  const tf = $("dc-tf");
  tf.replaceChildren(...DC_VIEWS.map(([iv]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = VIEW_BY_KEY[iv].label;
    b.setAttribute("aria-pressed", String(iv === dash.iv));
    b.addEventListener("click", () => { dash.iv = iv; saveDash(); renderDashChart(); });
    return b;
  }));
  avatar(e, $("dc-avatar"));
  $("dc-name").textContent = `${e.name} (${shortSym(e)})`;
  paintDashQuote();
  if (!dash.chart) {
    const css = getComputedStyle(document.documentElement);
    const c = (v) => css.getPropertyValue(v).trim();
    dash.chart = LightweightCharts.createChart($("dash-chart"), {
      autoSize: true,
      layout: { background: { color: c("--panel") }, textColor: c("--mute"), fontFamily: "Inter, system-ui, sans-serif" },
      grid: { vertLines: { color: c("--grid") }, horzLines: { color: c("--grid") } },
      rightPriceScale: { borderColor: c("--line") },
      timeScale: { borderColor: c("--line"), rightOffset: 3, tickMarkFormatter: (t, type) => tickLabel(t, type) },
      crosshair: { mode: 1 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: false, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: true },
      localization: { locale: "de-DE", priceFormatter: (p) => numFmt(p * curFactor(unitOf(dashEntry()))), timeFormatter: (t) => candleLabel(t, dash.ivUsed || "1h") },
    });
    dash.candles = dash.chart.addCandlestickSeries({ upColor: c("--up"), downColor: c("--down"), borderVisible: false, wickUpColor: c("--up"), wickDownColor: c("--down") });
    dash.vol = dash.chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    dash.chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  }
  let data;
  try { data = await loadInstrument(e); } catch { return; }
  if (gen !== dash.gen) return;
  const want = (DC_VIEWS.find(([k]) => k === dash.iv) || DC_VIEWS[3])[1];
  const iv = (data.series || {})[want] && data.series[want].length ? want : "1d";
  dash.rows = mergeSameLocalTime(((data.series || {})[iv] || []).map((r) => r.slice()));
  dash.step = STEP[iv];
  dash.ivUsed = iv;
  dash.chart.applyOptions({ timeScale: { timeVisible: STEP[iv] < 86400 } });
  dash.last = null;
  drawDash(true);
}

function drawDash(fit = false) {
  if (!dash.candles) return;
  const css = getComputedStyle(document.documentElement);
  const up = css.getPropertyValue("--up-a").trim(), down = css.getPropertyValue("--down-a").trim();
  dash.candles.setData(dash.rows.map((r) => ({ time: shift(r[0]), open: r[1], high: r[2], low: r[3], close: r[4] })));
  dash.vol.setData(dash.rows.map((r) => ({ time: shift(r[0]), value: r[5], color: r[4] >= r[1] ? up : down })));
  if (fit && dash.rows.length) {
    // genau der gewählte Zeitraum (z. B. 1D = heute), sonst der letzte Zeitraum mit Daten
    const rows = dash.rows;
    const perf = VIEW_BY_KEY[dash.iv].perf;
    let end = rows[rows.length - 1][0] + dash.step;
    let start = periodStart(perf, end);
    const from = Math.max(rows[0][0], start);
    try { dash.chart.timeScale().setVisibleRange({ from: shift(from), to: shift(rows[rows.length - 1][0]) }); }
    catch { dash.chart.timeScale().fitContent(); }
  }
}

// Laufende Kerze wächst mit jedem Live-Kurs (nur während die Börse handelt)
function dashLive() {
  const e = dashEntry();
  if (!e || !dash.rows.length || !dash.step) return;
  const q = quoteOf(e.id);
  if (!q || !q.live || q === dash.last) return;
  dash.last = q;
  const now = q.time;
  const last = dash.rows[dash.rows.length - 1];
  if (now < last[0] + dash.step) {
    last[2] = Math.max(last[2], q.price); last[3] = Math.min(last[3], q.price); last[4] = q.price;
    dash.candles.update({ time: shift(last[0]), open: last[1], high: last[2], low: last[3], close: last[4] });
  } else if (dash.step <= 3600) {
    const t = Math.floor(now / dash.step) * dash.step;
    const row = [t, q.price, q.price, q.price, q.price, 0];
    dash.rows.push(row);
    dash.candles.update({ time: shift(t), open: q.price, high: q.price, low: q.price, close: q.price });
  }
}

function paintDashQuote() {
  const e = dashEntry();
  if (!e) return;
  const price = curPriceOf(e);
  $("dc-price").textContent = e.kind === "index" ? `${numFmt(price)} Punkte` : priceText(e, price);
  const p = dayPerf(e, livePrice(e));
  const ch = $("dc-change");
  ch.className = `dc-chg ${trendClass(p ? p.pct : null)}`;
  ch.textContent = p ? `${p.abs >= 0 ? "+" : "-"}${e.kind === "index" ? numFmt(Math.abs(p.abs)) : fmtPrice(Math.abs(p.abs), unitOf(e))} (${pctText(p.pct)}) heute` : "";
  paintBadge($("dc-status"), e);
}
$("dc-name").addEventListener("click", () => openChart(dashEntry()));
$("dc-open").addEventListener("click", () => openChart(dashEntry()));
for (const b of document.querySelectorAll("[data-go]")) {
  b.addEventListener("click", () => {
    if (b.dataset.filter) setFilterKey(b.dataset.filter);
    navigate(b.dataset.go);
  });
}

// Live-Kurse: Startseite höchstens alle 2 Sekunden neu zeichnen
onQuote(() => {
  liveBox.last = Date.now();
  if (dash.queued || !homeVisible()) return;
  dash.queued = true;
  setTimeout(() => {
    dash.queued = false;
    if (!homeVisible()) return;
    renderTicker();
    renderWatch();
    renderDashMovers();
    renderOverview();
    paintDashQuote();
    dashLive();
  }, 2000);
});

// ---------- Live-Anzeige in der Seitenleiste ----------
const liveBox = { last: null };
function paintLiveBox() {
  const on = rtWanted() && Boolean(listLive.timer) && !document.hidden;
  $("live-dot").classList.toggle("on", on);
  $("live-state").textContent = on ? "Aktiv" : "Pausiert";
  $("live-time").textContent = liveBox.last
    ? `Letzte Aktualisierung: ${new Intl.DateTimeFormat("de-DE", { timeStyle: "medium", timeZone: TZ }).format(new Date(liveBox.last))}`
    : "Noch kein Live-Kurs";
}
setInterval(paintLiveBox, 1000);

// =====================================================================
// Auflösung der Kerzen und Indikatoren im großen Chart
// =====================================================================
const IV_NAME = { "1s": "1 Sekunde", "5s": "5 Sekunden", "15s": "15 Sekunden", "1m": "1 Minute", "5m": "5 Minuten", "15m": "15 Minuten",
  "30m": "30 Minuten", "1h": "1 Stunde", "4h": "4 Stunden", "12h": "12 Stunden", "1d": "1 Tag", "1w": "1 Woche", "1M": "1 Monat" };
function paintResolution() {
  const iv = state.interval;
  const src = asset.src && asset.src[iv];
  const what = chartType === "line" ? "Linie aus Schlusskursen je" : "Kerzen je";
  const from = src && src.name && src.name.startsWith("ticks") ? ", gebaut aus Live-Kursen"
    : src && src.live ? ", live" : "";
  $("res-label").textContent = IV_NAME[iv] ? `${what} ${IV_NAME[iv]}${from}` : "";
}

const IND_STORE = "ta.ind.v1";
const indSel = (() => { try { return new Set(JSON.parse(localStorage.getItem(IND_STORE) || "[]")); } catch { return new Set(); } })();
const ind = { lines: new Map(), panes: new Map(), queued: false };

function buildIndPanel() {
  const panel = $("ind-panel");
  const group = (title, list) => {
    const box = document.createElement("fieldset");
    box.className = "ind-group";
    box.innerHTML = `<legend>${title}</legend>`;
    for (const d of list) {
      const l = document.createElement("label");
      l.className = "check-line";
      l.innerHTML = `<input type="checkbox" value="${d.key}"> <span class="ind-swatch"></span><span></span>`;
      l.querySelector("span:last-child").textContent = d.label;
      if (d.color) l.querySelector(".ind-swatch").style.background = d.color;
      else l.querySelector(".ind-swatch").remove();
      const cb = l.querySelector("input");
      cb.checked = indSel.has(d.key);
      cb.addEventListener("change", () => {
        if (cb.checked) indSel.add(d.key); else indSel.delete(d.key);
        try { localStorage.setItem(IND_STORE, JSON.stringify([...indSel])); } catch {}
        paintIndBtn();
        renderIndicators();
      });
      box.append(l);
    }
    return box;
  };
  panel.replaceChildren(group("Im Kurschart", OVERLAYS), group("Eigene Fenster unter dem Chart", PANES));
  paintIndBtn();
}
function paintIndBtn() {
  $("ind-btn").textContent = indSel.size ? `Indikatoren (${indSel.size})` : "Indikatoren";
}
$("ind-btn").addEventListener("click", () => {
  const open = $("ind-panel").hidden;
  $("ind-panel").hidden = !open;
  $("ind-btn").setAttribute("aria-expanded", String(open));
});
buildIndPanel();

function scheduleIndicators() {
  if (ind.queued || !indSel.size) return;
  ind.queued = true;
  setTimeout(() => { ind.queued = false; renderIndicators(); }, 1000);
}

const dayKeyLocal = (t) => Math.floor(shift(t) / 86400);
const asPoints = (rows, values) => rows.map((r, i) => (values[i] == null ? { time: shift(r[0]) } : { time: shift(r[0]), value: values[i] }));

function renderIndicators() {
  if (!state.chart) return;
  const rows = state.rows || [];
  const wanted = new Map();
  for (const o of OVERLAYS) {
    if (!indSel.has(o.key)) continue;
    for (const line of overlayLines(o.key, rows, dayKeyLocal)) wanted.set(`${o.key}:${line.name}`, line);
  }
  for (const [k, series] of ind.lines) {
    if (!wanted.has(k)) { try { state.chart.removeSeries(series); } catch {} ind.lines.delete(k); }
  }
  for (const [k, line] of wanted) {
    let series = ind.lines.get(k);
    if (!series) {
      series = state.chart.addLineSeries({ color: line.color, lineWidth: 1, lineStyle: line.dashed ? 2 : 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      ind.lines.set(k, series);
    }
    series.setData(asPoints(rows, line.values));
  }
  for (const [k, p] of ind.panes) {
    if (!indSel.has(k)) { p.chart.remove(); p.box.remove(); ind.panes.delete(k); }
  }
  for (const def of PANES) {
    if (!indSel.has(def.key)) continue;
    const p = ind.panes.get(def.key) || makePane(def);
    fillPane(def.key, p, rows);
  }
  syncPanes();
}

function makePane(def) {
  const css = getComputedStyle(document.documentElement);
  const c = (n) => css.getPropertyValue(n).trim();
  const box = document.createElement("div");
  box.className = "pane";
  box.innerHTML = `<div class="pane-title"><span></span><span class="pane-val"></span></div><div class="pane-chart"></div>`;
  box.querySelector("span").textContent = def.label;
  $("panes").append(box);
  const chart = LightweightCharts.createChart(box.querySelector(".pane-chart"), {
    autoSize: true,
    layout: { background: { color: c("--panel") }, textColor: c("--mute"), fontFamily: "Inter, system-ui, sans-serif" },
    grid: { vertLines: { color: c("--grid") }, horzLines: { color: c("--grid") } },
    rightPriceScale: { borderColor: c("--line"), minimumWidth: 72 },
    timeScale: { visible: false, rightOffset: 3 },
    crosshair: { mode: 1 },
    handleScroll: false,
    handleScale: false,
    localization: { locale: "de-DE", priceFormatter: (v) => new Intl.NumberFormat("de-DE", { maximumFractionDigits: Math.abs(v) >= 100 ? 1 : 3 }).format(v) },
  });
  const p = { box, chart, series: {} };
  const line = (color, extra = {}) => chart.addLineSeries({ color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true, ...extra });
  if (def.key === "rsi") {
    p.series.rsi = line("#a855f7");
    p.series.rsi.createPriceLine({ price: 70, color: "rgba(234,57,67,.6)", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "" });
    p.series.rsi.createPriceLine({ price: 30, color: "rgba(22,199,132,.6)", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "" });
  } else if (def.key === "macd") {
    p.series.hist = chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
    p.series.line = line("#3b82f6");
    p.series.signal = line("#f5b041");
  } else if (def.key === "atr") {
    p.series.atr = line("#14b8a6");
  } else if (def.key === "adx") {
    p.series.adx = line("#e2e8f0");
    p.series.pdi = line("#16c784", { lineWidth: 1, lastValueVisible: false });
    p.series.mdi = line("#ea3943", { lineWidth: 1, lastValueVisible: false });
  }
  ind.panes.set(def.key, p);
  return p;
}

function fillPane(key, p, rows) {
  const fmt = (v) => (v == null ? "–" : new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 }).format(v));
  const last = (arr) => arr[arr.length - 1];
  let text = "";
  if (key === "rsi") {
    const v = rsiCalc(rows);
    p.series.rsi.setData(asPoints(rows, v));
    text = fmt(last(v));
  } else if (key === "macd") {
    const m = macdCalc(rows);
    p.series.hist.setData(rows.map((r, i) => (m.hist[i] == null ? { time: shift(r[0]) }
      : { time: shift(r[0]), value: m.hist[i], color: m.hist[i] >= 0 ? "rgba(22,199,132,.55)" : "rgba(234,57,67,.55)" })));
    p.series.line.setData(asPoints(rows, m.line));
    p.series.signal.setData(asPoints(rows, m.signal));
    text = `MACD ${fmt(last(m.line))}  Signal ${fmt(last(m.signal))}  Hist. ${fmt(last(m.hist))}`;
  } else if (key === "atr") {
    const v = atrCalc(rows);
    p.series.atr.setData(asPoints(rows, v));
    text = fmt(last(v));
  } else if (key === "adx") {
    const a = adxCalc(rows);
    p.series.adx.setData(asPoints(rows, a.adx));
    p.series.pdi.setData(asPoints(rows, a.pdi));
    p.series.mdi.setData(asPoints(rows, a.mdi));
    text = `ADX ${fmt(last(a.adx))}  +DI ${fmt(last(a.pdi))}  −DI ${fmt(last(a.mdi))}`;
  }
  if (rows.length && text === "–") text = "zu wenig Kerzen";
  p.box.querySelector(".pane-val").textContent = text;
}

function syncPanes() {
  if (!state.chart || !ind.panes.size) return;
  const r = state.chart.timeScale().getVisibleLogicalRange();
  if (r) for (const p of ind.panes.values()) p.chart.timeScale().setVisibleLogicalRange(r);
}
let paneSyncHooked = false;
function hookPaneSync() {
  if (paneSyncHooked || !state.chart) return;
  paneSyncHooked = true;
  state.chart.timeScale().subscribeVisibleLogicalRangeChange(() => syncPanes());
}

// ---------- Tageschart-Vorschau ----------
/** Verlauf seit Tagesbeginn; mit Live-Kurs wird der letzte Punkt ersetzt. */
function daySpark(e, price = null) {
  const m = e.metrics || {};
  const base = (m.spark_day && m.spark_day.length >= 3 ? m.spark_day : m.spark) || [];
  if (price == null || !base.length) return base;
  return [...base.slice(0, -1), price];
}

// ---------- Optionen ein- und ausklappen ----------
const OPTS_STORE = "ta.opts.open.v1";
function paintOptsSummary() {
  const off = CATEGORIES.filter((c) => !cats.has(c.key)).length;
  const parts = [];
  if (off) parts.push(`${off} ${off === 1 ? "Kategorie" : "Kategorien"} ausgeblendet`);
  if (!rtWanted()) parts.push("Echtzeit aus");
  if (currency === "$") parts.push("Anzeige in $");
  if (!$("movers-liquid").checked) parts.push("auch wenig gehandelte Werte");
  $("opts-sum").textContent = parts.join(", ");
}
function setOptsOpen(open) {
  $("opts-panel").hidden = !open;
  $("opts-btn").setAttribute("aria-expanded", String(open));
  try { localStorage.setItem(OPTS_STORE, open ? "1" : "0"); } catch {}
}
$("opts-btn").addEventListener("click", () => setOptsOpen($("opts-panel").hidden));
setOptsOpen((() => { try { return localStorage.getItem(OPTS_STORE) === "1"; } catch { return false; } })());
for (const id of ["rt-list-switch", "movers-liquid"]) $(id).addEventListener("change", paintOptsSummary);
document.querySelectorAll(".cur-toggle button").forEach((b) => b.addEventListener("click", () => setTimeout(paintOptsSummary)));
setInterval(paintOptsSummary, 2000);
paintOptsSummary();

// =====================================================================
// Einstellungen
// =====================================================================
const THEME_STORE = "ta.theme.v1";
const VENUE_STORE = "ta.venue.v1";
function venuePref() { try { return localStorage.getItem(VENUE_STORE) === "home" ? "home" : "tradegate"; } catch { return "tradegate"; } }

/** Einstellung „Heimatbörse“: Listenkurse von Yahoo (Heimatbörse) statt Tradegate. */
function applyVenue(index) {
  if (!index || venuePref() !== "home") return index;
  for (const e of index.instruments) {
    if (e.last_source !== "tradegate" || e.last_home == null) continue;
    e.last = e.last_home;
    e.last_ts = e.last_home_ts;
    e.last_source = "yahoo";
    e.change_1d = e.prev_close ? e.last / e.prev_close - 1 : null;
  }
  return index;
}

function applyTheme() {
  let t = "dark";
  try { t = localStorage.getItem(THEME_STORE) || "dark"; } catch {}
  const light = t === "light" || (t === "auto" && window.matchMedia("(prefers-color-scheme: light)").matches);
  document.documentElement.dataset.theme = light ? "light" : "dark";
  document.querySelector('meta[name="theme-color"]').setAttribute("content", light ? "#f5f7fb" : "#07090d");
  for (const b of document.querySelectorAll("#set-theme button")) b.setAttribute("aria-pressed", String(b.dataset.v === t));
}
applyTheme();
window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", applyTheme);

function renderSettings() {
  const sel = $("set-view");
  if (!sel.childElementCount) {
    for (const v of VIEWS) { const o = document.createElement("option"); o.value = v.key; o.textContent = v.label; sel.append(o); }
    sel.addEventListener("change", () => { viewKey = sel.value; try { localStorage.setItem(VIEW_STORE, sel.value); } catch {} note("Gespeichert. Gilt beim nächsten Öffnen eines Charts."); });
    $("set-venue").addEventListener("change", (ev) => {
      try { localStorage.setItem(VENUE_STORE, ev.target.value); } catch {}
      note("Gespeichert. Die Kurse werden neu geladen …");
      setTimeout(() => location.reload(), 600);
    });
    for (const b of document.querySelectorAll("#set-theme button")) {
      b.addEventListener("click", () => {
        try { localStorage.setItem(THEME_STORE, b.dataset.v); } catch {}
        applyTheme();
        note("Gespeichert. Die Charts übernehmen die Farben beim nächsten Öffnen.");
      });
    }
  }
  sel.value = viewKey;
  $("set-venue").value = venuePref();
  paintCurrency();
}
function note(text) { $("set-note").textContent = text; }
$("gear").addEventListener("click", () => navigate("status"));

// =====================================================================
// Portfolio: Positionen nur auf diesem Gerät, verschlüsselt mit dem App-Schlüssel
// =====================================================================
const PF_STORE = "ta.portfolio.v1";
const pf = { data: { positions: [], cash: 0 }, loaded: false, chart: null, series: null, queued: false };

async function loadPortfolio() {
  if (pf.loaded) return;
  pf.loaded = true;
  try {
    const box = JSON.parse(localStorage.getItem(PF_STORE) || "null");
    if (!box) return;
    const text = box.plain ? box.plain : state.key ? await openText(state.key, box) : null;
    if (text) pf.data = { positions: [], cash: 0, ...JSON.parse(text) };
  } catch {
    $("pf-chart-note").textContent = "Das gespeicherte Portfolio konnte nicht geöffnet werden (anderes Passwort?).";
  }
}
async function savePortfolio() {
  const text = JSON.stringify(pf.data);
  const box = state.key ? await sealText(state.key, text) : { plain: text };
  try { localStorage.setItem(PF_STORE, JSON.stringify(box)); } catch {}
}

const findEntry = (q) => {
  if (!q || !state.index) return null;
  const n = norm(q);
  const all = state.index.instruments;
  return all.find((e) => e.isin && e.isin.toLowerCase() === q.trim().toLowerCase())
    || all.find((e) => e.wkn && e.wkn.toLowerCase() === q.trim().toLowerCase())
    || all.find((e) => norm(`${e.name} (${e.ticker || e.id})`) === n)
    || all.find((e) => norm(e.id) === n || norm(e.ticker || "") === n)
    || all.find((e) => norm(e.name) === n) || null;
};

function positionValue(p) {
  const e = p.id ? byId(p.id) : null;
  const price = e ? curPriceOf(e) : null;
  const day = e ? dayPerf(e, livePrice(e)) : null;
  return { e, price, value: price != null ? price * p.qty : null, dayAbs: day ? day.abs * p.qty : null, dayPct: day ? day.pct : null,
    cost: p.buy ? p.buy * p.qty : null };
}

async function renderPortfolio() {
  await loadPortfolio();
  if (!state.index) return;
  // Auswahlliste für „Position hinzufügen“
  const dl = $("pf-assets");
  if (!dl.childElementCount) {
    dl.append(...state.index.instruments.filter((e) => e.kind !== "index" && e.kind !== "devisen").map((e) => {
      const o = document.createElement("option");
      o.value = `${e.name} (${e.ticker || e.id})`;
      return o;
    }));
  }
  $("pf-cash").value = pf.data.cash || "";
  const rows = pf.data.positions.map((p) => ({ p, ...positionValue(p) }));
  const cash = Number(pf.data.cash) || 0;
  const total = rows.reduce((a, r) => a + (r.value || 0), 0) + cash;
  const dayAbs = rows.reduce((a, r) => a + (r.dayAbs || 0), 0);
  const prevTotal = total - dayAbs;
  const costRows = rows.filter((r) => r.cost != null && r.value != null);
  const cost = costRows.reduce((a, r) => a + r.cost, 0);
  const gain = costRows.reduce((a, r) => a + r.value, 0) - cost;
  const money = (v) => fmtPrice(v, "€");
  const signed = (v) => `${v >= 0 ? "+" : "-"}${money(Math.abs(v))}`;
  $("pf-total").textContent = rows.length || cash ? money(total) : "–";
  $("pf-total-sub").textContent = rows.length ? `${rows.length} ${rows.length === 1 ? "Position" : "Positionen"}${cash ? ", davon " + money(cash) + " Bargeld" : ""}` : "Noch keine Positionen";
  $("pf-day").textContent = rows.length ? pctText(prevTotal ? dayAbs / prevTotal : null) : "–";
  $("pf-day").className = `kpi-value ${trendClass(dayAbs)}`;
  $("pf-day-sub").textContent = rows.length ? `${signed(dayAbs)} seit Vortagesschluss` : "";
  $("pf-gain").textContent = costRows.length ? signed(gain) : "–";
  $("pf-gain").className = `kpi-value ${trendClass(gain)}`;
  $("pf-gain-sub").textContent = costRows.length ? `${pctText(cost ? gain / cost : null)} gegenüber Kaufkurs${costRows.length < rows.length ? " (nur Positionen mit Kaufkurs)" : ""}` : "Kaufkurse eintragen, um den Gewinn zu sehen";

  // Tabelle
  const box = $("pf-table");
  if (!rows.length) {
    box.innerHTML = `<p class="small">Noch keine Positionen. Tippe auf „Position hinzufügen“ oder übernimm eine CSV-Datei aus deinem Depot.</p>`;
  } else {
    const tbl = document.createElement("table");
    tbl.className = "scr-table pf-tbl";
    tbl.innerHTML = "<thead><tr><th>Wert</th><th>Stück</th><th>Kurs</th><th>Wert</th><th>Heute</th><th>G/V</th><th>Anteil</th><th></th></tr></thead>";
    const body = document.createElement("tbody");
    rows.sort((a, b) => (b.value || 0) - (a.value || 0));
    for (const r of rows) {
      const tr = document.createElement("tr");
      const g = r.cost != null && r.value != null ? r.value - r.cost : null;
      const cells = [
        r.e ? r.e.name : `${r.p.name || r.p.isin} (nicht in der App)`,
        new Intl.NumberFormat("de-DE", { maximumFractionDigits: 6 }).format(r.p.qty),
        r.price == null ? "–" : priceText(r.e, r.price),
        r.value == null ? "–" : money(r.value),
        pctText(r.dayPct),
        g == null ? "–" : `${signed(g)} (${pctText(r.cost ? g / r.cost : null)})`,
        r.value == null || !total ? "–" : pctText(r.value / total).replace("+", ""),
      ];
      cells.forEach((c, i) => {
        const td = document.createElement("td");
        td.textContent = c;
        if (i === 4) td.className = trendClass(r.dayPct);
        if (i === 5) td.className = trendClass(g);
        tr.append(td);
      });
      const td = document.createElement("td");
      const del = document.createElement("button");
      del.type = "button";
      del.className = "row-del";
      del.textContent = "Entfernen";
      del.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        pf.data.positions = pf.data.positions.filter((x) => x !== r.p);
        await savePortfolio();
        renderPortfolio();
      });
      td.append(del);
      tr.append(td);
      if (r.e) tr.addEventListener("click", () => openChart(r.e));
      body.append(tr);
    }
    tbl.append(body);
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    wrap.append(tbl);
    box.replaceChildren(wrap);
  }
  renderDonut(rows, cash, total);
  renderPfHistory(rows, cash);
  ensureAllLive();
}

function renderDonut(rows, cash, total) {
  const groups = new Map();
  const label = { aktie: "Aktien", etf: "ETFs", krypto: "Krypto", rohstoff: "Rohstoffe" };
  for (const r of rows) {
    if (r.value == null) continue;
    const k = r.e ? label[r.e.kind] || "Sonstige" : "Sonstige";
    groups.set(k, (groups.get(k) || 0) + r.value);
  }
  if (cash) groups.set("Bargeld", cash);
  const colors = { Aktien: "#3b82f6", ETFs: "#14b8a6", Krypto: "#f5b041", Rohstoffe: "#a855f7", Bargeld: "#8691a3", Sonstige: "#475569" };
  const el = $("pf-donut");
  if (!total) { el.innerHTML = '<p class="small">Noch nichts zu verteilen.</p>'; return; }
  let acc = 0;
  const R = 42, C = 2 * Math.PI * R;
  const arcs = [...groups].map(([k, v]) => {
    const frac = v / total;
    const seg = `<circle r="${R}" cx="60" cy="60" fill="none" stroke="${colors[k]}" stroke-width="16" stroke-dasharray="${frac * C} ${C}" stroke-dashoffset="${-acc * C}" transform="rotate(-90 60 60)"/>`;
    acc += frac;
    return seg;
  }).join("");
  const legend = [...groups].map(([k, v]) => `<li><span class="dot" style="background:${colors[k]}"></span>${k}<b>${pctText(v / total).replace("+", "")}</b></li>`).join("");
  el.innerHTML = `<svg viewBox="0 0 120 120" class="donut" aria-hidden="true">${arcs}</svg><ul class="donut-legend">${legend}</ul>`;
}

// Wertentwicklung: Tagesschlusskurse × heutige Stückzahlen (Käufe/Verkäufe der letzten 30 Tage sind nicht bekannt)
async function renderPfHistory(rows, cash) {
  const key = JSON.stringify([rows.map((r) => [r.p.id, r.p.qty]), cash]);
  if (key === pf.histKey && Date.now() - (pf.histAt || 0) < 60_000) return;
  pf.histKey = key;
  pf.histAt = Date.now();
  const withData = rows.filter((r) => r.e);
  if (!withData.length) { $("pf-chart-note").textContent = ""; if (pf.series) pf.series.setData([]); return; }
  const since = Date.now() / 1000 - 31 * 86400;
  const byDay = new Map();
  for (const r of withData) {
    let data;
    try { data = await loadInstrument(r.e); } catch { continue; }
    const daily = ((data.series || {})["1d"] || []).filter((x) => x[0] >= since);
    for (const x of daily) {
      const d = Math.floor(shift(x[0]) / 86400) * 86400;
      const m = byDay.get(d) || new Map();
      m.set(r.e.id, x[4] * r.p.qty);
      byDay.set(d, m);
    }
  }
  // fehlende Tage eines Werts (z. B. Wochenende bei Aktien) mit dem letzten bekannten Wert füllen
  const days = [...byDay.keys()].sort((a, b) => a - b);
  const lastVal = new Map();
  const points = [];
  for (const d of days) {
    for (const [id, v] of byDay.get(d)) lastVal.set(id, v);
    if (lastVal.size < withData.length) continue;
    points.push({ time: d, value: [...lastVal.values()].reduce((a, b) => a + b, 0) + cash });
  }
  if (typeof LightweightCharts === "undefined") return;
  if (!pf.chart) {
    const css = getComputedStyle(document.documentElement);
    const c = (n) => css.getPropertyValue(n).trim();
    pf.chart = LightweightCharts.createChart($("pf-chart"), {
      autoSize: true,
      layout: { background: { color: c("--panel") }, textColor: c("--mute"), fontFamily: "Inter, system-ui, sans-serif" },
      grid: { vertLines: { visible: false }, horzLines: { color: c("--grid") } },
      rightPriceScale: { borderColor: c("--line") },
      timeScale: { borderColor: c("--line") },
      handleScroll: false, handleScale: false,
      localization: { locale: "de-DE", priceFormatter: (p) => numFmt(p * curFactor("€")) },
    });
    pf.series = pf.chart.addAreaSeries({ lineColor: "#16c784", topColor: "rgba(22,199,132,.25)", bottomColor: "rgba(22,199,132,0)", lineWidth: 2, priceLineVisible: false });
  }
  pf.series.setData(points);
  pf.chart.timeScale().fitContent();
  $("pf-chart-note").textContent = points.length ? "Berechnet mit deinen heutigen Stückzahlen und den Tagesschlusskursen; Käufe und Verkäufe im Zeitraum sind nicht berücksichtigt." : "Für diese Werte liegen noch keine Tageskurse vor.";
}

// Hinzufügen
$("pf-add-btn").addEventListener("click", () => { $("pf-form").hidden = !$("pf-form").hidden; if (!$("pf-form").hidden) $("pf-asset").focus(); });
$("pf-cancel").addEventListener("click", () => { $("pf-form").hidden = true; });
$("pf-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const e = findEntry($("pf-asset").value);
  if (!e) { $("pf-form-msg").textContent = "Diesen Wert gibt es in der App nicht. Wähle einen Eintrag aus der Liste oder gib die ISIN ein."; return; }
  const qty = Number($("pf-qty").value);
  if (!(qty > 0)) { $("pf-form-msg").textContent = "Bitte eine Stückzahl größer als 0 eintragen."; return; }
  const buy = $("pf-buy").value === "" ? null : Number($("pf-buy").value);
  pf.data.positions.push({ id: e.id, isin: e.isin || null, name: e.name, qty, buy, date: $("pf-date").value || null });
  await savePortfolio();
  ev.target.reset();
  $("pf-form-msg").textContent = `${e.name} hinzugefügt.`;
  renderPortfolio();
});
$("pf-cash").addEventListener("change", async (ev) => {
  pf.data.cash = Number(ev.target.value) || 0;
  await savePortfolio();
  renderPortfolio();
});

/** CSV einlesen: Spalten werden an den Überschriften erkannt (ISIN, Stück/Anzahl/Menge, Kaufkurs/Einstand). */
function parseCsv(text) {
  const lines = text.replace(/^﻿/, "").split(/\r?\n/).filter((l) => l.trim());
  const sepOf = (l) => (l.split(";").length >= l.split(",").length ? ";" : ",");
  const num = (v) => {
    if (v == null) return null;
    let t = String(v).replace(/["€\s]/g, "");
    if (/,\d{1,6}$/.test(t)) t = t.replace(/\./g, "").replace(",", ".");
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  };
  let headerIdx = lines.findIndex((l) => /isin/i.test(l));
  if (headerIdx < 0) return { error: "Keine Spalte „ISIN“ gefunden." };
  const sep = sepOf(lines[headerIdx]);
  const head = lines[headerIdx].split(sep).map((h) => h.replace(/"/g, "").trim().toLowerCase());
  const col = (re) => head.findIndex((h) => re.test(h));
  const ci = col(/isin/), cq = col(/st(ü|ue)ck|anzahl|menge|quantit|nominal|bestand/), cb = col(/kaufkurs|einstand|durchschnitt|avg|purchase/), cn = col(/name|bezeichnung|wertpapier/);
  if (cq < 0) return { error: "Keine Spalte für die Stückzahl gefunden (z. B. „Stück“ oder „Anzahl“)." };
  const out = [];
  for (const l of lines.slice(headerIdx + 1)) {
    const f = l.split(sep).map((x) => x.replace(/"/g, "").trim());
    const isin = (f[ci] || "").toUpperCase();
    if (!/^[A-Z]{2}[A-Z0-9]{9}\d$/.test(isin)) continue;
    const qty = num(f[cq]);
    if (!(qty > 0)) continue;
    out.push({ isin, qty, buy: cb >= 0 ? num(f[cb]) : null, name: cn >= 0 ? f[cn] : null });
  }
  return { rows: out };
}
$("pf-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const buf = await file.arrayBuffer();
  let text = new TextDecoder("utf-8").decode(buf);
  if (text.includes("�")) text = new TextDecoder("windows-1252").decode(buf);
  const res = parseCsv(text);
  if (res.error) { $("pf-import-msg").textContent = res.error; return; }
  if ($("pf-replace").checked) pf.data.positions = [];
  let known = 0;
  for (const r of res.rows) {
    const e = state.index.instruments.find((x) => x.isin === r.isin);
    if (e) known += 1;
    pf.data.positions.push({ id: e ? e.id : null, isin: r.isin, name: e ? e.name : r.name, qty: r.qty, buy: r.buy, date: null });
  }
  await savePortfolio();
  $("pf-import-msg").textContent = `${res.rows.length} Positionen übernommen, davon ${known} mit Live-Kurs in der App.`;
  ev.target.value = "";
  renderPortfolio();
});
// Live-Kurse: Portfolio höchstens alle 3 Sekunden neu bewerten
onQuote(() => {
  if (pf.queued || $("view-portfolio").hidden) return;
  pf.queued = true;
  setTimeout(() => { pf.queued = false; if (!$("view-portfolio").hidden) renderPortfolio(); }, 3000);
});

$("sort-sel").value = sortKey;
$("sort-sel").addEventListener("change", (ev) => {
  sortKey = ev.target.value;
  try { localStorage.setItem(SORT_STORE, sortKey); } catch {}
  renderList();
});
