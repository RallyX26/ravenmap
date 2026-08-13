/* SparrowMap — the public map.
 *
 * Every vehicle the network has seen, plotted where it was seen, with the
 * snapshot that proves it. Public-tier vehicles carry their plate. Private
 * vehicles carry a rolling daily alias and nothing else, because the server
 * never had their plate to give.
 */

/* RED for anything publicly owned, because that is the entire subject of this
   map. Police and gov are one thing on it - "a publicly owned vehicle doing
   public work on a public road is a public record" was always the argument,
   and the specific agency is a detail the detail panel can carry. Fleet is
   commercial, not government, so it keeps its own colour. */
const COLOR = {
  police:   '#ff3b47',
  gov:      '#ff3b47',
  emergency:'#ff3b47',
  fleet:    '#ffb547',
  civilian: '#55637a',
  unknown:  '#55637a',
};

/* What the map CALLS each class. The internal classification is unchanged -
   classify.py still decides police vs gov on its own evidence, and the detail
   panel still shows which - but the headline on a dot is the category that
   matters publicly. */
const CLASS_LABEL = {
  police:    'Government vehicle',
  gov:       'Government vehicle',
  emergency: 'Government vehicle',
  fleet:     'Fleet vehicle',
  civilian:  'Private vehicle',
  unknown:   'Unidentified',
};
const label_for = (v) => CLASS_LABEL[v] || v;

/* Private traffic gets its own colour rather than the civilian slate, which is
   barely separable from the dark basemap - a dot nobody can see does not
   communicate "the road is busy". Still deliberately colourless next to the
   public tiers: it reads as movement, not as an identity. */
const TRAFFIC = '#93a7c4';

/* How long a private-tier pass stays on the map.
 *
 * Public-tier sightings persist for the whole selected window, because a
 * publicly owned vehicle on a public road is a record. Private traffic is a
 * LIVE VIEW and nothing else: a dot appears as the car passes, fades, and is
 * gone. It is not clickable, it has no detail panel and it leaves nothing
 * behind, which is the same promise the storage layer already makes - the
 * plate was destroyed at the camera and the row expires in 14 days. The map
 * should not imply a persistence the system deliberately does not have. */
const TRAFFIC_FADE_S = 45;   // live view: a pass shows, then fades quickly

/* 0 = no limit, and it is the DEFAULT.
 *
 * A public sighting is a RECORD, kept indefinitely by policy
 * (public_retention_days: 0). Hiding one behind an hour-long window meant the
 * first patrol car this network ever caught was invisible on the map while the
 * header insisted it existed - and the map is the whole point of keeping them.
 *
 * This control never had much to do with private traffic anyway: that fades
 * after TRAFFIC_FADE_S regardless of what is selected here, because it is a
 * live view rather than a record. So the window is, and now says it is, a
 * filter on the public tier. */
const state = {
  filter: 'all',
  windowS: 0,
  showCams: true,
  sightings: new Map(),   // id -> record  (public tier: the records)
  markers: new Map(),     // id -> leaflet marker
  traffic: new Map(),     // id -> {rec, marker}  (private tier: the live view)
  camLayer: L.layerGroup(),
  pingLayer: L.layerGroup(),
  trafficLayer: L.layerGroup(),
  trailLayer: L.layerGroup(),
  reportLayer: L.layerGroup(),   // live driver reports (ephemeral, unverified)
  selected: null,
  trackHash: null,
};

/* ---------------------------------------------------------------- map ---- */

/* This opening view is a PLACEHOLDER and is expected to be replaced within a
 * few hundred ms, by whichever of these answers first:
 *
 *   1. the watched spans, once /api/nodes returns  (loadCameras -> fitBounds)
 *   2. `map_center` / `map_zoom` from /api/policy   (applyConfiguredView)
 *
 * It is rounded and region-level because a hardcoded street-level centre in
 * published source is a real camera's neighbourhood, and this file is public.
 * The deployment's actual centre is CONFIG - served, not compiled in. */
const map = L.map('map', { zoomControl: false, attributionControl: true })
  .setView([42.7, -84.5], 8);
// No zoom buttons at all: pinch and scroll zoom the map, and the buttons only
// got in the way - on a phone they sat over the SparrowMap logo. `zoomControl:
// false` above removes them. Keep the map sized to its container, because after
// the mobile layout stacks map-over-panel Leaflet renders short and leaves a
// grey gap otherwise.
// Leaflet locks tile geometry to the container size it saw at construction, so
// re-measure whenever the viewport changes shape (rotate, window resize) or
// after first paint. The map's HEIGHT is pure CSS (a fixed share of the phone
// screen); this only tells Leaflet to re-tile into it.
const fixMapSize = () => map.invalidateSize(false);
['load', 'orientationchange', 'resize'].forEach((ev) => addEventListener(ev, fixMapSize));
[200, 600].forEach((t) => setTimeout(fixMapSize, t));

// Publish the header's real height so the fixed About/Transparency panes start
// below it. On a phone the search box wraps onto its own row, making the header
// ~120px, and a hardcoded top hid each pane's heading behind the sticky bar.
const _bar = document.querySelector('header.bar');
const setHeadH = () =>
  document.documentElement.style.setProperty('--headh', Math.round(_bar.getBoundingClientRect().height) + 'px');
['load', 'orientationchange', 'resize'].forEach((ev) => addEventListener(ev, setHeadH));
setHeadH();

// A snapshot that fails to load must not leave a broken-image icon in the list.
// The CSP forbids inline onerror handlers, so catch the error in the capture
// phase (image errors do not bubble) and hide the element.
addEventListener('error', (e) => {
  const t = e.target;
  if (t && t.tagName === 'IMG' && (t.getAttribute('src') || '').startsWith('/snap/')) {
    t.style.display = 'none';
  }
}, true);

/* Same-origin on purpose - see hub.py TILES. The tiles are CARTO's, fetched
 * and cached by the hub, so a viewer's IP and the streets they chose to look
 * at never reach a third party. Attribution is still required and still
 * shown; proxying the bytes does not proxy the credit. */
