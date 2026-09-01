/* @claudebot on the LoRa MESH — the SAME identity as the internet bot, so people
 * keep ONE contact for Claude whether they reach it over the web or off-grid.
 *
 * It reads the local air gateway (127.0.0.1:8300, fed by the LoRa bridge),
 * trial-decrypts the compact binary frames with @claudebot's keys, replies with
 * a delivery receipt (and can send text), and puts frames back on the gateway to
 * ride the radio out to the phone. The mesh session is kept in its OWN ratchet
 * file so it never fights the mailbox bot's ratchet - one identity, two
 * independent secure sessions (which is already how it works: the phone's
 * off-grid page and its web app are different devices/origins anyway).
 *
 *   node claude_mesh.js poll [--watch]     receive + auto-ack
 *   node claude_mesh.js send "text"        reply to the last contact heard
 *   node claude_mesh.js reset              wipe the mesh session (fresh start)
 */
"use strict";
const path = require("path");
const fs = require("fs");
const R = require(path.join(__dirname, "..", "public", "sparrowsend-ratchet.js"));
const L = require(path.join(__dirname, "..", "public", "sparrowsend-lora.js"));
const subtle = globalThis.crypto.subtle, enc = new TextEncoder(), dec = new TextDecoder();

const STATE = path.join(__dirname, ".sparrow_claude_state.json");     // @claudebot identity + contacts
const MESH = path.join(__dirname, ".claude_mesh_rat.json");           // mesh-only ratchets + last-heard
const GATEWAY = process.env.SPARROW_GATEWAY || "http://127.0.0.1:8300";
const ARGS = process.argv.slice(2);

function b64u(u8){ return Buffer.from(u8).toString("base64").replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,""); }
function ub64u(s){ s=String(s).replace(/-/g,"+").replace(/_/g,"/"); while(s.length%4) s+="="; return new Uint8Array(Buffer.from(s,"base64")); }
function hex(u8){ let s=""; for(const b of u8) s+=(b+256).toString(16).slice(1); return s; }
async function sha256(u8){ return new Uint8Array(await subtle.digest("SHA-256", u8)); }
const sleep = ms => new Promise(r=>setTimeout(r,ms));

async function importID(j){ return {
  sign:{ privateKey: await subtle.importKey("jwk",j.sPriv,{name:"ECDSA",namedCurve:"P-256"},true,["sign"]) },
  dh:{   privateKey: await subtle.importKey("jwk",j.ePriv,{name:"ECDH",namedCurve:"P-256"},true,["deriveBits"]),
         publicKey:  await subtle.importKey("jwk",j.ePub,{name:"ECDH",namedCurve:"P-256"},true,[]) } }; }
async function loadID(st){
  if(!st.id) throw new Error("no @claudebot identity");
  const k = await importID(st.id);
  const eraw = new Uint8Array(await subtle.exportKey("raw", k.dh.publicKey));
  return { dhIdentity: { priv: st.id.ePriv, pub: R.b64(eraw) } };
}
function loadState(){ return JSON.parse(fs.readFileSync(STATE,"utf8")); }
function loadMesh(){ try{ return JSON.parse(fs.readFileSync(MESH,"utf8")); }catch(e){ return {rat:{}, last:null}; } }
// write-then-rename so a half-written mesh file can never corrupt the ratchets;
// EPERM/EBUSY-safe (Windows AV/indexer/the other process can hold the swap open).
function saveMesh(m){
  const tmp = MESH + ".tmp";
  try{ fs.writeFileSync(tmp, JSON.stringify(m)); }catch(e){ return; }
  for(let i=0;;i++){
    try{ fs.renameSync(tmp, MESH); return; }
    catch(e){
      if((e.code==="EPERM"||e.code==="EBUSY"||e.code==="EACCES") && i<25){ sleepSync(40); continue; }
      try{ fs.writeFileSync(MESH, JSON.stringify(m)); try{ fs.unlinkSync(tmp); }catch(_){} return; }catch(_){ return; }
    }
  }
}
// 🚨 TWO WRITERS: the always-on `poll --watch` loop and a one-off `send` are
// separate processes sharing the mesh ratchet. Without a lock a send's ratchet
// advance is silently clobbered by the watch loop's next save, and the channel
// breaks (same bug already fixed in claude_lora.js). Serialise every
// read-modify-write with a lock file; a lock older than 15s is stale and broken.
const LOCK = MESH + ".lock";
function sleepSync(ms){ try{ Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); }catch(e){ const t=Date.now(); while(Date.now()-t<ms){} } }
function lock(){ for(;;){ try{ fs.writeFileSync(LOCK, String(process.pid), {flag:"wx"}); return; }
  catch(e){ try{ if(Date.now()-fs.statSync(LOCK).mtimeMs > 15000) fs.unlinkSync(LOCK); }catch(_){} sleepSync(50); } } }
