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
/* 🚨 THE MAP IS POLICE-ONLY (core.py public_tiers). His call: "just police
   type vehicles - no regular city work trucks, no ambulance, no firefighters."
   So the headline says what it now means. `gov` and `emergency` keep their
   labels because historical rows and the reviewer's own vocabulary still use
   them - they simply cannot reach the public tier any more. */
const CLASS_LABEL = {
  police:    'Police vehicle',
  gov:       'Government vehicle',
  emergency: 'Emergency vehicle',
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
  // null until the first refresh answers, so the dot keeps saying "connecting"
  // rather than claiming either state before anything has been tried.
  online: null,
  // 🚦 THE WATCHED-ROAD BANDS DEFAULT TO OFF, AND THAT IS A PRESENTATION CALL,
  // NOT A PRIVACY ONE. Nothing about them leaks - a span is deliberately padded
  // so its midpoint does not localise the camera (road.py / SPAN_MIN_M).
  //
  // The problem is that on a map with few red dots, thirty green bands ARE the
  // map, and a visitor reads them as "thirty things detected here" - which is
  // the exact misreading the corridor shape was drawn to prevent. A band means
  // "somebody is watching this stretch", a dot means "a police vehicle passed".
  // When the dots are sparse the bands drown them out and the map appears to say
  // something it is not saying.
  //
  // ⏭️ TURN THIS BACK ON once the map carries enough sightings that the bands
  // read as context behind the dots instead of as the content. The toggle in the
  // header does it, and the choice is remembered - see SHOWCAMS_KEY.
  showCams: false,
  // Town badges default ON - they are how somebody who has just arrived sees
  // that this is a network rather than one street - but they are the first
  // thing in the way when you zoom into a road, so the answer is remembered.
  showPlaces: true,
  // Public traffic cameras: a different kind of coverage from a volunteer's
  // camera, so it gets its own switch. See the checkbox in index.html.
  //
  // 🚨 DEFAULT OFF SINCE THE FLEET REACHED 4,434. At eighteen cameras this was
  // a handful of squares; at four thousand it is four thousand Leaflet markers
  // built on every load, and he reported the map as laggy the moment it was
  // switched on. The layer is worth having and is one click away - it is just
  // not what the map should be doing before anybody asks. See the viewport
  // bound on /api/nodes, which is the other half of this.
  showPubCams: false,
  // 🚁 TWO AIRCRAFT SWITCHES, NOT ONE, AND THAT IS THE WHOLE POINT.
  //
  // "Government" is a registration category, and most of what falls in it is
  // universities and agricultural agencies - the FAA's own field, not a claim
  // this project makes. Drawn under one "aircraft" toggle they were
  // indistinguishable from a sheriff's helicopter unless you opened the popup,
  // which is exactly backwards for the one question people come here to ask.
  // Separated, "is that police" has an answer you can read off the map.
  showAircraft: false,        // law-enforcement registrations
  showAircraftGov: false,     // other government registrations, and circling
  sightings: new Map(),   // id -> record  (public tier: the records)
  markers: new Map(),     // id -> leaflet marker
  traffic: new Map(),     // id -> {rec, marker}  (private tier: the live view)
  camLayer: L.layerGroup(),
  placeLayer: L.layerGroup(),
  places: null,
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
/* 🚨 CANVAS, NOT SVG, FOR THE VECTOR LAYERS.
 *
 * Reported: the map goes laggy zooming in and out over Linden, ON A PHONE
 * ONLY. That last part is the diagnosis. Every sighting is an L.circleMarker,
 * and Leaflet's default renderer gives each one its own SVG element - so a
 * zoom is not "redraw some dots", it is a style recalculation and reflow over
 * hundreds of DOM nodes, which a desktop absorbs and a phone does not.
 *
 * preferCanvas draws all of them into one canvas instead. Identical positions,
 * identical colours, no clustering - clustering would have been the other
 * obvious fix and it is the wrong one here, because merging two sightings into
 * "2" invents a claim the map cannot support. Every dot is still exactly where
 * it was; only the machinery underneath changed.
 *
 * ⚠️ Canvas ignores per-path CSS classes, so the ONE vector that depends on a
 * stylesheet - the dashed trail (.trail{stroke-dasharray}) - is pinned back to
 * SVG explicitly where it is created. Icons and labels are DOM markers and are
 * unaffected either way.
 */
const map = L.map('map', { zoomControl: false, attributionControl: true,
                           preferCanvas: true })
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
// The camera layer is the only one whose SHAPE depends on the zoom - a span
// that is a corridor at z17 is a dot at z10 (see drawSpans). Redraw it on
// zoom, not on every move: panning cannot change which side of the legibility
// floor a span falls on, and redrawing on move would rebuild 30 layers per
// frame while dragging.
// showCams is checked here too: without it, zooming would put the cameras back
// on a map the visitor had switched them off on.
map.on('zoomend', () => {
  if (state.showCams && state.spans) drawSpans(state.spans);
  drawPlaces();
});
state.placeLayer.addTo(map);
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

/* Whether a PUBLIC-tier record is drawn and listed.
 *
 * Private traffic never reaches here - drawSighting hands it to drawTraffic
 * first - so "traffic" is expressed as "no public record passes", which is
 * exactly what it means: the live road with the records taken off it. */
function passes(s) {
  if (state.filter === 'none') return false;   // hide every vehicle marker
  if (state.filter === 'traffic') return false;  // live passes only
  if (state.filter === 'all') return true;
  return s.vclass === state.filter;
}

/* True when the current filter is capable of showing a public record at all.
 * Used to keep the "N more in the last 24h" nudge quiet when the records are
 * hidden BY CHOICE - offering to widen the window is no help when the window
 * is not what is hiding them, and it reads as the map arguing with itself. */
const filterShowsPublic = () =>
  state.filter !== 'none' && state.filter !== 'traffic';

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

/* How many vehicles are crossing a camera right now.
 *
 * ⚠️ ONE DEFINITION, THREE READOUTS. This number is printed in the stats row,
 * beside the live dot, and in the panel's traffic bar. They were computed
 * separately - two of them counted the live set, the third filtered it to the
 * last 60 seconds - and a filter at 60s over a set that is reaped at 45s can
 * never remove anything, so the third was the same number written a longer way.
 * Three copies of one figure is three chances for them to drift apart and for
 * the map to be seen disagreeing with itself, which costs more trust than the
 * figure earns. So: one function, and it is the size of the live set by
 * definition. If the fade window changes, every readout follows it. */
const movingNow = () => state.traffic.size;

/* The live dot carries two things at once: whether the last refresh worked, and
 * the same count as everything else. They are decided by different timers -
 * connection state on the 4s refresh, the count on the 1s reap - so writing the
 * text from whichever fired last had the dot reading "live · 25 passing" beside
 * a header saying "24 moving now". One definition was not enough; they also
 * have to be PAINTED from the same tick. This is the only writer, and both
 * timers call it. */
function paintLive() {
  const dot = $('#live');
  if (!dot || state.online === null) return;   // still saying "connecting"
  if (!state.online) { dot.lastChild.textContent = 'reconnecting'; return; }
  const n = movingNow();
  dot.lastChild.textContent = n ? `live · ${n} passing` : 'live';
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
  const n = movingNow();
  const el = $('#traffic');
  if (el) {
    el.textContent = n ? `${n} passing now` : 'road quiet';
    el.classList.toggle('busy', n > 0);
  }
  // The stats row is rewritten wholesale every 3s by loadStats, which renders
  // this figure as it stands at that moment; this keeps it moving in between.
  // Updating the one element rather than the row means the count ticks without
  // the rest of the header flickering.
  const mv = $('#movingnow');
  if (mv) {
    mv.textContent = n.toLocaleString();
    mv.classList.toggle('on', n > 0);
  }
  paintLive();
  emptyState();
}

function redrawAll() {
  state.pingLayer.clearLayers();
  state.markers.clear();
  // None also clears the live-traffic layer (its dots are added outside this
  // pass and would otherwise linger until they faded on their own).
  // "Traffic only" deliberately does NOT clear it - those dots are the view.
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
  $('#listtitle').textContent = state.filter === 'traffic'
    ? 'Live traffic'
    : state.trackHash
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
    const show = !state.trackHash && hidden > 0 && state.windowS !== 0
      && filterShowsPublic();
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

  // Under "Traffic only" the list is empty BY DESIGN, and an empty list under a
  // map full of moving dots reads as broken. Say why instead: a private pass has
  // no plate, no id and no detail page, so there has never been anything to put
  // in a row - which is the same point the tier itself is making.
  if (state.filter === 'traffic') {
    $('#list').innerHTML = `<li class="note">Private passes are not listed.
      There is no plate, no id and no detail to show &mdash; the dot on the map
      is the whole of what this system is allowed to know about one, and it
      fades after ${TRAFFIC_FADE_S} seconds.</li>`;
    return;
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
   editable only after a human agrees.

   🚨 WITH ONE EXCEPTION, AND IT IS THE POINT OF THE FOURTH BUTTON.
   "Shows a person" is not a claim about the vehicle, it is a claim about
   somebody who never asked to be in the photograph - an arm on the wheel of the
   car alongside, a face through a window, a watch. Waiting for review is fine
   when the cost of being wrong is a mislabelled truck; it is not fine when the
   cost accrues to a person for every hour the queue is behind. That reason
   takes the PICTURE down immediately (the sighting stays), and a reviewer then
   crops the person out and puts it back. See review_api.hold_photo. */
function openReport(id) {
  const box = $('#reportbox');
  box.classList.remove('hidden');
  box.innerHTML = `
    <div class="rlabel">What looks wrong here?</div>
    <div class="rreasons">
      <button class="btn alt" data-r="not_government">Not a government vehicle</button>
      <button class="btn alt" data-r="wrong_description">Wrong description</button>
      <button class="btn alt" data-r="privacy">Shows a person or private detail</button>
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
      // Ask for the one detail that makes a privacy flag actionable. A reviewer
      // cropping a person out is looking at a small photo and guessing which
      // part to cut; "the arm on the left" turns that into one drag.
      const note = $('#rnote');
      if (note) {
        note.placeholder = reason === 'privacy'
          ? 'Whereabouts in the picture? e.g. the arm and watch on the left'
          : 'Add a detail (optional), e.g. this is an SUV, not a motorcycle';
      }
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
        // 🚨 THE MESSAGE HAS TO MATCH WHAT ACTUALLY HAPPENED. "Nothing on the
        // map changes until they do" is true of every other reason and FALSE of
        // a privacy flag, which pulls the photograph immediately - and telling
        // somebody who just reported their own arm that nothing has changed yet
        // is the worst possible answer to give them. The server reports what it
        // did (`held`) rather than the page assuming from the reason it sent.
        let held = false;
        try { held = !!(await res.json()).held; } catch { held = false; }
        box.innerHTML = held
          ? '<div class="rmsg ok">Thanks. <b>The photo has been taken off the map '
            + 'straight away</b> while a person looks at it. The sighting itself '
            + 'stays; the picture comes back only cropped, or not at all.</div>'
          : '<div class="rmsg ok">Thanks. This was sent to the review '
            + 'queue for a person to check. Nothing on the map changes until they do.</div>';
        setTimeout(done, 6000);
        // The picture is gone from the server: drop it from the open panel too,
        // rather than leaving the flagged image on screen behind the receipt.
        if (held) { const im = document.querySelector('#detail img'); if (im) im.remove(); }
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

/* Full-screen view of one published photo.
   Built with DOM calls rather than innerHTML because the CSP forbids inline
   anything, and pinned to the viewport so a phone shows the crop as large as
   the screen allows. image-rendering is left alone deliberately - smoothing a
   200px crop invents detail that is not in the evidence. */
function openLightbox(s) {
  const ov = document.createElement('div');
  ov.className = 'lightbox';
  const img = document.createElement('img');
  img.src = `/snap/${encodeURIComponent(s.snap)}`;
  img.alt = 'snapshot, enlarged';
  const cap = document.createElement('div');
  cap.className = 'lbcap';
  cap.textContent = `${label_for(s.vclass)} · ${new Date(s.ts * 1000).toLocaleString()}`;
  const close = document.createElement('button');
  close.className = 'lbclose';
  close.textContent = '✕';
  close.setAttribute('aria-label', 'Close');
  const shut = () => { ov.remove(); removeEventListener('keydown', esc); };
  const esc = (e) => { if (e.key === 'Escape') shut(); };
  close.onclick = shut;
  // Tapping the backdrop closes; tapping the photo itself must not, or you
  // dismiss the thing you opened while trying to look at it.
  ov.onclick = (e) => { if (e.target === ov) shut(); };
  addEventListener('keydown', esc);
  ov.appendChild(img); ov.appendChild(cap); ov.appendChild(close);
  document.body.appendChild(ov);
}

async function select(id) {
  state.selected = id;
  const s = state.sightings.get(id) || await (await fetch(`/api/sighting/${id}`)).json();
  state.sightings.set(id, s);

  const pub = isPublic(s);
  const conf = s.vclass_conf != null ? `${Math.round(s.vclass_conf * 100)}%` : '—';

  $('#detail').innerHTML = `
    ${s.snap ? `<div class="shotwrap">
      <img src="/snap/${encodeURIComponent(s.snap)}" alt="snapshot" id="snapImg">
      <button class="viewbtn" id="btnBigger" title="View this photo larger"
        aria-label="View this photo larger">⤢ View</button>
    </div>` : ''}
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
    <!-- 🚨 A LINK BETWEEN SIGHTINGS IS A GUESS AND MUST READ AS ONE.
         "Possibly the same vehicle", never "unit 4021" - and the reason is
         printed next to it so a reader can disagree with the machine rather
         than take its word. A link nobody can check is an assertion with
         extra steps. Only published police and government rows can carry one;
         db.tag_sighting refuses everything else. -->
    ${s.vehicle_tag ? `<div class="why"><b>Possibly the same vehicle</b> as
      other sightings tagged <code>${esc(s.vehicle_tag)}</code>.
      ${esc(s.tag_why || '')} <i>This is inferred, not confirmed.</i></div>` : ''}
    ${pub ? '' : `<div class="why">This vehicle is private tier. Its plate was
      hashed at the camera and never stored, so there is no plate to show and
      no way to search for it. The identifier above is a rolling alias that
      changes daily.</div>`}
    <!-- Filled in async by the scanner lookup below; absent until it answers,
         because a dead link is worse than no link. -->
    <div class="why scanner hidden" id="scannerRow"></div>
    <div class="acts">
      <button class="btn" id="btnTrail">${pub ? 'Show trail' : 'Show today’s trail'}</button>
      <button class="btn alt" id="btnCenter">Centre</button>
      ${pub ? '<button class="btn alt" id="btnReport">Report a problem</button>' : ''}
      <button class="btn alt" id="btnClose" title="Back to the list">Back</button>
    </div>
    <div id="reportbox" class="reportbox hidden"></div>`;
  $('#detail').classList.remove('hidden');

  $('#btnCenter').onclick = () => map.setView([s.lat, s.lon], 17);

  /* 🔍 VIEW THE PHOTO LARGER.
     The published crop is at most 200px on its long edge and the panel shows it
     smaller still, so on a phone the vehicle is a smudge - you cannot check the
     claim the map is making, which is the one thing a viewer should always be
     able to do. Opening it full-screen does not add a single pixel of detail;
     it just stops the layout throwing away the ones that are there. */
  const big = $('#btnBigger');
  if (big) big.onclick = () => openLightbox(s);

  /* Where to LISTEN, for the place this sighting is in.
   *
   * 🚨 A LINK, NEVER A STREAM. Broadcastify allows a feed OWNER to embed their
   * OWN feed and forbids becoming a redistribution layer, so we send people to
   * their site rather than proxying their audio. Receiving unencrypted
   * public-safety radio is legal; rebroadcasting someone else's stream is a
   * contract question and the answer is no.
   *
   * State level on purpose: their county ids are opaque internal numbers, so a
   * deep link would sometimes name the wrong county - and being wrong about
   * which county is listening to whom is not a small error on this map.
   * Rendered only once it resolves, so a lookup failure leaves no dead link. */
  fetch(`/api/scanner?lat=${s.lat}&lon=${s.lon}`)
    .then((r) => r.json())
    .then((d) => {
      const row = $('#scannerRow');
      if (!row || !d || !d.ok || !d.url) return;
      const where = d.county ? `${d.county}, ${d.state}` : d.state;
      row.innerHTML = `<b>Listen:</b> public-safety radio for
        ${esc(where)} is carried on
        <a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">Broadcastify</a>.
        <span class="sub">Someone else's service, not ours. Many agencies are
        encrypted, so a feed may not exist.</span>`;
      row.classList.remove('hidden');
    })
    .catch(() => { /* no link is fine; a broken one is not */ });
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

  // Pinned to SVG: its dashes come from .trail in the stylesheet, and a
  // canvas path has no class for CSS to reach.
  L.polyline(pts, { color: col, weight: 2, opacity: 0.75, className: 'trail',
                    renderer: L.svg() })
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
/* 🚨 A FLORIDA VOLUNTEER OPENED THE MAP AND GOT LANSING, MICHIGAN.
 *
 * Reported, and it is exactly what the code did. The map is created at
 * [42.7, -84.5] - a hardcoded start that is this operator's own state - and
 * chooseView() was the only thing that ever moved it. It could not:
 *
 *   - `_spanBounds` needs PUBLISHED spans, and publishing a span is opt-in
 *     (publish_span defaults off). Measured live: 10 of 255 nodes have one.
 *   - the geolocation callback discarded its own failure - `() => {}` - so a
 *     refused or slow fix left `_geoLoc` null for ever.
 *
 * With neither, chooseView() fell through both branches and did nothing at all,
 * leaving the hardcoded view on screen. Nothing was broken enough to notice:
 * the map worked, it was simply looking at the wrong state, for everybody who
 * does not live near this operator.
 *
 * Their location comes FIRST now when it is available, and the initial fit
 * waits a moment for it rather than committing to somebody else's cameras.
 */
let _geoDone = false;
let _geoDeadline = 0;

function chooseView() {
  if (_userMovedMap) return;

  // A fix may still be seconds away. Committing to a view now and moving the
  // map under someone a moment later is worse than a short wait, so hold the
  // decision until geolocation has answered one way or the other.
  if (!_geoDone && !_geoLoc && Date.now() < _geoDeadline) {
    setTimeout(chooseView, 200);
    return;
  }

  // 🎯 WHERE THEY ARE, WHENEVER WE KNOW IT. This used to be the last resort;
  // it is the first choice. A volunteer opening the map wants their own
  // street, not the centre of everybody else's.
  //
  // ⚠️ `animate: false`, AND THAT IS NOT A PREFERENCE.
  // An animated setView across four zoom levels fires zoomstart and then flies.
  // Measured on the live page: the flight from the hardcoded start to a real
  // fix emitted zoomstart and never landed - the map sat on the start view with
  // an animation in limbo, so the correct branch ran, called setView, and
  // changed nothing anybody could see. Which is the worst shape a bug can take,
  // because every variable reads correct. A hard jump has no flight to lose.
  if (_geoLoc) {
    fittedOnce = true;
    if (_spanBounds &&
        _spanBounds.getCenter().distanceTo(L.latLng(_geoLoc)) < 60000) {
      // Their own area IS the covered area - frame the roads being watched.
      map.fitBounds(_spanBounds.pad(0.35), { maxZoom: 17, animate: false });
    } else {
      map.setView(_geoLoc, 12, { animate: false });
    }
    return;
  }

  // No location. Show the whole network rather than one hardcoded town: it is
  // honestly "here is where this project has cameras", and it is the same view
  // wherever the visitor happens to be.
  if (_spanBounds) {
    fittedOnce = true;
    map.fitBounds(_spanBounds.pad(0.25), { maxZoom: 13, animate: false });
  }
}

if (navigator.geolocation) {
  _geoDeadline = Date.now() + 4000;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      _geoLoc = [pos.coords.latitude, pos.coords.longitude];
      _geoDone = true;
      chooseView();
    },
    // ⚠️ NOT `() => {}`. Swallowing the failure is what left the view pinned:
    // nothing else ever learned that no fix was coming, so the wait above
    // would have run to its deadline every time and the fallback never got to
    // say why.
    () => { _geoDone = true; chooseView(); },
    { enableHighAccuracy: false, timeout: 8000, maximumAge: 600000 },
  );
} else {
  _geoDone = true;
}

/* A way to get back to yourself, because the automatic choice is made once and
 * a map you have panned is a map that will not re-centre (_userMovedMap). */
const MeControl = L.Control.extend({
  options: { position: 'topleft' },
  onAdd() {
    const d = L.DomUtil.create('button', '');
    d.type = 'button';
    d.title = 'Show my area';
    d.textContent = '◎';
    Object.assign(d.style, {
      width: '40px', height: '40px', borderRadius: '10px',
      border: '1px solid var(--line2)', background: '#0d1219cc',
      color: '#fff', fontSize: '18px', cursor: 'pointer', lineHeight: '1' });
    L.DomEvent.disableClickPropagation(d);
    L.DomEvent.on(d, 'click', (e) => {
      L.DomEvent.stop(e);
      if (_geoLoc) { map.setView(_geoLoc, 13, { animate: false }); return; }
      if (!navigator.geolocation) return;
      d.textContent = '…';
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          _geoLoc = [pos.coords.latitude, pos.coords.longitude];
          _geoDone = true;
          d.textContent = '◎';
          map.setView(_geoLoc, 13, { animate: false });
        },
        () => { d.textContent = '◎'; },
        { enableHighAccuracy: true, timeout: 10000 });
    });
    return d;
  },
});
map.addControl(new MeControl());

/* 🎯 SHOW THE WHOLE NETWORK.
 *
 * The find-me control answers "what is near me". This answers the other
 * question people ask on arriving - "how much of this is there?" - and it is
 * the one the opening view can no longer answer, because the map now opens on
 * the visitor rather than on everything.
 *
 * It fits whatever is actually plotted: the watched roads if any are published,
 * otherwise the public sightings themselves. Falling back to a hardcoded
 * country view would be another constant that is wrong for every deployment
 * except this one, which is the mistake that opened the map on Lansing for a
 * volunteer in Florida.
 */
const AllControl = L.Control.extend({
  options: { position: 'topleft' },
  onAdd() {
    const d = L.DomUtil.create('button', '');
    d.type = 'button';
    d.title = 'Show the whole map';
    d.textContent = '⤢';
    Object.assign(d.style, {
      width: '40px', height: '40px', borderRadius: '10px',
      border: '1px solid var(--line2)', background: '#0d1219cc',
      color: '#fff', fontSize: '18px', cursor: 'pointer', lineHeight: '1' });
    L.DomEvent.disableClickPropagation(d);
    L.DomEvent.on(d, 'click', (e) => {
      L.DomEvent.stop(e);
      // A deliberate move by a person, so it must stick: mark the map as
      // user-moved or the next automatic choice would pull them back.
      _userMovedMap = true;
      const pts = [];
      if (_spanBounds) { pts.push(_spanBounds.getNorthEast(), _spanBounds.getSouthWest()); }
      state.sightings.forEach((s) => {
        if (s.tier === 'public' && s.lat != null && s.lon != null) pts.push([s.lat, s.lon]);
      });
      if (!pts.length) return;
      map.fitBounds(L.latLngBounds(pts).pad(0.15), { maxZoom: 12, animate: false });
    });
    return d;
  },
});
map.addControl(new AllControl());

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
/* The viewport, snapped OUTWARD to the server's grid and padded by one cell.
 *
 * ⚠️ THE PADDING IS WHAT STOPS THIS REFETCHING CONSTANTLY. Without it, sitting
 * exactly on a cell boundary makes every small drag flip the key back and
 * forth, so the one thing that was supposed to reuse a cached answer would
 * request twice as often as before.
 *
 * Snapped in the SAME direction as hub._snap_box: outward. A client that
 * rounded to nearest would ask for a box the server then widened, get a
 * superset back, and quietly disagree with the server about which key it was
 * on - correct output, wasted cache. */
const BOX_SNAP = 0.1;
function camBoxKey() {
  const b = map.getBounds();
  const f = (v) => Math.floor(v / BOX_SNAP) * BOX_SNAP - BOX_SNAP;
  const c = (v) => Math.ceil(v / BOX_SNAP) * BOX_SNAP + BOX_SNAP;
  return [f(b.getSouth()), f(b.getWest()), c(b.getNorth()), c(b.getEast())]
    .map((n) => n.toFixed(1)).join(',');
}

async function loadCameras() {
  // 🚨 DO NOT DOWNLOAD 4,800 CAMERAS IN ORDER TO HIDE THEM.
  //
  // This response is ~90 KB without the public traffic cameras and 1.48 MB
  // with them, and the layer that draws them defaults OFF because 4,800
  // markers made the map lag on a phone. Filtering after the download bounded
  // the DRAWING and left the megabyte and its parse exactly where they were -
  // which is most of what "the map is laggy" actually was.
  //
  // With the layer ON it is bounded by VIEWPORT as well, snapped to the same
  // ~11 km grid the server keys its cache on - so panning reuses one cached
  // answer instead of minting a new one per drag, and the answer is always a
  // superset of what is on screen.
  let url = '/api/nodes?public_cams=0';
  if (state.showPubCams) {
    url = `/api/nodes?box=${camBoxKey()}`;
  }
  const cams = await (await fetch(url)).json();
  state.camLayer.clearLayers();

  // Open on the area that actually has cameras, rather than a hardcoded zoom
  // that is wrong for every deployment except the one it was written for.
  // Spans only now - a span-less node contributes no geometry to fit to, and
  // that is correct: it has no published location to open on.
  /* 🎥 PUBLIC TRAFFIC CAMERAS GET A MARKER, because they are the only cameras
   * on this map whose position is already public - the transport department
   * publishes it in the same feed the pictures come from. A volunteer's camera
   * still gets no dot, ever: that one describes a house.
   *
   * Drawn distinctly on purpose. This project's argument is that a VOLUNTEER
   * pointed a camera at their own street, and a viewer has to be able to tell
   * which dots are that and which are a government camera we are reading.
   */
  state.publicCamLayer = state.publicCamLayer || L.layerGroup().addTo(map);
  // Clear FIRST, then bail - returning early would leave the markers drawn
  // after the box is unticked and the toggle would look dead until a reload.
  state.publicCamLayer.clearLayers();
  // A guard, NOT an early return: returning here would abandon loadCameras
  // before it draws the watched-road spans and picks the opening view, so
  // unticking one checkbox would quietly break two unrelated things.
  // \uD83D\uDEA8 circleMarker, NOT marker+divIcon, AND THAT IS THE WHOLE FIX FOR THE
  // PHONE FREEZE.
  //
  // L.marker with a divIcon builds a REAL DOM ELEMENT per camera and ignores
  // preferCanvas entirely - that is what a marker is. At eighteen cameras
  // nobody noticed; at several thousand it is several thousand nodes for the
  // browser to lay out, and a phone stops responding while it does.
  //
  // The sighting dots were moved to canvas for exactly this reason once
  // already ("the phone lag was SVG reflow"); the traffic cameras were left
  // behind and became the bigger layer. A circleMarker honours the map's
  // preferCanvas and is drawn, not built - thousands cost one canvas pass.
  //
  // \u26A0\uFE0F The look changes slightly: a canvas circle instead of a rounded square
  // with a glyph in it. Canvas cannot render a DOM box, and a layer that a
  // phone cannot open is not a nicer icon.
  if (state.showPubCams) cams.filter((c) => c.kind === 'public_cam' && c.lat != null).forEach((c) => {
    L.circleMarker([c.lat, c.lon], {
      radius: 4, weight: 1.5,
      color: '#7fd1ff', fillColor: '#1b2a3d', fillOpacity: 0.9,
    }).bindPopup(
      '<b>' + esc(c.name) + '</b><br>'
      + '<span style="color:#93a3b3">A public traffic camera, read by SparrowMap.'
      + ' Not a volunteer\u2019s camera.</span><br>'
      + esc(String(c.sightings || 0)) + ' passes seen'
    ).addTo(state.publicCamLayer);
  });

  const spans = cams.filter((c) => c.span && c.span.length);
  state.spans = spans;          // kept so zoomend can redraw without refetching
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
  drawSpans(spans);
}

/* ---------------------------------------------------- towns, at low zoom -- */

/* 🚨 THIS EXISTS BECAUSE A SPAN BECOMES A MARKER WHEN YOU ZOOM OUT.
 * An 80 m watched span is 91 px at zoom 17 and under one pixel by zoom 12, so a
 * state-wide view drew a scatter of tiny green smears - which read as "a thing
 * is at this spot", the precise impression the corridor shape exists to
 * prevent. Reported as: "it still looks too much like markers."
 *
 * A town badge is the honest unit at that zoom. It says what is actually known
 * from ten miles up - somebody is watching in Brighton, and there are three
 * cameras there - and it cannot be misread as a location, because a town is
 * not a place a car was.
 *
 * It also publishes LESS than the map already does. A span names a stretch of a
 * named street; the town containing it is strictly coarser. Nothing new is
 * exposed by aggregating upward.
 *
 * The two layers never show together: below the threshold you get towns, above
 * it you get the roads themselves. Showing both would put a badge on top of the
 * detail it stands in for. */
const PLACE_MAX_ZOOM = 13;

async function loadPlaces() {
  try {
    const d = await (await fetch('/api/places')).json();
    state.places = d.places || [];
  } catch (e) {
    state.places = [];      // a failed fetch draws nothing, never a guess
  }
  drawPlaces();
}

function drawPlaces() {
  state.placeLayer.clearLayers();
  // 🚨 CLEAR FIRST, THEN BAIL. Returning before clearLayers would leave the
  // badges on screen after the box is unticked and the toggle would look dead
  // until the next reload - the same trap the watched-roads toggle documents.
  if (!state.showPlaces) return;
  if (!state.places || map.getZoom() > PLACE_MAX_ZOOM) return;

  const G = '#3ddc97';

  /* 🚨 THE BADGE HAS TO SHRINK AS YOU ZOOM OUT, OR IT BECOMES THE BLOB IT
   * REPLACED. A radius fixed in pixels is a radius that covers more GROUND the
   * further out you go: at zoom 3 a 20 px circle spans several hundred miles,
   * so the eastern states merged into one green mass and the red sightings
   * underneath disappeared. That is the same "it looks like a marker" failure
   * as the spans, arriving from the opposite direction.
   *
   * So the badge is small enough at continental zoom to read as a scatter of
   * distinct towns, and grows as the view narrows and there is room. */
  const z = map.getZoom();
  const zf = Math.max(0.42, Math.min(1, (z - 1) / 8));
  // Below this the circle is too small to hold a number legibly, and a
  // half-clipped digit reads as damage rather than data.
  const LABEL_MIN_R = 11;

  state.places.forEach((p) => {
    // Area, not radius, tracks the count - a radius proportional to cameras
    // makes three cameras look nine times one. Clamped so a single camera is
    // still findable and a big town does not swallow the county.
    const r = Math.round(Math.max(9, Math.min(26, 7 + Math.sqrt(p.cameras) * 5)) * zf);
    const live = p.online > 0;

    L.circleMarker([p.lat, p.lon], {
      radius: r, color: G, weight: live ? 2 : 1.2,
      opacity: live ? 0.85 : 0.45,
      fillColor: G, fillOpacity: live ? 0.22 : 0.12,
      // The badge stands for a whole town, so it must not behave like a
      // sighting: no click-to-open, and it sits under the red dots.
      interactive: true,
    }).bindTooltip(
      `<b>${esc(p.place)}</b><br>${p.cameras} camera${p.cameras === 1 ? '' : 's'}` +
      (p.online ? ` &middot; <span style="color:${G}">${p.online} online</span>` : '') +
      `<br><i style="opacity:.6">zoom in to see the watched roads</i>`,
      { direction: 'top', offset: [0, -r] }
    ).addTo(state.placeLayer);

    // The count inside the badge, so the map is readable without hovering -
    // which matters most on a phone, where there is no hover at all. Dropped
    // entirely when the circle is too small to hold it: a clipped digit reads
    // as a rendering fault, and at that zoom the dot is the message anyway.
    if (r >= LABEL_MIN_R) {
      L.marker([p.lat, p.lon], {
        interactive: false,
        icon: L.divIcon({
          className: 'placelabel',
          html: `<span>${p.cameras}</span>`,
          iconSize: [r * 2, r * 2], iconAnchor: [r, r],
        }),
      }).addTo(state.placeLayer);
    }
  });
}

/* 🚨 THE SPAN IS DRAWN TO SCALE, SO ZOOMING OUT DELETES IT.
 * An 80 m span is 91 px at zoom 17, 5.7 px at 13, and 0.7 px at zoom 10. The
 * previous rendering only survived down there BY ACCIDENT: `lineCap: 'round'`
 * draws a half-disc at each end however short the line gets, so a collapsed
 * span still left a ~5 px dot at 0.75 opacity. That dot WAS the "pin" the
 * corridor was built to remove - and removing it removed the zoomed-out map
 * with it. Reported as "I can't see the watched roads anymore when zoomed out".
 * The two were never separate properties, which is why the corridor change
 * could not have been tested at one zoom and called done.
 *
 * It bites now because the network went national after the video: the zoom
 * that shows every camera at once is exactly the zoom where every span is
 * sub-pixel.
 *
 * A mark at the span's MIDPOINT is safe here and nowhere else. Below the
 * legibility floor a single pixel covers more ground than the entire span, so
 * the dot cannot localise anything the span was not already publishing. The
 * argument against a centre point is a HIGH-zoom argument - at zoom 12 one
 * pixel is 28 m, already wider than the padding SPAN_MIN_M adds to hide the
 * midpoint. So: the corridor whenever it is legible, a plain dot when it is
 * not, and never both at once. */
const SPAN_LEGIBLE_PX = 14;

function drawSpans(spans) {
  state.camLayer.clearLayers();
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
    // 🚨 A CORRIDOR ALONG THE ROAD, NOT A BLOB, AND NOT A LOZENGE.
    // A 5px round-capped stroke over an 80 m span draws a short fat pill with
    // bulging ends, and that reads as a MARKER - a thing at a place. It is the
    // opposite of what is being claimed: the camera's own position is jittered
    // and deliberately unpublished, and the span is the only honest unit.
    //
    // The obvious alternative, a soft radial haze, would be WORSE. A blob has a
    // visual centre and the eye finds it instantly - and that centre is the span
    // midpoint, which is exactly what SPAN_MIN_M exists to make uninformative.
    // road.py pads the span to a minimum length "so its midpoint no longer
    // localises the camera"; a rendering with a bright middle hands that back.
    //
    // So: a wide, faint band of EVEN intensity along the whole stretch, with a
    // thin brighter line on the road itself. Uniform end to end, no hotspot to
    // read a position out of, and square ends (lineCap 'butt') because round
    // caps are what made it look like a pin in the first place.
    const tip = `${esc(c.name)}${c.road_name ? ' &middot; ' + esc(c.road_name) : ''}
       <br>${status}<br>${c.sightings} sightings
       <br><i style="opacity:.6">this stretch of road is watched; the camera's
       own position is not published</i>`;

    // How long is this span ON SCREEN right now? Measured per span, not by a
    // zoom cutoff: a 28 m stretch of Hamrick and an 80 m stretch of a highway
    // stop being legible at different zooms, and the honest test is whether
    // THIS line can still be seen as a line.
    const a = map.latLngToLayerPoint(L.latLng(c.span[0]));
    const b = map.latLngToLayerPoint(L.latLng(c.span[1]));
    if (a.distanceTo(b) < SPAN_LEGIBLE_PX) {
      // The dot carries more opacity than the corridor because it has far
      // less area to carry it: the band is 18 px wide and can read at 0.14,
      // while a 4 px dot covering roughly a fortieth of that cannot. Offline
      // still reads quieter than live - 1-3 of 31 cameras are online at any
      // moment, so most of this layer is the offline state and it has to be
      // legible without pretending those cameras are live.
      // Verified at z11 on the real basemap; both this and a fainter version
      // render, so this is a legibility margin, not a fix for a blank map.
      L.circleMarker(L.latLng((c.span[0][0] + c.span[1][0]) / 2,
                              (c.span[0][1] + c.span[1][1]) / 2), {
        radius: 4, color: G, weight: 1.5, fillColor: G,
        opacity: live ? 0.95 : 0.7, fillOpacity: live ? 0.75 : 0.35,
      }).bindTooltip(tip, { sticky: true }).addTo(state.camLayer);
      return;
    }

    L.polyline(c.span, {
      color: G, weight: 18, opacity: live ? 0.14 : 0.06,
      lineCap: 'butt', interactive: false,
    }).addTo(state.camLayer);
    L.polyline(c.span, {
      color: G, weight: 2.5, opacity: live ? 0.65 : 0.28, lineCap: 'butt',
    }).bindTooltip(tip, { sticky: true }).addTo(state.camLayer);
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
/* The watched-roads toggle, and it REMEMBERS.
 *
 * A visitor who turns the bands on, pans, and has them snap back off on the
 * next load will conclude the toggle is broken rather than that it is
 * per-session. The default lives in `state.showCams`; this only overrides it
 * once somebody has expressed a preference, so flipping the default later
 * still reaches everyone who has never touched the control.
 *
 * localStorage can throw outright in private browsing, so every access is
 * guarded - a map that fails to load because it could not remember a checkbox
 * would be a poor trade. */
const SHOWCAMS_KEY = 'sparrow.showCams';
try {
  const saved = localStorage.getItem(SHOWCAMS_KEY);
  if (saved !== null) state.showCams = saved === '1';
} catch (e) { /* private mode: keep the default */ }

const SHOWPLACES_KEY = 'sparrow.showPlaces';
try {
  const saved = localStorage.getItem(SHOWPLACES_KEY);
  if (saved !== null) state.showPlaces = saved === '1';
} catch (err) { /* the default stands */ }
const _showplaces = $('#showplaces');
if (_showplaces) {
  // Agree with state before anyone sees it, for the reason spelled out below.
  _showplaces.checked = state.showPlaces;
  _showplaces.onchange = (e) => {
    state.showPlaces = e.target.checked;
    try { localStorage.setItem(SHOWPLACES_KEY, state.showPlaces ? '1' : '0'); }
    catch (err) { /* not remembering is survivable; not drawing is not */ }
    drawPlaces();
  };
}

const SHOWPUBCAMS_KEY = 'sparrow.showPubCams';
try {
  const saved = localStorage.getItem(SHOWPUBCAMS_KEY);
  if (saved !== null) state.showPubCams = saved === '1';
} catch (err) { /* the default stands */ }
const _showpubcams = $('#showpubcams');
if (_showpubcams) {
  _showpubcams.checked = state.showPubCams;
  _showpubcams.onchange = (e) => {
    state.showPubCams = e.target.checked;
    try { localStorage.setItem(SHOWPUBCAMS_KEY, state.showPubCams ? '1' : '0'); }
    catch (err) { /* not remembering is survivable */ }
    loadCameras();
  };
}

/* 🚁 AIRCRAFT ON THE MAP.
 *
 * The detector has existed for a while and only /planes could see it, which is
 * a page nobody visits. The thing worth showing was never "aircraft" - it is
 * an ORBIT: sustained circling at low altitude over one spot, which is what a
 * police helicopter working a scene looks like from above, and which no amount
 * of registration data can tell you because registration is a claim somebody
 * filed once while an orbit is a thing an aircraft is doing right now.
 *
 * So this layer draws three things and nothing else: aircraft that are
 * ORBITING, aircraft on a GOVERNMENT registration, and aircraft flagged as LAW
 * ENFORCEMENT. Everything else in the sky is a light aircraft going somewhere
 * and would bury the signal it is here to show.
 *
 * ⚠️ Bounded to what is on screen and polled slowly. The upstream is OpenSky's
 * free tier - somebody else's service, shared by everyone - and a map that
 * refetched the whole sky on every pan would be both useless and rude.
 */
const AIR_KEY = 'sparrow.showAircraft';
const airLayer = L.layerGroup().addTo(map);
let _airTimer = null, _airBusy = false;

function airIcon(a) {
  // 🚨 COLOUR ANSWERS "IS IT POLICE", BECAUSE THAT IS THE QUESTION.
  //
  // It used to answer "is it circling", which meant a sheriff's helicopter in
  // transit and a university survey plane were the same colour, and a circling
  // crop duster was the reddest thing on the map. The ring still marks an
  // orbit - that is behaviour and worth seeing - but the colour is ownership.
  const orbit = !!a.orbit;
  const col = a.law_enforcement ? '#ff3b47' : (orbit ? '#ffb547' : '#7fb4ff');
  const rot = Math.round(a.track || 0);
  return L.divIcon({
    className: 'airmark',
    iconSize: [30, 30], iconAnchor: [15, 15],
    html: `<div class="airwrap${orbit ? ' orbit' : ''}">`
        + `<svg viewBox="0 0 24 24" width="22" height="22"`
        + ` style="transform:rotate(${rot}deg)">`
        + `<path fill="${col}" stroke="#06090f" stroke-width="1"`
        + ` d="M12 2l1.6 7.2 7.4 3.2v1.6l-7.4-1.6-1 5.2 2.6 1.8v1.2L12 20l-3.2.6v-1.2l2.6-1.8-1-5.2-7.4 1.6v-1.6l7.4-3.2z"/>`
        + `</svg></div>`,
  });
}

function airPopup(a) {
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const bits = [];
  if (a.orbit) {
    // Say what was MEASURED, not just "circling". This is the claim most worth
    // being able to check, and the number is the whole basis for it.
    const d = a.orbit_detail || {};
    bits.push('<b style="color:#ff3b47">Circling</b>'
      + (d.path_km ? ` — flew ${Math.round(d.path_km)} km and stayed within `
                   + `${(d.net_km || 0).toFixed(1)} km` : ''));
  }
  // ⚠️ SAY "NOT LAW ENFORCEMENT" OUT LOUD. Most government registrations are
  // universities and agricultural agencies, and leaving that implied is how
  // somebody reads a university survey plane as a police aircraft. The map
  // exists to let people check things, so the negative has to be stated.
  if (a.law_enforcement) {
    bits.push('<b style="color:#ff3b47">Law enforcement</b> registration');
  } else if (a.gov) {
    bits.push('<b>Government</b> registration — <b>not</b> law enforcement');
  }
  if (a.owner) bits.push(esc(a.owner));
  if (a.alt_m != null) bits.push(`${Math.round(a.alt_m * 3.28084).toLocaleString()} ft`);
  return `<div class="pop"><h4>${esc(a.call || a.n_number || a.icao)}</h4>`
       + bits.map((b) => `<div class="sub">${b}</div>`).join('')
       + `<div class="sub dim">Live position from ADS-B, which aircraft `
       + `broadcast publicly. SparrowMap does not track aircraft.</div></div>`;
}

// 🚨 WHICH SWITCH OWNS AN AIRCRAFT. Law enforcement wins outright, so a
// sheriff's helicopter is never hidden behind the "other government" box no
// matter what else is true of it. Everything else the layer draws - other
// government registrations, and anything CIRCLING regardless of who owns it -
// belongs to the second box. An aircraft that is neither is not drawn at all.
function airIsPolice(a) { return !!a.law_enforcement; }
function airIsOther(a) { return !a.law_enforcement && (!!a.gov || !!a.orbit); }

function drawAircraft(list) {
  airLayer.clearLayers();
  if (!state.showAircraft && !state.showAircraftGov) return;
  (list || []).forEach((a) => {
    if (a.lat == null || a.lon == null) return;
    const show = (state.showAircraft && airIsPolice(a))
              || (state.showAircraftGov && airIsOther(a));
    if (!show) return;
    L.marker([a.lat, a.lon], { icon: airIcon(a), zIndexOffset: 400 })
      .bindPopup(airPopup(a))
      .addTo(airLayer);
  });
}

function loadAircraft() {
  if (!state.showAircraft && !state.showAircraftGov) {
    airLayer.clearLayers();
    return;
  }
  if (_airBusy) return;                 // a slow answer must not stack up
  _airBusy = true;
  const b = map.getBounds();
  const box = [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]
    .map((n) => n.toFixed(3)).join(',');
  fetch(`/api/aircraft?box=${box}`, { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => { if (d && d.aircraft) drawAircraft(d.aircraft); })
    .catch(() => { /* the rest of the map does not depend on this */ })
    .then(() => { _airBusy = false; });
}

const AIRGOV_KEY = 'sparrow.showAircraftGov';
function _wireAir(sel, key, prop) {
  try {
    const saved = localStorage.getItem(key);
    if (saved !== null) state[prop] = saved === '1';
  } catch (err) { /* the default stands */ }
  const el = $(sel);
  if (!el) return;
  el.checked = state[prop];
  el.onchange = (e) => {
    state[prop] = e.target.checked;
    try { localStorage.setItem(key, state[prop] ? '1' : '0'); }
    catch (err) { /* not remembering is survivable */ }
    // ⚠️ Always redraw, never just clear: turning one box off must leave the
    // OTHER box's aircraft on the map. Clearing the layer here is what makes a
    // shared layer with two switches go wrong.
    loadAircraft();
    if (!state.showAircraft && !state.showAircraftGov) airLayer.clearLayers();
  };
}
_wireAir('#showair', AIR_KEY, 'showAircraft');
_wireAir('#showairgov', AIRGOV_KEY, 'showAircraftGov');

// Slow on purpose - see the note above about whose service this is.
if (_airTimer) clearInterval(_airTimer);
_airTimer = setInterval(loadAircraft, 30000);
map.on('moveend', () => {
  if (state.showAircraft || state.showAircraftGov) loadAircraft();
});
if (state.showAircraft || state.showAircraftGov) loadAircraft();

/* Traffic cameras follow the viewport - but ONLY when the snapped box actually
 * changes.
 *
 * ⚠️ A bare `moveend -> loadCameras()` would refetch on every nudge of the map,
 * which is how a fix for downloading too much turns into downloading more
 * often. The whole point of snapping to a grid is that most movement does not
 * change the answer, so most movement must not ask for it again. */
let _camBox = null;
map.on('moveend', () => {
  if (!state.showPubCams) return;
  const k = camBoxKey();
  if (k === _camBox) return;
  _camBox = k;
  loadCameras();
});

/* The View button: open the chips, close them, and keep the label honest.
 *
 * ⚠️ It does NOT own the filter. The chips still do, and their existing click
 * handler still runs - this listens for the same click and follows. Two things
 * deciding what the filter is would be the same "one rule, many owners" bug
 * this codebase keeps finding in itself; the label is a READOUT, never a
 * source of truth. */
(function () {
  const btn = $('#viewbtn'), menu = $('#filters'), now = $('#viewnow');
  if (!btn || !menu || !now) return;
  const open = (on) => {
    menu.hidden = !on;
    btn.setAttribute('aria-expanded', on ? 'true' : 'false');
  };
  btn.addEventListener('click', (e) => { e.stopPropagation(); open(menu.hidden); });
  menu.addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    now.textContent = chip.textContent.trim();
    open(false);
  });
  // Anywhere else on the page closes it, including on the map.
  document.addEventListener('click', (e) => {
    if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) open(false);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') open(false); });
  // Start agreeing with whatever chip is actually selected.
  const on = menu.querySelector('.chip.on');
  if (on) now.textContent = on.textContent.trim();
})();

const _showcams = $('#showcams');
// The checkbox must be made to AGREE with state before anyone sees it. The
// markup ships checked; if the stored answer or the default is off, a control
// that says "on" over a map with no bands is worse than no control.
_showcams.checked = state.showCams;
_showcams.onchange = (e) => {
  state.showCams = e.target.checked;
  try { localStorage.setItem(SHOWCAMS_KEY, state.showCams ? '1' : '0'); }
  catch (err) { /* not remembering is survivable; not drawing is not */ }
  // Unticking has to CLEAR what is already drawn. loadCameras() returns early
  // when showCams is false, so without this the bands stay on screen and the
  // toggle looks dead until the next reload.
  if (!state.showCams) state.camLayer.clearLayers();
  loadCameras();
};

/* ----------------------------------------------------------------- data -- */

/* Two requests, because the two tiers want opposite things.
 *
 * RECORDS go back as far as the window says - which now defaults to all of
 * them. TRAFFIC only ever wants the last couple of minutes, because that is
 * how long a dot survives. Asking for both in one call meant that selecting
 * "everything" also dragged down every private pass ever recorded, almost all
 * of which would be drawn and immediately reaped. Two bounded queries beat one
 * that grows without limit. */
/* The Layers button: open the switches, close them, and keep the count honest.
 *
 * ⚠️ It OWNS NOTHING. Every checkbox keeps its own existing handler; this only
 * shows and hides the panel and counts what is ticked. Two things deciding
 * whether a layer is on is the "one rule, many owners" bug the View button
 * already had to avoid. */
(function () {
  const btn = $('#layerbtn');
  const panel = $('#layers');
  if (!btn || !panel) return;
  const boxes = () => panel.querySelectorAll('input[type=checkbox]');
  const count = () => {
    const n = [...boxes()].filter((b) => b.checked).length;
    const el = $('#layern');
    if (el) el.textContent = String(n);
  };
  btn.onclick = (e) => {
    e.stopPropagation();
    const open = panel.hasAttribute('hidden');
    panel.toggleAttribute('hidden', !open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  // Tapping the map closes it, the same as the View menu - a panel that can
  // only be dismissed by the button that opened it traps a phone user.
  document.addEventListener('click', (e) => {
    if (!panel.contains(e.target) && e.target !== btn) {
      panel.setAttribute('hidden', '');
      btn.setAttribute('aria-expanded', 'false');
    }
  });
  boxes().forEach((b) => b.addEventListener('change', count));
  count();
})();

/* 🚨 "A POSSIBLE PATROL CAR HERE IS WAITING ON A PERSON."
 *
 * Unreviewed police-classed sightings, drawn as a slow pulse over a ~5 km cell.
 * The point is to say that something is pending and roughly where, WITHOUT
 * asserting a patrol car is there - the classifier runs at about 95% precision,
 * so one in twenty of these is not one, and a confident dot would be the map
 * making a claim no human has checked.
 *
 * ⚠️ IT IS DELIBERATELY UNLIKE A SIGHTING. Sightings are small, sharp and
 * exactly placed because they are records. This is large, soft, dashed and
 * pulsing because it is a question. If the two ever start looking alike,
 * something has gone wrong with the argument this map makes.
 *
 * ⚠️ The coarsening is the SERVER's (db.pending_areas). Rounding here would be
 * decoration - the precise position would already have been sent.
 */
let pendingLayer = null;

async function loadPending() {
  try {
    const d = await (await fetch('/api/pending', { cache: 'no-store' })).json();
    pendingLayer = pendingLayer || L.layerGroup().addTo(map);
    pendingLayer.clearLayers();
    (d.cells || []).forEach((c) => {
      const km = (d.cell_deg || 0.05) * 111;
      L.circle([c.lat, c.lon], {
        radius: km * 500,               // half the cell, in metres
        className: 'pendingPulse',
        color: '#ffb547', weight: 2, dashArray: '6 6',
        fillColor: '#ffb547', fillOpacity: 0.08, interactive: true,
      }).bindPopup(
        `<div class="pop"><h4>Possible patrol car, not yet reviewed</h4>`
        + `<div class="sub">${c.n} sighting${c.n === 1 ? '' : 's'} in this area `
        + `the detector called police or government, waiting on a person.</div>`
        + `<div class="sub dim">The area is deliberately rough and the exact `
        + `position is not published: nobody has checked these yet, and about `
        + `one in twenty will not be a patrol car.</div></div>`
      ).addTo(pendingLayer);
    });
  } catch (err) { /* the map is fine without it */ }
}

/* The opening view, drawn before the live data can possibly arrive.
 *
 * 🚨 THE MAP USED TO OPEN EMPTY FOR ABOUT 0.7s ON EVERY VISIT. Two live API
 * calls - measured 57.8 KB and 175.5 KB - and both answer max-age=4 with
 * cf-cache-status DYNAMIC, so the edge caches neither and every visitor is a
 * pair of database queries for an answer identical for all of them.
 *
 * /static/snapshot.json is a plain file, so the edge DOES cache it: the first
 * paint costs one cached fetch and no origin work.
 *
 * ⚠️ IT NEVER OVERWRITES LIVE DATA. If load() has already landed - a fast
 * connection, or a soft refresh - this returns without touching anything. A
 * snapshot arriving late and painting stale dots over fresh ones would be
 * worse than the blank map it replaces.
 *
 * ⚠️ AND IT DOES NOT CLAIM TO BE LIVE. The status pill stays "connecting"
 * until real data arrives; a stale snapshot presented as live would be the
 * project telling a small lie on its own front page every few minutes.
 */
let _liveArrived = false;

async function drawSnapshot() {
  try {
    const d = await (await fetch('/static/snapshot.json')).json();
    if (_liveArrived || !d) return;
    (d.public || []).forEach((r) => state.sightings.set(r.id, r));
    (d.traffic || []).forEach((r) => { if (r.tier !== 'public') drawTraffic(r); });
    redrawAll();
  } catch (err) { /* the live path is the real one; this is a head start */ }
}

async function load() {
  const trafficCut = bucketed(Date.now() / 1000 - TRAFFIC_FADE_S);
  const [pub, live] = await Promise.all([
    fetch(`/api/sightings?since=${windowCut()}&vclass=public&limit=2000`)
      .then((r) => r.json()),
    fetch(`/api/sightings?since=${trafficCut}&limit=400`).then((r) => r.json()),
  ]);
  // ⚠️ The clear() is why drawSnapshot must never run after this: live data
  // replaces the snapshot wholesale rather than merging with it.
  _liveArrived = true;
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
  // 🚨 THREE FUNCTIONS WANTED TO SET THE OPENING VIEW AND `fittedOnce` WAS THE
  // ONLY THING KEEPING THEM APART - a flag read AFTER an await, so which one
  // won depended on which network call returned first. That is how a Florida
  // volunteer ended up looking at Lansing: the configured centre is this
  // deployment's own town, and it only had to arrive at the right moment.
  //
  // It is now the LAST resort rather than a competitor. If a location fix is
  // still plausibly coming, this stays out of the way entirely - chooseView
  // owns the decision and has its own fallback for having no fix at all.
  if (fittedOnce || _userMovedMap) return;
  if (!_geoDone && Date.now() < _geoDeadline) return;
  if (_geoLoc || _spanBounds) return;      // chooseView has something better
  const c = p.map_center;
  if (Array.isArray(c) && c.length === 2 && c.every(Number.isFinite)) {
    map.setView(c, p.map_zoom || 13, { animate: false });
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
  // "Moving now" is the only figure up here that is not the server's - it is
  // the size of the live traffic set this page is already drawing, so the
  // number in the header and the dots on the map are the same fact and cannot
  // disagree. ageTraffic keeps it ticking between these rewrites.
  //
  // The last hour rides in the title because the live figure alone is
  // ambiguous in the direction that matters: 0 is the normal state of a small
  // network on a quiet road, and it looks identical to every camera being off.
  // The hourly count is what tells a visitor which one they are looking at.
  const hour = s.traffic_1h ?? null;
  const movingTitle = hour === null
    ? 'Vehicles crossing a camera in the last 45 seconds.'
    : `Vehicles crossing a camera in the last 45 seconds. ${hour.toLocaleString()} passes in the last hour, so 0 here means a quiet road rather than a network that has stopped.`;
  const everProduced = s.nodes_ever_produced ?? '?';
  const hours = s.heartbeats_total
    ? `<span title="${s.heartbeats_total.toLocaleString()} heartbeats, one every 30 seconds. A lower bound: heartbeats were not always enabled and dropped ones are never counted."><b>${Math.round(s.heartbeats_total / 120).toLocaleString()}</b> hours watched</span>`
    : '';
  $('#stats').innerHTML = `
    <span title="${everProduced} of these have ever sent a sighting. Enrolling a camera is one tap; keeping one running is the real contribution."><i>${s.nodes_online}</i>/<b>${s.nodes_active}</b> cameras online</span>
    ${hours}
    <span class="movingstat" title="${movingTitle}"><b id="movingnow" class="${movingNow() ? 'on' : ''}">${movingNow().toLocaleString()}</b> moving now</span>
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
    // Make "live" tangible: the dot reads "live" on a quiet road and
    // "live · N passing" when traffic is actually crossing, so the map
    // obviously IS the live view. paintLive owns the text; this owns whether
    // the connection is up.
    state.online = true;
    if (dot) dot.classList.add('on');
  } catch (e) {
    state.online = false;
    if (dot) dot.classList.remove('on');
  }
  paintLive();
}

// 🚨 FIRST, AND DELIBERATELY NOT AWAITED. The snapshot is a cached static file
// and usually wins the race against refresh() by a wide margin, so the map has
// dots before the live query has left the building. If it loses, it checks
// _liveArrived and does nothing - see drawSnapshot.
drawSnapshot();
refresh();
loadPending();
loadCameras();
// Towns are independent of the watched-roads toggle: they are what the map
// shows INSTEAD of spans when zoomed out, not a second copy of them, so they
// load whether or not the bands are switched on.
loadPlaces();
loadStats();
applyConfiguredView();
policyBanner();
// Pending is a slow signal - a human deciding takes minutes, not seconds - so
// it is polled far less often than the map data it sits beside.
setInterval(loadPending, 60000);
setInterval(refresh, CACHE_BUCKET_S * 1000);  // new sightings; matches the cache window
// 🚨 30s, HIS CALL, AND IT IS ALSO TEN TIMES LESS LOAD.
// Every open tab was asking for the counters every three seconds. On an
// ordinary day that is invisible; during a viral wave it is the single
// chattiest thing the site does, from the largest number of clients, for a
// figure nobody reads that often. The numbers it drives - cameras online,
// passes today, moving now - do not change meaningfully inside half a minute,
// and nodes_online now has a 5-minute posting window behind it anyway.
setInterval(loadStats, 30000);
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
    // 🚨 ROUND AND 44px, MATCHING THE BUG AND REFRESH BUTTONS DIRECTLY BELOW.
    // This was a 40px rounded SQUARE sitting at the top of a column of round
    // 44px circles, which reads as a control that arrived from somewhere else.
    // Same shape, same size, same edge - see sitenav.js, which lays the rest of
    // that stack out.
    Object.assign(d.style, {
      width: '44px', height: '44px', borderRadius: '50%',
      border: '1px solid #2a3547', background: '#111621ee',
      color: '#fff', fontSize: '18px', cursor: 'pointer', lineHeight: '1',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      backdropFilter: 'blur(6px)' });
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
  // ⚠️ "PRIVATE plates", NOT "plates". A government plate is kept readable and
  // searchable on purpose - that is the entire public tier - and snapshot.py
  // redacts only when tier != "public". The unscoped version of this sentence
  // promised something the system does not do and was never meant to do, which
  // is a worse failure than promising nothing.
  const p = mk('div', 'SparrowMap runs on volunteer cameras. Point a spare phone '
    + 'at a street and it maps the patrols that pass. Private plates are '
    + 'destroyed on the device and never uploaded.',
    { color: '#93a3b3', marginBottom: '20px' });
  const add = mk('a', 'Add a camera', { display: 'block', padding: '14px',
    borderRadius: '11px', background: '#3b82f6', color: '#fff', fontWeight: '600',
    textDecoration: 'none', marginBottom: '10px' });
  add.href = '/app';

  /* 🚨 SIGN IN BELONGS HERE MORE THAN ANYWHERE ELSE ON THE SITE, AND THE
   * REASON IS NOT CONVENIENCE.
   *
   * `sparrow.introSeen` lives in the SAME localStorage as the camera key. So
   * the browser eviction that loses somebody their camera - Safari wipes
   * script-writable storage after 7 days for a site not installed to the home
   * screen - also clears this flag. Every volunteer who is about to report
   * that their camera "got deleted" sees THIS CARD FIRST, and until now the
   * only thing it offered them was "Add a camera", which is how they ended up
   * enrolling a second one and orphaning the first. 160 of 262 nodes have
   * never produced anything; one street is enrolled six times.
   *
   * A returning owner and a brand-new visitor are indistinguishable here, so
   * the card has to serve both without pushing either. The camera invitation
   * stays the primary button; the way back is offered beside it, with the one
   * sentence that stops the duplicate being made.
   */
  const row = mk('div', null, { display: 'flex', gap: '8px', marginBottom: '10px' });
  const secondary = {
    flex: '1', display: 'block', padding: '12px 8px', borderRadius: '11px',
    background: '#131c27', border: '1px solid #22303c', color: '#c7d2dc',
    fontWeight: '600', fontSize: '12.5px', textDecoration: 'none',
    textAlign: 'center', cursor: 'pointer', whiteSpace: 'nowrap',
    minWidth: '0' };
  const drive = mk('a', '🚗 Driving', secondary);
  drive.href = '/drive';
  // A shop with a camera already on the street is coverage that costs nobody a
  // spare phone. It was reachable only from a paragraph inside /app.
  const biz = mk('a', '🏪 Business', secondary);
  biz.href = '/business';
  const signin = mk('a', '🔑 Sign in', secondary);
  signin.href = '/signin';
  row.append(drive, biz, signin);

  const backNote = mk('div',
    'Set up a camera before? Nothing is deleted — sign in with your key rather '
    + 'than adding it again.',
    { color: '#6f8296', fontSize: '12px', lineHeight: '1.5', marginBottom: '14px' });

  const skip = mk('button', 'Just browsing', { display: 'block', width: '100%',
    padding: '12px', borderRadius: '11px', background: 'transparent',
    border: '1px solid #22303c', color: '#7f93a6', cursor: 'pointer',
    font: 'inherit' });
  const done = () => { try { localStorage.setItem('sparrow.introSeen', '1'); } catch (e) {} ov.remove(); };
  skip.addEventListener('click', done);
  // Every route out of this card marks it seen. A link that navigates without
  // doing so brings the card back on the next visit to the map, which reads as
  // the site having forgotten the choice that was just made.
  [add, drive, biz, signin].forEach((el) => el.addEventListener('click', done));
  ov.addEventListener('click', (e) => { if (e.target === ov) done(); });
  card.append(h, p, add, row, backNote, skip);
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
  await Promise.all([load(), loadCameras(), loadPlaces(), loadStats(), policyBanner()]);
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

  /* The box lives in a dialog now (see index.html). Opening and closing it is
   * all that changed here - the search itself, below, never learns about it.
   *
   * ⚠️ FOCUS AFTER THE UNHIDE, NOT BEFORE. A hidden element cannot take focus,
   * so focusing in the same tick does nothing and the phone keyboard does not
   * appear - which turns a one-tap search into a two-tap one for no visible
   * reason. */
  const modal = document.querySelector('#platemodal');
  const opener = document.querySelector('#plateopen');
  if (modal && opener) {
    const openModal = () => {
      modal.hidden = false;
      /* 🚨 FOCUS IN THE SAME TASK AS THE TAP, NOT IN A FRAME CALLBACK.
       * This used requestAnimationFrame to focus "after the unhide", which
       * works on a desktop and fails on a phone: iOS only opens the keyboard
       * for a focus() that happens inside the user gesture, and a rAF callback
       * is a new task, so the box appeared and the keyboard did not. Measured
       * as document.activeElement staying on <body> after the click.
       * Setting hidden=false takes effect immediately, so the element is
       * already focusable on the very next line - the frame wait bought
       * nothing and cost the keyboard. */
      if (input) { try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); } input.select(); }
    };
    const closeModal = () => {
      modal.hidden = true;
      box.style.display = 'none';
      box.innerHTML = '';
    };
    opener.addEventListener('click', openModal);
    const bg = document.querySelector('#plateclose');
    if (bg) bg.addEventListener('click', closeModal);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !modal.hidden) closeModal();
    });
    /* Picking a result means "take me there", so the dialog gets out of the
     * way. Bound on the results list rather than inside the click handler
     * below, so a result added by any future code path still closes it. */
    box.addEventListener('click', (e) => {
      if (e.target.closest('.hit')) setTimeout(closeModal, 60);
    });
  }

  const close = () => { box.style.display = 'none'; box.innerHTML = ''; };

  // A place result is deliberately a DIFFERENT shape from a sighting: no
  // image, no plate, an arrow instead. Someone skimming must never mistake
  // "here is that town" for "here is a vehicle we recorded".
  const placesHtml = (places) => !places.length ? '' :
    `<h4>${places.length} place${places.length === 1 ? '' : 's'}</h4>`
    + places.map((p) => `
        <div class="hit place" data-lat="${p.lat}" data-lon="${p.lon}" data-zoom="13">
          <div>
            <b>&rarr; ${esc(p.name.split(',')[0])}</b>
            <div class="sub">${esc(p.name.split(',').slice(1).join(',').trim())}</div>
          </div>
        </div>`).join('');

  function render(q, rows, places) {
    places = places || [];
    if (!rows.length) {
      box.innerHTML = placesHtml(places)
        + `<h4>No plate matches ${esc(q)}</h4>
        <div class="none"><b>Plate search only finds government vehicles.</b><br>
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
        </div>`).join('') + placesHtml(places);
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
    /* One box, two questions. People type a town into a search field on a map
     * because that is what search fields on maps do, and getting back "no
     * plates match Champaign" is the box telling them they used it wrong.
     *
     * Both are asked at once and both are shown, plates first: this box is
     * labelled for plates, and a place result must never look like a sighting.
     * The geocode is PROXIED (see /api/geocode) so a visitor of a site about
     * being watched never hands a third party their IP and the name of the
     * place they were curious about. */
    try {
      const [plates, places] = await Promise.all([
        fetch('/api/plate?q=' + encodeURIComponent(q))
          .then((x) => x.json()).catch(() => ({ results: [] })),
        fetch('/api/geocode?q=' + encodeURIComponent(q))
          .then((x) => x.json()).catch(() => ({ results: [] })),
      ]);
      // 🚨 AN ERROR IS NOT AN EMPTY RESULT SET.
      // The server answers a failed place lookup with {"error": ...} and no
      // `results`, and this rendered that as "no matches" - so a Nominatim ban,
      // a rate-limit or an upstream outage looked exactly like "that town isn't
      // on the map". A person searching their own town would conclude the
      // project does not cover them, and nothing anywhere would record a
      // failure. The whole class of bug this codebase keeps finding: something
      // broke, and the UI reported a confident, wrong answer instead.
      const err = places.error || plates.error;
      if (err && !(places.results || []).length && !(plates.results || []).length) {
        box.innerHTML = `<div class="none">Search is temporarily unavailable`
          + ` &mdash; ${esc(String(err).slice(0, 80))}</div>`;
        box.style.display = '';
        return;
      }
      render(q, plates.results || [], places.results || []);
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
    if (hit.classList.contains('place')) {
      // A town, not a vehicle: fly there and close, no marker and no claim.
      map.setView([lat, lon], parseInt(hit.dataset.zoom, 10) || 13);
      close();
      return;
    }
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
