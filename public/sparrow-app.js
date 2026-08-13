/* SparrowMap, as one program.
 *
 * One page, three modes, one piece of state. The detector logic here is the
 * same code that was verified in the browser - letterboxing, the anchor
 * decode, the pass tracker, the sub-resolution crop - moved rather than
 * rewritten, because it was measured working and a rewrite would have to earn
 * that back.
 *
 * Modes a person cannot use yet are DISABLED rather than hidden-and-broken:
 * before a camera is registered there is exactly one thing to do.
 */
const $ = (s) => document.querySelector(s);
const KEY = 'sparrow.node';

let node = JSON.parse(localStorage.getItem(KEY) || 'null');
let pos = null, chosenCam = '';

/* ---- 0 · a key handed to this device ---------------------------------
 * A scanned QR or an opened key file lands as #k=<id>.<token>.
 * 🚨 THE FRAGMENT IS NEVER SENT TO A SERVER. That is the point: the capability
 * travels inside the picture and is read here, so no request line, proxy log
 * or Referer header ever contained it. Stripped from history immediately, so
 * it cannot be shoulder-read from the URL bar or re-adopted after the camera
 * has been handed on.
 */
function adoptKey() {
  const m = (location.hash || '').match(/^#k=([A-Za-z0-9_]+)\.([A-Za-z0-9_\-]+)$/);
  if (!m) return;
  const [, id, token] = m;
  const prior = node;
  if (prior && prior.id && prior.id !== id &&
      !confirm(`This device already holds ${prior.id}.\n\nReplace it with ${id}?`)) {
    history.replaceState(null, '', location.pathname);
    return;
  }
  node = { id, token, name: (prior && prior.id === id && prior.name) || id };
  localStorage.setItem(KEY, JSON.stringify(node));
  history.replaceState(null, '', location.pathname);
  return true;
}
adoptKey();

// A key arriving while the app is ALREADY open changes only the fragment, so
// nothing reloads and nothing would have happened - which is exactly what a
// person pasting a key into the address bar, or following a key link from
// another tab, would do.
window.addEventListener('hashchange', () => {
  if (adoptKey()) { refreshChrome(); go('watch'); }
});

/* ---- chrome ---------------------------------------------------------- */
const say = (el, t, ok) => { el.textContent = t;
  el.className = 'msg' + (ok === true ? ' ok' : ok === false ? ' bad' : ''); };

function net(state, text) {
  $('#netdot').className = 'dot' + (state ? ' ' + state : '');
  $('#nettext').textContent = text;
}

function refreshChrome() {
  const has = !!(node && node.id && node.token);
  $('#who').textContent = has ? `${node.name || node.id} · ${node.id}`
                              : 'no camera on this device';
  document.querySelectorAll('nav.modes button').forEach((b) => {
    if (b.dataset.m !== 'setup') b.disabled = !has;
  });
  if (has && $('#nname')) {
    $('#nname').value = node.name && node.name !== node.id ? node.name : '';
  }
}

let mode = 'setup';
function go(m) {
  if (m !== 'setup' && !(node && node.token)) return;
  mode = m;
  document.querySelectorAll('main section').forEach((s) =>
    s.classList.toggle('hidden', s.id !== 'm-' + m));
  document.querySelectorAll('nav.modes button').forEach((b) =>
    b.classList.toggle('on', b.dataset.m === m));
  // Leaving a mode must release its camera, or the next mode cannot open one.
  if (m !== 'watch' && running) stop();
  if (m === 'key') loadQR();
  if (m === 'setup' || m === 'watch') listCameras();
}
document.querySelectorAll('nav.modes button').forEach((b) => {
  b.onclick = () => go(b.dataset.m);
});

/* ---- cameras --------------------------------------------------------- */
async function listCameras() {
  try {
    const cams = (await navigator.mediaDevices.enumerateDevices())
      .filter((d) => d.kind === 'videoinput');
    const sel = $('#camsel');
    if (!sel) return;
    // Labels only exist after permission has been granted once, which is why
    // this is called again when a stream starts rather than only on load.
    sel.innerHTML = '<option value="">default camera</option>'
      + cams.map((c, i) => `<option value="${c.deviceId}">${
          (c.label || ('camera ' + (i + 1))).replace(/[<>]/g, '')}</option>`).join('');
    sel.value = chosenCam;
  } catch (e) { /* a convenience, never a blocker */ }
}
document.addEventListener('change', (e) => {
  if (e.target && e.target.id === 'camsel') chosenCam = e.target.value;
});

function videoConstraints() {
  return chosenCam
    ? { deviceId: { exact: chosenCam }, width: { ideal: 1280 }, height: { ideal: 960 } }
    // `ideal` not `exact`: exact fails outright on any device with no rear
    // camera, which is every desktop.
    : { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 960 } };
}

