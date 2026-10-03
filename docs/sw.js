// Offline support. Page shell: answer from cache, refresh in the background. Data: network first, cache as fallback.
const CACHE = "radar-v3";
const SHELL = ["./", "style.css", "app.js", "icon.svg", "manifest.webmanifest"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", event => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== location.origin) return;
  const fresh = () => fetch(request).then(response => {
    if (response.ok) caches.open(CACHE).then(cache => cache.put(request, response.clone()));
    return response.clone();
  });
  if (url.pathname.includes("/data/")) {
    event.respondWith(fresh().catch(() => caches.match(request)));
  } else {
    event.respondWith(caches.match(request).then(cached => {
      const update = fresh().catch(() => cached);
      return cached || update;
    }));
  }
});
