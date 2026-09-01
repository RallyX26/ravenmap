/* Full HTTP round-trip against a running hub's tag-routed air relay.
 *
 *   Alice: encrypt (ratchet) -> base64 frame -> sendTag -> PoW -> POST /api/air/put
 *   Bob:   recvTag -> POST /api/air/get -> decode -> decrypt -> assert
 *
 * Also checks the PoW gate (an unstamped put is refused with need_bits) and
 * that a stranger's tag returns nothing.
 *
 * Run against a local hub:  node tools/test_air_http.js http://127.0.0.1:8271
 */
"use strict";
const path = require("path");
const R = require(path.join(__dirname, "..", "public", "sparrowsend-ratchet.js"));
const Air = require(path.join(__dirname, "..", "public", "sparrowsend-air.js"));
const Pow = require(path.join(__dirname, "..", "public", "sparrowsend-pow.js"));
const enc = new TextEncoder(), dec = new TextDecoder();
const BASE = process.argv[2] || "http://127.0.0.1:8271";

let pass = 0, fail = 0;
function ok(n, c) { (c ? (pass++, console.log("  ok  " + n)) : (fail++, console.log("FAIL  " + n))); }

async function post(route, body) {
  const r = await fetch(BASE + route, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  let j = {}; try { j = await r.json(); } catch (e) {}
  return { status: r.status, j };
}
async function identity() {
  const g = await R.genDH();
  return { priv: g.priv, pub: g.pub, dhIdentity: { priv: g.priv, pub: g.pub } };
}

(async () => {
  const alice = await identity(), bob = await identity(), carol = await identity();
  const ep = Air.epochNow();

  // Alice seals a message and frames it.
  const rs = await R.initSender(alice.dhIdentity, bob.pub);
  const text = "reached you from off-grid, no tower involved";
  const msg = await R.encrypt(rs, enc.encode(JSON.stringify({ r: text })));
  const frame = Buffer.from(JSON.stringify(msg), "utf8").toString("base64");
  const tag = await Air.sendTag(alice.priv, bob.pub, ep);

  // 1) An unstamped put is refused with a difficulty to meet.
  const bad = await post("/api/air/put", { tag, frame, pt: 0, pn: 0 });
  ok("put without PoW is refused (400 + need_bits)",
     bad.status === 400 && typeof bad.j.need_bits === "number");

  // 2) A properly stamped put is accepted.
  const stamp = await Pow.stamp(tag, frame, bad.j.need_bits || Pow.POW_BITS);
  const put = await post("/api/air/put", { tag, frame, pt: stamp.ts, pn: stamp.nonce });
  ok("stamped put accepted (200 + seq)", put.status === 200 && put.j.ok === true && put.j.seq === 0);

  // 3) Bob polls his recv-tag and gets the frame back, byte-identical.
  const rtag = await Air.recvTag(bob.priv, alice.pub, bob.pub, ep);
  ok("bob's recv-tag equals alice's send-tag", rtag === tag);
  const got = await post("/api/air/get", { tag: rtag, since: 0 });
  ok("get returns exactly one frame", got.status === 200 && got.j.m.length === 1);
  ok("frame survived the hub byte-for-byte", got.j.m[0].b === frame);

  // 4) It decrypts to the original.
  const back = JSON.parse(Buffer.from(got.j.m[0].b, "base64").toString("utf8"));
  const rr = await R.initReceiver(bob.dhIdentity, alice.pub);
  const out = JSON.parse(dec.decode(await R.decrypt(rr, back)));
  ok("bob decrypts the original plaintext", out.r === text);

  // 5) A stranger's tag yields nothing (routing really is by the shared secret).
  const ctag = await Air.recvTag(carol.priv, alice.pub, carol.pub, ep);
  const none = await post("/api/air/get", { tag: ctag, since: 0 });
  ok("a third party's tag returns no frames", none.status === 200 && none.j.m.length === 0);

  // 6) The cursor advances: re-polling from head returns nothing new.
  const again = await post("/api/air/get", { tag: rtag, since: got.j.seq });
  ok("cursor from head returns nothing new", again.j.m.length === 0);

  // 7) BATCH get: a client polls many recv-tags in one request. Send Alice->Bob
  //    on a fresh epoch and confirm the batch returns it keyed by tag, and that
  //    an unrelated tag in the same batch simply returns nothing.
  const ep2 = Air.epochNow() + 7;                    // an epoch nothing else used
  const t2 = await Air.sendTag(alice.priv, bob.pub, ep2);
  const frame2 = Buffer.from(JSON.stringify(
    await R.encrypt(await R.initSender(alice.dhIdentity, bob.pub),
                    enc.encode(JSON.stringify({ r: "batch hello" })))), "utf8").toString("base64");
  const st2 = await Pow.stamp(t2, frame2, Pow.POW_BITS);
  await post("/api/air/put", { tag: t2, frame: frame2, pt: st2.ts, pn: st2.nonce });
  const rt2 = await Air.recvTag(bob.priv, alice.pub, bob.pub, ep2);
  const empty = await Air.recvTag(carol.priv, alice.pub, carol.pub, ep2);
  const batch = await post("/api/air/get", { tags: [{ tag: rt2, since: 0 }, { tag: empty, since: 0 }] });
  ok("batch returns the real tag's frame", batch.j.results[rt2] && batch.j.results[rt2].m.length === 1);
  ok("batch omits an empty tag", !batch.j.results[empty]);
  ok("batch frame is byte-identical", batch.j.results[rt2].m[0].b === frame2);

  console.log("\n%d passed, %d failed", pass, fail);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