function camError(e) {
  return location.protocol === 'https:' || location.hostname === 'localhost'
    ? 'Camera refused: ' + e.message
    : 'The camera needs https. Open this page on the https address.';
}

/* ---- 1 · setup ------------------------------------------------------- */
$('#btnGeo').onclick = () => {
  say($('#setupMsg'), 'Asking for location…');
  navigator.geolocation.getCurrentPosition(
    (p) => { pos = p.coords;
      say($('#setupMsg'), `Got it: ${p.coords.latitude.toFixed(5)}, `
                        + `${p.coords.longitude.toFixed(5)}`, true); },
    (e) => say($('#setupMsg'), 'Location refused: ' + e.message, false),
    { enableHighAccuracy: true, timeout: 15000 });
};

$('#btnEnroll').onclick = async () => {
  const name = $('#nname').value.trim();
  if (!name || (!pos && !node)) {
    return say($('#setupMsg'), 'Need a name and a position.', false);
  }
  // What KIND of camera this is changes what the map draws. A fixed camera in
  // a window watches a stretch of road and that stretch is published; a phone
  // you carry watches wherever it happens to be pointing and must never have a
  // road drawn for it. Hard-coding 'fixed' here gave four phones an imaginary
  // 80 m road running due north - so ask, and default to the safe answer.
  const kind = $('#kindsel') ? $('#kindsel').value : 'phone';
  const body = { name, kind, reach_m: +$('#reach').value || 30 };
  if (pos) { body.lat = pos.latitude; body.lon = pos.longitude;
             if (pos.heading != null) body.heading = pos.heading; }
  // Proving ownership when updating. Without it the server refuses, which is
  // what stops anyone re-registering somebody else's camera.
  if (node) { body.node_id = node.id; body.token = node.token; }

  const r = await fetch('/api/enroll', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then((x) => x.json()).catch((e) => ({ error: String(e) }));
  if (r.error) return say($('#setupMsg'), r.error, false);

  node = { id: r.id, token: r.token, name };
  if (r.review_token) node.review_token = r.review_token;
  localStorage.setItem(KEY, JSON.stringify(node));
  say($('#setupMsg'), `Registered as ${r.id}. Watch and Key are open now.`, true);
  stage('registered');
  showReviewBanner(node.review_token);
  refreshChrome();
};

/* Show the owner where to review their OWN camera's catches. The token is
 * captured at enrollment; a camera enrolled before tokens existed fetches it
 * once, proving ownership with its node token. Built with DOM calls (no inline
 * style/script) so it survives the strict CSP. */
function showReviewBanner(tok) {
  // ⚠️ The aim link must NOT be hostage to the review token.
  // This returned early whenever there was no review token, and the aim link
  // hung off the end of it - so the cameras least likely to have been set up
  // carefully would have been the ones never offered the chance to aim.
  if (!tok) { showAimBanner(); return; }
  let el = document.getElementById('reviewBanner');
  if (!el) {
    el = document.createElement('div');
    el.id = 'reviewBanner';
    Object.assign(el.style, {
      margin: '10px 0', padding: '10px 12px', borderRadius: '10px',
      border: '1px solid #244', background: '#0f1621', color: '#bfe3d0',
      font: '13px/1.5 system-ui, sans-serif', wordBreak: 'break-all' });
    const host = document.querySelector('main') || document.body;
    host.insertBefore(el, host.firstChild);
  }
  el.textContent = '';

  // 🔑 A TAPPABLE LINK, NOT A TOKEN TO COPY.
  // This used to print the raw token and expect the volunteer to select 32
  // characters out of a banner and paste them into a box on their phone.
  // Most never did, so cameras contributed catches their owner never saw and
  // could not rule on. The token rides in the FRAGMENT, which is never sent to
  // the server, and /rv strips it from the address bar as soon as it is read.
  const line = document.createElement('div');
  line.textContent = 'Your camera has catches to review. ';
  const a = document.createElement('a');
  a.href = location.origin + '/rv#t=' + encodeURIComponent(tok);
  a.textContent = 'Open your review page →';
  Object.assign(a.style, { color: '#7fd1ff', fontWeight: '600' });
  line.appendChild(a);

  const hint = document.createElement('div');
  hint.textContent = 'Bookmark it. It only ever shows YOUR camera.';
  Object.assign(hint.style, { opacity: '.7', marginTop: '4px', fontSize: '12px' });

  // Kept, smaller, for anyone moving to another device or re-adding by hand.
  const code = document.createElement('code');
  code.textContent = tok;
  Object.assign(code.style, { userSelect: 'all', color: '#7fd1ff' });
  const lbl = document.createElement('div');
  Object.assign(lbl.style, { opacity: '.55', marginTop: '6px', fontSize: '11px' });
  lbl_label(lbl, 'token: ');
  lbl.appendChild(code);

  el.appendChild(line); el.appendChild(hint); el.appendChild(lbl);
  showAimLink(el);
}

/* 🧭 THE AIM LINK, BECAUSE UNTIL NOW THERE WAS NOWHERE TO AIM FROM.
 * The placement screen only ever existed inside camctl, the DESKTOP tool - so
 * no volunteer has ever been asked which way their camera points, and 28 of 31
 * cameras sit at heading 0. The server then treats that 0 as a real aim and
 * points the cone due north, which is how a camera watching one street ends up
 * drawn on the one perpendicular to it.
 *
 * The key rides in the SAME #k=<id>.<token> fragment the camera app already
 * uses, so it is never in a request line, and /aim strips it from the address
 * bar as soon as it is read. */
function showAimBanner() {
  if (!node || !node.id || !node.token) return;
  // The review banner already carries the aim link, so never show both.
  if (document.getElementById('aimBanner') ||
      document.getElementById('reviewBanner')) return;
  const el = document.createElement('div');
  el.id = 'aimBanner';
  Object.assign(el.style, {
    margin: '10px 0', padding: '10px 12px', borderRadius: '10px',
    border: '1px solid #244', background: '#0f1621', color: '#bfe3d0',
    font: '13px/1.5 system-ui, sans-serif' });
  const host = document.querySelector('main') || document.body;
  host.insertBefore(el, host.firstChild);
  showAimLink(el);
}

function showAimLink(host) {
  if (!node || !node.id || !node.token) return;
  const wrap = document.createElement('div');
  Object.assign(wrap.style, { marginTop: '8px' });
  const a = document.createElement('a');
  a.href = location.origin + '/aim#k=' + encodeURIComponent(node.id) +
           '.' + encodeURIComponent(node.token);
  a.textContent = 'Point this camera at the right road →';
  Object.assign(a.style, { color: '#3ddc97', fontWeight: '600' });
  const why = document.createElement('div');
  why.textContent = 'Do this when you set it up, and again any time you move it.';
  Object.assign(why.style, { opacity: '.7', marginTop: '4px', fontSize: '12px' });
  wrap.appendChild(a); wrap.appendChild(why);
  host.appendChild(wrap);
}
function lbl_label(node, text) { node.appendChild(document.createTextNode(text)); }

async function ensureReviewToken() {
  if (!(node && node.id && node.token)) return;
  if (node.review_token) { showReviewBanner(node.review_token); return; }
  try {
    const r = await fetch('/api/rv/my-token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + node.token },
      body: JSON.stringify({ node_id: node.id }),
    }).then((x) => x.json());
    if (r && r.new && r.review_token) {
      node.review_token = r.review_token;
      localStorage.setItem(KEY, JSON.stringify(node));
      showReviewBanner(node.review_token);
    } else {
      showAimBanner();          // no review token to mint, but aiming still applies
    }
  } catch (e) { showAimBanner(); /* offline review is fine; aiming is local */ }
}

