/* The transparency panel: live policy and counters.
 *
 * The decisions log was deliberately removed - see transparency.html.
 *
 * Extracted from transparency.html so the standalone page and the unified
 * shell on the map render it from ONE implementation. This codebase has been
 * bitten repeatedly by the other arrangement - is_operator_addr and the
 * government-vehicle call both had to be collapsed into single functions after
 * a rule was fixed in one copy and not the other.
 *
 * Exposed as window.sparrowTransparency() so the shell can call it again after
 * injecting the markup, because innerHTML does not run scripts.
 */
window.sparrowTransparency = async function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  const LABELS = {
    public_tiers:            ['Vehicle classes published with a readable plate', (v) => v.join(', ')],
    civilian_retention_days: ['Private sightings deleted after', (v) => `${v} days`],
    public_retention_days:   ['Public sightings deleted after', (v) => v ? `${v} days` : 'never (public record)'],
    pepper_rotation_days:    ['Hash key rotated every', (v) => `${v} days`],
    /* 🚨 THIS LINE BECAME UNTRUE THE DAY PUBLIC TRAFFIC CAMERAS WERE ADDED.
     * It said, flatly, that camera positions are published only to within the
     * jitter. That is still exactly right for every VOLUNTEER camera - the
     * whole reason the jitter exists is that a volunteer's camera position
     * describes their house - and it is now wrong as a blanket statement,
     * because government traffic cameras publish their own exact coordinates
     * and SparrowMap passes them straight through.
     *
     * On the one page whose entire purpose is that its claims can be checked,
     * a sentence that is 95% true is worse than a longer one that is true. */
    node_position_jitter_m:  ['Volunteer camera positions published to within',
                              (v) => `${v} m`],
    min_plate_confidence:    ['Minimum plate confidence to record', (v) => v],
    public_threshold:        ['Confidence needed to publish a plate', (v) => v],
    private_plate_lookup:    ['Can anyone look up a private plate?', (v) => v ? 'YES' : 'no'],
    stores_video:            ['Does any video reach this server?', (v) => v ? 'YES' : 'no'],
    stores_full_frames:      ['Are full camera frames stored?', (v) => v ? 'YES' : 'no (vehicle crops only)'],
  };

  // body
    // The decisions log is gone (see transparency.html). /api/audit is no
    // longer fetched at all: it 404s on a mirror, and a request that can only
    // fail is not worth making on every page view.
    const [p, s] = await Promise.all([
      fetch('/api/policy').then(r => r.json()),
      fetch('/api/stats').then(r => r.json()),
    ]);

    $('#cards').innerHTML = [
      [s.nodes_online + ' / ' + s.nodes_active, 'cameras online'],
      [s.sightings_24h.toLocaleString(), 'sightings, 24h'],
      [s.public_24h.toLocaleString(), 'public sightings, 24h'],
      [(s.sightings_24h - s.public_24h).toLocaleString(), 'private passes, no plate kept'],
      [s.vehicles_24h.toLocaleString(), 'distinct vehicles'],
    ].map(([b, t]) => `<div class="card"><b>${b}</b><span>${t}</span></div>`).join('');

    /* Sources, stated plainly. Somebody reading this page is deciding whether
     * to believe the map, and "where do these sightings come from" is the
     * first thing they need - especially now that not all of them come from
     * volunteers. Counted live rather than asserted, so it cannot drift. */
    try {
      const nodes = await fetch('/api/nodes').then(r => r.json());
      const pub = nodes.filter(n => n.kind === 'public_cam');
      const note = $('#sources');
      if (note) {
        note.innerHTML = pub.length
          ? `<b>${pub.length}</b> of the cameras feeding this map are <b>public `
            + `government traffic cameras</b>, read from feeds their transport `
            + `authority publishes. They are marked as such on the map and in `
            + `the API (<code>kind=public_cam</code>), and their exact position `
            + `is shown because the authority publishes it. Every other camera `
            + `is a volunteer's, and its position is never published.`
          : `Every camera feeding this map is a volunteer's, and no camera `
            + `position is published.`;
      }
    } catch (e) { /* the table below still stands on its own */ }

    $('#policy').innerHTML = '<tr><th>Setting</th><th>Value</th></tr>' +
      Object.entries(LABELS).map(([k, [lab, fmt]]) => {
        if (p[k] === undefined) return '';
        const val = fmt(p[k]);
        const cls = (k === 'private_plate_lookup' || k === 'stores_video' || k === 'stores_full_frames')
          ? (String(val).startsWith('no') ? 'yes' : 'no') : '';
        return `<tr><td class="n">${esc(lab)}</td><td class="v ${cls}">${esc(val)}</td></tr>`;
      }).join('');

};
