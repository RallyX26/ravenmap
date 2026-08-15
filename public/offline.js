/* "You are offline" - said once, plainly, by the page that knows.
 *
 * 🚨 AN APP THAT OPENS WITH NO DATA AND SAYS NOTHING IS INDISTINGUISHABLE FROM
 * A BROKEN ONE. The service worker now lets SparrowMap open without a signal,
 * which is the point of shipping it as a web app rather than through a store.
 * But an empty map is exactly what a map with no sightings looks like, so
 * without this the improvement reads as "the app stopped working" - the same
 * silent-empty-state failure this codebase keeps finding, arriving through a
 * new door.
 *
 * ⚠️ navigator.onLine IS NOT A CONNECTION TEST. It reports whether the device
 * has a network interface up, so it is true on a hotel wifi that has not been
 * paid for, on a captive portal, on a train tunnel's dying signal, and whenever
 * the box itself is down. Those are precisely the cases somebody needs to be
 * told about. It is used here only as an instant hint; what actually decides is
 * whether requests are succeeding.
 *
 * 🌐 AND IT COSTS NO REQUESTS. The map already polls /api/stats every three
 * seconds, cameras every five and reports every ten. Those calls are a
 * continuous, free liveness signal, so this observes them rather than adding a
 * heartbeat of its own - on a page that is going round social media, one extra
 * poll per open tab is real load on a 2-vCPU box.
 */
(function () {
  "use strict";

  var GRACE_MS = 12000;   // consecutive failure time before we say anything
  var firstFail = 0;      // when the current run of failures started
  var shown = false;
  var el = null;

  function css() {
    if (document.getElementById("offcss")) return;
    var s = document.createElement("style");
    s.id = "offcss";
    s.textContent =
      ".offbar{position:fixed;left:0;right:0;top:0;z-index:2400;" +
      "background:#7a2318;color:#ffe9e4;font:600 13px/1.45 system-ui,sans-serif;" +
      "padding:calc(8px + env(safe-area-inset-top)) 14px 8px;text-align:center;" +
      "box-shadow:0 2px 14px rgba(0,0,0,.5)}" +
      ".offbar small{display:block;font-weight:400;opacity:.85;font-size:12px;margin-top:2px}";
    document.head.appendChild(s);
  }

  function show() {
    if (shown) return;
    shown = true;
    css();
    el = document.createElement("div");
    el.className = "offbar";
    el.setAttribute("role", "status");
    // 🚨 SAY WHAT IS AND IS NOT TRUE. The temptation is "you are offline", but
    // the page cannot tell a dead phone signal from a dead server, and telling
    // somebody their connection is down when the site is down sends them to
    // reboot their router. It also has to say what the map is showing, because
    // an empty map that means "no data reached us" and an empty map that means
    // "nothing has passed" look identical.
    el.innerHTML = "No connection — this map is not updating" +
      "<small>Showing what was already loaded. Sightings will not appear until " +
      "you are back online.</small>";
    document.body.appendChild(el);
  }

  function hide() {
    shown = false;
    if (el) { el.remove(); el = null; }
  }

  function mark(ok) {
    if (ok) { firstFail = 0; if (shown) hide(); return; }
    var now = Date.now();
    if (!firstFail) firstFail = now;
    // A single failed poll is a blip, not an outage. Requiring a RUN of them
    // keeps the bar from flashing on every dropped request, which would train
    // people to ignore it - and it is the one thing that must still be believed
    // when it does appear.
    if (now - firstFail >= GRACE_MS) show();
  }

  // The browser's own signal, used only to move faster: losing the interface is
  // instant and certain, so there is no reason to sit through the grace period.
  window.addEventListener("offline", function () { firstFail = 1; show(); });
  window.addEventListener("online", function () { mark(true); });

  /* Observe fetch. It passes everything through untouched and re-throws, so the
   * worst case is that this file does nothing - never that it breaks a caller.
   * Only SAME-ORIGIN requests count: a blocked third party says nothing about
   * whether this site is reachable. */
  var orig = window.fetch;
  if (typeof orig === "function") {
    window.fetch = function (input, init) {
      var same = false;
      try {
        var u = new URL(
          (input && input.url) ? input.url : String(input), location.href);
        same = (u.origin === location.origin);
      } catch (e) { same = false; }
      var p;
      try { p = orig.call(this, input, init); }
      catch (e) { if (same) mark(false); throw e; }
      return p.then(function (r) {
        // A 5xx from our own box is not a working connection to the map either,
        // and it is the shape an overloaded box actually fails in.
        if (same) mark(!(r.status >= 500));
        return r;
      }, function (err) {
        if (same) mark(false);
        throw err;
      });
    };
  }

  if (navigator.onLine === false) { firstFail = 1; show(); }
})();