/* ---- 2 · watch (the detector) ---------------------------------------- */
const SIZE = 320, CONF = 0.45, SEND_EDGE = 200, MIN_FRAMES = 3, GONE_MS = 900;
const VEHICLE = { 2: 'car', 3: 'motorcycle', 5: 'bus', 7: 'truck' };
let session = null, stream = null, running = false, wakeLock = null, beatTimer = null;

/* The last moment a frame actually went through the detector. The beat reads
 * it, so "online" means this phone is really watching - not just that a timer
 * is ticking in a tab whose detector has wedged or been throttled by the
 * browser. A quiet street still beats; only a stopped pipeline goes dark. */
let lastWork = 0;
const STALL_AFTER_MS = 120000;

/* 🚨 THE BEAT REPORTS ITSELF, IMMEDIATELY.
 *
 * The map only learns a camera is up on its next poll, and the operator had no
 * way to tell "working" from "silently broken" without staring at another
 * device for a minute. That is unusable feedback. The phone knows the answer
 * the instant the request returns, so it says so here: LIVE on a 200, and the
 * actual reason on anything else, rather than swallowing the failure. */
async function sendBeat() {
  if (!(node && node.id && node.token)) return;
  if (!lastWork || Date.now() - lastWork > STALL_AFTER_MS) {
    net('warn', 'starting…');
    return;
  }
  try {
    const r = await fetch('/api/heartbeat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + node.token },
      body: JSON.stringify({ node_id: node.id }),
    });
    if (r.ok) {
      net('on', 'LIVE on the map');
      say($('#watchMsg'), 'Watching — this camera is LIVE on the map.', true);
    } else if (r.status === 401) {
      net('warn', 'not signed in');
      say($('#watchMsg'), 'The hub rejected this camera’s key (401). '
        + 'Re-register it in Setup.', false);
    } else if (r.status === 404) {
      net('warn', 'unknown camera');
      say($('#watchMsg'), 'The hub does not know this camera (404). '
        + 'Re-register it in Setup.', false);
    } else {
      net('warn', 'hub error ' + r.status);
    }
  } catch (e) {
    // Offline or the hub is unreachable. Detection keeps running; say so
    // plainly rather than leaving a stale "LIVE" on screen.
    net('warn', 'offline — retrying');
  }
}
let tracks = [], nextId = 1, seen = 0, sent = 0, fpsEMA = 0, lastT = 0;
let LB = { s: 1, ox: 0, oy: 0 };

