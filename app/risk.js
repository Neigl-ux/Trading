// Phase 4 – Positionsrechner der App. Rechnet nur, handelt nicht.
// Dieselben Regeln wie src/tradingagent/risk.py; die Werte stammen aus config/risk.yaml
// (ein Test prüft, dass beide übereinstimmen).

export const RISK = {
  startCapital: 5000,
  feeStock: 8,          // € je Order (Aktien, ETFs)
  feeCryptoPct: 0.2,    // % vom Betrag
  spreadPct: 0.1,       // Ersatz, wenn Tradegate keinen Briefkurs liefert
  spreadCryptoPct: 0.05,
  slipPct: 0.05,
  riskPct: 1,           // höchstens 1 % des Kontos je Trade
  stopAtr: 2,           // Stop = Einstieg − 2 × ATR(14)
  maxPosPct: 20,
  maxPosCryptoPct: 10,
  maxFeePct: 1,         // Gebühr über 1 % des Betrags: nicht handeln
};

const isCrypto = (kind) => kind === "krypto";

export function fee(kind, amount) {
  return isCrypto(kind) ? Math.round(Math.abs(amount) * RISK.feeCryptoPct) / 100 : RISK.feeStock;
}

/** Kaufkurs: Briefkurs (oder Kurs + halber Ersatz-Spread), dazu Schlupf. */
export function fillBuy(price, kind, ask) {
  const half = (isCrypto(kind) ? RISK.spreadCryptoPct : RISK.spreadPct) / 200;
  const base = ask > 0 ? ask : price * (1 + half);
  return base * (1 + RISK.slipPct / 100);
}

export function stopFromAtr(price, atr) {
  return price - RISK.stopAtr * atr;
}

/**
 * Stückzahl für einen Kauf. Gibt immer ein Ergebnis zurück; `reject` nennt den Grund, wenn nicht gehandelt werden darf.
 * equity = Kontowert, cash = verfügbares Geld (Standard: equity).
 */
export function sizePosition({ equity, cash = equity, price, stop, kind = "aktie", ask = null, riskPct = RISK.riskPct }) {
  const fill = fillBuy(price, kind, ask);
  const dist = fill - stop;
  const out = { qty: 0, fill, amount: 0, fee: 0, riskEur: 0, budget: equity * riskPct / 100, cappedBy: null, reject: null };
  if (!(equity > 0) || !(price > 0)) return { ...out, reject: "Kontowert und Kurs angeben" };
  if (!(stop > 0) || !(dist > 0)) return { ...out, reject: "Der Stop muss unter dem Kaufkurs liegen" };
  const step = isCrypto(kind) ? 1e-6 : 1;
  const floor = (x) => Math.floor(x / step + 1e-9) * step;
  let qty = floor(out.budget / dist);
  const capPct = isCrypto(kind) ? RISK.maxPosCryptoPct : RISK.maxPosPct;
  if (qty * fill > equity * capPct / 100) {
    qty = floor(equity * capPct / 100 / fill);
    out.cappedBy = `höchstens ${capPct} % des Kontos je Position`;
  }
  const affordable = isCrypto(kind) ? floor(cash / (fill * (1 + RISK.feeCryptoPct / 100))) : floor(Math.max(0, cash - RISK.feeStock) / fill);
  if (qty > affordable) { qty = affordable; out.cappedBy = "verfügbares Geld"; }
  qty = Math.max(0, Math.round(qty * 1e6) / 1e6);
  const amount = qty * fill;
  const f = qty > 0 ? fee(kind, amount) : 0;
  Object.assign(out, { qty, amount, fee: f, riskEur: qty * dist });
  if (qty <= 0) out.reject = "Bei diesem Stop wäre die Position 0 Stück";
  else if (f > amount * RISK.maxFeePct / 100) out.reject = `Die Gebühr wäre über ${RISK.maxFeePct} % des Betrags – Position zu klein`;
  return out;
}