L.tileLayer('/api/tile/{z}/{x}/{y}.png', {
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO &middot; SparrowMap',
  maxZoom: 20,
}).addTo(map);

state.camLayer.addTo(map);
state.trafficLayer.addTo(map);
state.trailLayer.addTo(map);
state.pingLayer.addTo(map);
state.reportLayer.addTo(map);

/* ------------------------------------------------------------- helpers --- */

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function ago(ts) {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return `${Math.max(0, Math.round(d))}s`;
  if (d < 3600) return `${Math.round(d / 60)}m`;
  if (d < 86400) return `${Math.round(d / 3600)}h`;
  return `${Math.round(d / 86400)}d`;
}

/* 🚨 BUCKET `since` SO THE REQUEST URL IS STABLE, OR THE EDGE CACHE NEVER HITS.
   /api/sightings is short-cacheable, but a `since` of Date.now() changes every
   call, so every request would be a unique URL and Cloudflare could cache none
   of it. Rounding down to a fixed bucket means every viewer in the same window
   requests the IDENTICAL url, so the origin serves it once and the edge serves
   the crowd. The bucket matches the server's max-age (15s). */
const CACHE_BUCKET_S = 4;   // live view: new passes appear within a few seconds
const bucketed = (sec) => Math.floor(sec / CACHE_BUCKET_S) * CACHE_BUCKET_S;

/* The oldest timestamp the window admits. 0 means no limit - decided here
   once, because the same rule is needed at four call sites and a constant
   re-derived in four places is a constant that will eventually disagree. */
const windowCut = () => state.windowS ? bucketed(Date.now() / 1000 - state.windowS) : 0;

const isPublic = (s) => s.tier === 'public';
const label = (s) => isPublic(s) ? (s.plate_text || '—')
  : `private · ${(s.plate_hash || '').slice(2, 8)}`;

/* Public-tier dots hold their weight for the whole window - they are records.
   The visual hierarchy is the argument: this map is about who is watching, not
   about whoever drove past. */
function pingStyle(s) {
  // With no window there is nothing to fade against, so records stay solid.
  const age = state.windowS
    ? Math.min(1, (Date.now() / 1000 - s.ts) / state.windowS) : 0;
  return {
    radius: 7,
    color: COLOR[s.vclass] || COLOR.unknown,
    fillColor: COLOR[s.vclass] || COLOR.unknown,
    fillOpacity: 0.9 * (1 - age * 0.45),
    opacity: 1 - age * 0.35,
    weight: 1.5,
  };
}

function passes(s) {
  if (state.filter === 'none') return false;   // hide every vehicle marker
  if (state.filter === 'all') return true;
  return s.vclass === state.filter;
}

/* ------------------------------------------------------------- markers --- */

function drawSighting(s) {
  // "None" hides vehicle dots entirely - private live traffic included, since it
  // is a vehicle marker too. The watched-roads tickbox is independent, so None
  // + roads-on shows just the road lines.
  if (state.filter === 'none') return;
  if (!isPublic(s)) return drawTraffic(s);

  state.sightings.set(s.id, s);
  const old = state.markers.get(s.id);
  if (old) state.pingLayer.removeLayer(old);
  if (!passes(s)) { state.markers.delete(s.id); return; }

  const m = L.circleMarker([s.lat, s.lon], pingStyle(s));
  m.on('click', () => select(s.id));
  m.addTo(state.pingLayer);
  state.markers.set(s.id, m);
}

/* ------------------------------------------------------- live traffic ---- */

/* A private pass. Deliberately inert: no click handler, no tooltip, no entry
   in the list, no id anyone can look up. It exists to show that the road is
   busy, which is the honest sum of what the system is allowed to know about
   it, and then it disappears. */
function drawTraffic(s) {
  if (state.traffic.has(s.id)) return;
  const m = L.circleMarker([s.lat, s.lon], {
    radius: 5, color: TRAFFIC, fillColor: TRAFFIC,
    fillOpacity: 0.8, opacity: 0.95, weight: 1,
    interactive: false,        // unclickable, not just click-does-nothing
  }).addTo(state.trafficLayer);
  state.traffic.set(s.id, { rec: s, marker: m });
}

/* One timer fades and reaps every traffic dot. Per-dot timers would mean
   hundreds of them on a busy road, all firing independently. */
function ageTraffic() {
  const t = Date.now() / 1000;
  for (const [id, e] of state.traffic) {
    const a = (t - e.rec.ts) / TRAFFIC_FADE_S;
    if (a >= 1) {
      state.trafficLayer.removeLayer(e.marker);
      state.traffic.delete(id);
      continue;
    }
    // Bright and full-size as it passes, then thinning away to nothing. The
    // curve is deliberately back-loaded so a fresh pass reads as an event
    // rather than as one more faint dot among the dying ones.
    const k = Math.pow(1 - a, 0.6);
    e.marker.setStyle({ fillOpacity: 0.8 * k, opacity: 0.95 * k,
                        radius: 5 * (0.45 + 0.55 * k) });
  }
  const n = state.traffic.size;
  const el = $('#traffic');
  if (el) {
    el.textContent = n ? `${n} passing now` : 'road quiet';
    el.classList.toggle('busy', n > 0);
  }
  emptyState();
}

function redrawAll() {
  state.pingLayer.clearLayers();
  state.markers.clear();
  // None also clears the live-traffic layer (its dots are added outside this
  // pass and would otherwise linger until they faded on their own).
  if (state.filter === 'none') {
    state.trafficLayer.clearLayers();
    state.traffic.clear();
  }
  const cut = windowCut();
  [...state.sightings.values()]
    .filter((s) => s.ts > cut)
    .sort((a, b) => a.ts - b.ts)
    .forEach(drawSighting);
  renderList();
}

/* --------------------------------------------------------------- panel --- */

