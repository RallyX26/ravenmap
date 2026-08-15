/* "Add to your phone / desktop" - the button for an app that was already
 * installable and never said so.
 *
 * 🚨 THE MANIFEST HAS BEEN COMPLETE FOR AGES. Icons, scope, standalone display,
 * a service worker - everything needed to install, and no surface anywhere that
 * mentions it. Chrome hides its own install affordance in a menu most people
 * never open, and iOS has no automatic prompt at all, so in practice nobody
 * installed it. Same shape as the review queue that was built and unreachable:
 * shipped is not the same as findable.
 *
 * Three states, because the platforms genuinely differ and pretending otherwise
 * is what produces a button that does nothing on an iPhone:
 *
 *   installed         -> say nothing at all. A button offering to install the
 *                        app you are already inside is noise that makes the
 *                        rest of the page look careless.
 *   prompt available  -> Chrome/Edge/Android fired beforeinstallprompt, so the
 *                        browser will do it properly. One tap.
 *   iOS Safari        -> no API exists. Apple requires Share -> Add to Home
 *                        Screen, so the honest thing is to SHOW that, not to
 *                        offer a button that silently fails.
 */
(function () {
  "use strict";

  function installed() {
    try {
      return (window.matchMedia
              && window.matchMedia("(display-mode: standalone)").matches)
             || window.navigator.standalone === true;
    } catch (e) { return false; }
  }

  // iPhone/iPad Safari. Chrome on iOS cannot install either, and reports the
  // same platform, so the instructions are right for both.
  function isIOS() {
    var ua = navigator.userAgent || "";
    return /iPad|iPhone|iPod/.test(ua)
        || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);  // iPadOS
  }

  var DISMISS = "sparrow.install.dismissed";
  function dismissed() {
    try { return localStorage.getItem(DISMISS) === "1"; } catch (e) { return false; }
  }

  var CSS = [
    ".instwrap{position:fixed;right:12px;z-index:1250;bottom:calc(70px + env(safe-area-inset-bottom));",
    "  display:flex;align-items:center;gap:8px;background:#141c28;color:#e8eef6;",
    "  border:1px solid #2a3547;border-radius:999px;padding:9px 8px 9px 14px;",
    "  box-shadow:0 10px 30px rgba(0,0,0,.45);font:600 13px system-ui,sans-serif}",
    ".instwrap button{background:none;border:0;color:inherit;font:inherit;cursor:pointer}",
    ".instwrap .go{color:#5ee08a}",
    ".instwrap .x{color:#6b7a8d;font-size:16px;line-height:1;padding:0 4px}",
    ".insthow{position:fixed;inset:0;z-index:1400;display:flex;align-items:flex-end;",
    "  justify-content:center;background:rgba(4,7,12,.72);padding:16px}",
    ".insthow .card{background:#0f151f;border:1px solid #27354a;border-radius:16px;",
    "  padding:18px;max-width:420px;width:100%;color:#c7d2dc;font:14px/1.6 system-ui,sans-serif}",
    ".insthow b{color:#e8eef6;display:block;margin-bottom:8px;font-size:16px}",
    ".insthow ol{margin:10px 0 0;padding-left:20px}",
    ".insthow li{margin:7px 0}",
    ".insthow .close{margin-top:14px;width:100%;padding:12px;border-radius:10px;",
    "  background:#1b2432;border:1px solid #2b3a4f;color:#e8eef6;font:600 14px system-ui;cursor:pointer}"
  ].join("\n");

  var deferred = null, wrap = null;

  function style() {
    if (document.getElementById("instcss")) return;
    var s = document.createElement("style");
    s.id = "instcss"; s.textContent = CSS;
    document.head.appendChild(s);
  }

  function hide() {
    if (wrap) { wrap.remove(); wrap = null; }
  }

  function howToIOS() {
    var back = document.createElement("div");
    back.className = "insthow";
    back.innerHTML =
      '<div class="card"><b>Add SparrowMap to your Home Screen</b>' +
      'iPhone does not let a website install itself, so this is two taps in ' +
      'Safari:' +
      '<ol><li>Tap the <b>Share</b> button at the bottom of Safari ' +
      '(the square with an arrow).</li>' +
      '<li>Scroll down and tap <b>Add to Home Screen</b>.</li></ol>' +
      '<button class="close">Got it</button></div>';
    back.addEventListener("click", function (e) {
      if (e.target === back || e.target.className === "close") back.remove();
    });
    document.body.appendChild(back);
  }

  function show(label, onClick) {
    style();
    hide();
    wrap = document.createElement("div");
    wrap.className = "instwrap";
    var go = document.createElement("button");
    go.className = "go";
    go.textContent = label;
    go.addEventListener("click", onClick);
    var x = document.createElement("button");
    x.className = "x";
    x.setAttribute("aria-label", "not now");
    x.textContent = "×";
    // Remembered, because an install nudge that comes back on every page load
    // is an advert. One "no" is an answer.
    x.addEventListener("click", function () {
      try { localStorage.setItem(DISMISS, "1"); } catch (e) {}
      hide();
    });
    wrap.appendChild(go);
    wrap.appendChild(x);
    document.body.appendChild(wrap);
  }

  // Chrome/Edge/Android: the browser tells us it is installable, and only then.
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();               // keep it; fire it from our own button
    deferred = e;
    if (installed() || dismissed()) return;
    show("⬇ Add to home screen", async function () {
      hide();
      try {
        deferred.prompt();
        await deferred.userChoice;    // accepted or not, the prompt is spent
      } catch (err) { /* the browser withdrew it; nothing useful to say */ }
      deferred = null;
    });
  });

  // Installed while the page was open: take the offer away immediately.
  window.addEventListener("appinstalled", function () {
    try { localStorage.setItem(DISMISS, "1"); } catch (e) {}
    hide();
  });

  function boot() {
    if (installed() || dismissed()) return;
    // iOS never fires beforeinstallprompt, so its button is shown on its own
    // terms rather than waiting for an event that cannot arrive.
    if (isIOS()) show("⬇ Add to home screen", howToIOS);
    // Everyone else waits for the event above. Chrome fires it within a moment
    // of load when the criteria are met; if it never fires, no button appears,
    // which is correct - there is nothing to install into.
  }

  if (document.body) boot();
  else document.addEventListener("DOMContentLoaded", boot);
})();
