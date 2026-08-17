/* Does drive.html still upload the same car every five seconds?
 *
 * This does NOT reimplement the rule - a test that restates the logic it is
 * checking passes for both the right and the wrong version. It pulls the two
 * decisive expressions out of drive.html as TEXT and evaluates those, so an
 * inverted comparison or a dropped clause in the shipped file fails here.
 *
 *   node tools/test_drive_dedup.js
 */
'use strict';
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'public', 'drive.html');
const src = fs.readFileSync(SRC, 'utf8');

function need(re, what) {
  const m = src.match(re);
  if (!m) { console.error('FAIL: could not find ' + what + ' in drive.html'); process.exit(1); }
  return m;
}

// The constant, straight from the file.
const RESEND_WIDTH = parseFloat(need(/var RESEND_WIDTH=([\d.]+);/, 'RESEND_WIDTH')[1]);
const MIN_FRAMES = parseInt(need(/MIN_FRAMES=(\d+)/, 'MIN_FRAMES')[1], 10);
const MIN_SEND_PX = parseInt(need(/var MIN_SEND_PX=(\d+);/, 'MIN_SEND_PX')[1], 10);

// The freshness gate, verbatim.
const unsentExpr = need(/var unsent = ([^;]+);/, 'the `unsent` gate')[1];
// The selection condition, verbatim.
const selectExpr = need(/if\(tr\.seen>=MIN_FRAMES && tr\.best && unsent && \(([^)]+)\)\)/,
                        'the winBest selection condition')[1];

console.log('from drive.html: RESEND_WIDTH=%s MIN_FRAMES=%s MIN_SEND_PX=%s',
            RESEND_WIDTH, MIN_FRAMES, MIN_SEND_PX);
console.log('  unsent gate : %s', unsentExpr);
console.log('  selection   : %s', selectExpr);
console.log();

/* Run one frame of the real decision against a track. Returns the new winBest. */
function offer(tr, area, winBest) {
  const trW = tr.bestBox ? (tr.bestBox[2] - tr.bestBox[0]) : 0;
  const unsent = eval(unsentExpr);                      // eslint-disable-line no-eval
  if (tr.seen >= MIN_FRAMES && tr.best && unsent && eval(selectExpr)) {
    return { area: area, box: tr.bestBox, tr: tr };
  }
  return winBest;
}

/* Close a 5s window the way dloop does. */
function closeWindow(winBest) {
  if (!winBest) return null;
  const wBest = winBest.box[2] - winBest.box[0];
  if (wBest >= MIN_SEND_PX) { winBest.tr.sentW = wBest; return winBest.tr; }
  return null;
}

function track(id, w) {
  return { id: id, seen: MIN_FRAMES, best: true, bestBox: [0, 0, w, w / 2] };
}

let fails = 0;
function check(name, got, want) {
  const ok = got === want;
  console.log('  [' + (ok ? 'ok  ' : 'FAIL') + '] ' + name.padEnd(52)
              + ' got ' + got + ', want ' + want);
  if (!ok) fails++;
}

// ---- 1. the car he was following: constant size, 30 windows -----------------
// This is the measured bug: 30 consecutive uploads, identical scores.
const lead = track('lead', 300);
let sends = 0;
for (let w = 0; w < 30; w++) {
  let wb = null;
  wb = offer(lead, 300 * 150, wb);
  if (closeWindow(wb)) sends++;
}
check('a car followed for 30 windows is uploaded once', sends, 1);

// ---- 2. the overtaken cruiser: it must not be blocked by the lead car -------
// Same 30 windows, but from window 10 a second vehicle is alongside and GROWING.
const lead2 = track('lead2', 300);
const cruiser = track('cruiser', 130);
let leadSends = 0, cruiserSends = 0;
for (let w = 0; w < 30; w++) {
  let wb = null;
  wb = offer(lead2, 300 * 150, wb);
  if (w >= 10) {
    const cw = 130 + (w - 10) * 12;              // closing on it as we overtake
    cruiser.bestBox = [0, 0, cw, cw / 2];
    wb = offer(cruiser, cw * (cw / 2), wb);
  }
  const sent = closeWindow(wb);
  if (sent === lead2) leadSends++;
  if (sent === cruiser) cruiserSends++;
}
check('the overtaken vehicle does get uploaded', cruiserSends >= 1, true);
check('the followed car still only goes once', leadSends, 1);

// ---- 3. a genuine second look is still allowed -------------------------------
const closing = track('closing', 130);
let looks = 0;
for (const w of [130, 140, 160, 200, 240, 300, 400]) {
  closing.bestBox = [0, 0, w, w / 2];
  let wb = offer(closing, w * (w / 2), null);
  if (closeWindow(wb)) looks++;
}
check('a vehicle that gets much closer is re-sent', looks > 1, true);
check('  but not on every window', looks < 7, true);

// ---- 4. the small-vehicle floor still holds ---------------------------------
const tiny = track('tiny', MIN_SEND_PX - 20);
let tinySends = 0;
for (let w = 0; w < 5; w++) {
  let wb = offer(tiny, 100 * 50, null);
  if (closeWindow(wb)) tinySends++;
}
check('a vehicle under MIN_SEND_PX is never uploaded', tinySends, 0);

// ---- 5. opening the page must not mint a camera ------------------------------
// 426 of 570 enrolled volunteer cameras had never sent a crop, because /drive
// enrolled on a GPS fix alone. Enrolment must wait for a vehicle worth sending.
const enrolExpr = need(/if\(!driveNode && !driveNodePending && me && ([^)]+)\)\{/,
                       'the enrolment gate')[1];
console.log();
console.log('  enrolment gate: ' + enrolExpr);
console.log();

function wouldEnrol(winBest) {
  const wCand = winBest ? (winBest.box[2] - winBest.box[0]) : 0;
  return eval(enrolExpr);                               // eslint-disable-line no-eval
}

check('parked with the page open enrols nothing', wouldEnrol(null), false);
check('a distant vehicle under the floor enrols nothing',
      wouldEnrol({ box: [0, 0, MIN_SEND_PX - 30, 40] }), false);
check('a vehicle worth uploading does enrol',
      wouldEnrol({ box: [0, 0, MIN_SEND_PX + 60, 90] }), true);

console.log();
if (fails) { console.error(fails + ' check(s) FAILED'); process.exit(1); }
console.log('all checks passed');