const pre = document.createElement('canvas'); pre.width = pre.height = SIZE;
const pctx = pre.getContext('2d', { willReadFrequently: true });
const cropCv = document.createElement('canvas');

/* The model is ~38 MB and on a phone that is a real wait. Fetching it here
 * rather than letting the runtime do it silently means the bar can move: a
 * stalled download and a slow one look identical otherwise, and that is exactly
 * the ambiguity that made "is my camera working?" unanswerable. */
async function loadModel() {
  net('busy', 'loading detector');
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.simd = true;
  ort.env.wasm.wasmPaths = '/vendor/';

  let bytes = null;
  try {
    const res = await fetch('/vendor/yolo11s.onnx');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const total = +res.headers.get('Content-Length') || 37925610;
    const reader = res.body && res.body.getReader ? res.body.getReader() : null;
    if (reader) {
      const chunks = []; let got = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value); got += value.length;
        const pct = Math.min(99, Math.round((got / total) * 100));
        $('#loadbar').style.width = pct + '%';
        // Name the thing being downloaded, every time it repaints. This line
        // is what someone stares at while deciding whether to kill it, and
        // "Downloading the detector" alone does not say that the detector is
        // a model file running ON THIS PHONE - which is the answer to the
        // question actually being asked.
        say($('#watchMsg'), `Downloading the vehicle detector — `
          + `${(got / 1048576).toFixed(0)} of ${(total / 1048576).toFixed(0)} MB `
          + `(${pct}%). Runs on this phone; first time only.`);
      }
      bytes = new Uint8Array(got); let at = 0;
      for (const c of chunks) { bytes.set(c, at); at += c.length; }
    } else {
      bytes = new Uint8Array(await res.arrayBuffer());
    }
  } catch (e) {
    // Fall back to letting the runtime fetch it by URL; no progress, but it
    // still works (and a cached copy makes this instant anyway).
    bytes = null;
  }

  say($('#watchMsg'), 'Detector downloaded. Starting it on this phone…');
  session = await ort.InferenceSession.create(bytes || '/vendor/yolo11s.onnx',
    { executionProviders: ['wasm'], graphOptimizationLevel: 'all' });
  $('#loadbar').style.width = '100%';
  say($('#watchMsg'), 'Detector ready and cached — instant next time.', true);
}

