/* Sparrow Send service worker. Scoped to /send (registered with {scope:'/send'})
 * so it never touches the map, which has its own /sw.js. It makes the page
 * installable and gives it a small offline shell; message traffic (/api/send/*)
 * is always network, never cached. */
var CACHE = "sparrowsend-v1";
var SHELL = ["/send", "/static/sparrowsend-ratchet.js", "/static/send.webmanifest",
             "/static/icon-192.png", "/static/icon-512.png", "/vendor/jsqr.min.js"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE)
    .then(function (c) { return Promise.all(SHELL.map(function (u) {
      return c.add(u).catch(function () {}); })); })   // one missing asset must not fail install
    .then(function () { return self.skipWaiting(); }));
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener("fetch", function (e) {
  var url = new URL(e.request.url);
  if (e.request.method !== "GET") return;             // never cache posts
  if (url.pathname.indexOf("/api/") === 0) return;    // messages are live
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
