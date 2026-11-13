// Service Worker: App-Gerüst offline verfügbar, Daten immer frisch aus dem Netz.
const CACHE = "ta-shell-v38";
const SHELL = ["./", "index.html", "style.css", "app.js", "crypto.js", "format.js", "tradegate.js", "perf.js", "feeds.js", "finnhub.js", "engine.js", "indicators.js", "assistant.js", "risk.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png", "vendor/lightweight-charts.standalone.production.js"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return; // Börsen-APIs nie cachen
  // Netz zuerst, bei Funkloch die letzte Version
  // „no-cache“: immer beim Server nachfragen, ob es eine neue Version gibt (sonst mischt der
  // Browser-Zwischenspeicher alte und neue Dateien, z. B. neues HTML mit altem CSS)
  const req = e.request.mode === "navigate" ? e.request : new Request(e.request, { cache: "no-cache" });
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok && !url.pathname.includes("/data/")) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request, { ignoreSearch: true })),
  );
});
