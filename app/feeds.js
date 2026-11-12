// Live-Marktdaten für Kryptowährungen, direkt vom Handy aus.
// Erste Wahl: Binance (Kerzen und 24-Stunden-Ticker per WebSocket, Historie per REST).
// Ersatz:     Kraken WebSocket v2, falls Binance nicht erreichbar ist.
// Bricht die Verbindung ab, wird automatisch neu verbunden; danach meldet der
// Feed onResync(), damit die App die Lücke per REST nachlädt.

const BINANCE_REST = "https://api.binance.com/api/v3/klines";
// Port 443 statt 9443: wird von Mobilfunknetzen nicht blockiert
const BINANCE_WS = "wss://stream.binance.com:443/stream?streams=";
const KRAKEN_WS = "wss://ws.kraken.com/v2";
const KRAKEN_MINUTES = { "1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080 };
const BACKOFF = [1, 2, 5, 10, 20, 30];

/** Historische Kerzen von Binance: [[start, o, h, l, c, v], …] */
export async function binanceKlines(symbol, interval, limit = 1000, signal) {
  const url = `${BINANCE_REST}?symbol=${symbol}&interval=${interval}&limit=${limit}`;
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`Binance ${res.status}`);
  const data = await res.json();
  return data.map((k) => [Math.floor(k[0] / 1000), +k[1], +k[2], +k[3], +k[4], +k[5]]);
}

/** Neue Kerzen in eine Reihe einsortieren: gleiche Startzeit ersetzen, keine Doppelten. */
export function mergeRows(rows, incoming) {
  if (!incoming.length) return rows;
  const map = new Map(rows.map((r) => [r[0], r]));
  for (const r of incoming) map.set(r[0], r);
  return [...map.values()].sort((a, b) => a[0] - b[0]);
}

export class CryptoFeed {
  /**
   * @param {object} o
   *   binance: "BTCEUR", kraken: "BTC/EUR", intervals: ["1m", …]
   *   onKline(interval, row, closed), onTicker({price, open, high, low, volume, time}),
   *   onStatus(text, source), onResync()
   */
  constructor(o) {
    this.o = o;
    this.intervals = new Set(o.intervals);
    this.source = o.binance ? "binance" : "kraken";
    this.ws = null;
    this.stopped = false;
    this.failures = 0;
    this.received = false;
    this.connectedOnce = false;
    this.timer = null;
    this.watchdog = null;
  }

  start() { this.stopped = false; this.connect(); }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    clearInterval(this.watchdog);
    if (this.ws) { this.ws.onclose = null; try { this.ws.close(); } catch {} }
    this.ws = null;
  }

  setIntervals(list) {
    const next = new Set(list);
    const same = next.size === this.intervals.size && [...next].every((i) => this.intervals.has(i));
    if (same) return;
    this.intervals = next;
    if (!this.stopped) { this.reopen(); }
  }

  reopen() {
    if (this.ws) { this.ws.onclose = null; try { this.ws.close(); } catch {} }
    this.connect();
  }

  status(text) { this.o.onStatus && this.o.onStatus(text, this.source); }

  connect() {
    clearTimeout(this.timer);
    clearInterval(this.watchdog);
    this.lastMessage = Date.now();
    try {
      this.ws = this.source === "binance" ? this.openBinance() : this.openKraken();
    } catch {
      this.scheduleReconnect();
      return;
    }
    // Kommt 45 Sekunden lang nichts an, gilt die Verbindung als tot
    this.watchdog = setInterval(() => {
      if (Date.now() - this.lastMessage > 45_000 && this.ws) { try { this.ws.close(); } catch {} }
    }, 10_000);
  }

  scheduleReconnect() {
    if (this.stopped) return;
    this.failures += 1;
    // Binance mehrfach ohne jede Nachricht gescheitert: auf Kraken wechseln
    if (this.source === "binance" && !this.received && this.failures >= 3 && this.o.kraken) {
      this.source = "kraken";
      this.failures = 0;
    }
    const wait = BACKOFF[Math.min(this.failures - 1, BACKOFF.length - 1)] * 1000;
    this.status(`Verbindung unterbrochen, neuer Versuch in ${wait / 1000} s`);
    this.timer = setTimeout(() => this.connect(), wait);
  }

  onOpen() {
    this.failures = 0;
    this.status("live");
    if (this.connectedOnce && this.o.onResync) this.o.onResync();
    this.connectedOnce = true;
  }

  onClose() {
    clearInterval(this.watchdog);
    if (!this.stopped) this.scheduleReconnect();
  }

  // ---------- Binance ----------
  openBinance() {
    const s = this.o.binance.toLowerCase();
    const streams = [...this.intervals].map((i) => `${s}@kline_${i}`);
    streams.push(`${s}@ticker`);
    const ws = new WebSocket(BINANCE_WS + streams.join("/"));
    ws.onopen = () => this.onOpen();
    ws.onclose = () => this.onClose();
    ws.onerror = () => {};
    ws.onmessage = (msg) => {
      this.lastMessage = Date.now();
      this.received = true;
      let d;
      try { d = JSON.parse(msg.data).data; } catch { return; }
      if (!d) return;
      if (d.e === "kline") {
        const k = d.k;
        this.o.onKline && this.o.onKline(k.i, [Math.floor(k.t / 1000), +k.o, +k.h, +k.l, +k.c, +k.v], k.x);
      } else if (d.e === "24hrTicker") {
        this.o.onTicker && this.o.onTicker({ price: +d.c, open: +d.o, high: +d.h, low: +d.l, volume: +d.v, time: d.E / 1000 });
      }
    };
    return ws;
  }

  // ---------- Kraken (Ersatz) ----------
  openKraken() {
    const ws = new WebSocket(KRAKEN_WS);
    const pair = this.o.kraken;
    ws.onopen = () => {
      ws.send(JSON.stringify({ method: "subscribe", params: { channel: "ticker", symbol: [pair] } }));
      for (const i of this.intervals) {
        if (KRAKEN_MINUTES[i]) ws.send(JSON.stringify({ method: "subscribe", params: { channel: "ohlc", symbol: [pair], interval: KRAKEN_MINUTES[i] } }));
      }
      this.onOpen();
    };
    ws.onclose = () => this.onClose();
    ws.onerror = () => {};
    const byMinutes = Object.fromEntries(Object.entries(KRAKEN_MINUTES).map(([k, v]) => [v, k]));
    ws.onmessage = (msg) => {
      this.lastMessage = Date.now();
      let m;
      try { m = JSON.parse(msg.data); } catch { return; }
      if (!m || !Array.isArray(m.data)) return;
      this.received = true;
      for (const d of m.data) {
        if (m.channel === "ticker" && d.last != null) {
          this.o.onTicker && this.o.onTicker({ price: +d.last, open: null, high: +d.high, low: +d.low, volume: +d.volume, time: Date.now() / 1000 });
        } else if (m.channel === "ohlc" && d.interval_begin) {
          const iv = byMinutes[d.interval];
          if (iv) this.o.onKline && this.o.onKline(iv, [Math.floor(Date.parse(d.interval_begin) / 1000), +d.open, +d.high, +d.low, +d.close, +d.volume], false);
        }
      }
    };
    return ws;
  }
}
