/* Prove the LoRa page's login-key import: an internet Sparrow Send account
 * (identity + a contact + a live ratchet) exported as an SS1 key, then imported
 * with the exact transform useLoginKey() does, yields a working session - a
 * message sealed with the IMPORTED ratchet decrypts on the contact's side.
 *
 * Run: node tools/test_loginkey.js
 */
"use strict";
const path = require("path");
const R = require(path.join(__dirname, "..", "public", "sparrowsend-ratchet.js"));
const L = require(path.join(__dirname, "..", "public", "sparrowsend-lora.js"));
const subtle = globalThis.crypto.subtle, enc = new TextEncoder(), dec = new TextDecoder();
let pass = 0, fail = 0;
const ok = (n, c) => (c ? (pass++, console.log("  ok  " + n)) : (fail++, console.log("FAIL  " + n)));

function b64u(u8){ return Buffer.from(u8).toString("base64").replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,""); }
function ub64u(s){ s=s.replace(/-/g,"+").replace(/_/g,"/"); while(s.length%4) s+="="; return new Uint8Array(Buffer.from(s,"base64")); }
function b64std(u8){ return Buffer.from(u8).toString("base64"); }
async function deriveKek(passwd, salt, iters, usage){
  const base = await subtle.importKey("raw", enc.encode(passwd), "PBKDF2", false, ["deriveKey"]);
  return subtle.deriveKey({name:"PBKDF2",salt,iterations:iters,hash:"SHA-256"}, base, {name:"AES-GCM",length:256}, false, usage);
}

(async () => {
  // --- build an internet account: identity (ECDSA+ECDH) + contact Alice ---
  const sign = await subtle.generateKey({name:"ECDSA",namedCurve:"P-256"}, true, ["sign","verify"]);
  const dh = await R.genDH();
  const idBundle = {
    sPriv: await subtle.exportKey("jwk", sign.privateKey), sPub: await subtle.exportKey("jwk", sign.publicKey),
    ePriv: dh.priv, ePub: await subtle.importKey("raw", R.ub64(dh.pub), {name:"ECDH",namedCurve:"P-256"}, true, [])
             .then(k => subtle.exportKey("jwk", k))
  };
  const alice = await R.genDH();
  const aliceSignPub = await subtle.exportKey("raw", (await subtle.generateKey({name:"ECDSA",namedCurve:"P-256"}, true, ["sign","verify"])).publicKey);
  const aliceMb = [...new Uint8Array(await subtle.digest("SHA-256", aliceSignPub))].map(b=>(b+256).toString(16).slice(1)).join("").slice(0,32);
  const aliceERaw = R.ub64(alice.pub);
  const contacts = [{ name:"Alice", mb:aliceMb, sRaw:b64u(aliceSignPub), eRaw:b64u(aliceERaw), verified:true }];

  // his live ratchet WITH Alice (as the internet app would have it), keyed by mb
  const hisRat = await R.initSender({priv:dh.priv,pub:dh.pub}, alice.pub);
  const ratMap = { ["sparrow.send.rat."+aliceMb]: JSON.stringify(hisRat) };

  // --- export it as an SS1 login key (what send.html does) ---
  const payload = JSON.stringify({ id: JSON.stringify(idBundle), contacts: JSON.stringify(contacts), rat: ratMap, seen:{} });
  const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
  const kekE = await deriveKek("hunter2", salt, 250000, ["encrypt"]);
  const ct = new Uint8Array(await subtle.encrypt({name:"AES-GCM",iv}, kekE, enc.encode(payload)));
  const key = "SS1." + b64u(enc.encode(JSON.stringify({ v:1, it:250000, s:b64u(salt), i:b64u(iv), c:b64u(ct) })));

  // --- import it the way useLoginKey() does, into a fake sendlora store ---
  const STORE = {};
  async function useLoginKey(keyStr, passwd){
    let s = keyStr.trim(); if (s.indexOf("SS1.")===0) s = s.slice(4);
    const blob = JSON.parse(dec.decode(ub64u(s)));
    const kek = await deriveKek(passwd, ub64u(blob.s), blob.it, ["decrypt"]);
    const pt = await subtle.decrypt({name:"AES-GCM",iv:ub64u(blob.i)}, kek, ub64u(blob.c));
    const pl = JSON.parse(dec.decode(pt)), idb = JSON.parse(pl.id);
    const epub = await subtle.importKey("jwk", idb.ePub, {name:"ECDH",namedCurve:"P-256"}, true, []);
    const eraw = new Uint8Array(await subtle.exportKey("raw", epub));
    const newID = { priv: idb.ePriv, pub: b64std(eraw) };
    const cs = JSON.parse(pl.contacts||"[]"), CTS = [];
    for (const c of cs){
      const pub = b64std(ub64u(c.eRaw));
      CTS.push({ pub, name:c.name, verified:!!c.verified });
      const rk = "sparrow.send.rat."+c.mb;
      if (pl.rat[rk]) STORE["sprwlora.rat."+pub.slice(0,22)] = pl.rat[rk];
    }
    STORE["sprwlora.id"] = newID; STORE["sprwlora.ct"] = CTS;
    return CTS.length;
  }

  ok("wrong password is rejected", await useLoginKey(key, "nope").then(()=>false).catch(()=>true));
  const n = await useLoginKey(key, "hunter2");
  ok("imported the contact", n === 1 && STORE["sprwlora.ct"][0].name === "Alice");
  ok("identity carried over (same ECDH pub)", STORE["sprwlora.id"].pub === b64std(R.ub64(dh.pub)));
  const cpub = STORE["sprwlora.ct"][0].pub;
  ok("contact pub is Alice's ECDH key", cpub === b64std(aliceERaw));
  ok("Alice's ratchet copied under the pub key", !!STORE["sprwlora.rat."+cpub.slice(0,22)]);

  // --- the imported session actually works: LoRa page seals -> Alice decrypts ---
  const importedRat = JSON.parse(STORE["sprwlora.rat."+cpub.slice(0,22)]);
  const mid = new Uint8Array([7,7,7,1]);
  const msg = await R.encrypt(importedRat, L.packPlain(L.K_TEXT, mid, enc.encode("off-grid, same account")));
  const frame = L.msgToFrame(msg);
  const aliceSide = await R.initReceiver({priv:alice.priv,pub:alice.pub}, dh.pub);   // Alice's ratchet with him
  const got = L.parsePlain(await R.decrypt(aliceSide, L.frameToMsg(frame)));
  ok("Alice decrypts a message from the imported LoRa account", dec.decode(got.body) === "off-grid, same account");

  console.log("\n%d passed, %d failed", pass, fail);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
