/* Auto-rekey verification: simulate the exact fork Matthew hits - one identity,
 * two devices (web app + off-grid page) whose ratchets diverge - and prove the
 * session SELF-HEALS with no "Reset secure session" tap. Pure ratchet + LoRa wire,
 * mirroring what sendlora.html / send.html / claude_mesh.js do on receive.
 *
 *   node test_rekey.js
 */
"use strict";
const path = require("path");
const R = require(path.join(__dirname, "..", "public", "sparrowsend-ratchet.js"));
const L = require(path.join(__dirname, "..", "public", "sparrowsend-lora.js"));
const enc = new TextEncoder(), dec = new TextDecoder();
let pass = 0, fail = 0;
function ok(c, m){ if(c){ pass++; console.log("  ok  " + m); } else { fail++; console.log("  FAIL " + m); } }
const clone = s => JSON.parse(JSON.stringify(s));

// Encrypt a (kind, body) as a ratchet message that has crossed the LoRa wire and back.
async function seal(st, kind, bodyBytes){
  const mid = new Uint8Array([1,2,3,4]);
  const msg = await R.encrypt(st, L.packPlain(kind, mid, bodyBytes || new Uint8Array(0)));
  return L.frameToMsg(L.ub64(L.b64(L.msgToFrame(msg))));   // round-trip the bytes
}
// Try to decrypt on a CLONE (never mutate the live session on a miss). Returns
// {plain} or null.
async function trial(st, wireMsg){
  const c = clone(st);
  try{ const pt = await R.decrypt(c, wireMsg); return { plain: L.parsePlain(pt), st: c }; }
  catch(e){ return null; }
}

async function establish(idA, idB){
  // A sends first (initSender), B receives first (initReceiver), then B replies.
  let a = await R.initSender(idA, idB.pub);
  let b = await R.initReceiver(idB, idA.pub);
  const m1 = await seal(a, L.K_TEXT, enc.encode("hi"));
  const r1 = await trial(b, m1); b = r1.st;
  const m2 = await seal(b, L.K_TEXT, enc.encode("yo"));
  const r2 = await trial(a, m2); a = r2.st;
  return { a, b, okEstablish: !!(r1 && r2 && dec.decode(r1.plain.body)==="hi" && dec.decode(r2.plain.body)==="yo") };
}

async function main(){
  console.log("auto-rekey / self-heal");
  const idA = await R.genDH();   // "Claude" side (stable device)
  const idB = await R.genDH();   // Matthew: ONE identity, two devices below

  // --- 1. baseline session ---
  let { a, b, okEstablish } = await establish(idA, idB);
  ok(okEstablish, "baseline session establishes and talks both ways");

  // --- 2. FORK: b2 is Matthew's off-grid page, a stale snapshot of b ---
  let b2 = clone(b);
  //     web device (b) sends one more; A advances. b2 is now behind/forked.
  const mWeb = await seal(b, L.K_TEXT, enc.encode("from web"));
  const rWeb = await trial(a, mWeb); a = rWeb.st;
  ok(rWeb && dec.decode(rWeb.plain.body)==="from web", "web device still in sync with A");

  // --- 3. stale off-grid device sends: A can neither decrypt nor ADOPT it ---
  const mOff = await seal(b2, L.K_TEXT, enc.encode("from off-grid"));
  const liveMiss = await trial(a, mOff);
  ok(!liveMiss, "A's live session CANNOT read the forked off-grid message");
  const adoptMiss = await trial(await R.initReceiver(idA, idB.pub), mOff);
  ok(!adoptMiss, "a fresh initReceiver also CANNOT read it (it is forked, not a fresh session) -> A must INITIATE");

  // --- 4. INITIATE (addressed-lane behaviour): A starts a fresh session + REKEY marker ---
  let aFresh = await R.initSender(idA, idB.pub);
  const mRekey = await seal(aFresh, L.K_REKEY, new Uint8Array(0));

  // --- 5. off-grid device ADOPTs: live miss, fresh initReceiver reads the marker ---
  ok(!(await trial(b2, mRekey)), "off-grid live session cannot read the REKEY (as expected)");
  const adopt = await trial(await R.initReceiver(idB, idA.pub), mRekey);
  ok(adopt && adopt.plain.kind === L.K_REKEY, "off-grid ADOPTs the fresh session and reads the REKEY marker");
  let b2Healed = adopt.st;

  // --- 6. off-grid RESENDS its message on the healed session; A now reads it ---
  const mResend = await seal(b2Healed, L.K_TEXT, enc.encode("from off-grid"));
  const got = await trial(aFresh, mResend); aFresh = got && got.st;
  ok(got && dec.decode(got.plain.body)==="from off-grid", "after heal, the message that triggered it arrives");

  // --- 7. the session keeps working both ways afterwards ---
  const mAfter = await seal(aFresh, L.K_TEXT, enc.encode("still good"));
  const gotAfter = await trial(b2Healed, mAfter);
  ok(gotAfter && dec.decode(gotAfter.plain.body)==="still good", "healed session keeps working both directions");

  // --- 8. pure ADOPT path: A re-keys and sends real text; B adopts with no marker ---
  let aRK = await R.initSender(idA, idB.pub);
  const mDirect = await seal(aRK, L.K_TEXT, enc.encode("rekeyed hello"));
  const bAdopt = await trial(await R.initReceiver(idB, idA.pub), mDirect);
  ok(bAdopt && dec.decode(bAdopt.plain.body)==="rekeyed hello", "ADOPT: a fresh session's first real message decrypts via fresh initReceiver");

  // --- 9. SAFETY: a stranger cannot trigger an adoption for contact B ---
  const idS = await R.genDH();
  let s = await R.initSender(idS, idA.pub);
  const mStranger = await seal(s, L.K_TEXT, enc.encode("intruder"));
  const strangerAdopt = await trial(await R.initReceiver(idA, idB.pub), mStranger);
  ok(!strangerAdopt, "SAFETY: a stranger's frame is NOT adopted as contact B (identity-authenticated)");

  console.log("\n" + pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
}
main().catch(e => { console.error(e); process.exit(1); });