function renderList() {
  const cut = windowCut();
  let rows = [...state.sightings.values()].filter((s) => s.ts > cut && passes(s));
  if (state.trackHash) rows = rows.filter((s) => s.plate_hash === state.trackHash);
  rows.sort((a, b) => b.ts - a.ts);

  // Public tier only. A private pass has no identifier, no detail page and no
  // trail, so a list row for it would be a row you cannot click carrying
  // nothing you can read - and listing them alongside records implies the
  // system holds something on them that it does not.
  $('#listtitle').textContent = state.trackHash
    ? `Trail · ${rows.length} sightings`
    // "sightings", not "vehicles" - the list has one row per PASS, and
              // without a plate there is no way to know how many vehicles that
              // is. Calling it vehicles here while the header honestly says
              // "-- distinct vehicles" would put the contradiction back.
    : `Public sightings · ${rows.length}`;
  $('#clearsel').classList.toggle('hidden', !state.trackHash);

  // ⚠️ THE HEADER AND THE PANEL WERE CONTRADICTING EACH OTHER.
  // The header counts public sightings over 24h; the panel and the map only
  // ever show the selected window, which defaults to one hour. So the first
  // patrol car this camera ever caught read as "1 public sightings" up top and
  // "0" in the panel with nothing on the map, and the only way to reconcile
  // that was to know how the window works. A public sighting is rare and it is
  // a record - if one exists and the window is hiding it, the map should say
  // so and offer to widen rather than leave a contradiction on screen.
  const el = $('#windowhint');
  const hidden = (lastStats?.public_24h || 0) - rows.length;
  if (el) {
    const show = !state.trackHash && hidden > 0 && state.windowS !== 0;
    el.style.display = show ? '' : 'none';
    if (show) {
      el.innerHTML = `${hidden} more public sighting${hidden === 1 ? '' : 's'}
        in the last 24h &mdash; <button class="ghost" id="widen">show everything</button>`;
      $('#widen').onclick = () => {
        $('#window').value = '0';
        state.windowS = 0;
        load();
      };
    }
  }

  $('#list').innerHTML = rows.slice(0, 300).map((s) => `
    <li data-id="${s.id}" class="${s.id === state.selected ? 'sel' : ''}">
      <i class="sw" style="background:${COLOR[s.vclass] || COLOR.unknown}"></i>
      <div class="who">
        <b class="${isPublic(s) ? '' : 'priv'}" style="${isPublic(s)
          ? 'color:' + (COLOR[s.vclass] || COLOR.unknown) : ''}">${
          isPublic(s) && !s.plate_text ? esc(label_for(s.vclass)) : esc(label(s))}</b>
        <span>${esc(s.color || '')} ${esc(s.body || '')} · ${esc(s.node_id)}</span>
      </div>
      <div class="when">${ago(s.ts)}</div>
    </li>`).join('');
}

$('#list').addEventListener('click', (e) => {
  const li = e.target.closest('li');
  if (li) select(Number(li.dataset.id));
});

function closeDetail() {
  state.selected = null;
  $('#detail').classList.add('hidden');
  // A trail drawn from the detail panel belongs to the detail panel; leaving
  // it on the map after closing leaves a line nobody can explain or remove.
  if (state.trackHash) {
    state.trackHash = null;
    state.trailLayer.clearLayers();
  }
  renderList();
}

// Escape gets you out of anything.
addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && state.selected != null) closeDetail();
});

/* Public correction. Anyone can flag a published sighting - "that is an SUV, not
   a motorcycle", "that is not a government vehicle". The flag does NOT change the
   map; it drops the sighting into the operator's review queue, where a human
   confirms or retracts it. That is the whole trust model: correctable by anyone,
   editable only after a human agrees. */
function openReport(id) {
  const box = $('#reportbox');
  box.classList.remove('hidden');
  box.innerHTML = `
    <div class="rlabel">What looks wrong here?</div>
    <div class="rreasons">
      <button class="btn alt" data-r="not_government">Not a government vehicle</button>
      <button class="btn alt" data-r="wrong_description">Wrong description</button>
      <button class="btn alt" data-r="other">Something else</button>
    </div>
    <textarea id="rnote" maxlength="500" rows="2"
      placeholder="Add a detail (optional), e.g. this is an SUV, not a motorcycle"></textarea>
    <div class="acts">
      <button class="btn" id="rsend" disabled>Send to review</button>
      <button class="btn alt" id="rcancel">Cancel</button>
    </div>
    <div id="rmsg" class="rmsg"></div>`;

  let reason = null;
  box.querySelectorAll('.rreasons .btn').forEach((b) => {
    b.onclick = () => {
      box.querySelectorAll('.rreasons .btn').forEach((x) => x.classList.remove('on'));
      b.classList.add('on');
      reason = b.dataset.r;
      $('#rsend').disabled = false;
    };
  });
  const done = () => { box.classList.add('hidden'); box.innerHTML = ''; };
  $('#rcancel').onclick = done;
  $('#rsend').onclick = async () => {
    if (!reason) return;
    $('#rsend').disabled = true;
    try {
      const res = await fetch('/api/report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, reason, note: ($('#rnote').value || '').slice(0, 500) }),
      });
      if (res.ok) {
        box.innerHTML = '<div class="rmsg ok">Thanks. This was sent to the review '
          + 'queue for a person to check. Nothing on the map changes until they do.</div>';
        setTimeout(done, 4500);
      } else {
        $('#rmsg').textContent = 'Could not send that. Please try again.';
        $('#rsend').disabled = false;
      }
    } catch {
      $('#rmsg').textContent = 'Could not send that. Please try again.';
      $('#rsend').disabled = false;
    }
  };
}

