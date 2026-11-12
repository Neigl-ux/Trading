// Technische Indikatoren auf Kerzen [zeit, open, high, low, close, volume].
// Jede Funktion liefert ein Array gleicher Länge; null, solange zu wenig Kerzen vorliegen.

const close = (r) => r[4];

export function sma(rows, n, pick = close) {
  const out = new Array(rows.length).fill(null);
  let sum = 0;
  for (let i = 0; i < rows.length; i++) {
    sum += pick(rows[i]);
    if (i >= n) sum -= pick(rows[i - n]);
    if (i >= n - 1) out[i] = sum / n;
  }
  return out;
}

/** EMA mit SMA-Start (erste n Kerzen), danach Glättung 2/(n+1). */
export function emaValues(values, n) {
  const out = new Array(values.length).fill(null);
  const k = 2 / (n + 1);
  let prev = null, sum = 0, count = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v == null) continue;
    if (prev == null) {
      sum += v; count += 1;
      if (count === n) { prev = sum / n; out[i] = prev; }
      continue;
    }
    prev = v * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}
export const ema = (rows, n) => emaValues(rows.map(close), n);

export function bollinger(rows, n = 20, mult = 2) {
  const mid = sma(rows, n);
  const up = new Array(rows.length).fill(null), low = new Array(rows.length).fill(null);
  for (let i = n - 1; i < rows.length; i++) {
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += (rows[j][4] - mid[i]) ** 2;
    const sd = Math.sqrt(s / n);
    up[i] = mid[i] + mult * sd;
    low[i] = mid[i] - mult * sd;
  }
  return { mid, up, low };
}

/** VWAP, der an jedem neuen Tag (Tagesbeginn nach dayKey) neu startet. Ohne Volumen: null. */
export function vwap(rows, dayKey) {
  const out = new Array(rows.length).fill(null);
  let pv = 0, vol = 0, day = null;
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    const d = dayKey(r[0]);
    if (d !== day) { day = d; pv = 0; vol = 0; }
    const typical = (r[2] + r[3] + r[4]) / 3;
    pv += typical * (r[5] || 0);
    vol += r[5] || 0;
    out[i] = vol > 0 ? pv / vol : null;
  }
  return out;
}

/** RSI nach Wilder (Glättung 1/n), Start mit dem Durchschnitt der ersten n Veränderungen. */
export function rsi(rows, n = 14) {
  const out = new Array(rows.length).fill(null);
  if (rows.length <= n) return out;
  let gain = 0, loss = 0;
  for (let i = 1; i <= n; i++) {
    const d = rows[i][4] - rows[i - 1][4];
    if (d > 0) gain += d; else loss -= d;
  }
  gain /= n; loss /= n;
  out[n] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  for (let i = n + 1; i < rows.length; i++) {
    const d = rows[i][4] - rows[i - 1][4];
    gain = (gain * (n - 1) + Math.max(d, 0)) / n;
    loss = (loss * (n - 1) + Math.max(-d, 0)) / n;
    out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  }
  return out;
}

export function macd(rows, fast = 12, slow = 26, signal = 9) {
  const f = ema(rows, fast), s = ema(rows, slow);
  const line = rows.map((_, i) => (f[i] != null && s[i] != null ? f[i] - s[i] : null));
  const sig = emaValues(line, signal);
  const hist = line.map((v, i) => (v != null && sig[i] != null ? v - sig[i] : null));
  return { line, signal: sig, hist };
}

function trueRange(rows, i) {
  const r = rows[i];
  if (i === 0) return r[2] - r[3];
  const pc = rows[i - 1][4];
  return Math.max(r[2] - r[3], Math.abs(r[2] - pc), Math.abs(r[3] - pc));
}

