/* End-to-end proof of the LoRa MESH lane against the LIVE hub, no radio needed.
 *
 * Two identities that are each other's contacts. "LoRaUser" (off-grid) seals a
 * message and puts it on the shared MESH tag; "NetUser" (internet Send) polls the
 * SAME tag, routes by the envelope's `from`, and decrypts - exactly what
 * send.html's airPoll + ingest do. Then the reverse. Proves an off-grid message
 * reaches an internet Send contact and back, through the hub, sealed throughout.
 *
 * Run: node tools/test_mesh.js [https://map.sparrowmap.com]
 */
"use strict";
const path = require("path");
const R = require(path.join(__dirname, "..", "public", "sparrowsend-ratchet.js"));
const Pow = require(path.join(__dirname, "..", "public", "sparrowsend-pow.js"));
const enc = new TextEncoder(), dec = new TextDecoder();
const BASE = process.argv[2] || "https://map.sparrowmap.com";
const MESH_TAG = "46405a125e36f24591546321b6ec0ec3";   // must match send.html + hub_air_link
let pass = 0, fail = 0;
const ok = (n, c) => (c ? (pass++, console.log("  ok  " + n)) : (fail++, console.log("FAIL  " + n)));

function b64u(u8){ return Buffer.from(u8).toString("base64").replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,""); }
async function post(route, body){
  const r = await fetch(BASE + route, { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(body) });
  let j={}; try{ j = await r.json(); }catch(e){}
  return { status:r.status, j };
}
async function identity(){
  const s = await crypto.subtle.generateKey({name:"ECDSA",namedCurve:"P-256"}, true, ["sign","verify"]);
  const g = await R.genDH();
  const sRaw = new Uint8Array(await crypto.subtle.exportKey("raw", s.publicKey));
  const eRaw = R.ub64(g.pub);
  const address = b64u(new TextEncoder().encode(JSON.stringify({ s:b64u(sRaw), e:b64u(eRaw) })));
  const mailbox = [...new Uint8Array(await crypto.subtle.digest("SHA-256", sRaw))].map(b=>(b+256).toString(16).slice(1)).join("").slice(0,32);
  return { dhIdentity:{ priv:g.priv, pub:g.pub }, ePub:g.pub, address, mailbox };
}
// Put a sealed envelope on the MESH lane (what a bridged LoRa frame becomes).
async function meshPut(fromId, toId, mid, text){
  const rs = await R.initSender(fromId.dhIdentity, toId.ePub);
  const msg = await R.encrypt(rs, enc.encode(JSON.stringify({ x: text })));
  const envStr = JSON.stringify({ from: fromId.address, mid, r: msg });
  const frame = Buffer.from(envStr, "utf8").toString("base64");
  const st = await Pow.stamp(MESH_TAG, frame, Pow.POW_BITS);
  const put = await post("/api/air/put", { tag: MESH_TAG, frame, pt: st.ts, pn: st.nonce });
  return put.status === 200;
}
// Poll the MESH lane and decrypt any envelope from a known contact (ingest-style).
async function meshRecv(meId, fromId, wantMid){
  const g = await post("/api/air/get", { tag: MESH_TAG, since: 0 });
  for (const m of (g.j.m || [])){
    let env; try{ env = JSON.parse(Buffer.from(m.b, "base64").toString("utf8")); }catch(e){ continue; }
    if (!env.from || env.mid !== wantMid) continue;      // route by mid for this test
    const rr = await R.initReceiver(meId.dhIdentity, fromId.ePub);
    try{ const pt = await R.decrypt(rr, env.r); return JSON.parse(dec.decode(pt)).x; }catch(e){}
  }
  return null;
}

(async () => {
  const lora = await identity();   // off-grid phone user
  const net  = await identity();   // internet Send user
  const mid1 = b64u(crypto.getRandomValues(new Uint8Array(9)));
  const mid2 = b64u(crypto.getRandomValues(new Uint8Array(9)));

  // off-grid -> internet
  ok("LoRa user's sealed frame accepted on the mesh lane",
     await meshPut(lora, net, mid1, "reached you from off-grid over LoRa"));
  ok("internet Send user receives + decrypts it off the mesh lane",
     (await meshRecv(net, lora, mid1)) === "reached you from off-grid over LoRa");

  // internet -> off-grid (the reply the bridge carries back to the radio)
  ok("internet reply accepted on the mesh lane",
     await meshPut(net, lora, mid2, "got it - replying over the bridge"));
  ok("off-grid user receives + decrypts the reply",
     (await meshRecv(lora, net, mid2)) === "got it - replying over the bridge");

  // a stranger with the public tag sees only ciphertext (cannot read it)
  const g = await post("/api/air/get", { tag: MESH_TAG, since: 0 });
  const leaked = (g.j.m || []).some(m => Buffer.from(m.b, "base64").toString("utf8").includes("off-grid over LoRa"));
  ok("plaintext never appears on the shared lane", !leaked);

  console.log("\n%d passed, %d failed", pass, fail);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