async function select(id) {
  state.selected = id;
  const s = state.sightings.get(id) || await (await fetch(`/api/sighting/${id}`)).json();
  state.sightings.set(id, s);

  const pub = isPublic(s);
  const conf = s.vclass_conf != null ? `${Math.round(s.vclass_conf * 100)}%` : '—';

  $('#detail').innerHTML = `
    ${s.snap ? `<img src="/snap/${encodeURIComponent(s.snap)}" alt="snapshot">` : ''}
    <div class="plate ${pub ? '' : 'priv'}">${esc(label(s))}</div>
    <div class="kv">
      <span>class</span><b style="color:${COLOR[s.vclass] || COLOR.unknown}">${
        esc(label_for(s.vclass))} · ${conf}</b>
      <span>classified</span><b>${esc(s.vclass)}</b>
      <span>seen</span><b>${new Date(s.ts * 1000).toLocaleString()}</b>
      <span>camera</span><b>${esc(s.node_id)}</b>
      <span>vehicle</span><b>${esc(s.color || '?')} ${esc(s.body || '')}</b>
      <span>heading</span><b>${s.heading != null ? Math.round(s.heading) + '°' : '—'}</b>
      <span>signed</span><b>${s.sig_ok ? 'yes' : 'no'}</b>
      ${s.detections > 1 ? `<span>detections</span><b title="The tracker saw this
        vehicle as several separate tracks while it crossed the frame. They are
        one pass, folded together.">${s.detections} merged</b>` : ''}
    </div>
    <div class="why"><b>Why this class:</b> ${esc(s.vclass_why || 'no signals recorded')}</div>
    ${pub ? '' : `<div class="why">This vehicle is private tier. Its plate was
      hashed at the camera and never stored, so there is no plate to show and
      no way to search for it. The identifier above is a rolling alias that
      changes daily.</div>`}
    <div class="acts">
      <button class="btn" id="btnTrail">${pub ? 'Show trail' : 'Show today’s trail'}</button>
      <button class="btn alt" id="btnCenter">Centre</button>
      ${pub ? '<button class="btn alt" id="btnReport">Report a problem</button>' : ''}
      <button class="btn alt" id="btnClose" title="Back to the list">Back</button>
    </div>
    <div id="reportbox" class="reportbox hidden"></div>`;
  $('#detail').classList.remove('hidden');

  $('#btnCenter').onclick = () => map.setView([s.lat, s.lon], 17);
  $('#btnTrail').onclick = () => showTrail(s.plate_hash);
  if (pub) $('#btnReport').onclick = () => openReport(s.id);
  // There was no way out of the detail panel once it opened - it covered the
  // list and stayed until another sighting was clicked. A view you can enter
  // and not leave is a dead end.
  $('#btnClose').onclick = closeDetail;

  const m = state.markers.get(id);
  if (m) { m.bringToFront(); map.panTo([s.lat, s.lon]); }
  renderList();
}

/* ---------------------------------------------------------------- trail -- */

async function showTrail(hash) {
  if (!hash) return;
  const rows = await (await fetch(`/api/track/${encodeURIComponent(hash)}`)).json();
  state.trailLayer.clearLayers();
  if (!rows.length) return;

  state.trackHash = hash;
  const pts = rows.map((r) => [r.lat, r.lon]);
  const col = COLOR[rows[0].vclass] || COLOR.unknown;

  L.polyline(pts, { color: col, weight: 2, opacity: 0.75, className: 'trail' })
    .addTo(state.trailLayer);
  rows.forEach((r, i) => {
    L.circleMarker([r.lat, r.lon], {
      radius: i === rows.length - 1 ? 6 : 3.5, color: col, fillColor: col,
      fillOpacity: 0.9, weight: 1,
    }).on('click', () => select(r.id)).addTo(state.trailLayer);
  });
  map.fitBounds(L.latLngBounds(pts).pad(0.25));

  const ps = rows[0].patrol_score;
  if (ps != null && ps > 0.55) {
    $('#detail').insertAdjacentHTML('beforeend',
      `<div class="why" style="border-color:${COLOR.police}">
         <b>Patrol-shaped movement (${Math.round(ps * 100)}%).</b> Many passes,
         spread across the clock, reversing over the same stretch. Nobody's
         commute looks like this.</div>`);
  }
  renderList();
}

$('#clearsel').onclick = () => {
  state.trackHash = null;
  state.trailLayer.clearLayers();
  renderList();
};

/* -------------------------------------------------------------- cameras -- */

let fittedOnce = false;
let _geoLoc = null, _spanBounds = null, _userMovedMap = false;
map.on('dragstart zoomstart', () => { _userMovedMap = true; });

// Choose the opening view. If there are watched roads AND the visitor is near
// them (<60 km), show the roads - the data is the point. If the visitor is far,
// open on THEIR own city instead of yanking them to another state's cameras.
// With no cameras at all, open on their city. Runs when geolocation resolves or
// the cameras load, whichever is last, and never fights a visitor who has
// already panned. Deliberately NO IP lookup - SparrowMap does not send anyone's
// location to a geo service to guess where they are; the browser asks, once.
function chooseView() {
  if (_userMovedMap) return;
  if (_spanBounds) {
    const near = !_geoLoc ||
      _spanBounds.getCenter().distanceTo(L.latLng(_geoLoc)) < 60000;
    fittedOnce = true;
    if (near) map.fitBounds(_spanBounds.pad(0.35), { maxZoom: 17 });
    else map.setView(_geoLoc, 11);
  } else if (_geoLoc) {
    fittedOnce = true;
    map.setView(_geoLoc, 12);
  }
}
if (navigator.geolocation) {
  navigator.geolocation.getCurrentPosition(
    (pos) => { _geoLoc = [pos.coords.latitude, pos.coords.longitude]; chooseView(); },
    () => {},
    { enableHighAccuracy: false, timeout: 8000, maximumAge: 600000 },
  );
}

/* A camera is drawn as ONE thing: the stretch of road it watches.
 *
 * There used to be a second thing - a dot at the camera's jittered position,
 * with a vague wedge joining it to the span. Both are gone, and the server no
 * longer sends the coordinates to draw them with (see hub.py /api/nodes).
 *
 * The reasoning: the span is drawn ACCURATELY on purpose, because people are
 * entitled to know exactly where they are recorded, and it describes a public
 * road. The camera point described a HOUSE, and 60 m of jitter on a
 * residential street narrows that to a handful of front doors rather than
 * hiding it. Publishing both meant publishing the honest fact and, next to it,
 * a weaker claim about the same camera that could only make the first one
 * sharper. Keep the road. Drop the door. */
async function loadCameras() {
  const cams = await (await fetch('/api/nodes')).json();
  state.camLayer.clearLayers();

  // Open on the area that actually has cameras, rather than a hardcoded zoom
  // that is wrong for every deployment except the one it was written for.
  // Spans only now - a span-less node contributes no geometry to fit to, and
  // that is correct: it has no published location to open on.
  const spans = cams.filter((c) => c.span && c.span.length);
  if (spans.length) _spanBounds = L.latLngBounds(spans.flatMap((c) => c.span));
  // chooseView() decides between the watched roads and the visitor's own city.
  // maxZoom in there matters: a single node's span is ~80 m across, and fitting
  // that tightly lands past zoom 20 where the basemap has no tiles.
  chooseView();

  // No count is rendered here on purpose: the stats bar already publishes
  // "<online>/<active> cameras online" from /api/stats, and that figure counts
  // the carried phones and un-snapped windows this layer cannot draw. A second
  // count computed a second way is how two numbers that must agree stop
  // agreeing.
  if (!state.showCams) return;

  // Only nodes with a road snap are drawable. A carried phone, or a window
  // camera Overpass could not snap to a way, contributes NOTHING to this
  // layer - it has no published geometry. It still appears in the count above,
  // and its sightings still appear as dots wherever they were taken.
  spans.forEach((c) => {
    const live = c.online;
    const G = '#3ddc97';

    // 'quiet' and 'offline' are different facts and used to be the same word,
    // because online was inferred from traffic. A camera can now say it is
    // watching an empty street.
    const seenAgo = c.last_seen ? ago(c.last_seen) + ' ago' : 'never';
    const status = live
      ? `<b style="color:${G}">online</b> · last vehicle ${seenAgo}`
      : `<b style="color:#8794a8">offline</b> · last vehicle ${seenAgo}`;

    // The road being watched, drawn on the road. This is the whole camera
    // layer now. The tooltip hangs off the span because there is no longer a
    // point to hang it off - and that is the honest place for it, since the
    // span is the only thing being claimed.
    L.polyline(c.span, {
      color: G, weight: 5, opacity: live ? 0.75 : 0.3, lineCap: 'round',
    }).bindTooltip(
      `${esc(c.name)}${c.road_name ? ' &middot; ' + esc(c.road_name) : ''}
       <br>${status}<br>${c.sightings} sightings
       <br><i style="opacity:.6">this stretch of road is watched; the camera's
       own position is not published</i>`,
      { sticky: true }).addTo(state.camLayer);
  });
}

/* ------------------------------------------------------------- controls -- */

document.querySelectorAll('.chip').forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll('.chip').forEach((x) => x.classList.remove('on'));
    b.classList.add('on');
    state.filter = b.dataset.f;
    redrawAll();
  };
});

$('#window').onchange = (e) => { state.windowS = Number(e.target.value); load(); };
$('#showcams').onchange = (e) => { state.showCams = e.target.checked; loadCameras(); };

/* ----------------------------------------------------------------- data -- */

/* Two requests, because the two tiers want opposite things.
 *
 * RECORDS go back as far as the window says - which now defaults to all of
 * them. TRAFFIC only ever wants the last couple of minutes, because that is
 * how long a dot survives. Asking for both in one call meant that selecting
 * "everything" also dragged down every private pass ever recorded, almost all
 * of which would be drawn and immediately reaped. Two bounded queries beat one
 * that grows without limit. */
async function load() {
  const trafficCut = bucketed(Date.now() / 1000 - TRAFFIC_FADE_S);
  const [pub, live] = await Promise.all([
    fetch(`/api/sightings?since=${windowCut()}&vclass=public&limit=2000`)
      .then((r) => r.json()),
    fetch(`/api/sightings?since=${trafficCut}&limit=400`).then((r) => r.json()),
  ]);
  state.sightings.clear();
  pub.forEach((r) => state.sightings.set(r.id, r));
  live.forEach((r) => { if (r.tier !== 'public') drawTraffic(r); });
  redrawAll();
  ageTraffic();
}

let lastStats = null;

function emptyState(stats) {
  // The empty-state panel was removed at the owner's request - a live map reads
  // as live on its own, and the panel covered it. Keep the cached stats (other
  // code reads lastStats) and clear any panel a previous version left behind.
  lastStats = stats || lastStats;
  const el = document.getElementById('empty');
  if (el) el.remove();
}

/* Why the map has no police on it.
 *
 * An empty public tier and a quiet street look identical from outside, and a
 * visitor who cannot tell them apart will assume the network does not work.
 * Saying it plainly costs nothing and is the same argument as the transparency
 * page: a claim this project cannot support yet is a claim it does not make,
 * and that is a feature worth showing rather than a gap worth hiding. */
/* Move the map to the deployment's configured centre - but only while nothing
 * better has happened. loadCameras() fitting to the real watched spans is
 * strictly better than any configured guess, and it can land first or second
 * depending on which fetch returns quicker, so this defers to `fittedOnce`
 * rather than assuming an order. Without that guard the map visibly jumps back
 * off the cameras a moment after finding them. */
async function applyConfiguredView() {
  let p;
  try { p = await (await fetch('/api/policy')).json(); } catch { return; }
  if (fittedOnce) return;
  const c = p.map_center;
  if (Array.isArray(c) && c.length === 2 && c.every(Number.isFinite)) {
    map.setView(c, p.map_zoom || 13);
  }
}

async function policyBanner() {
  let p;
  try { p = await (await fetch('/api/policy')).json(); } catch { return; }
  const el = document.getElementById('policybar');
  if (!el) return;
  const main = document.querySelector('main');
  if (p.publishes_public_tier) {
    el.style.display = 'none';
    main.style.top = '52px';
    return;
  }
  el.style.display = '';
  el.innerHTML = `<b>Public-tier reporting is off.</b> SparrowMap is not yet
    willing to call a vehicle a police vehicle: the classifier has not been
    validated against locally labelled footage, and an unvalidated one was
    wrong every time it was checked. Traffic is still counted and cameras are
    still live &mdash; nothing is being asserted that cannot be supported.
    <a href="/transparency">How this is decided &rarr;</a>`;
  // main is absolutely positioned under a fixed-height header, so it has to be
  // pushed down by however tall the notice wraps to on this screen.
  const push = () => { main.style.top = (52 + el.offsetHeight) + 'px';
                       map.invalidateSize(); };
  push();
  window.addEventListener('resize', push);
}

async function loadStats() {
  const s = await (await fetch('/api/stats')).json();
  emptyState(s);
  // 'vehicles' counts DISTINCT PUBLIC-TIER vehicles. It used to count distinct
  // plate hashes across every tier, which counted the empty-string hash shared
  // by every plateless pass as one vehicle - so a map identifying nobody
  // reported "1 vehicle". It is also the wrong thing to report even when it
  // works: the system cannot count distinct private vehicles, by design, and a
  // figure that implies it can is a claim this project should never make.
  // ⚠️ A DASH, NOT A ZERO, WHEN THE QUESTION CANNOT BE ASKED.
  //
  // "public vehicles" counts DISTINCT vehicles, which needs a plate to tell
  // them apart. This camera reads none - 22px of plate against the 60 needed -
  // so the figure sat at 0 beside "4 public sightings" and read as a
  // contradiction. It is the difference between counting none and being unable
  // to count, and printing the second as the first is exactly the mistake of
  // treating a gap in the instrument as evidence of absence.
  const countable = s.vehicles_countable ?? 0;
  const vehicles = countable
    ? `<b>${s.vehicles_24h.toLocaleString()}</b> distinct vehicles`
    : `<b title="Distinct vehicles can only be counted when a plate is read.
This camera reads none, so the sightings above cannot be told apart.">&mdash;</b> distinct vehicles`;
  // 🚨 COMMENTS GO HERE, NOT INSIDE THE TEMPLATE LITERAL.
  // HTML comments inside a `...` string are just text, and a BACKTICK inside
  // one closes the string. That is what took the whole map down: app.js failed
  // to parse, so nothing ran and the page sat on "connecting", "everything
  // quiet", no sightings - while the server was perfectly healthy the whole
  // time. Nothing in the markup below is commented; the reasoning lives here.
  //
  // "online / enrolled" stays: 29 enrolled is true and it is the encouraging
  // framing of a network that is growing. nodes_ever_produced rides in the
  // title as its honest companion - enrolling is one tap, contributing is the
  // thing, and most of the enrolled have never sent a sighting.
  //
  // "hours watched" is every heartbeat any camera ever sent, added up. Both
  // node types beat every 30 SECONDS (run_live.py, sparrow-app.js:500), so
  // beats/120 is hours. It is a LOWER BOUND: heartbeats were not always on,
  // dropped beats are never made up, early browser nodes beat at 45s. It
  // undercounts, which is the safe direction for a front-page figure, and it
  // measures patience - most of what this project asks of a volunteer.
  //
  // Every figure with a time window says so ON THE FIGURE. "passes 24h" once
  // carried the qualifier while "public sightings" did not, so the second read
  // as a running total against an all-time count published elsewhere.
  const everProduced = s.nodes_ever_produced ?? '?';
  const hours = s.heartbeats_total
    ? `<span title="${s.heartbeats_total.toLocaleString()} heartbeats, one every 30 seconds. A lower bound: heartbeats were not always enabled and dropped ones are never counted."><b>${Math.round(s.heartbeats_total / 120).toLocaleString()}</b> hours watched</span>`
    : '';
  $('#stats').innerHTML = `
    <span title="${everProduced} of these have ever sent a sighting. Enrolling a camera is one tap; keeping one running is the real contribution."><i>${s.nodes_online}</i>/<b>${s.nodes_active}</b> cameras online</span>
    ${hours}
    <span><b>${(s.traffic_24h ?? 0).toLocaleString()}</b> passes 24h</span>
    <span><i>${s.public_24h.toLocaleString()}</i> public sightings 24h</span>
    <span>${vehicles}</span>`;
}