/** ATR nach Wilder. */
export function atr(rows, n = 14) {
  const out = new Array(rows.length).fill(null);
  if (rows.length < n + 1) return out;
  let a = 0;
  for (let i = 1; i <= n; i++) a += trueRange(rows, i);
  a /= n;
  out[n] = a;
  for (let i = n + 1; i < rows.length; i++) {
    a = (a * (n - 1) + trueRange(rows, i)) / n;
    out[i] = a;
  }
  return out;
}

/** ADX nach Wilder, dazu +DI und −DI. */
export function adx(rows, n = 14) {
  const len = rows.length;
  const adxOut = new Array(len).fill(null), pdiOut = new Array(len).fill(null), mdiOut = new Array(len).fill(null);
  if (len < 2 * n + 1) return { adx: adxOut, pdi: pdiOut, mdi: mdiOut };
  let tr = 0, pdm = 0, mdm = 0;
  const dm = (i) => {
    const up = rows[i][2] - rows[i - 1][2], down = rows[i - 1][3] - rows[i][3];
    return [up > down && up > 0 ? up : 0, down > up && down > 0 ? down : 0];
  };
  for (let i = 1; i <= n; i++) {
    tr += trueRange(rows, i);
    const [p, m] = dm(i);
    pdm += p; mdm += m;
  }
  const dx = [];
  for (let i = n; i < len; i++) {
    if (i > n) {
      tr = tr - tr / n + trueRange(rows, i);
      const [p, m] = dm(i);
      pdm = pdm - pdm / n + p;
      mdm = mdm - mdm / n + m;
    }
    const pdi = tr ? 100 * pdm / tr : 0, mdi = tr ? 100 * mdm / tr : 0;
    pdiOut[i] = pdi; mdiOut[i] = mdi;
    dx.push(pdi + mdi ? 100 * Math.abs(pdi - mdi) / (pdi + mdi) : 0);
    if (dx.length === n) adxOut[i] = dx.reduce((a, b) => a + b, 0) / n;
    else if (dx.length > n) adxOut[i] = (adxOut[i - 1] * (n - 1) + dx[dx.length - 1]) / n;
  }
  return { adx: adxOut, pdi: pdiOut, mdi: mdiOut };
}

// Verfügbare Indikatoren für die Auswahl in der App
export const OVERLAYS = [
  { key: "ema9", label: "EMA 9", color: "#f5b041" },
  { key: "ema20", label: "EMA 20", color: "#3b82f6" },
  { key: "ema50", label: "EMA 50", color: "#a855f7" },
  { key: "ema100", label: "EMA 100", color: "#14b8a6" },
  { key: "ema200", label: "EMA 200", color: "#ef4444" },
  { key: "sma20", label: "SMA 20", color: "#94a3b8" },
  { key: "sma50", label: "SMA 50", color: "#e2e8f0" },
  { key: "boll", label: "Bollinger-Bänder (20, 2)", color: "#60a5fa" },
  { key: "vwap", label: "VWAP (je Tag)", color: "#f472b6" },
];
export const PANES = [
  { key: "rsi", label: "RSI (14)" },
  { key: "macd", label: "MACD (12, 26, 9)" },
  { key: "atr", label: "ATR (14)" },
  { key: "adx", label: "ADX (14)" },
];

/** Linien eines Overlays als Liste { name, color, values }. */
export function overlayLines(key, rows, dayKey) {
  const def = OVERLAYS.find((o) => o.key === key);
  if (key.startsWith("ema")) return [{ name: def.label, color: def.color, values: ema(rows, +key.slice(3)) }];
  if (key.startsWith("sma")) return [{ name: def.label, color: def.color, values: sma(rows, +key.slice(3)) }];
  if (key === "boll") {
    const b = bollinger(rows);
    return [
      { name: "BB oben", color: def.color, values: b.up },
      { name: "BB Mitte", color: "rgba(96,165,250,.5)", values: b.mid, dashed: true },
      { name: "BB unten", color: def.color, values: b.low },
    ];
  }
  if (key === "vwap") return [{ name: def.label, color: def.color, values: vwap(rows, dayKey) }];
  return [];
}