/* 🚨 LETTERBOX, NEVER STRETCH. Squashing the frame into the square distorts
   every vehicle by the aspect ratio: the same model on the same picture found
   ZERO vehicles stretched and a car at 0.88 letterboxed. */
function letterbox(src, sw, sh) {
  LB.s = Math.min(SIZE / sw, SIZE / sh);
  const dw = Math.round(sw * LB.s), dh = Math.round(sh * LB.s);
  LB.ox = (SIZE - dw) / 2; LB.oy = (SIZE - dh) / 2;
  pctx.fillStyle = '#727272';
  pctx.fillRect(0, 0, SIZE, SIZE);
  pctx.drawImage(src, LB.ox, LB.oy, dw, dh);
}

const iou = (a, b) => {
  const x0 = Math.max(a[0], b[0]), y0 = Math.max(a[1], b[1]);
  const x1 = Math.min(a[2], b[2]), y1 = Math.min(a[3], b[3]);
  const i = Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
  const ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - i;
  return ua > 0 ? i / ua : 0;
};

function decode(out) {
  const d = out.data, N = out.dims[2], hits = [];
  for (let i = 0; i < N; i++) {
    let best = -1, bs = 0;
    for (const c of [2, 3, 5, 7]) { const v = d[(4 + c) * N + i]; if (v > bs) { bs = v; best = c; } }
    if (bs < CONF) continue;
    const cx = d[i], cy = d[N + i], w = d[2*N + i], h = d[3*N + i];
    // Undo the padding, THEN the scale. Order matters.
    hits.push({ cls: VEHICLE[best], score: bs, box: [
      (cx - w/2 - LB.ox) / LB.s, (cy - h/2 - LB.oy) / LB.s,
      (cx + w/2 - LB.ox) / LB.s, (cy + h/2 - LB.oy) / LB.s] });
  }
  // The export carries no NMS at this opset; without this one car arrives as
  // six overlapping boxes and six "vehicles".
  hits.sort((a, b) => b.score - a.score);
  const keep = [];
  for (const h of hits) if (!keep.some((k) => iou(k.box, h.box) > 0.45)) keep.push(h);
  return keep;
}

function drawOverlay(hits, vw, vh) {
  const cv = $('#ov'), g = cv.getContext('2d');
  if (cv.width !== cv.clientWidth) { cv.width = cv.clientWidth; cv.height = cv.clientHeight; }
  const s = Math.max(cv.width / vw, cv.height / vh);
  const ox = (cv.width - vw * s) / 2, oy = (cv.height - vh * s) / 2;
  g.clearRect(0, 0, cv.width, cv.height);
  g.lineWidth = 2; g.strokeStyle = '#3d8cff'; g.fillStyle = '#3d8cff';
  g.font = '600 11px ui-monospace,monospace';
  for (const h of hits) {
    const [x0, y0, x1, y1] = h.box;
    g.strokeRect(x0*s+ox, y0*s+oy, (x1-x0)*s, (y1-y0)*s);
    g.fillText(h.cls, x0*s+ox, Math.max(10, y0*s+oy - 3));
  }
}