/* 🚨 NO PERSISTENT STREAM. This used to hold an EventSource('/api/live') open,
   which on a threaded server is ONE PINNED THREAD PER OPEN TAB - it does not
   survive thousands of viewers. Instead the map REFRESHES on a timer against
   the briefly edge-cached /api/sightings, so the crowd is served by Cloudflare
   and the origin sees ~one fetch per window. The `#live` dot now reflects
   whether the last refresh succeeded rather than a socket's state. */
async function refresh() {
  const dot = $('#live');
  try {
    await load();
    if (dot) {
      dot.classList.add('on');
      // Make "live" tangible: show how many vehicles have passed a camera in the
      // last minute. It reads "live" on a quiet road and "live · N passing" when
      // traffic is actually crossing, so the map obviously IS the live view.
      const now = Date.now() / 1000;
      let passes = 0;
      state.traffic.forEach((e) => { if (now - e.rec.ts < 60) passes += 1; });
      dot.lastChild.textContent = passes ? `live · ${passes} passing` : 'live';
    }
  } catch (e) {
    if (dot) { dot.classList.remove('on'); dot.lastChild.textContent = 'reconnecting'; }
  }
}

refresh();
loadCameras();
loadStats();
applyConfiguredView();
policyBanner();
setInterval(refresh, CACHE_BUCKET_S * 1000);  // new sightings; matches the cache window
setInterval(loadStats, 3000);     // live counters - matches the 3s edge cache
setInterval(loadCameras, 5000);   // 'online' reacts within a beat or two

