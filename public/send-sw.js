/* Sparrow Send service worker. Scoped to /send (registered with {scope:'/send'})
 * so it never touches the map, which has its own /sw.js. It makes the page
 * installable and gives it a small offline shell; message traffic (/api/send/*)
 * is always network, never cached. */
var CACHE = "sparrowsend-v3";
var SHELL = ["/send", "/static/sparrowsend-ratchet.js?v=1", "/static/sparrowsend-pow.js?v=1",
             "/static/send.webmanifest", "/static/icon-192.png", "/static/icon-512.png",
             "/vendor/jsqr.min.js"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE)
    .then(function (c) { return Promise.all(SHELL.map(function (u) {
      return c.add(u).catch(function () {}); })); })   // one missing asset must not fail install
    .then(function () { return self.skipWaiting(); }));
});
self.addEventListener("activate", function (e) {
  // 🚨 CacheStorage is per-ORIGIN, not per-scope. The map PWA lives on this same
  // origin, so deleting "every cache that isn't mine" wiped the map's ~38 MB
  // detector-model cache (sparrow-v6) the moment a camera volunteer opened
  // Sparrow Send - forcing a full re-download before their camera could detect.
  // Only ever delete OUR OWN old versions (the "sparrowsend-" prefix).
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k.indexOf("sparrowsend-") === 0 && k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener("fetch", function (e) {
  var url = new URL(e.request.url);
  if (e.request.method !== "GET") return;             // never cache posts
  if (url.pathname.indexOf("/api/") === 0) return;    // messages are live
  // 🚨 The PAGE itself is NETWORK-FIRST so a new deploy shows immediately and a
  // cached page can never pin an old UI (that stranded the old icon). The cache
  // is only the OFFLINE fallback. Static assets stay cache-first below.
  var isDoc = e.request.mode === "navigate" || url.pathname === "/send";
  if (isDoc) {
    e.respondWith(fetch(e.request).then(function (res) {
      if (res && res.ok) {
        var copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put("/send", copy); });
      }
      return res;
    }).catch(function () { return caches.match("/send"); }));
    return;
  }
  e.respondWith(
    caches.match(e.request).then(function (hit) {
      return hit || fetch(e.request).then(function (res) {
        if (res && res.ok && url.origin === location.origin) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(e.request, copy); });
        }
        return res;
      }).catch(function () { return caches.match("/send"); });
    })
  );
});