/* The crop is shrunk below plate legibility HERE, on the device. This is the
   privacy control, not a bandwidth saving - and the server measures it rather
   than believing this page. */
function cropOf(video, box) {
  const [x0, y0, x1, y1] = box;
  const pw = (x1-x0) * 0.06, ph = (y1-y0) * 0.06;
  const sx = Math.max(0, x0-pw), sy = Math.max(0, y0-ph);
  const sw = Math.min(video.videoWidth - sx, (x1-x0) + 2*pw);
  const sh = Math.min(video.videoHeight - sy, (y1-y0) + 2*ph);
  const s = Math.min(1, SEND_EDGE / Math.max(sw, sh));
  cropCv.width = Math.max(1, Math.round(sw * s));
  cropCv.height = Math.max(1, Math.round(sh * s));
  cropCv.getContext('2d').drawImage(video, sx, sy, sw, sh, 0, 0, cropCv.width, cropCv.height);
  return cropCv.toDataURL('image/jpeg', 0.82);
}

async function submit(tr) {
  try {
    const r = await fetch('/api/sightings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + node.token },
      body: JSON.stringify({
        node_id: node.id, ts: Date.now() / 1000, source: 'phone_node',
        body: tr.cls, det_conf: tr.score,
        plate_text: '', plate_conf: 0, evidence: {}, snap_b64: tr.best }),
    }).then((x) => x.json());
    if (r.error) { net('busy', 'hub: ' + r.error); return; }
    sent++; $('#sSent').textContent = sent;
    net('on', 'sending');
  } catch (e) { net('busy', 'hub unreachable'); }
}

function reap(now) {
  tracks = tracks.filter((tr) => {
    if (now - tr.last < GONE_MS) return true;
    // One sighting per vehicle, sent when it LEAVES - not one per frame.
    if (tr.seen >= MIN_FRAMES && tr.best && !tr.sent) { tr.sent = true; submit(tr); }
    return false;
  });
}

async function loop() {
  if (!running) return;
  const v = $('#video');
  if (v.readyState < 2) return requestAnimationFrame(loop);
  letterbox(v, v.videoWidth, v.videoHeight);
  const px = pctx.getImageData(0, 0, SIZE, SIZE).data;
  const f = new Float32Array(3 * SIZE * SIZE);
  for (let i = 0, n = SIZE * SIZE; i < n; i++) {
    f[i] = px[i*4] / 255; f[n+i] = px[i*4+1] / 255; f[2*n+i] = px[i*4+2] / 255;
  }
  let hits = [];
  try {
    const out = await session.run({ images: new ort.Tensor('float32', f, [1,3,SIZE,SIZE]) });
    hits = decode(out[Object.keys(out)[0]]);
  } catch (e) {
    say($('#watchMsg'), 'Detector error: ' + e.message, false);
    running = false; return;
  }
  lastWork = Date.now();          // a real frame went through the detector
  const now = performance.now();
  if (lastT) fpsEMA = fpsEMA * 0.8 + (1000 / (now - lastT)) * 0.2;
  lastT = now;
  $('#sFps').textContent = fpsEMA.toFixed(1);

  for (const h of hits) {
    let tr = tracks.find((t) => iou(t.box, h.box) > 0.3);
    if (!tr) {
      tr = { id: nextId++, seen: 0, best: null, bestArea: 0, sent: false, cls: h.cls };
      tracks.push(tr); seen++; $('#sSeen').textContent = seen;
    }
    tr.box = h.box; tr.last = now; tr.seen++; tr.cls = h.cls; tr.score = h.score;
    // Keep the BIGGEST view, not the first: a car is smallest as it enters.
    const area = (h.box[2]-h.box[0]) * (h.box[3]-h.box[1]);
    if (area > tr.bestArea) { tr.bestArea = area; tr.best = cropOf(v, h.box); }
  }
  reap(now);
  drawOverlay(hits, v.videoWidth, v.videoHeight);
  requestAnimationFrame(loop);
}