/* Live driver reports: ephemeral, unverified crowd pins from driving mode. An
 * amber ring, deliberately unlike a verified sighting; cleared and redrawn each
 * poll since the server drops the expired ones. */
async function loadReports() {
  let reports;
  try { reports = (await (await fetch('/api/drive/reports')).json()).reports || []; }
  catch (e) { return; }
  state.reportLayer.clearLayers();
  const now = Date.now() / 1000;
  for (const r of reports) {
    const mins = Math.max(0, Math.round((now - r.ts) / 60));
    L.circleMarker([r.lat, r.lon], {
      radius: 8, color: '#f5a623', weight: 2,
      fillColor: '#f5a623', fillOpacity: 0.3,
    }).bindPopup(`Live driver report — patrol<br>${mins}m ago · `
        + `${r.confirms} confirmation${r.confirms === 1 ? '' : 's'}`)
      .addTo(state.reportLayer);
  }
}
loadReports();
setInterval(loadReports, 10000);

/* Patrol hotspots: every confirmed government sighting ever, as a translucent
 * heat so a driver can see which areas run hot. Off by default, toggled by a
 * control button. No plugin - overlapping low-opacity circles glow where
 * patrols cluster; the server already aggregated them to a grid. */
state.heatLayer = L.layerGroup();
state.heatOn = false;
async function loadHeat() {
  let cells;
  try { cells = (await (await fetch('/api/heat')).json()).cells || []; }
  catch (e) { return; }
  state.heatLayer.clearLayers();
  let max = 1;
  for (const c of cells) if (c.n > max) max = c.n;
  for (const c of cells) {
    const t = c.n / max;
    L.circle([c.lat, c.lon], {
      radius: 70 + t * 140, stroke: false,
      fillColor: '#ff3b30', fillOpacity: 0.12 + t * 0.30,
    }).addTo(state.heatLayer);
  }
}
function toggleHeat() {
  state.heatOn = !state.heatOn;
  const b = document.getElementById('heatBtn');
  if (state.heatOn) {
    loadHeat();
    state.heatLayer.addTo(map);
    if (b) { b.style.background = '#ff3b3033'; b.style.color = '#ff6b60'; }
  } else {
    map.removeLayer(state.heatLayer);
    state.heatLayer.clearLayers();
    if (b) { b.style.background = '#0d1219cc'; b.style.color = '#fff'; }
  }
}
const HeatControl = L.Control.extend({
  options: { position: 'topright' },
  onAdd() {
    const d = L.DomUtil.create('button', 'heatctl');
    d.id = 'heatBtn';
    d.title = 'Show everywhere patrols have been (hotspots)';
    d.textContent = '🔥';
    Object.assign(d.style, {
      width: '40px', height: '40px', borderRadius: '10px',
      border: '1px solid var(--line2)', background: '#0d1219cc',
      color: '#fff', fontSize: '18px', cursor: 'pointer', lineHeight: '1' });
    L.DomEvent.disableClickPropagation(d);
    L.DomEvent.on(d, 'click', (e) => { L.DomEvent.stop(e); toggleHeat(); });
    return d;
  },
});
map.addControl(new HeatControl());

