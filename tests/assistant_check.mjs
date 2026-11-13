import { answer, findInstruments, searchKB, improvements } from "../app/assistant.js";
const inst = [
  { id: "EUNL", name: "iShares Core MSCI World", ticker: "EUNL.DE", kind: "etf", isin: "IE00B4L5Y983", tier: "core", metrics: { perf: { "7d": 0.01, "30d": 0.02 } } },
  { id: "SAP", name: "SAP", ticker: "SAP.DE", kind: "aktie", isin: "DE0007164600", tier: "core", metrics: {} },
  { id: "EXXT", name: "iShares Nasdaq-100", ticker: "EXXT.DE", kind: "etf", isin: "DE000A0F5UF5", tier: "core", metrics: {} },
  { id: "DIE", name: "D'Ieteren", ticker: "DIE.BR", kind: "aktie", tier: "extended", metrics: {} },
];
const adv = { values: { EUNL: { s: 72, g: "interessant", r: 2, dd: 0.12, st: 1, w: ["Trend intakt"] }, SAP: { s: 35, g: "vorsicht", r: 4, dd: 0.3, st: 0, w: [] } },
  plan: { items: [{ id: "EUNL", eur: 120, role: "Kern" }], mode: "einfach" }, world: { level: "ruhig" } };
const ctx = {
  instruments: inst, adv, price: () => 100, day: () => -0.012, status: () => "LIVE", fmtPrice: (e, p) => `${p} €`, fmtEur: (x) => `${Math.round(x)} €`,
  kindLabel: (k) => k, name: (id) => (inst.find((e) => e.id === id) || {}).name || id,
  portfolio: () => ({ rows: [{ e: inst[1], name: "SAP", value: 600, cost: 1000 }, { e: inst[0], name: "iShares Core MSCI World", value: 400, cost: 300 }], total: 1000, dayAbs: -12 }),
  movers: () => ({ top: [{ e: inst[1], pct: 0.05 }], flop: [{ e: inst[0], pct: -0.02 }] }), setup: () => ({ portfolio: true, finnhub: false }),
};
const check = (cond, msg) => { if (!cond) { console.error("FEHLER:", msg); process.exit(1); } };
check(findInstruments("Wie steht SAP heute?", inst)[0].id === "SAP", "SAP erkannt");
check(findInstruments("wie steht der msci world", inst)[0].id === "EUNL", "MSCI World erkannt");
check(!findInstruments("die Lage ist gut", inst).length, "kein Fehltreffer bei 'die'");
check(findInstruments("IE00B4L5Y983?", inst)[0].id === "EUNL", "ISIN erkannt");
check(/72 Punkte/.test(answer("Wie steht der MSCI World?", ctx).text), "Wertantwort mit Punktzahl");
check(/Vergleich/.test(answer("Vergleiche SAP und MSCI World", ctx).text), "Vergleich");
check(/Klumpenrisiko/.test(answer("Was würdest du an meinem Portfolio verbessern?", ctx).text), "Klumpen erkannt");
check(/Stufe 1 erreicht/.test(answer("Ist eine Nachkauf-Stufe erreicht?", ctx).text), "Stufen");
check(/120 €/.test(answer("Wie soll mein Sparplan aussehen?", ctx).text), "Sparplan");
check(/Golden Cross/.test(answer("Was ist ein Golden Cross?", ctx).text), "Begriff");
check(/1\.000 €/.test(answer("wie viel steuern zahle ich auf gewinne", ctx).text), "Steuern");
check(/Gewinner/.test(answer("Wer sind die Gewinner heute?", ctx).text), "Gewinner");
check(/keine Antwort/.test(answer("Wie wird das Wetter morgen?", ctx).text), "Rückfall");
check(/Finnhub/.test(answer("Was fehlt der App noch? Verbesserungsvorschläge", ctx).text), "App-Ideen");
check(improvements(ctx.portfolio(), ctx).some((t) => /im Minus/.test(t)), "Verlust-Hinweis");
console.log("ok");
