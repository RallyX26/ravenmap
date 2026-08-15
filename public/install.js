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

  function isAndroid() { return /Android/.test(navigator.userAgent || ""); }

  /* 🚨 THE BROWSER SOMEBODY IS IN WHEN THEY TAP A LINK IN AN APP.
   *
   * A volunteer trying to install reported no download button, and sent a
   * screenshot of the menu they were searching: Search this screen, Copy URL,
   * Find on page, Refresh, Auto Dark mode, Resize text, Translate page. No
   * "Add to Home screen", because that is not a browser - it is the Google
   * app's in-app browser. It was reached from a link, which is how most people
   * arrive at a site that is going round social media, so this is the COMMON
   * case and not an edge one.
   *
   * An in-app browser cannot install a web app. It has no home-screen menu, and
   * its storage is its own - so even the parts that do work are thrown away
   * when it closes. Nothing this page does can change that. The only useful
   * move is to get them out of it, and the only honest thing to show is how.
   *
   * `GSA/` is the Google app. `; wv)` is any Android WebView, which covers
   * Facebook, Instagram, Messenger, Line, TikTok and the rest - all of which
   * open links the same way. Chrome's own UA never contains either.
   */
  function inAppBrowser() {
    var ua = navigator.userAgent || "";
    return /\bGSA\/|; wv\)|FBAN|FBAV|FB_IAB|Instagram|Line\/|MicroMessenger|TikTok|Snapchat/.test(ua);
  }

  // Hands the URL to Chrome. Android resolves an intent: URL to the named
  // package, so this is one tap rather than "copy the address, open Chrome,
  // paste it" - which is where people give up.
  function chromeIntent() {
    return "intent://" + location.host + location.pathname
         + "#Intent;scheme=https;package=com.android.chrome;end";
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

  function hasKey() {
    try { return !!JSON.parse(localStorage.getItem("sparrow.node") || "null"); }
    catch (e) { return false; }
  }

  /* 🚨 AN INSTALLED APP ON IPHONE STARTS WITH AN EMPTY CUPBOARD.
   *
   * A home-screen web app on iOS gets its OWN storage container, separate from
   * Safari's. So a camera owner who installs the app is signed out the moment
   * they open it - their key is still in Safari, and the new app cannot see it.
   * That is Apple's design and nothing this page does can share storage across
   * it. On Android there is no such split: Chrome mints a WebAPK that uses the
   * same profile, so the key comes with it.
   *
   * What CAN be fixed is the surprise. Somebody who installs and is then asked
   * to sign in, with their key sitting in a browser they have just navigated
   * away from, reasonably concludes the app is broken - and the earlier report
   * of a "deleted" camera is exactly what that looks like from their side. So
   * an owner is told BEFORE they install, and handed their key to carry across
   * while it is still in front of them.
   *
   * Only for an owner. A visitor with no camera has nothing to carry and does
   * not need a warning about a step they will never take. */
  function keyWarning() {
    if (!hasKey()) return "";
    return '<div style="background:#241c0c;border:1px solid #5c4a1d;' +
      'border-radius:10px;padding:11px 12px;margin:12px 0 0;color:#f0c674;' +
      'font-size:13px;line-height:1.5"><b>Copy your camera key first.</b> ' +
      'An installed app has its own storage on iPhone, so it will ask you to ' +
      'sign in once. Copy it now while it is in front of you.</div>';
  }

  function addKeyButton(card) {
    if (!hasKey() || !window.sparrowCopyButton) return;
    var n = null;
    try { n = JSON.parse(localStorage.getItem("sparrow.node") || "null"); }
    catch (e) { return; }
    if (!n || !n.id || !n.token) return;
    var b = window.sparrowCopyButton(
      function () { return location.origin + "/node#k=" + n.id + "." + n.token; },
      { label: "🔑 Copy my camera key", done: "✓ Key copied — paste it after installing" });
    b.style.cssText += ";display:block;width:100%;margin-top:10px;padding:12px";
    var btn = card.querySelector("button.close");
    card.insertBefore(b, btn);
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
      keyWarning() +
      '<button class="close">Got it</button></div>';
    back.addEventListener("click", function (e) {
      if (e.target === back || e.target.className === "close") back.remove();
    });
    addKeyButton(back.querySelector(".card"));
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

  /* Two different problems, so two different sets of words. Telling somebody in
   * Chrome that they are in an app's built-in browser is worse than saying
   * nothing: it is confidently wrong, and they will go looking for a browser
   * they are already using. */
  function howToAndroid(inApp) {
    var body = inApp
      ? 'You are in an app\'s built-in browser (this page opened from a link). ' +
        'It cannot install apps, which is why there is no "Add to Home screen" ' +
        'anywhere in its menu.' +
        '<ol><li>Tap <b>Open in Chrome</b> below.</li>' +
        '<li>In Chrome, tap <b>⬇ Add to home screen</b> at the bottom of the ' +
        'map — or use Chrome\'s ⋮ menu → <b>Add to Home screen</b>.</li></ol>'
      : 'Your browser has not offered to install it. Chrome, Edge, Samsung ' +
        'Internet and Brave can all do it:' +
        '<ol><li>Open the browser\'s menu (⋮ or ≡).</li>' +
        '<li>Tap <b>Add to Home screen</b>, or <b>Install app</b>.</li></ol>' +
        'If it is not there, tap <b>Open in Chrome</b> below.';
    var back = document.createElement("div");
    back.className = "insthow";
    back.innerHTML =
      '<div class="card"><b>' +
      (inApp ? "Open it in Chrome first" : "Adding SparrowMap to your phone") +
      '</b>' + body +
      '<a class="close" style="display:block;text-align:center;text-decoration:none;' +
      'background:#1c7a45;border-color:#2a9c5b;color:#fff" href="' + chromeIntent() +
      '">Open in Chrome →</a>' +
      '<button class="close">Not now</button></div>';
    back.addEventListener("click", function (e) {
      // The anchor must NOT be caught here - it has to navigate.
      if (e.target === back || e.target.tagName === "BUTTON") back.remove();
    });
    document.body.appendChild(back);
  }

  function boot() {
    if (installed() || dismissed()) return;
    // iOS never fires beforeinstallprompt, so its button is shown on its own
    // terms rather than waiting for an event that cannot arrive.
    if (isIOS()) { show("⬇ Add to home screen", howToIOS); return; }

    // 🚨 AN ANDROID USER WHO CANNOT INSTALL MUST BE TOLD WHY, NOT SHOWN NOTHING.
    // The old comment here said a missing button "is correct - there is nothing
    // to install into". That is true of the code and useless to the person: on
    // a phone the page IS installable, they are simply in a browser that cannot
    // do it, and silence reads as "this site has no app". Say which browser to
    // use instead.
    if (isAndroid() && inAppBrowser()) {
      show("⬇ Add to home screen", function () { howToAndroid(true); });
      return;
    }

    // A real Android browser that has not fired the event yet: wait, then offer
    // the instructions rather than nothing. Chrome fires within a moment of
    // load when the criteria are met, so anything still silent after this is
    // either already installed or a browser that will not offer it.
    if (isAndroid()) {
      setTimeout(function () {
        if (deferred || installed() || dismissed() || wrap) return;
        show("⬇ Add to home screen", function () { howToAndroid(false); });
      }, 4000);
    }
    // Desktop waits for the event above and shows nothing without it, which is
    // right - there is no home screen to add to.
  }

  if (document.body) boot();
  else document.addEventListener("DOMContentLoaded", boot);
})();
