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
    // Businesses already pointing a camera at a street are the cheapest
    // coverage this project can get - the hardware and the sightline are
    // already paid for. It was reachable only from a paragraph inside
    // /app and a link near the bottom of the landing page.
    { href: "/business",     icon: "🏪", label: "Business" },
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
    /* The top link. Sized and coloured to read as a quiet secondary control,
       because on the map it sits next to "Add a camera" - the primary ask of
       the whole site - and must not compete with it. */
    /* 🚨 `display` IS !important, AND THAT IS NOT DEFENSIVENESS FOR ITS OWN
       SAKE. style.css line 295 is `@media (max-width:860px){header nav a{
       display:none}}` - a deliberate declutter of the map header on a phone,
       written long before anything was injected into it. It matches ANY anchor
       in that nav, so this link was invisible on every phone while being
       present, styled and correct in the DOM. Reported as "I don't see the
       login button anywhere".
       That rule has already done this once: its own comment records `nav a`
       previously matching the BOTTOM bar too and removing the only route to
       the contributor page. A rule that hides by tag and position will keep
       catching things that arrive later, so this link states what it is rather
       than hoping. */
    /* 🚨 ONE FIXED COLUMN, PLACED BY MEASUREMENT, NOT BY GUESSWORK.
       The link and the bug button were each positioned with their own hard
       offset from the top of the viewport - 10px and 56px. That works on a
       page whose header is short and lands ON TOP of the map's header on a
       phone, where it wraps to ~130px: reported with "My camera" sitting over
       the LIVE indicator and the bug button hidden behind the header
       completely. An offset guessed from one page cannot be right on all of
       them, so fit() measures the real header instead (see placeTools). */
    /* A ROW, not a column. Stacked, the second item landed on the map panel's
       "watched roads" checkbox - the corner is only one control tall before it
       runs into the page. Side by side, both sit in the strip above the panel
       and nothing below is covered. */
    ".swtools{position:fixed;z-index:9001;right:12px;display:flex;",
    "  flex-direction:row;align-items:center;gap:8px}",
    ".swtoplink{display:inline-flex !important;align-items:center;gap:6px;",
    "  white-space:nowrap;padding:7px 12px;border-radius:999px;",
    "  text-decoration:none;background:#141c28;border:1px solid #2a3547;",
    "  color:#cfe3f5;font:600 12.5px system-ui,-apple-system,sans-serif;",
    "  -webkit-tap-highlight-color:transparent;visibility:visible;opacity:1}",
    ".swtoplink:hover{border-color:#3d8cff;color:#fff}",
    /* On a narrow screen the header is deliberately stripped back for the
       search box, so an extra pill inside it would crowd the one control that
       matters there. It lifts out to the top-right corner instead - which is
       free on a phone, because refresh.js moves ITS button to the bottom right
       below 860px. Same element, no second copy to drift. */
    "@media (max-width:520px){.swtoplink{padding:6px 10px;font-size:12px}}",
    "@media print{.swtoplink{display:none !important}}",
    /* Under the top link, clear of both the header and refresh.js's button. */
    /* 44x44 to match refresh.js's button exactly: these two now sit in one
       stack on the map with Leaflet's own control above them, and three
       controls in a line that are three different sizes read as three
       accidents rather than one set. */
    ".swbug{position:fixed;z-index:9001;width:44px;height:44px;",
    "  border-radius:50%;border:1px solid #2a3547;background:#111621ee;",
    "  color:#cfe3f5;font-size:18px;line-height:1;cursor:pointer;",
    "  display:flex;align-items:center;justify-content:center;",
    "  backdrop-filter:blur(6px);-webkit-tap-highlight-color:transparent}",
    ".swbug:hover{border-color:#3d8cff}",
    "@media print{.swbug{display:none}}",
    ".swbugwrap{position:fixed;inset:0;z-index:9500;display:flex;",
    "  align-items:flex-end;justify-content:center;background:rgba(4,7,12,.72);",
    "  padding:16px}",
    ".swbugcard{background:#0f151f;border:1px solid #27354a;border-radius:16px;",
    "  padding:18px;max-width:460px;width:100%;color:#c7d2dc;",
    "  font:14px/1.6 system-ui,sans-serif;max-height:92vh;overflow-y:auto}",
    ".swbugcard b{color:#e8eef6;display:block;margin-bottom:8px;font-size:16px}",
    ".swbugcard p{margin:6px 0 10px}",
    ".swbugcard textarea{width:100%;background:#080b11;color:#e8eef6;",
    "  border:1px solid #2a3244;border-radius:9px;padding:10px;font:inherit;",
    "  resize:vertical;box-sizing:border-box}",
    ".swbugpick{display:block;text-align:center;padding:11px;border-radius:10px;",
    "  background:#131c27;border:1px solid #2a3244;margin-top:10px;",
    "  cursor:pointer;font-weight:600}",
    ".swbugshot img{max-width:100%;border-radius:9px;margin-top:10px;",
    "  border:1px solid #2a3244}",
    ".swbugnote{font-size:12px;color:#8a97a8;line-height:1.5}",
    ".swbugmsg{font-size:13px;color:#7fd1ff;min-height:18px}",
    ".swbugsend{width:100%;margin-top:8px;padding:13px;border-radius:10px;",
    "  border:0;background:#3d8cff;color:#04101f;font:700 15px system-ui;",
    "  cursor:pointer}",
    ".swbugsend:disabled{opacity:.6}",
    ".swbugclose{width:100%;margin-top:8px;padding:11px;border-radius:10px;",
    "  background:#1b2432;border:1px solid #2b3a4f;color:#c7d2dc;",
    "  font:600 14px system-ui;cursor:pointer}",
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

  /* 🔑 THE WAY BACK TO YOUR OWN CAMERA, AT THE TOP OF EVERY PAGE.
   *
   * Not in the bottom bar. That bar is already seven items and scrolls
   * sideways on a phone, so an eighth would be the one nobody sees - and this
   * is specifically for people who have just been told their camera is gone
   * and are looking for a way in.
   *
   * ⚠️ IT IS BUILT HERE RATHER THAN IN EACH PAGE because the pages do not
   * agree on what a header is: the map has <header class="bar"> with a <nav>,
   * the review page and photos have a bare <header>, and drive, business,
   * about, status, guide, transparency and hardware have none at all. Editing
   * nine files to add one link is how the review page ended up with no way
   * home in the first place. This adapts: into the header if there is one,
   * floating if there is not.
   *
   * It reads the key rather than always saying the same thing. Somebody who
   * already holds a camera does not need to be offered a sign-in - they need
   * the way to their camera - and a site whose whole pitch is "no account"
   * should not show a permanent Sign in button to a passer-by with nothing to
   * sign in to. Same slot, honest label either way.
   */
  /* Where the floating column goes: under the page's own header if it has
   * one, otherwise at the top. Measured, because the headers on these pages
   * range from nothing at all to the map's two-row bar on a phone - and that
   * bar changes height when the search field wraps, which no constant can
   * follow. Re-measured on resize and rotation for the same reason. */
  function placeTools() {
    var col = document.querySelector(".swtools");
    if (!col) return;
    var top = 10;

    /* 🚨 PREFER --headh, THE PAGE'S OWN ANSWER, OVER MEASURING IT MYSELF.
     *
     * app.js publishes the map header's real height as --headh and keeps it
     * updated on load, resize and orientationchange - it exists precisely
     * because the search box wraps onto a second row on a phone, taking the
     * header from ~56px to ~120px. refresh.js already positions its button
     * from it.
     *
     * My own getBoundingClientRect ran while the header was still one row, so
     * the column landed ON the search row and the Sign in pill covered the
     * FIND button. Reported with a screenshot. Measuring a thing that is still
     * settling gives a confident wrong answer; asking the page for the number
     * it maintains does not.
     */
    var hv = 0;
    try {
      hv = parseFloat(getComputedStyle(document.documentElement)
                        .getPropertyValue("--headh"));
    } catch (e) { hv = 0; }

    if (hv > 0) {
      top = Math.round(hv) + 10;
    } else {
      var hdr = document.querySelector("header");
      if (hdr) {
        var r = hdr.getBoundingClientRect();
        // Only clear it if it is actually pinned at the top of the screen; a
        // header that scrolls away should not push these buttons down the page.
        var pos = getComputedStyle(hdr).position;
        if ((pos === "fixed" || pos === "sticky" || r.top <= 1) && r.height > 0) {
          top = Math.round(r.bottom) + 10;
        }
      }
    }
    /* 🚨 AND CLEAR OF THE REFRESH BUTTON, WHICH LIVES AT THE SAME COORDINATES.
     *
     * refresh.js positions itself at right:12px, top:calc(var(--headh) + 10px).
     * This column computed the identical spot, so on the map the Sign in pill
     * and the bug button sat directly on top of it. Reported twice.
     *
     * Measured rather than hard-coded around, because refresh.js MOVES: below
     * 860px it drops to the bottom-right corner, and duplicating that
     * breakpoint here would be a second copy of a rule that is free to change.
     * If the button is up here, sit under it; if it has gone to the bottom,
     * take the space it left.
     */
    /* 🚨 SWAPPED, HIS CALL: SIGN IN TAKES THE TOP SLOT, REFRESH GOES BELOW IT.
     *
     * Sign in is the control a volunteer is hunting for - it is the way back to
     * a camera they think they have lost - and refresh is the one you reach for
     * only when something already looks wrong. The more-wanted control gets the
     * corner your thumb finds first.
     *
     * ⚠️ ONE FUNCTION OWNS THIS STACK NOW. refresh.js still styles its own
     * button; it just no longer decides WHERE it sits when this column is on
     * the page. Two components positioning themselves against the same corner
     * from different files is exactly how they ended up on top of each other
     * twice today - the fix is a single owner, not a third guess at the
     * offsets. If this column is not present (a page without sitenav.js),
     * refresh.js keeps its own placement and nothing here runs.
     */
    var ref = document.querySelector(".swrefresh");
    var refTop = top;                       // the slot the column is vacating
    /* 🚨 AND OUT OF THE SIDE PANEL, WHICH OWNS THE RIGHT EDGE ON A DESKTOP.
     *
     * Clearing the header and the refresh button still left the Sign in pill
     * sitting on the map's "watched roads" checkbox - a real control, covered.
     * The map's #panel occupies the right-hand side on a wide screen, so
     * "top-right, below the furniture" is not empty space there at all.
     *
     * Measured, and only when the panel is genuinely a right-hand column: on a
     * phone the same element is a bottom sheet spanning the full width, and
     * treating that as something to dodge would push these buttons off the
     * screen entirely.
     */
    var right = 12;
    var panel = document.querySelector(".panel, #panel");
    if (panel) {
      var pb = panel.getBoundingClientRect();
      if (pb.width > 0 && pb.left > window.innerWidth / 2
          && pb.right > window.innerWidth - 40) {
        right = Math.round(window.innerWidth - pb.left) + 12;
      }
    }
    /* TWO GROUPS, EACH WHERE IT BELONGS.
     *
     *   the CORNER  - Sign in, alone. It is the widest control and the one a
     *                 volunteer is hunting for, so it gets the strip above the
     *                 panel where nothing else lives.
     *   the MAP     - the round buttons, in one column under Leaflet's own
     *                 control, clear of the panel: heat, bug, refresh. Same
     *                 size, same gap, same right edge, so they read as one set
     *                 rather than three things that happened to land nearby.
     */
    col.style.right = "12px";
    col.style.top = "calc(" + top + "px + env(safe-area-inset-top))";

    // Start the map stack under whatever Leaflet has put in its top-right
    // corner, so our buttons continue that column instead of colliding with it.
    var stackTop = top;
    var lt = document.querySelector(".leaflet-top.leaflet-right");
    if (lt) {
      var lb = lt.getBoundingClientRect();
      if (lb.height > 0) stackTop = Math.round(lb.bottom) + 8;
    }

    var bug = document.querySelector(".swbug");
    var GAP = 8, SIZE = 44;
    if (bug) {
      bug.style.right = right + "px";
      bug.style.top = "calc(" + stackTop + "px + env(safe-area-inset-top))";
    }
    /* Refresh sits under the bug, on the same right edge. Only while it is a
     * TOP-corner button - below 860px refresh.js moves it to the bottom right
     * on purpose, and that placement is better than anything invented here. */
    if (ref) {
      var rb = ref.getBoundingClientRect();
      if (rb.height > 0 && rb.top < window.innerHeight / 2) {
        ref.style.right = right + "px";
        ref.style.top = "calc(" + (stackTop + (bug ? SIZE + GAP : 0))
                      + "px + env(safe-area-inset-top))";
      } else {
        ref.style.top = "";
        ref.style.right = "";
        // With refresh at the bottom, the bug button is the whole stack.
        if (bug) bug.style.top = "calc(" + stackTop
                               + "px + env(safe-area-inset-top))";
      }
    }
  }

  /* ⚠️ NOT ON /drive. Driving mode already has its own control rail down the
   * right-hand side and its own way out, and the screenshot showed these two
   * landing on top of both. Two more floating buttons over a moving map, in a
   * car, is clutter at best and a mis-tap at worst - the one page where the
   * fewest controls is the right answer. */
  var NO_TOOLS = { "/signin": 1, "/app": 1, "/login/camera": 1, "/drive": 1 };

  function topLink() {
    if (document.querySelector(".swtoplink")) return;
    var here = location.pathname.replace(/\/+$/, "") || "/";
    if (NO_TOOLS[here]) return;

    var has = false;
    try { has = !!JSON.parse(localStorage.getItem("sparrow.node") || "null"); }
    catch (e) { has = false; }

    var a = document.createElement("a");
    a.className = "swtoplink";
    a.href = has ? "/app" : "/signin";
    a.textContent = has ? "📷 My camera" : "🔑 Sign in";
    a.title = has ? "Open your camera"
                  : "Already set up a camera? Sign in with your key";

    // ⚠️ IT NO LONGER GOES INSIDE THE HEADER. Injecting it there put it in the
    // same row as the wordmark and the live indicator, and on a phone it sat
    // on top of them. It also meant fighting style.css's deliberate
    // `header nav a{display:none}` declutter. A column of its own, placed
    // under whatever header exists, is right on every page instead of on one.
    toolCol().appendChild(a);
  }

  function toolCol() {
    var col = document.querySelector(".swtools");
    if (!col) {
      col = document.createElement("div");
      col.className = "swtools";
      document.body.appendChild(col);
    }
    return col;
  }

  /* 🐞 REPORT A BUG, BESIDE THE WAY BACK IN.
   *
   * Next to the sign-in link because the two are wanted by the same person at
   * the same moment: something is wrong and they want a human. Until now the
   * only route was finding an email address on another page, which nobody in
   * the middle of a problem does - every fault fixed today arrived because
   * somebody happened to already have the operator's contact details.
   *
   * A screenshot is the whole point. "It says no road visible but clearly it
   * is", "the login button is not anywhere", "it opens on Lansing" - each took
   * one picture to diagnose and would have taken a dozen messages to describe.
   */
  function bugButton() {
    if (document.querySelector(".swbug")) return;
    if (NO_TOOLS[location.pathname.replace(/\/+$/, "") || "/"]) return;
    var b = document.createElement("button");
    b.type = "button";
    b.className = "swbug";
    b.textContent = "🐞";
    b.title = "Report a problem";
    b.setAttribute("aria-label", "report a problem");
    b.addEventListener("click", openBugSheet);
    // Positioned by placeTools into the map-side stack, not the corner.
    document.body.appendChild(b);
  }

  function openBugSheet() {
    if (document.querySelector(".swbugwrap")) return;
    var back = document.createElement("div");
    back.className = "swbugwrap";
    var card = document.createElement("div");
    card.className = "swbugcard";
    // Built with DOM calls rather than one innerHTML blob: this file is loaded
    // on every page including ones under a strict CSP, and nothing here should
    // depend on markup parsing.
    var h = document.createElement("b"); h.textContent = "Report a problem";
    var p1 = document.createElement("p");
    p1.textContent = "What went wrong? A screenshot helps more than anything - "
      + "most bugs are obvious in a picture and hard to describe.";
    var ta = document.createElement("textarea");
    ta.className = "swbugdesc"; ta.rows = 4;
    ta.placeholder = "What were you doing, and what happened?";
    var pick = document.createElement("label");
    pick.className = "swbugpick";
    pick.textContent = "📷 Attach a screenshot";
    var input = document.createElement("input");
    input.type = "file"; input.accept = "image/*"; input.hidden = true;
    pick.appendChild(input);
    var prev = document.createElement("div"); prev.className = "swbugshot";
    var note = document.createElement("p");
    note.className = "swbugnote";
    note.textContent = "\u26A0\uFE0F Check your screenshot does not show your "
      + "camera key or QR code - those are the password to your camera. It is "
      + "sent privately to the person who runs SparrowMap and is never published.";
    var msg = document.createElement("div"); msg.className = "swbugmsg";
    var send = document.createElement("button");
    send.className = "swbugsend"; send.type = "button"; send.textContent = "Send";
    var cancel = document.createElement("button");
    cancel.className = "swbugclose"; cancel.type = "button";
    cancel.textContent = "Cancel";
    card.append(h, p1, ta, pick, prev, note, msg, send, cancel);
    back.appendChild(card);
    document.body.appendChild(back);

    var shot = "";
    input.addEventListener("change", function () {
      var f = this.files && this.files[0];
      if (!f) return;
      if (f.size > 12 * 1024 * 1024) {
        msg.textContent = "That image is very large - please crop it.";
        return;
      }
      var fr = new FileReader();
      fr.onload = function () {
        /* Downscale in the BROWSER. A phone screenshot is several megabytes
         * and the box has 3 GB and two cores. Re-encoding here also drops any
         * EXIF before it leaves the device, rather than trusting the server to
         * strip a GPS fix out of a picture sent by somebody reporting a bug on
         * a project about locations. */
        var im = new Image();
        im.onload = function () {
          var sc = Math.min(1, 1400 / Math.max(im.width, im.height));
          var cv = document.createElement("canvas");
          cv.width = Math.max(1, Math.round(im.width * sc));
          cv.height = Math.max(1, Math.round(im.height * sc));
          cv.getContext("2d").drawImage(im, 0, 0, cv.width, cv.height);
          shot = cv.toDataURL("image/jpeg", 0.8);
          prev.textContent = "";
          var thumb = new Image(); thumb.src = shot; prev.appendChild(thumb);
          msg.textContent = "Screenshot attached.";
        };
        im.onerror = function () { msg.textContent = "Could not read that image."; };
        im.src = fr.result;
      };
      fr.readAsDataURL(f);
    });

    send.addEventListener("click", function () {
      var desc = ta.value.trim();
      if (!desc && !shot) {
        msg.textContent = "Say what went wrong, or attach a screenshot.";
        return;
      }
      send.disabled = true;
      msg.textContent = "Sending\u2026";
      fetch("/api/bug", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ desc: desc, shot: shot, page: location.pathname })
      }).then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      }).then(function (res) {
        if (!res.ok) {
          send.disabled = false;
          msg.textContent = res.d.error || "Could not send that.";
          return;
        }
        card.textContent = "";
        var t = document.createElement("b"); t.textContent = "Thank you";
        var pp = document.createElement("p");
        pp.textContent = "That went straight to the person who runs SparrowMap. "
          + "Reference " + res.d.id + ".";
        var cl = document.createElement("button");
        cl.className = "swbugclose"; cl.type = "button"; cl.textContent = "Close";
        cl.addEventListener("click", function () { back.remove(); });
        card.append(t, pp, cl);
      }).catch(function () {
        send.disabled = false;
        msg.textContent = "Could not reach SparrowMap. Check your connection.";
      });
    });

    back.addEventListener("click", function (e) {
      if (e.target === back || e.target.className === "swbugclose") back.remove();
    });
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
    topLink();
    bugButton();
    placeTools();
    // The map's header grows a row when the search field wraps, so the
    // measurement has to survive a rotation rather than be taken once.
    window.addEventListener("resize", placeTools);
    window.addEventListener("orientationchange", placeTools);
    // --headh is published on 'load', which is AFTER this script runs, so the
    // first placement has to be redone once the page settles.
    window.addEventListener("load", placeTools);
    setTimeout(placeTools, 400);
    setTimeout(placeTools, 1500);
    // refresh.js adds its button from its own script tag, which may run after
    // this one; without a later pass the column would keep the spot it took
    // before the button existed.
    setTimeout(placeTools, 3000);
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