function unlock(){ try{ fs.unlinkSync(LOCK); }catch(e){} }

async function gwGet(cur){ return (await (await fetch(GATEWAY+"/air?since="+cur, {headers:{"X-Air-From":"wire"}})).json()); }
async function gwPut(frameB64){ await fetch(GATEWAY+"/air", {method:"POST", headers:{"X-Air-From":"wire"}, body:frameB64}); }

// Seal a plaintext (kind/mid/body) to a contact's mesh ratchet and put it on the air.
async function sealSend(ID, mesh, ct, kind, mid, body){
  let rs = mesh.rat[ct.mb];
  if(!rs) rs = await R.initSender(ID.dhIdentity, R.b64(ub64u(ct.eRaw)));
  const msg = await R.encrypt(rs, L.packPlain(kind, mid, body));
  mesh.rat[ct.mb] = rs;
  await gwPut(L.b64(L.msgToFrame(msg)));
}

// Commit an adopted/advanced ratchet and act on one decrypted mesh plaintext.
async function handleMeshPlain(ID, mesh, ct, clone, pt){
  mesh.rat[ct.mb] = clone;
  const p = L.parsePlain(pt);
  if(p.kind === L.K_REKEY){ saveMesh(mesh); return; }   // session healed; nothing to display or ack
  if(p.kind === L.K_ACK){ saveMesh(mesh); return; }
  if(p.kind === L.K_TEXT){
    const text = dec.decode(p.body);
    mesh.last = ct.mb;
    console.log("MESH " + ct.mb.slice(0,6) + ": " + JSON.stringify(text));
    // delivery receipt so the phone shows the second tick
    const ack = new Uint8Array(5); ack.set(p.mid,0); ack[4]=0;
    try{ await sealSend(ID, mesh, ct, L.K_ACK, globalThis.crypto.getRandomValues(new Uint8Array(4)), ack); }catch(e){}
  }
  saveMesh(mesh);
}