/* First-visit nudge: people were not finding the "Add a camera" link, so on a
 * first visit put the invitation front and centre. Shown once per browser
 * (localStorage), dismissible, and built with DOM calls so it survives the CSP.
 */
function showIntro() {
  try { if (localStorage.getItem('sparrow.introSeen')) return; } catch (e) { return; }
  const ov = document.createElement('div');
  Object.assign(ov.style, { position: 'fixed', inset: '0', zIndex: '3000',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'rgba(0,0,0,.6)', padding: '20px' });
  const card = document.createElement('div');
  Object.assign(card.style, { maxWidth: '380px', width: '100%',
    background: '#0d1219', border: '1px solid #22303c', borderRadius: '16px',
    padding: '26px', textAlign: 'center', color: '#c7d2dc',
    font: '15px/1.55 system-ui,sans-serif', boxShadow: '0 24px 70px rgba(0,0,0,.6)' });
  const mk = (tag, text, style) => {
    const el = document.createElement(tag);
    if (text) el.textContent = text;
    if (style) Object.assign(el.style, style);
    return el;
  };
  const h = mk('div', 'Watch the watchers',
    { fontSize: '21px', fontWeight: '700', color: '#fff', marginBottom: '10px' });
  const p = mk('div', 'SparrowMap runs on volunteer cameras. Point a spare phone '
    + 'at a street and it maps the patrols that pass — plates destroyed on the '
    + 'device, never uploaded.', { color: '#93a3b3', marginBottom: '20px' });
  const add = mk('a', 'Add a camera', { display: 'block', padding: '14px',
    borderRadius: '11px', background: '#3b82f6', color: '#fff', fontWeight: '600',
    textDecoration: 'none', marginBottom: '10px' });
  add.href = '/app';
  const skip = mk('button', 'Just browsing', { display: 'block', width: '100%',
    padding: '12px', borderRadius: '11px', background: 'transparent',
    border: '1px solid #22303c', color: '#7f93a6', cursor: 'pointer',
    font: 'inherit' });
  const done = () => { try { localStorage.setItem('sparrow.introSeen', '1'); } catch (e) {} ov.remove(); };
  skip.addEventListener('click', done);
  add.addEventListener('click', done);
  ov.addEventListener('click', (e) => { if (e.target === ov) done(); });
  card.append(h, p, add, skip);
  ov.appendChild(card);
  document.body.appendChild(ov);
}
setTimeout(showIntro, 700);
setInterval(renderList, 10000);   // keep the "3m ago" column honest
setInterval(ageTraffic, 1000);    // the live traffic view

/* Soft refresh for the shared refresh button (public/refresh.js).
 *
 * Defined HERE rather than in an inline tag on the page, because index.html
 * loads this file with <script src=...> - and a script element with a src
 * IGNORES its inline content. The hook was invisible there, so the button
 * silently fell back to a full reload and threw away the map view every time,
 * which is the one thing the soft path exists to preserve. */
