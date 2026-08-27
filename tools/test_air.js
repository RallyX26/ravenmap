/* Client-side contract for the tag-routed air (sparrowsend-air.js).
 *
 * Proves the two things the whole design rests on, with NO server:
 *   1. TAG SYMMETRY + DIRECTION - the tag Alice derives to send to Bob equals
 *      the tag Bob derives to receive from Alice, and it is NOT the tag Alice
 *      would poll for her own mail (so a sender never sees its own frames).
 *   2. A full Double-Ratchet message survives being carried as a base64 air
 *      frame: encrypt -> JSON -> base64 -> (air) -> base64-decode -> decrypt.
 *
 * Run: node tools/test_air.js   (Node 18+, uses globalThis.crypto)
 */
"use strict";
const path = require("path");
const R = require(path.join(__dirname, "..", "public", "sparrowsend-ratchet.js"));
const Air = require(path.join(__dirname, "..", "public", "sparrowsend-air.js"));
const subtle = globalThis.crypto.subtle;
const enc = new TextEncoder(), dec = new TextDecoder();

let pass = 0, fail = 0;
function ok(name, cond) { (cond ? (pass++, console.log("  ok  " + name))
                                : (fail++, console.log("FAIL  " + name))); }

// Build a Sparrow identity's ECDH key material the way the app does: a JWK
// private key plus the raw public key (base64), i.e. R.genDH's output.
async function identity() {
  const g = await R.genDH();                 // { priv: JWK, pub: b64(rawPub) }
  return { priv: g.priv, pub: g.pub, dhIdentity: { priv: g.priv, pub: g.pub } };
}

(async () => {
  const alice = await identity();
  const bob = await identity();
  const carol = await identity();
  const epoch = Air.epochNow();

  // 1) SYMMETRY: Alice's send-tag to Bob == Bob's recv-tag from Alice.
  const aToB = await Air.sendTag(alice.priv, bob.pub, epoch);
  const bFromA = await Air.recvTag(bob.priv, alice.pub, bob.pub, epoch);
  ok("tag symmetry (alice.send->bob == bob.recv<-alice)", aToB === bFromA);
  ok("tag is 16 bytes hex", /^[0-9a-f]{32}$/.test(aToB));

  // DIRECTION: the reverse direction is a DIFFERENT tag (Bob->Alice), so a
  // sender polling its own recv-tag never collides with what it sent.
  const bToA = await Air.sendTag(bob.priv, alice.pub, epoch);
  const aFromB = await Air.recvTag(alice.priv, bob.pub, alice.pub, epoch);
  ok("reverse symmetry (bob.send->alice == alice.recv<-bob)", bToA === aFromB);
  ok("directions differ (A->B != B->A)", aToB !== bToA);
  ok("sender does not poll its own send-tag", aToB !== aFromB);

  // ISOLATION: Carol cannot compute Alice<->Bob's tag (she lacks the secret).
  const cGuess = await Air.sendTag(carol.priv, bob.pub, epoch);
  ok("a third party derives a different tag", cGuess !== aToB);

  // ROTATION: a different epoch yields a different tag (unlinkable over time).
  const aToBnext = await Air.sendTag(alice.priv, bob.pub, epoch + 1);
  ok("tag rotates by epoch", aToBnext !== aToB);

  // pollEpochs offers current + previous for boundary/skew coverage.
  const pe = Air.pollEpochs();
  ok("pollEpochs returns [now, now-1]", pe.length === 2 && pe[0] - pe[1] === 1);

  // 2) A real ratchet message rides an air frame intact.
  const rs = await R.initSender(alice.dhIdentity, bob.pub);
  const plaintext = "no mobile data out here, and this still reached you";
  const msg = await R.encrypt(rs, enc.encode(JSON.stringify({ r: plaintext })));
  // The air frame is just the sealed envelope, base64'd (opaque to the hub).
  const frameB64 = Buffer.from(JSON.stringify(msg), "utf8").toString("base64");
  ok("air frame is plain base64", /^[A-Za-z0-9+/]+=*$/.test(frameB64));

  // Bob receives the frame off the air under the shared tag, decodes, decrypts.
  const back = JSON.parse(Buffer.from(frameB64, "base64").toString("utf8"));
  const rr = await R.initReceiver(bob.dhIdentity, alice.pub);
  const got = JSON.parse(dec.decode(await R.decrypt(rr, back)));
  ok("message decrypts after the air round-trip", got.r === plaintext);

  // The hub sees only the tag and the base64 frame - neither reveals content
  // or identity. (Sanity: the plaintext must not appear in what crosses.)
  ok("plaintext is absent from the air frame",
     frameB64.indexOf(Buffer.from(plaintext).toString("base64").slice(0, 8)) === -1
     && !dec.decode(Buffer.from(frameB64, "base64")).includes(plaintext));

  console.log("\n%d passed, %d failed", pass, fail);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
