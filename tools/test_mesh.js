/* End-to-end proof of the LoRa MESH lane in its REAL (binary) format, live hub.
 *
 * A "board" peer (off-grid, LoRa-style) seals a compact binary frame and puts it
 * on the shared MESH tag; an "internet" peer pulls the SAME tag, trial-decrypts
 * the frame against its contacts (frameToMsg + ratchet, on a clone), and reads
 * the text - exactly what send.html's meshRecvFrame does. Then the reverse, plus
 * a read-receipt (K_ACK) and a stranger frame that must NOT match anyone.
 *
 * Run: node tools/test_mesh.js [https://map.sparrowmap.com]
 */
"use strict";
const path = require("path");
const R = require(path.join(__dirname, "..", "public", "sparrowsend-ratchet.js"));
const L = require(path.join(__dirname, "..", "public", "sparrowsend-lora.js"));
const Pow = require(path.join(__dirname, "..", "public", "sparrowsend-pow.js"));
const enc = new TextEncoder(), dec = new TextDecoder();
const BASE = process.argv[2] || "https://map.sparrowmap.com";
const MESH_TAG = "46405a125e36f24591546321b6ec0ec3";
let pass = 0, fail = 0;
const ok = (n, c) => (c ? (pass++, console.log("  ok  " + n)) : (fail++, console.log("FAIL  " + n)));

async function post(route, body){
  const r = await fetch(BASE + route, { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(body) });
  let j={}; try{ j = await r.json(); }catch(e){}
  return { status:r.status, j };
}
async function id(){ const g = await R.genDH(); return { dh:{priv:g.priv,pub:g.pub}, pub:g.pub }; }

// board-style: seal a plaintext into a binary frame and put it on the mesh.
async function meshPut(state, kind, mid, body){
  const msg = await R.encrypt(state, L.packPlain(kind, mid, body));
  const frame = L.b64(L.msgToFrame(msg));
  const st = await Pow.stamp(MESH_TAG, frame, Pow.POW_BITS);
  return (await post("/api/air/put", { tag: MESH_TAG, frame, pt: st.ts, pn: st.nonce })).status === 200;
}
// internet-style: pull the mesh, trial-decrypt each frame against [contactState].
async function meshRecv(states){
  const g = await post("/api/air/get", { tag: MESH_TAG, since: 0 });
  const out = [];
  for (const m of (g.j.m || [])){
    let bytes; try{ bytes = L.ub64(m.b); }catch(e){ continue; }
    const msg = L.frameToMsg(bytes); if(!msg) continue;
    for (const s of states){
      const clone = JSON.parse(JSON.stringify(s.st));
      try{ const pt = await R.decrypt(clone, msg); s.st = clone;
        const p = L.parsePlain(pt); out.push({ who:s.who, kind:p.kind, mid:L.midHex(p.mid), text: dec.decode(p.body) }); break; }catch(e){}
    }
  }
  return out;
}

(async () => {
  const board = await id(), net = await id(), stranger = await id();
  // established session (they are contacts): board<->net
  const bToNet = await R.initSender(board.dh, net.pub);
  const netFromB = { who:"net", st: await R.initReceiver(net.dh, board.pub) };
  const netToB = await R.initSender(net.dh, board.pub);
  const bFromNet = { who:"board", st: await R.initReceiver(board.dh, net.pub) };

  const m1 = new Uint8Array([9,9,0,1]);
  ok("board seals a text frame onto the mesh", await meshPut(bToNet, L.K_TEXT, m1, enc.encode("off-grid, but I reached you")));

  const got = await meshRecv([ netFromB, { who:"stranger", st: await R.initReceiver(stranger.dh, board.pub) } ]);
  const t1 = got.find(x => x.who==="net" && x.mid===L.midHex(m1));
  ok("internet peer trial-decrypts it (and the stranger does NOT)",
     !!t1 && t1.text==="off-grid, but I reached you" && !got.some(x=>x.who==="stranger"));

  // reverse: internet replies over the mesh; board reads it
  const m2 = new Uint8Array([9,9,0,2]);
  ok("internet reply sealed onto the mesh", await meshPut(netToB, L.K_TEXT, m2, enc.encode("got you, replying over the bridge")));
  const back = await meshRecv([ bFromNet ]);
  const t2 = back.find(x => x.mid===L.midHex(m2));
  ok("board reads the reply", !!t2 && t2.text==="got you, replying over the bridge");

  // a read receipt (K_ACK) carries the acked mid
  const m3 = new Uint8Array([9,9,0,3]);
  const ackBody = new Uint8Array(5); ackBody.set(m1,0); ackBody[4]=0;
  ok("an ACK frame goes on the mesh", await meshPut(netToB, L.K_ACK, m3, ackBody));
  const acks = await meshRecv([ bFromNet ]);
  const a1 = acks.find(x => x.mid===L.midHex(m3));
  ok("ACK is delivered as a K_ACK", !!a1 && a1.kind===L.K_ACK);

  // no plaintext ever appears on the shared lane
  const g = await post("/api/air/get", { tag: MESH_TAG, since: 0 });
  ok("plaintext never on the shared lane",
     !(g.j.m||[]).some(m => { try{ return dec.decode(L.ub64(m.b)).includes("reached you"); }catch(e){ return false; } }));

  console.log("\n%d passed, %d failed", pass, fail);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