/* 🚨 WHERE SETUP DIES. 20 of the first 36 cameras registered and never sent a
 * single heartbeat, and nothing recorded why - a denied camera permission, a
 * 38 MB model download that gave up on mobile data, and a closed tab all
 * looked identical from the server. They need completely different fixes, so
 * the biggest number in the project was the least actionable.
 *
 * A stage name from a fixed list and a four-way platform bucket. No user
 * agent, no screen size, nothing that identifies a device - and it is
 * fire-and-forget: a camera must never treat a telemetry failure as a setup
 * failure, so nothing here is awaited and nothing here can throw. */
function platformBucket() {
  var u = navigator.userAgent || '';
  if (/iPhone|iPad|iPod/i.test(u)) return 'ios';
  if (/Android/i.test(u)) return 'android';
  if (/Mobi/i.test(u)) return 'other';
  return 'desktop';
}
function stage(name) {
  try {
    if (!(node && node.id && node.token)) return;
    fetch('/api/node/progress', {
      method: 'POST',
      // ⚠️ THE TOKEN GOES IN THE HEADER, exactly as sendBeat() sends it.
      // _token_ok() reads the Authorization header and nothing else; only
      // /api/enroll also accepts it in the body. Sending it in the body here
      // returned 401 for every call, which - because this is fire-and-forget -
      // would have recorded absolutely nothing while looking like it worked.
      headers: { 'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + node.token },
      body: JSON.stringify({ node_id: node.id, stage: name,
                             platform: platformBucket() }),
      keepalive: true,        // survives the tab closing, which is the case
    }).catch(function () {});  // we most want to hear about
  } catch (e) { /* never let reporting break setting up */ }
}

async function start() {
  stage('starting');
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints(), audio: false });
  } catch (e) { return say($('#watchMsg'), camError(e), false); }
  stage('camera_granted');
  $('#video').srcObject = stream;
  listCameras();
  stage('preview');
  if (!session) {
    stage('model_loading');
    try { await loadModel(); }
    catch (e) { return say($('#watchMsg'), 'Could not load the detector: ' + e.message, false); }
  }
  stage('model_ready');
  try { wakeLock = await navigator.wakeLock.request('screen');
        $('#pWake').className = 'pill on'; $('#pWake').textContent = 'screen kept on'; }
  catch (e) { $('#pWake').className = 'pill warn'; $('#pWake').textContent = 'screen may sleep'; }

  running = true;
  $('#stage').classList.add('live');
  $('#btnStart').classList.add('hidden');
  $('#btnStop').classList.remove('hidden');
  net('on', 'watching');
  say($('#watchMsg'), 'Watching. Leave this window visible.', true);
  // A phone on a quiet street posts no sightings for minutes, but it is still
  // watching - so beat on its own, or the map shows it offline within the
  // 90-second window. A sighting also beats; this covers the gaps between.
  lastWork = Date.now();          // the stream is open; the loop starts next
  stage('detecting');
  sendBeat();
  beatTimer = setInterval(sendBeat, 30000);
  requestAnimationFrame(loop);
}

function stop() {
  running = false;
  lastWork = 0;                   // stopped watching: stop claiming to be online
  if (beatTimer) { clearInterval(beatTimer); beatTimer = null; }
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
  const cv = $('#ov'); cv.getContext('2d').clearRect(0, 0, cv.width, cv.height);
  $('#stage').classList.remove('live');
  $('#btnStart').classList.remove('hidden');
  $('#btnStop').classList.add('hidden');
  $('#pWake').className = 'pill'; $('#pWake').textContent = 'screen lock';
  net('', 'idle');
  say($('#watchMsg'), 'Stopped.');
}
$('#btnStart').onclick = start;
$('#btnStop').onclick = stop;

