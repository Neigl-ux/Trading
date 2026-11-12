// Live-Kurse von US-Börsen über den Finnhub-WebSocket (kostenloser Tarif: bis zu 50 Werte gleichzeitig).
// Der Schlüssel kommt vom Gerät des Nutzers und steht nie im Code oder im Repository.

export const MAX_SYMBOLS = 50;

// US-Haupthandel 9:30–16:00 Uhr New York, werktags
export function usMarketOpen(date = new Date()) {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(date).map((x) => [x.type, x.value]),
  );
  if (p.weekday === "Sat" || p.weekday === "Sun") return false;
  const m = Number(p.hour) * 60 + Number(p.minute);
  return m >= 9 * 60 + 30 && m < 16 * 60;
}

/**
 * Ein gemeinsamer Strom für Liste und Chart.
 * want(set) legt fest, welche Symbole gerade gebraucht werden; der Strom meldet sich
 * selbst an und ab und verbindet nach Abbrüchen neu.
 */
export class FinnhubStream {
  constructor({ token, onTrade, onStatus, url = "wss://ws.finnhub.io" }) {
    this.token = token;
    this.onTrade = onTrade;
    this.onStatus = onStatus || (() => {});
    this.url = url;
    this.wanted = new Set();
    this.subscribed = new Set();
    this.ws = null;
    this.retry = 0;
    this.failedBeforeOpen = 0;
    this.timer = null;
    this.stopped = false;
  }

  want(symbols) {
    this.wanted = new Set([...symbols].slice(0, MAX_SYMBOLS));
    if (this.stopped) return;
    if (!this.wanted.size) return this.close();
    if (!this.ws) return this.connect();
    if (this.ws.readyState === WebSocket.OPEN) this.sync();
  }

  connect() {
    clearTimeout(this.timer);
    let ws;
    try {
      ws = new WebSocket(`${this.url}?token=${encodeURIComponent(this.token)}`);
    } catch {
      return this.schedule();
    }
    this.ws = ws;
    this.subscribed = new Set();
    let opened = false;
    ws.onopen = () => {
      opened = true;
      this.retry = 0;
      this.failedBeforeOpen = 0;
      this.onStatus("live");
      this.sync();
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "trade" && Array.isArray(msg.data)) {
        // pro Symbol nur den jüngsten Handel weitergeben
        const last = new Map();
        for (const t of msg.data) {
          const prev = last.get(t.s);
          if (!prev || t.t >= prev.t) last.set(t.s, t);
        }
        for (const t of last.values()) this.onTrade(t.s, Number(t.p), t.t / 1000);
      } else if (msg.type === "error") {
        this.onStatus("error", msg.msg || "Finnhub meldet einen Fehler.");
      }
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (!opened) this.failedBeforeOpen += 1;
      if (this.stopped || !this.wanted.size) return;
      // Mehrfach keine Verbindung: meist ein falscher Schlüssel
      if (this.failedBeforeOpen >= 3) {
        this.onStatus("rejected");
        return;
      }
      this.onStatus("reconnect");
      this.schedule();
    };
  }

  schedule() {
    clearTimeout(this.timer);
    const wait = Math.min(30_000, 2000 * 2 ** this.retry);
    this.retry += 1;
    this.timer = setTimeout(() => { if (!this.stopped && this.wanted.size) this.connect(); }, wait);
  }

  sync() {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    for (const s of this.subscribed) if (!this.wanted.has(s)) { ws.send(JSON.stringify({ type: "unsubscribe", symbol: s })); this.subscribed.delete(s); }
    for (const s of this.wanted) if (!this.subscribed.has(s)) { ws.send(JSON.stringify({ type: "subscribe", symbol: s })); this.subscribed.add(s); }
  }

  close() {
    clearTimeout(this.timer);
    if (this.ws) {
      const ws = this.ws;
      this.ws = null;
      try { ws.close(); } catch {}
    }
    this.subscribed = new Set();
  }

  stop() {
    this.stopped = true;
    this.close();
  }
}
