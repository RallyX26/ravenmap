/* What changed, shown once, to the people it applies to.
 *
 * 🚨 AN UPDATE POPUP IS A THING THAT GETS ABUSED, so the constraints are in the
 * code and not only in the etiquette:
 *
 *   * It shows AT MOST ONE update - the newest unseen one. A queue of modals is
 *     how people learn to dismiss without reading, and the next one will be the
 *     one that mattered.
 *   * 'owner' updates are shown only to devices that have actually registered a
 *     camera. A visitor reading the map is not interrupted to be told about a
 *     feature they cannot use.
 *   * Dismissing records the id, so it never returns. Ids are never reused -
 *     see updates.json.
 *   * It never blocks the page underneath. This map exists to show live traffic;
 *     an announcement that hides it is worse than no announcement.
 *   * If updates.json is missing, malformed, or the fetch fails, NOTHING is
 *     shown. An announcement channel that can break the page it announces into
 *     is not worth having.
 */
(function () {
  'use strict';

  var SEEN = 'sparrow.updates.seen';   // array of ids already dismissed
  var NODE = 'sparrow.node';           // set by the camera app on this device

  function seen() {
    try { return JSON.parse(localStorage.getItem(SEEN) || '[]') || []; }
    catch (e) { return []; }
  }
  function markSeen(id) {
    try {
      var s = seen();
      if (s.indexOf(id) < 0) s.push(id);
      // Keep it from growing without bound; ids are small and few, but this is
      // localStorage and something else on this origin has to live there too.
      localStorage.setItem(SEEN, JSON.stringify(s.slice(-50)));
    } catch (e) { /* private mode: it will show again, which is survivable */ }
  }
  function ownsCamera() {
    try {
      var n = JSON.parse(localStorage.getItem(NODE) || 'null');
      return !!(n && n.id && n.token);
    } catch (e) { return false; }
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /* Never land on top of the bottom navigation. On the map that bar is Map /
   * About / Transparency / Add a camera - the whole of the phone's navigation -
   * and a toast pinned to `bottom: 12px` sat right across it. An announcement
   * that covers the way out of the page is worse than no announcement. */
  function bottomOffset() {
    var gap = 12;
    var nav = document.getElementById('modes');
    if (nav) {
      var r = nav.getBoundingClientRect();
      // Only if it really is pinned to the bottom of the viewport.
      if (r.height && r.bottom >= window.innerHeight - 2) gap += r.height;
    }
    return 'calc(' + gap + 'px + env(safe-area-inset-bottom, 0px))';
  }

  function show(u) {
    if (document.getElementById('updateToast')) return;

    var box = document.createElement('div');
    box.id = 'updateToast';
    box.setAttribute('role', 'status');
    Object.assign(box.style, {
      position: 'fixed', zIndex: '9000', left: '12px', right: '12px',
      bottom: bottomOffset(),
      maxWidth: '460px', margin: '0 auto', padding: '14px 16px',
      borderRadius: '14px', border: '1px solid #1f6f4d', background: '#0f1621',
      color: '#e8edf5', boxShadow: '0 10px 30px rgba(0,0,0,.5)',
      font: '14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif'
    });

    var h = document.createElement('div');
    h.textContent = u.title;
    Object.assign(h.style, { fontWeight: '700', marginBottom: '4px' });

    var when = document.createElement('div');
    when.textContent = 'New · ' + esc(u.date);
    Object.assign(when.style, { color: '#3ddc97', fontSize: '11px',
      letterSpacing: '.06em', textTransform: 'uppercase', marginBottom: '6px' });

    var p = document.createElement('div');
    p.textContent = u.body;
    Object.assign(p.style, { color: '#c6d0de' });

    var row = document.createElement('div');
    Object.assign(row.style, { display: 'flex', gap: '8px', marginTop: '12px' });

    var dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.textContent = 'Dismiss';
    Object.assign(dismiss.style, {
      flex: '0 0 auto', font: 'inherit', padding: '9px 14px', cursor: 'pointer',
      borderRadius: '9px', border: '1px solid #263141', background: '#141c28',
      color: '#c6d0de' });
    dismiss.onclick = function () { markSeen(u.id); box.remove(); };

    row.appendChild(dismiss);

    if (u.href && u.action) {
      var a = document.createElement('a');
      a.href = u.href;
      a.textContent = u.action;
      Object.assign(a.style, {
        flex: '1 1 auto', textAlign: 'center', font: 'inherit', fontWeight: '600',
        padding: '9px 14px', borderRadius: '9px', background: '#3ddc97',
        color: '#07120d', textDecoration: 'none' });
      // Following the action counts as having read it.
      a.onclick = function () { markSeen(u.id); };
      row.appendChild(a);
    }

    box.appendChild(when); box.appendChild(h); box.appendChild(p); box.appendChild(row);
    document.body.appendChild(box);
  }

  function run(doc) {
    var list = (doc && doc.updates) || [];
    var already = seen();
    var owner = ownsCamera();
    for (var i = 0; i < list.length; i++) {
      var u = list[i];
      if (!u || !u.id || !u.title || !u.body) continue;
      if (already.indexOf(u.id) >= 0) continue;
      if (u.audience === 'owner' && !owner) continue;
      show(u);          // newest unseen one only
      return;
    }
  }

  /* The first-run panel ("Watch the watchers") asks one question and deserves
   * an undivided answer. Two prompts stacked on a first visit is how both get
   * dismissed unread, so wait until it has been answered. app.js records that
   * as sparrow.introSeen. */
  function introUp() {
    try { return !localStorage.getItem('sparrow.introSeen'); }
    catch (e) { return false; }
  }

  function start() {
    if (introUp()) {
      // Cheap poll rather than an event: app.js owns that panel and does not
      // announce its dismissal, and inventing an event for it would mean
      // editing the file this one is deliberately independent of.
      var tries = 0;
      var t = setInterval(function () {
        if (!introUp()) { clearInterval(t); start(); }
        else if (++tries > 120) clearInterval(t);   // ~2 min, then leave it
      }, 1000);
      return;
    }
    fetch('/static/updates.json', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) run(d); })
      .catch(function () { /* silent on purpose - see the header */ });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