async function batteryWatch() {
  if (!navigator.getBattery) { $('#pBatt').textContent = 'mains'; return; }
  const b = await navigator.getBattery();
  const upd = () => {
    const pct = Math.round(b.level * 100);
    $('#pBatt').textContent = `battery ${pct}%${b.charging ? ' ⚡' : ''}`;
    $('#pBatt').className = 'pill' + (b.charging || pct > 20 ? ' on' : ' warn');
    // Stop rather than flatten someone's device. A node that killed the
    // machine it runs on does not get run twice.
    if (running && !b.charging && pct <= 20) {
      stop(); say($('#watchMsg'), 'Stopped at 20% battery. Plug in and start again.', false);
    }
  };
  b.addEventListener('levelchange', upd);
  b.addEventListener('chargingchange', upd);
  upd();
}

// A hidden window is a camera that sees nothing. Say so rather than letting
// someone believe their node is running while it is minimised.
document.addEventListener('visibilitychange', async () => {
  if (document.hidden && running) {
    say($('#watchMsg'), 'This window is hidden — the camera is not watching.', false);
  } else if (running) {
    say($('#watchMsg'), 'Watching.', true);
    if (!wakeLock) { try { wakeLock = await navigator.wakeLock.request('screen'); } catch (e) {} }
  } else if (!running && mode === 'watch') {
    $('#stage').classList.remove('live');     // whatever is on screen is old
  }
});

/* Log-by-hand was REMOVED deliberately. It let a person type any plate,
 * tick the evidence signals themselves, and submit at plate_conf 0.95
 * attributed to a real camera at a real position. Since a confirmed public
 * sighting is what makes a plate searchable, that path could publish an
 * arbitrary plate about an arbitrary vehicle. The camera decides what it
 * sees; a person only reviews it afterwards. */

/* ---- 4 · key --------------------------------------------------------- */
async function loadQR() {
  if (!node) return;
  const r = await fetch('/api/key/qr', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    // The token goes in the BODY. A key in a query string is written to every
    // proxy log, kept in history, and leaked in the Referer of any link.
    body: JSON.stringify({ node_id: node.id, token: node.token, origin: location.origin }),
  });
  if (!r.ok) return say($('#kMsg'), 'Could not build the key.', false);
  $('#qr').src = URL.createObjectURL(await r.blob());
}

$('#kFile').onclick = () => {
  const url = `${location.origin}/app#k=${node.id}.${node.token}`;
  const html = `<!doctype html><meta charset="utf-8"><title>SparrowMap camera key</title>
<body style="background:#0a0d12;color:#e6ecf5;font:15px system-ui;padding:40px;max-width:520px;margin:0 auto">
<h2>SparrowMap camera key</h2>
<p>This file is the key to one camera. Opening it gives this device control.</p>
<p><a style="color:#4da3ff;font-size:18px" href="${url}">Open the camera &rarr;</a></p>
<p style="color:#8794a8;font-size:13px">Anyone who has this file has the camera.
Keep it like a house key. If it gets out, rotate the key and this file stops working.</p>
</body>`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
  a.download = `sparrow-camera-key-${node.id}.html`;
  a.click();
  say($('#kMsg'), 'Saved. Copy it to a USB stick and keep it safe.', true);
};

$('#kPrint').onclick = () => window.print();

$('#kRotate').onclick = async () => {
  if (!confirm('Rotate the key?\n\nEvery copy of the current key stops working '
             + 'immediately, including any you printed or shared.')) return;
  const r = await fetch('/api/key/rotate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ node_id: node.id, token: node.token }),
  }).then((x) => x.json());
  if (r.error) return say($('#kMsg'), r.error, false);
  node.token = r.token;
  localStorage.setItem(KEY, JSON.stringify(node));
  await loadQR();
  say($('#kMsg'), 'Rotated. The old key is dead — save this new one.', true);
};

/* ---- boot ------------------------------------------------------------ */
refreshChrome();
batteryWatch();
ensureReviewToken();
listCameras();
go(node && node.token ? 'watch' : 'setup');

/* Register the service worker: it makes this page INSTALLABLE (a browser offers
   "Install" only for a page backed by a worker, over HTTPS) and lets a node run
   through a network blip. Only available in a secure context (HTTPS/localhost),
   so `serviceWorker in navigator` is false on a plain-http LAN box - the guard
   skips it there rather than erroring. */
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