async function receiveLoop(watch){
  const st = loadState();
  const ID = await loadID(st);
  const contacts = st.contacts || [];
  let cur = 0;
  console.log("claude_mesh: @claudebot on the LoRa mesh via " + GATEWAY + " (" + contacts.length + " contact(s))");
  do {
    try {
      const g = await gwGet(cur);
      if((g.seq||0) < cur) cur = 0;                       // gateway restarted
      const frames = g.m || [];
      if(!frames.length){ if(watch) await sleep(2000); continue; }
      lock();
      let mesh = loadMesh();                              // fresh under the lock: a `send` between polls advanced it
      try {
      for(const m of frames){
        cur = m.s + 1;
        let bytes; try{ bytes = L.ub64(m.b); }catch(e){ continue; }
        const msg = L.frameToMsg(bytes); if(!msg) continue;   // not a MSG frame
        // Two passes. First the live session for each contact; then, if none read
        // it, a fresh initReceiver per contact - a peer whose other device forked
        // the ratchet will re-key, and only that contact can produce a frame a
        // fresh receiver decrypts (the AEAD tag authenticates the identity-derived
        // root), so ADOPTing it is safe even on this broadcast lane. This is the
        // receive half of auto-rekey; @claudebot only ADOPTs, never initiates.
        // A contact is ONE identity on SEVERAL devices (pager, board phone page,
        // web) and each device forks the ratchet. One slot per contact meant the
        // last device heard LOCKED OUT the others - a phone-page message turned
        // to gibberish minutes after a pager message. So: a PRIMARY session (the
        // device heard most recently - replies go to it) plus a STABLE of up to
        // 3 other live forks, all tried on receive. Whichever fork decrypts gets
        // promoted to primary.
        let handled = false;
        for(const ct of contacts){
          if(!ct.eRaw) continue;
          const stb = (mesh.stable && mesh.stable[ct.mb]) || [];
          const cands = [];
          if(mesh.rat[ct.mb]) cands.push({rs: mesh.rat[ct.mb], stbIdx: -1});
          stb.forEach((rs,i)=>cands.push({rs, stbIdx: i}));
          for(const c of cands){
            const clone = JSON.parse(JSON.stringify(c.rs));
            let pt; try{ pt = await R.decrypt(clone, msg); }catch(e){ continue; }   // not this fork
            if(c.stbIdx >= 0){                        // a stable fork spoke: promote it
              stb.splice(c.stbIdx, 1);
              if(mesh.rat[ct.mb]) stb.unshift(mesh.rat[ct.mb]);
              mesh.stable = mesh.stable || {}; mesh.stable[ct.mb] = stb.slice(0,3);
              console.log("MESH " + ct.mb.slice(0,6) + ": another device spoke - promoting its session");
            }
            await handleMeshPlain(ID, mesh, ct, clone, pt); handled = true; break;
          }
          if(handled) break;
        }
        if(handled) continue;
        for(const ct of contacts){
          if(!ct.eRaw) continue;
          let fresh; try{ fresh = await R.initReceiver(ID.dhIdentity, R.b64(ub64u(ct.eRaw))); }catch(e){ continue; }
          const clone = JSON.parse(JSON.stringify(fresh));
          let pt; try{ pt = await R.decrypt(clone, msg); }catch(e){ continue; }   // not a fresh session from them
          console.log("MESH " + ct.mb.slice(0,6) + ": re-keyed, adopting fresh session");
          // The outgoing primary is another device's still-live fork: keep it in
          // the stable rather than orphaning it.
          if(mesh.rat[ct.mb]){ mesh.stable = mesh.stable || {};
            const s2 = mesh.stable[ct.mb] || []; s2.unshift(mesh.rat[ct.mb]); mesh.stable[ct.mb] = s2.slice(0,3); }
          await handleMeshPlain(ID, mesh, ct, clone, pt); break;
        }
      }
      } finally { unlock(); }
    } catch(e){ /* gateway down / transient */ }
    if(watch) await sleep(2000);
  } while(watch);
}

async function main(){
  const cmd = ARGS[0] || "poll";
  if(cmd === "reset"){ saveMesh({rat:{}, last:null}); console.log("mesh session wiped - fresh start"); return; }
  if(cmd === "send"){
    const text = ARGS.slice(1).join(" ");
    if(!text){ console.log('usage: send "text"'); return; }
    const st = loadState(); const ID = await loadID(st);
    lock();                                    // serialise with the --watch loop's ratchet writes
    try {
      const mesh = loadMesh();                 // fresh under the lock
      const ct = (st.contacts||[]).find(c => c.mb === mesh.last) || (st.contacts||[])[0];
      if(!ct){ console.log("no contact to reply to"); return; }
      // A sealed frame is 73B header + 5B plain-overhead + text + 16B tag against a
      // 250B radio cap, so text over ~156 chars OVERFLOWS and dies silently - it
      // happened twice in one night. Split long texts at word boundaries into
      // frames of <=140 chars each, sent in order on the same chain.
      const parts = [];
      let rest = text;
      while(rest.length > 140){
        let cut = rest.lastIndexOf(" ", 140); if(cut < 100) cut = 140;
        parts.push(rest.slice(0, cut)); rest = rest.slice(cut).replace(/^ /, "");
      }
      parts.push(rest);
      for(const p of parts)
        await sealSend(ID, mesh, ct, L.K_TEXT, globalThis.crypto.getRandomValues(new Uint8Array(4)), enc.encode(p));
      saveMesh(mesh);
      console.log("sent to " + ct.mb.slice(0,6) + " over the mesh" + (parts.length>1 ? " ("+parts.length+" frames)" : ""));
    } finally { unlock(); }
    return;
  }
  await receiveLoop(ARGS.includes("--watch"));
}
main().catch(e => { console.error(e); process.exit(1); });
