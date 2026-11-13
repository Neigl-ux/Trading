// Gibt Konstanten und Rechenergebnisse des App-Positionsrechners als JSON aus (Vergleich in test_risk_js.py).
import { RISK, sizePosition } from "../app/risk.js";
const cases = JSON.parse(process.argv[2]);
const out = cases.map((c) => { const r = sizePosition(c); return { qty: r.qty, fill: r.fill, fee: r.fee, reject: !!r.reject }; });
console.log(JSON.stringify({ RISK, out }));
