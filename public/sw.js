/* SparrowMap service worker.
 *
 * Two jobs: make the camera app INSTALLABLE (a browser only offers "Install"
 * for a page backed by a service worker, over HTTPS), and make a running node
 * OFFLINE-RESILIENT so a camera keeps working through a network blip.
 *
 * It only ever caches this origin's own files. The vendored detector runtime
 * (the ~36 MB model + wasm) is cached the first time it's fetched, so a node
 * that has run once can start again with no network.
 */
const CACHE = 'sparrow-v6';
const SHELL = [
  '/app',
  '/static/sparrow-app.js',
  '/static/style.css',
  '/static/refresh.js',
  '/static/icon-192.png',
  '/static/manifest.webmanifest',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      // Best-effort: a missing shell file must not fail the whole install.
      .then((c) => Promise.allSettled(SHELL.map((u) => c.add(u))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;

  // Cache-first ONLY for the vendored runtime: it is pinned and large (the
  // detector model + wasm), so a node that has run once starts again offline
  // and never re-downloads it.
  if (url.pathname.startsWith('/vendor/')) {
    e.respondWith(
      caches.open(CACHE).then((c) =>
        c.match(e.request).then((hit) =>
          hit || fetch(e.request).then((res) => {
            if (res.ok) c.put(e.request, res.clone());
            return res;
          })
        )
      )
    );
    return;
  }

  // Our own app code (/static/) is NETWORK-FIRST: always try the network so a
  // shipped fix lands on the very next refresh, and fall back to the cached copy
  // only when offline. Stale-while-revalidate served the OLD build on the first
  // load after a deploy (the fix landed a load late), which read as "the browser
  // is not loading the new one". Freshness matters more than a few ms here; the
  // large pinned runtime under /vendor/ is what stays cache-first for offline.
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      fetch(e.request).then((res) => {
        if (res.ok) caches.open(CACHE).then((c) => c.put(e.request, res.clone()));
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  // Network-first for pages and the API; fall back to the cached shell offline
  // so the app opens instead of showing the browser's error page.
  e.respondWith(
    fetch(e.request).catch(() =>
      caches.match(e.request).then((hit) => hit || caches.match('/app'))
    )
  );
});
