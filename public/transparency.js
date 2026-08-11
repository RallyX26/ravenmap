/* The transparency panel: live policy, counters and the decisions log (the
 * operator's confirms and retractions, and any public flags - NOT who searched).
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
    node_position_jitter_m:  ['Camera positions published to within', (v) => `${v} m`],
    min_plate_confidence:    ['Minimum plate confidence to record', (v) => v],
    public_threshold:        ['Confidence needed to publish a plate', (v) => v],
    private_plate_lookup:    ['Can anyone look up a private plate?', (v) => v ? 'YES' : 'no'],
    stores_video:            ['Does any video reach this server?', (v) => v ? 'YES' : 'no'],
    stores_full_frames:      ['Are full camera frames stored?', (v) => v ? 'YES' : 'no (vehicle crops only)'],
  };

  // body
    const [p, s, a] = await Promise.all([
      fetch('/api/policy').then(r => r.json()),
      fetch('/api/stats').then(r => r.json()),
      fetch('/api/audit').then(r => r.json()),
    ]);

    $('#cards').innerHTML = [
      [s.nodes_online + ' / ' + s.nodes_active, 'cameras online'],
      [s.sightings_24h.toLocaleString(), 'sightings, 24h'],
      [s.public_24h.toLocaleString(), 'public sightings, 24h'],
      [(s.sightings_24h - s.public_24h).toLocaleString(), 'private passes, no plate kept'],
      [s.vehicles_24h.toLocaleString(), 'distinct vehicles'],
    ].map(([b, t]) => `<div class="card"><b>${b}</b><span>${t}</span></div>`).join('');

    $('#policy').innerHTML = '<tr><th>Setting</th><th>Value</th></tr>' +
      Object.entries(LABELS).map(([k, [lab, fmt]]) => {
        if (p[k] === undefined) return '';
        const val = fmt(p[k]);
        const cls = (k === 'private_plate_lookup' || k === 'stores_video' || k === 'stores_full_frames')
          ? (String(val).startsWith('no') ? 'yes' : 'no') : '';
        return `<tr><td class="n">${esc(lab)}</td><td class="v ${cls}">${esc(val)}</td></tr>`;
      }).join('');

    $('#audit').innerHTML = '<tr><th>When</th><th>Action</th><th>Vehicle</th><th>From</th></tr>' +
      (a.length ? a.map(r => `<tr>
          <td class="n">${new Date(r.ts * 1000).toLocaleString()}</td>
          <td class="v">${esc(r.action)}</td>
          <td class="v">${esc(r.target)}</td>
          <td class="n">${esc(r.who)}</td></tr>`).join('')
        : '<tr><td class="n" colspan="4">No decisions recorded yet.</td></tr>');

};