window.sparrowRefresh = async () => {
  await Promise.all([load(), loadCameras(), loadStats(), policyBanner()]);
};

/* ---- government plate search ------------------------------------------
 *
 * The one control on this map that could be mistaken for the thing SparrowMap
 * exists to oppose. So it is built to be honest about its own limits: it can
 * only find a plate on a PUBLIC-tier sighting that a human has confirmed, and
 * the server never scans anything else - not as a display rule, but in the
 * query itself, because a search that scans everything and hides the results
 * still answers the question.
 *
 * The empty state therefore says what the box cannot do, rather than a bare
 * "no results". Somebody typing their own plate in to see whether they are
 * being tracked deserves a straight answer.
 */
(function () {
  const form = document.querySelector('#plateform');
  const box = document.querySelector('#plateresults');
  if (!form || !box) return;
  const input = document.querySelector('#plateq');

  const close = () => { box.style.display = 'none'; box.innerHTML = ''; };

  function render(q, rows) {
    if (!rows.length) {
      box.innerHTML = `<h4>No match for ${esc(q)}</h4>
        <div class="none"><b>This only finds government vehicles.</b><br>
        A plate appears here only when a camera published the vehicle as
        a government vehicle <i>and</i> an operator confirmed it.
        Private vehicles are never searched &mdash; their plates are destroyed
        in the image at the camera and never reach this server, so there is
        nothing here to find.</div>`;
      box.style.display = '';
      return;
    }
    box.innerHTML = `<h4>${rows.length} sighting${rows.length === 1 ? '' : 's'}</h4>`
      + rows.map((r) => `
        <div class="hit" data-id="${r.id}" data-lat="${r.lat}" data-lon="${r.lon}">
          ${r.snap ? `<img src="/snap/${encodeURIComponent(r.snap)}" alt="" loading="lazy">` : ''}
          <div>
            <b>${esc(r.plate_text || '')}</b>
            <div class="sub">${esc(r.vclass || '')} &middot; ${ago(r.ts)}</div>
            <div class="sub">${esc(r.node_id || '')}</div>
          </div>
        </div>`).join('');
    box.style.display = '';
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (q.length < 3) {
      box.innerHTML = `<div class="none">Type at least three characters.</div>`;
      box.style.display = '';
      return;
    }
    try {
      const d = await fetch('/api/plate?q=' + encodeURIComponent(q)).then((x) => x.json());
      render(q, d.results || []);
    } catch (err) {
      box.innerHTML = `<div class="none">Search unavailable.</div>`;
      box.style.display = '';
    }
  });

  // Clicking a hit flies the map to it, which is the only reason to have the
  // result list at all - a plate on its own tells you nothing.
  box.addEventListener('click', (e) => {
    const hit = e.target.closest('.hit');
    if (!hit) return;
    const lat = parseFloat(hit.dataset.lat), lon = parseFloat(hit.dataset.lon);
    // `map` is the module-level Leaflet instance declared above; this IIFE is
    // in the same file and the same scope, so no global is needed.
    if (!isNaN(lat) && !isNaN(lon)) map.setView([lat, lon], 17);
    close();
  });

  document.addEventListener('click', (e) => {
    if (!box.contains(e.target) && !form.contains(e.target)) close();
  });
  input.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
})();

/* ---- the site as one program ------------------------------------------
 *
 * Map, About and Transparency as modes rather than pages. The content for the
 * two text modes is FETCHED from /about and /transparency instead of being
 * copied in here, so the copy has exactly one home. A promise about what this
 * project does with people's data must not be able to say one thing on a page
 * and something slightly different in a panel.
 *
 * /about and /transparency still work as standalone URLs - they are linked
 * from documentation and may be bookmarked - and they are also the source this
 * reads from, so neither can silently drift from the other.
 */
(function modes() {
  const bar = document.querySelector('#modes');
  if (!bar) return;
  const loaded = {};

  async function fill(name) {
    const pane = document.querySelector('#pane-' + name);
    if (loaded[name]) return;
    try {
      const html = await fetch('/' + name).then((r) => r.text());
      // Take the page's body AND its <style>. The standalone pages carry their
      // own styles in <head>; injecting the body alone dropped them, so the
      // panel rendered unstyled - stat numbers glued to their labels, tables
      // and tier boxes unformatted. A <style> IS applied when set via innerHTML
      // (only <script> is inert), so this keeps the panel identical to the
      // standalone page from one source, with no CSS copied into style.css.
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const body = doc.querySelector('.doc') || doc.body;
      const styles = [...doc.querySelectorAll('style')].map((s) => s.outerHTML).join('');
      pane.querySelector('.paneinner').innerHTML = styles + body.innerHTML;
      loaded[name] = true;
      // innerHTML never runs scripts, so the transparency panel has to be
      // started by hand - from the same shared module the standalone page
      // uses, not a second copy of the rendering.
      if (name === 'transparency' && window.sparrowTransparency) {
        window.sparrowTransparency();
      }
    } catch (e) {
      pane.querySelector('.paneinner').innerHTML =
        '<p class="note">Could not load this section.</p>';
    }
  }

  function go(name) {
    document.querySelectorAll('.pane').forEach((p) => { p.hidden = true; });
    bar.querySelectorAll('button').forEach((b) =>
      b.classList.toggle('on', b.dataset.m === name));
    if (name !== 'map') {
      document.querySelector('#pane-' + name).hidden = false;
      fill(name);
    } else {
      // Leaflet mis-sizes itself if the container changed while hidden.
      setTimeout(() => map.invalidateSize(), 50);
    }
    // A shareable URL per mode, without a page load.
    history.replaceState(null, '', name === 'map' ? '/' : '/#' + name);
  }

  bar.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-m]');
    if (b) go(b.dataset.m);
  });

  // Opening /#about lands straight on it, so a link to a section still works.
  const initial = (location.hash || '').replace('#', '');
  if (['about', 'transparency'].includes(initial)) go(initial);
})();
