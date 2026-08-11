/* Landing-page behaviour, kept OUT of the HTML so the deployed Cloudflare Pages
   site can ship a strict Content-Security-Policy (script-src 'self') via
   landing/_headers. Cloudflare Pages cannot add a per-response nonce, and an
   inline <script> would force either 'unsafe-inline' (which defeats the point)
   or a brittle hash that silently breaks the page the next time a byte changes.
   External + 'self' is the robust choice. */

/* ---- live figures -------------------------------------------------------
   The static fallback stays in the HTML, so a failed or slow fetch degrades to
   "an honest snapshot with its date on it" rather than to a blank or a zero.

   🚨 A ZERO HERE WOULD BE A LIE IN THE OTHER DIRECTION. "0 private plates
   stored" is the site's strongest claim, so an empty response rendering as 0
   across all three rows would look like a perfect record. Every value is
   checked for being a real number first, the third row is only drawn from a
   payload proving 0 stored plates, and an all-zero payload (a broken hub) is
   refused so the honest snapshot stays. */
(async () => {
  const el = id => document.getElementById(id);
  const num = v => (typeof v === 'number' && isFinite(v) && v >= 0) ? v : null;
  try {
    const r = await fetch('/api/stats.json', {cache: 'no-store'});
    if (!r.ok) return;
    const s = await r.json();
    const seen = num(s.vehicles_seen), sight = num(s.sightings),
          conf = num(s.confirmed_public), priv = num(s.private_seen),
          plates = num(s.private_plates_stored), cams = num(s.cameras);
    if (plates === null || plates !== 0) return;   // claim unverified: keep snapshot
    if (!seen || !sight) return;                   // all-zero => broken hub, keep snapshot
    const fmt = n => n.toLocaleString('en-GB');
    if (seen !== null) el('fig-seen').textContent = fmt(seen);
    if (seen !== null && sight !== null && cams !== null)
      el('cap-seen').textContent =
        `${cams === 1 ? 'One camera' : cams + ' cameras'}, real traffic. That is `
        + `${fmt(sight)} recorded sightings, because one car crossing the view is `
        + `often caught more than once.`;
    if (conf !== null) el('fig-confirmed').textContent = fmt(conf);
    if (priv !== null)
      el('cap-plates').textContent =
        `Out of ${fmt(priv)} vehicles that were not government.`;
    if (s.as_of) el('ledger-asof').textContent =
      'What it has actually done, updated ' + s.as_of;
  } catch (e) { /* keep the snapshot */ }
})();

/* ---- call-to-action buttons --------------------------------------------
   Fill either value in and its button appears; leave it empty and the button
   is simply not rendered, so the page never ships a dead link. */
const INSTAGRAM_URL = "https://instagram.com/sparrowmap";
const CONTACT_EMAIL = "sparrowmap@icloud.com";

(function () {
  const host = document.getElementById("actions");
  if (!host) return;
  const out = [];
  if (INSTAGRAM_URL) {
    const a = document.createElement("a");
    a.className = "btn"; a.href = INSTAGRAM_URL; a.rel = "noopener";
    a.textContent = "Follow for the opening";
    out.push(a);
  }
  if (CONTACT_EMAIL) {
    const a = document.createElement("a");
    a.className = "btn ghost";
    a.href = "mailto:" + CONTACT_EMAIL + "?subject=I%20want%20to%20run%20a%20camera";
    a.textContent = "Ask about running a camera";
    out.push(a);
  }
  if (!out.length) {
    const p = document.createElement("p");
    p.style.cssText = "font-family:var(--mono);font-size:13px;color:var(--steel);margin:0";
    p.textContent = "Opening to volunteers soon.";
    out.push(p);
  }
  host.replaceChildren(...out);
})();
