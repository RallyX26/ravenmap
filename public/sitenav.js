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
    { href: "/",             icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4 3 6.5v13L9 17l6 3 6-2.5v-13L15 7z"/><path d="M9 4v13M15 7v13"/></svg>', label: "Map",          mode: "map" },
    { href: "/drive",        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M5 16v2a1 1 0 0 1-1 1H3.5A1.5 1.5 0 0 1 2 17.5V12l2.2-5.1A2 2 0 0 1 6 5.6h12a2 2 0 0 1 1.8 1.3L22 12v5.5a1.5 1.5 0 0 1-1.5 1.5H20a1 1 0 0 1-1-1v-2"/><path d="M2 12h20"/><circle cx="6.5" cy="15" r="1"/><circle cx="17.5" cy="15" r="1"/></svg>', label: "Driving" },
    // Sparrow Send - end-to-end encrypted 1:1 messaging built on the same
    // no-phone-number identity. Announced 2026-08-24.
    { href: "/send",         icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="M22 2 15 22 11 13 2 9z"/></svg>', label: "Send" },
    // Growth, live capacity and costs, generated from the database. Labelled
    // "Support" rather than "Donate" because the page is mostly numbers - the
    // costs and the bad retention figure included - and the ask is the last
    // thing on it rather than the point of it.
    { href: "/support",      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20.8 5.6a5 5 0 0 0-7.1 0L12 7.3l-1.7-1.7a5 5 0 0 0-7.1 7.1l8.8 8.8 8.8-8.8a5 5 0 0 0 0-7.1z"/></svg>', label: "Support" },
    { href: "/app",          icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5A1.5 1.5 0 0 1 4.5 7h2.2l1.2-2h8.2l1.2 2h2.2A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/><circle cx="12" cy="13" r="3.2"/></svg>', label: "Add a camera" },
    // Businesses already pointing a camera at a street are the cheapest
    // coverage this project can get - the hardware and the sightline are
    // already paid for. It was reachable only from a paragraph inside
    // /app and a link near the bottom of the landing page.
    { href: "/IPCamera",     icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h11a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H4z"/><path d="M17 10.5 21 8v7l-4-2.5z"/><path d="M8 15v4M5 19h6"/></svg>', label: "IP Camera" },
    // Feed sightings from your own code/device via the two endpoints. Dev
    // facing, but it belongs in the bar so people running their own kit (a Pi,
    // an SBC, a fleet) can find it without being told the URL.
    { href: "/contribute",   icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="12" r="2.2"/><circle cx="17" cy="6" r="2.2"/><circle cx="17" cy="18" r="2.2"/><path d="M7.9 11 15.1 7M7.9 13l7.2 4"/></svg>', label: "Contribute" },
    // Every how-to in one place - how it works, add a camera, IP camera, the
    // API, the RF beta, review. Put here so the RF/phone/Pi beta pages are
    // reachable from the map and every page, not just by knowing the URL.
    { href: "/guides",       icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11v16H5.5A1.5 1.5 0 0 1 4 18.5z"/><path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H13v16h5.5a1.5 1.5 0 0 0 1.5-1.5z"/></svg>', label: "Guides" },
    { href: "/rv",           icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5 9.5 18 20 6.5"/></svg>',       label: "Review" },
    { href: "/about",        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><circle cx="12" cy="7.8" r=".9" fill="currentColor" stroke="none"/></svg>',       label: "About",        mode: "about" },
    { href: "/transparency", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.8-6.5 10-6.5S22 12 22 12s-3.8 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.8"/></svg>', label: "Transparency", mode: "transparency" },
    { href: "/status",       icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19v-4M9.3 19V11M14.7 19V7M20 19V4"/></svg>', label: "Status" },
    // The Tor onion mirror - a censorship-resistant way to reach the map. Here
    // so people who need it can find it from any page.
    { href: "/tor",          icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3c-3 3-4.5 6-4.5 9a4.5 4.5 0 0 0 9 0c0-3-1.5-6-4.5-9z"/><path d="M12 21v-4.5"/></svg>', label: "Tor" }
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
    ".swtools{display:flex;flex-direction:row;align-items:center;gap:8px}",
    /* 🚨 THE CORNERS ARE STACKS NOW, AND THAT IS THE WHOLE FIX.
       Five separate overlap reports came from five floating controls that each
       positioned THEMSELVES against the same two corners from a different file:
       sitenav's Sign in pill, the bug button, the old refresh button
       (right:12px, and it MOVED to the bottom below 860px), install.js
       (right:12px, bottom:70px - six pixels from refresh's 64px, so they drew
       on top of each other), and Leaflet's own heat button. Every fix up to
       this one was a sixth guess at the offsets, which is why a new pair
       collided each time. (Refresh has since been removed outright.)
       A flex column cannot overlap its own children. So the corner became a
       real container that everything is MOVED INTO, and the only numbers left
       to measure are where each container starts - two, instead of one per
       control per page. Anything floating added later joins the stack and is
       laid out for free. */
    /* 🚨 THE TOOLS ARE PART OF THE HEADER NOW, NOT FLOATING OVER THE PAGE.
       Six overlap reports, and every one of them was the same mistake wearing
       a different hat: a control positioned OVER the page cannot know what is
       underneath it. Stacking them fixed the controls colliding with EACH
       OTHER, and the very next screenshot showed the stack sitting on the
       Transparency heading and the search box instead - because a stack still
       floats, and floating is the bug.
       An element in normal flow reserves its own space. The header grows by a
       row, everything below it moves down, and there is no page, no width and
       no future control where that can overlap anything. app.js publishes the
       header's measured height as --headh and the map sizes itself from it, so
       the extra row is accounted for everywhere without a second number.
       ⚠️ Map controls are NOT in here. Leaflet's own buttons belong over the
       map - a map is a canvas, and covering a patch of it is what a map control
       is for. This row is for the controls that belong to the SITE. */
    ".swbar{display:flex;align-items:center;justify-content:flex-end;gap:8px;",
    "  flex:1 0 100%;order:99;padding:2px 0 2px}",
    /* The strip a header-less page gets. Deliberately plain: it is furniture,
       not a second brand bar, and it must not compete with the page's own
       first heading. */
    ".swstrip{display:flex;justify-content:flex-end;padding:8px 12px 0;",
    "  width:100%;box-sizing:border-box}",
    ".swstrip .swbar{padding:0}",
    "#swTR,#swBR{position:fixed;display:flex;flex-direction:column;",
    "  align-items:flex-end;gap:8px;pointer-events:none}",
    "#swTR{z-index:9001}#swBR{z-index:1250;right:12px}",
    /* The children gave up their own placement when they joined. !important
       because install.js still ships the stylesheet that puts its button in a
       corner, and it is right to - that rule is what positions it on a page
       that has no stack. (refresh.js did the same until it was removed.) */
    /* 🚨 .swbar BELONGS IN THIS LIST, AND LEAVING IT OUT COST A WHOLE ROUND.
       The reset was written for the two floating stacks and the header row was
       added afterwards, so the adopted buttons kept their own position:fixed
       and went on drawing at the coordinates their own stylesheet chose - the
       Sign in pill and the bug button measured 44x32px on top of each other
       INSIDE a flex row, which is a contradiction until you notice that a
       fixed child is not laid out by its parent at all.
       An element only joins a layout if it is actually in it. */
    "#swTR>*,#swBR>*,.swbar>*{pointer-events:auto;position:relative !important;",
    "  top:auto !important;right:auto !important;bottom:auto !important;",
    "  left:auto !important;margin:0 !important;float:none !important}",
    /* And the page must end above the nav bar - see padBottom(). Not a blanket
       rule here on purpose: the map and /rv are full-height documents with
       overflow:hidden, and padding their body moves the map instead of the
       text. Only a page that actually scrolls needs it. */
    ".swtoplink{display:inline-flex !important;align-items:center;gap:6px;",
    "  white-space:nowrap;padding:7px 12px;border-radius:999px;",
    "  text-decoration:none;background:#141c28;border:1px solid #2a3547;",
    "  color:#cfe3f5;font:600 12.5px system-ui,-apple-system,sans-serif;",
    "  -webkit-tap-highlight-color:transparent;visibility:visible;opacity:1}",
    ".swtoplink:hover{border-color:#3d8cff;color:#fff}",
    /* On a narrow screen the header is deliberately stripped back for the
       search box, so an extra pill inside it would crowd the one control that
       matters there. It lifts out to the top-right corner instead, which is
       free on a phone. Same element, no second copy to drift. */
    "@media (max-width:520px){.swtoplink{padding:6px 10px;font-size:12px}}",
    "@media print{.swtoplink{display:none !important}}",
    /* Under the top link, clear of the header. */
    /* 44x44, the shared size for every round control on the map: they sit in
       one stack with Leaflet's own control above them, and controls in a line
       that are different sizes read as several accidents rather than one set.
       ⚠️ Keep any new round control at 44x44 for the same reason. */
    ".swbug{position:fixed;z-index:9001;width:44px;height:44px;",
    "  border-radius:50%;border:1px solid #2a3547;background:#111621ee;",
    "  color:#cfe3f5;font-size:18px;line-height:1;cursor:pointer;",
    "  display:flex;align-items:center;justify-content:center;",
    "  backdrop-filter:blur(6px);-webkit-tap-highlight-color:transparent}",
    ".swbug:hover{border-color:#3d8cff}",
    "@media print{.swbug{display:none}}",
    /* 🚨 CENTRED, AND THE SCROLL LIVES ON THE WRAP, NOT THE CARD.
       Reported from a phone: the Send and Close buttons sat below the bottom
       of the screen with no way to reach them. Two causes, and both had to go.

       `align-items:flex-end` pinned the card to the bottom of the wrap, and
       the wrap is `inset:0` - i.e. the full viewport. On iOS that box is the
       viewport with the browser chrome HIDDEN, so while the URL bar is on
       screen the actually-visible area is shorter than the wrap and anything
       aligned to its bottom edge is underneath the chrome. `max-height:92vh`
       on the card had the same bug for the same reason: 92vh can be taller
       than the screen you are looking at.

       Scrolling now belongs to the wrap, with `margin:auto` on the card. That
       centres it when it fits and scrolls the whole overlay when it does not.
       `align-items:center` would NOT be safe here: when a flex item overflows
       a centred container the overflow goes off BOTH ends, so the top of the
       card becomes unreachable instead of the bottom. `margin:auto` does not
       have that failure. dvh is used where supported so the height tracks the
       chrome instead of ignoring it. */
    ".swbugwrap{position:fixed;inset:0;z-index:9500;display:flex;",
    "  justify-content:center;background:rgba(4,7,12,.72);",
    "  padding:16px 16px calc(16px + env(safe-area-inset-bottom));",
    "  overflow-y:auto;-webkit-overflow-scrolling:touch;",
    "  overscroll-behavior:contain}",
    "@supports (height:100dvh){.swbugwrap{height:100dvh}}",
    ".swbugcard{background:#0f151f;border:1px solid #27354a;border-radius:16px;",
    "  padding:18px;max-width:460px;width:100%;color:#c7d2dc;margin:auto;",
    "  font:14px/1.6 system-ui,sans-serif}",
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
    /* Matches the map shell's bar (dark glass, one blue accent, even width on a
       wide screen, horizontal scroll on a phone) so every page's bottom bar
       looks the same, not the old teal/red one. */
    "nav.sitenav{position:fixed;left:0;right:0;bottom:0;z-index:1200;display:flex;",
    "  background:rgba(7,10,16,.9);-webkit-backdrop-filter:blur(18px) saturate(1.4);",
    "  backdrop-filter:blur(18px) saturate(1.4);border-top:1px solid rgba(120,170,235,.14);",
    "  overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none}",
    "nav.sitenav::-webkit-scrollbar{display:none}",
    "nav.sitenav a,nav.sitenav button{flex:1 1 0;min-width:0;white-space:nowrap;",
    "  background:none;border:0;color:#8ea2b8;cursor:pointer;text-decoration:none;",
    "  text-align:center;padding:10px 4px calc(10px + env(safe-area-inset-bottom));",
    "  font:600 10.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;",
    "  letter-spacing:.09em;text-transform:uppercase;border-top:2px solid transparent;",
    "  -webkit-tap-highlight-color:transparent}",
    "@media(max-width:820px){nav.sitenav a,nav.sitenav button{flex:0 0 auto;min-width:64px}}",
    "nav.sitenav a{color:#8ea2b8}",
    "nav.sitenav .on,nav.sitenav a.on{color:#2b86e0;border-top-color:#2b86e0;background:rgba(43,134,224,.10)}",
    "nav.sitenav .ic{display:block;margin-bottom:3px;line-height:0}",
    "nav.sitenav .ic svg{width:19px;height:19px;display:inline-block;vertical-align:middle}"
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
     * header from ~56px to ~120px.
     *
     * My own getBoundingClientRect ran while the header was still one row, so
     * the column landed ON the search row and the Sign in pill covered the
     * FIND button. Reported with a screenshot. Measuring a thing that is still
     * settling gives a confident wrong answer; asking the page for the number
     * it maintains does not.
     */
    /* 🚨 TAKE THE LARGEST HONEST ANSWER, NOT THE FIRST ONE.
     *
     * This preferred --headh and fell back to measuring. Both can be wrong at
     * the moment they are read: --headh is published by app.js on 'load', and a
     * measurement taken while the search box has not yet wrapped describes a
     * one-row header that is about to become two. Trusting either one alone put
     * the pill back on top of the FIND button on a phone - the same collision,
     * for the third time, from a different direction.
     *
     * So: consult every source that could know, and take the LOWEST edge any of
     * them reports. Being too low costs a few pixels of map. Being too high
     * covers a control, which is the failure that keeps happening.
     */
    var bottoms = [];
    try {
      var hv = parseFloat(getComputedStyle(document.documentElement)
                            .getPropertyValue("--headh"));
      if (hv > 0) bottoms.push(hv);
    } catch (e) { /* no variable on this page */ }

    var hdr = document.querySelector("header");
    if (hdr) {
      var r = hdr.getBoundingClientRect();
      var pos = getComputedStyle(hdr).position;
      if ((pos === "fixed" || pos === "sticky" || r.top <= 1) && r.height > 0) {
        bottoms.push(r.bottom);
      }
    }
    // The search row is the thing that actually wraps, and on the map it is the
    // lowest part of the header - so ask it directly rather than inferring it.
    var form = document.querySelector("header form, header .plateform, #plateform");
    if (form) {
      var fr = form.getBoundingClientRect();
      if (fr.height > 0 && fr.top < window.innerHeight / 2) bottoms.push(fr.bottom);
    }
    if (bottoms.length) top = Math.round(Math.max.apply(null, bottoms)) + 10;
    /* The refresh button used to be positioned from here as well, because it
     * placed itself at the identical coordinates and the two collided twice.
     * It has since been removed entirely (his call) along with refresh.js, so
     * this column is now the only thing claiming the corner.
     *
     * ⚠️ THE RULE THAT CAUSED THAT COLLISION STILL APPLIES: one function owns
     * this stack. Anything new that wants the top-right gets adopted here, not
     * positioned from its own file. Two components measuring the same corner
     * from different files is exactly how they ended up on top of each other.
     */
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
    /* ------------------------------------------------------------------
     * Build the two stacks, then place only the stacks.
     * ------------------------------------------------------------------ */
    function stack(id) {
      var el = document.getElementById(id);
      if (!el) {
        el = document.createElement("div");
        el.id = id;
        document.body.appendChild(el);
      }
      return el;
    }
    function adopt(box, el) {
      if (!el) return;
      if (el.parentNode !== box) box.appendChild(el);
    }

    var TR = stack("swTR"), BR = stack("swBR");

    /* The header row, where it exists. Every page that has a header gets one;
     * a page without one falls back to the corner stack below, which is still
     * better than each control choosing for itself. */
    /* 🚨 AND WHEN THERE IS NO HEADER, MAKE ONE. DO NOT FALL BACK TO FLOATING.
     *
     * Seven pages here have no <header> at all - transparency, status, about,
     * guide, hardware, bugs, business - and they were the pages the corner
     * stack was still floating over. On a phone that is a 44px button sitting
     * on the body text of a page whose entire job is to be read, which is the
     * screenshot that started this: the bug button on the Transparency
     * paragraph, the Sign in pill across the heading.
     *
     * Moving the tools into a header only helped the pages that had one. So a
     * page without one gets a strip of its own at the top, in flow, and the
     * floating corner stops being used anywhere. "Position it more carefully"
     * has now been tried six times; reserving the space is the version that
     * cannot come back. */
    var head = document.querySelector("header.bar, header");
    var bar = null;
    if (!head) {
      head = document.querySelector(".swstrip");
      if (!head && document.body) {
        head = document.createElement("div");
        head.className = "swstrip";
        document.body.insertBefore(head, document.body.firstChild);
      }
    }
    if (head) {
      bar = head.querySelector(".swbar");
      if (!bar) {
        bar = document.createElement("div");
        bar.className = "swbar";
        head.appendChild(bar);
      }
      /* 🚨 A ROW ONLY WRAPS IF THE CONTAINER LETS IT.
       *
       * .swbar asks for flex:1 0 100%, which is meant to push it onto a line of
       * its own under the search box. The map header is a flex row with the
       * default flex-wrap:nowrap, so instead of wrapping it simply STRETCHED
       * the header: measured 1892px of bar inside a 1568px viewport, putting
       * Sign in at x=2538 - off the right-hand edge, invisible, on the busiest
       * page on the site.
       *
       * Nothing overlapped, which is why this survived an overlap check and had
       * to be caught by reading the coordinates. "Not colliding" and "on the
       * screen" are different claims. */
      if (getComputedStyle(head).display.indexOf("flex") >= 0) {
        head.style.flexWrap = "wrap";
      }
    }

    /* Order top to bottom: the control someone is HUNTING for goes first.
     * Sign in is the way back to a camera a volunteer thinks they have lost;
     * refresh is what you reach for only once something already looks wrong. */
    var site = bar || TR;
    /* 🚨 "What's new" JOINS THE STACK instead of floating alone. Reported from an
     * iPhone: on a phone the map header wraps and the header's own "What's new"
     * button landed on a second row by itself (top-left), while Sign in and the
     * bug button sat lower-right - three controls at three heights, visibly
     * unaligned. It is exactly the kind of thing this stack exists to absorb: one
     * owner, one right-aligned group. Adopted FIRST so it reads left-to-right as
     * What's new · Sign in · bug. Only index.html has it; adopt() no-ops on null
     * elsewhere. The button node is unchanged, so its modal handler still binds. */
    adopt(site, document.getElementById("newsopen"));
    adopt(site, col);

    /* Leaflet's own controls join the same column rather than being dodged.
     * Reading their edge and stacking under it - the previous approach - only
     * works while Leaflet keeps its margin and its corner, and put the heat
     * button 2px out of line the one time it did not. Moving them in makes the
     * alignment structural. Re-runs of this function re-adopt anything Leaflet
     * has added since, which is why the map's own buttons stay in the set. */
    var lt = document.querySelector(".leaflet-top.leaflet-right");
    if (lt) {
      [].slice.call(lt.children).forEach(function (c) { adopt(TR, c); });
    }

    adopt(site, document.querySelector(".swbug"));

    /* 🚨 REFRESH AND THE INSTALL BANNER GO TO THE SAME CORNER, SO THEY GO TO
     * THE SAME STACK. This is the pair in the screenshot: "Add to home screen"
     * drawn straight through the refresh button, because 70px and 64px are not
     * far enough apart to be two rows. Whichever corner refresh has chosen for
     * this width, the banner is now directly above it and never on it. */
    /* The refresh button used to be adopted into Leaflet's own top-right
     * corner here, so that it and the fire button laid out as one column
     * instead of computing coordinates near each other. Both the button and
     * refresh.js are now gone.
     *
     * ⚠️ KEEP THE TECHNIQUE IF ANYTHING ELSE EVER WANTS THAT CORNER: append it
     * to `.leaflet-top.leaflet-right` and let Leaflet do the spacing, and look
     * the corner up INSIDE placeTools rather than at module scope - at module
     * scope Leaflet has not built it yet, so the lookup is always null and the
     * control silently stays where it was. */
    adopt(BR, document.querySelector(".instwrap"));

    TR.style.right = right + "px";
    TR.style.top = "calc(" + top + "px + env(safe-area-inset-top))";
    TR.style.display = TR.children.length ? "flex" : "none";

    /* The bottom stack clears the nav bar by measuring it, because the nav is
     * the one piece of furniture that is genuinely a fixed height per page. */
    var navh = 0;
    var nav = document.querySelector("nav.sitenav");
    if (nav) {
      var nb = nav.getBoundingClientRect();
      if (nb.height > 0) navh = Math.round(nb.height);
    }
    /* 🚨 AND CLEAR THE MAP'S FILTER ROW, NOT JUST THE NAV. Reported from a phone:
     * the "Add to home screen" banner sat on top of the View / Layers controls.
     * On a phone the panel is in flow right under a short map, so its `.win`
     * filter row lands in the same band as this stack. When that row is in the
     * lower half of the screen, lift the stack above it (capped, so it can never
     * jump to the top of the page). */
    var floor = navh + 10;
    var win = document.querySelector(".win");
    if (win) {
      var wb = win.getBoundingClientRect();
      if (wb.height > 0 && wb.top < window.innerHeight
          && wb.bottom > window.innerHeight * 0.5) {
        var above = Math.round(window.innerHeight - wb.top) + 10;
        floor = Math.max(floor, Math.min(above, Math.round(window.innerHeight * 0.6)));
      }
    }
    BR.style.bottom = "calc(" + floor + "px + env(safe-area-inset-bottom))";
    BR.style.display = BR.children.length ? "flex" : "none";

    /* 🚨 THE PAGE HAS TO END ABOVE THE NAV BAR.
     *
     * The nav is fixed to the bottom of the window, so on a scrolling page the
     * last ~58px of content is drawn underneath it and cannot be reached by
     * scrolling further - measured on /transparency, where it swallowed the
     * end of the policy table, the one thing that page exists to show.
     *
     * ⚠️ ONLY where the document actually scrolls. The map and /rv are
     * full-height documents with overflow:hidden; padding their body moves the
     * map down instead of moving text up, which is a worse bug than the one
     * being fixed. Measured rather than guessed from the path. */
    if (nav && navh && document.body) {
      var bodyStyle = getComputedStyle(document.body);
      var scrolls = bodyStyle.overflow !== "hidden"
                 && getComputedStyle(document.documentElement).overflow !== "hidden";
      if (scrolls) {
        document.body.style.paddingBottom =
          "calc(" + (navh + 16) + "px + env(safe-area-inset-bottom))";
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

    /* 🚨 NOT "MY CAMERA" ON THE MAP. HIS CALL, AND HE IS RIGHT.
     *
     * This pill does two different jobs depending on who is holding the phone.
     * For somebody with no camera it is "Sign in", the way back to a camera
     * they think they have lost, and that belongs on the busiest page on the
     * site. For somebody who already HAS one it is "My camera" - a shortcut to
     * /app, which the bottom bar already offers as "Add a camera", on the one
     * page where map space is the whole point.
     *
     * So the sign-in half stays everywhere and the shortcut half stops
     * competing with the map. Same element, one condition, no second copy. */
    var onMap = (here === "/");
    if (has && onMap) return;

    var a = document.createElement("a");
    a.className = "swtoplink";
    a.href = has ? "/app" : "/signin";
    a.textContent = has ? "My camera" : "Sign in";
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
    b.innerHTML = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M12 8a4 4 0 0 1 4 4v3a4 4 0 0 1-8 0v-3a4 4 0 0 1 4-4z"/><path d="M9.5 8.5 8 6.5M14.5 8.5 16 6.5M8 12H4.5M16 12h3.5M8.4 16 5.5 18M15.6 16l2.9 2"/></svg>';
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
    pick.textContent = "Attach a screenshot";
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
    /* Re-place over the first few seconds rather than guessing when the page
     * has settled. Fonts load, the search box wraps, other scripts add their
     * own buttons from their own script tags, and app.js publishes --headh on
     * 'load' - each of those moves something this depends on, and every one of
     * them has produced a collision at least once. Six cheap reads beat one
     * confident measurement taken too early. */
    [200, 600, 1200, 2500, 4000].forEach(function (ms) {
      setTimeout(placeTools, ms);
    });
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
