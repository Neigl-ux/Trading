// Service Worker: App-Gerüst offline verfügbar, Daten immer frisch aus dem Netz.
const CACHE = "ta-shell-v4";
const SHELL = ["./", "index.html", "style.css", "app.js", "crypto.js", "format.js", "tradegate.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];

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
  e.respondWith(
    fetch(e.request)
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
