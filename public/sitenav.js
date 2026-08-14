/* The bottom bar, defined ONCE and dropped on every page.
 *
 * 🚨 WHY THIS EXISTS. The shared review pool had no way out. rv.html carries a
 * "Map" link, but it lives inside `view-app` - one of three views - so anyone
 * landing on /rv/pool without a token, or sitting in the picker, saw no header
 * and no exit at all. Reached from the installed app there is no browser back
 * button either, so the page was a dead end. That is the same trap /aim and
 * /rv/retracted each hit separately, which is the tell that per-page navigation
 * was the wrong shape: every new page has to remember to grow its own way home,
 * and eventually one does not.
 *
 * So the bar is a component rather than markup that gets copied. One list of
 * destinations, one set of styles, injected wherever this script is included.
 * Copying the <nav> into a dozen files would have been faster today and would
 * drift by the second edit.
 *
 * ⚠️ IT MUST NOT COVER THE PAGE IT IS ADDED TO, and the pages come in two
 * shapes that need opposite treatment - see fit() below.
 */
(function () {
  "use strict";

  // The single list. index.html renders the same items with its About and
  // Transparency entries as in-place panes rather than links, which is what
  // `mode` marks - see PANES below.
  var ITEMS = [
    { href: "/",             icon: "🗺", label: "Map",          mode: "map" },
    { href: "/drive",        icon: "🚗", label: "Driving" },
    { href: "/app",          icon: "📷", label: "Add a camera" },
    { href: "/rv",           icon: "✅",       label: "Review" },
    { href: "/about",        icon: "ℹ",       label: "About",        mode: "about" },
    { href: "/transparency", icon: "👁", label: "Transparency", mode: "transparency" },
    { href: "/status",       icon: "📶", label: "Status" }
  ];

  // Self-contained styling, so this works on a page that does not load
  // style.css (most of them do not). Same rules the map's bar already used.
  //
  // 🚨 SEVEN ITEMS DO NOT FIT A PHONE, SO IT SCROLLS RATHER THAN SQUASHES.
  // With flex:1 every item shared the width equally and shrank below its own
  // label - "Add a camera" wrapped to two lines at 390px with only five items.
  // flex:1 0 auto fills the width on a desktop and refuses to shrink below the
  // label on a phone, so the overflow is a swipe instead of illegible stubs.
  var CSS = [
    "nav.sitenav{position:fixed;left:0;right:0;bottom:0;z-index:1200;display:flex;",
    "  background:#0d1219;border-top:1px solid #25303f;",
    "  overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none}",
    "nav.sitenav::-webkit-scrollbar{display:none}",
    "nav.sitenav a,nav.sitenav button{flex:1 0 auto;min-width:62px;white-space:nowrap;",
    "  background:none;border:0;color:#8a97a8;cursor:pointer;text-decoration:none;",
    "  text-align:center;padding:10px 4px calc(10px + env(safe-area-inset-bottom));",
    "  font:600 10.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;",
    "  letter-spacing:.09em;text-transform:uppercase;border-top:2px solid transparent;",
    "  -webkit-tap-highlight-color:transparent}",
    "nav.sitenav a{color:#5fb3a1}",
    "nav.sitenav .on,nav.sitenav a.on{color:#e8eef6;border-top-color:#ff3b47;background:#111621}",
    "nav.sitenav .ic{display:block;font-size:15px;margin-bottom:2px;letter-spacing:0}"
  ].join("\n");

  function build(panes) {
    var nav = document.createElement("nav");
    nav.className = "sitenav";
    nav.id = "modes";           // app.js binds its pane switching to this id
    var here = location.pathname.replace(/\/+$/, "") || "/";
    ITEMS.forEach(function (it) {
      // On the map, About and Transparency are panes over the map rather than
      // separate pages, so they are buttons there and links everywhere else.
      var asPane = panes && it.mode;
      var el = document.createElement(asPane ? "button" : "a");
      if (asPane) el.dataset.m = it.mode;
      else el.href = it.href;
      el.innerHTML = '<span class="ic">' + it.icon + "</span>";
      el.appendChild(document.createTextNode(it.label));
      // Mark where you are. On the map the pane logic takes this over
      // immediately; elsewhere it is the only thing that says so.
      var target = it.href.replace(/\/+$/, "") || "/";
      if (!panes && (here === target || (target !== "/" && here.indexOf(target + "/") === 0))) {
        el.className = "on";
      } else if (panes && it.mode === "map") {
        el.className = "on";
      }
      nav.appendChild(el);
    });
    return nav;
  }

  /* Make room for it. The pages come in two shapes and they need OPPOSITE
   * treatment, which is the whole reason this is a function and not a line of
   * CSS:
   *
   *   SCROLLING pages (guide, status, about) grow downward, so the bar would
   *   sit over the last paragraph. Padding at the bottom of the body is enough.
   *
   *   APP-SHELL pages (rv.html) are height:100% with overflow:hidden and size
   *   their own panels off that height - `main{height:calc(100% - 53px)}`.
   *   Padding does nothing there because nothing scrolls; the bar would simply
   *   cover the review buttons. Shrinking the BODY instead cascades into every
   *   percentage-height child, so the page lays itself out correctly with no
   *   knowledge of this component.
   */
  function fit(h) {
    var cs = getComputedStyle(document.body);
    var appShell = cs.overflow === "hidden" || cs.overflowY === "hidden";
    if (appShell) {
      document.body.style.height = "calc(100% - " + h + "px)";
    } else {
      var pad = parseFloat(cs.paddingBottom) || 0;
      document.body.style.paddingBottom = (pad + h) + "px";
    }
  }

  function go() {
    // A page that already has this bar (the map builds it with panes, below)
    // must not get a second one.
    if (document.querySelector("nav.sitenav")) return;
    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);
    var panes = !!window.SPARROW_NAV_PANES;
    var nav = build(panes);
    document.body.appendChild(nav);
    fit(Math.ceil(nav.getBoundingClientRect().height));
  }

  // 🚨 IMMEDIATELY IF THE BODY EXISTS, NOT ON DOMContentLoaded.
  // These are classic scripts at the end of <body>, so they all run BEFORE
  // that event. app.js binds its pane switching to #modes the moment it is
  // parsed, so waiting would have meant app.js finding no bar and the map's
  // About and Transparency tabs silently doing nothing - a bar that looks
  // right and is dead, which is the failure mode this session has already hit
  // twice. Included from <head> instead, there is no body yet, so that case
  // still waits.
  if (document.body) {
    go();
  } else {
    document.addEventListener("DOMContentLoaded", go);
  }
})();
