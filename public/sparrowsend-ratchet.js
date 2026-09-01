/* Sparrow Send — the Double Ratchet, the same algorithm Signal uses for forward
 * secrecy and break-in recovery.
 *
 * Two ratchets turning together:
 *   - a DH ratchet: every time the peer sends a new ratchet public key, both
 *     sides do a fresh ECDH and reseed the root key, so a key stolen today
 *     cannot read tomorrow's messages (break-in recovery / future secrecy);
 *   - a symmetric KDF-chain ratchet: every message advances a chain and derives
 *     a one-time message key that is deleted after use, so a key stolen today
 *     cannot read yesterday's messages (forward secrecy).
 *
 * Built on the Web Crypto API only (P-256 ECDH, HKDF-SHA256, HMAC-SHA256,
 * AES-256-GCM) — no external library, works in the browser and in Node. State
 * is fully serialisable so a conversation survives a reload.
 *
 * ⚠️ Honest scope vs Signal: this uses the same Double Ratchet, but the initial
 * shared secret is X3DH-lite (a DH of the two long-term identity keys, no
 * one-time prekeys), so the very FIRST message's forward secrecy rests on the
 * identity key. Every message after the first has full ratchet forward secrecy.
 * No post-quantum step yet. The relay still sees metadata (see send_relay.py).
 */
(function (root) {
  "use strict";
  var subtle = (globalThis.crypto && globalThis.crypto.subtle);
  var getRandom = function (n) { return globalThis.crypto.getRandomValues(new Uint8Array(n)); };
  var te = new TextEncoder();

  // ---- bytes <-> base64 (standard, for state storage) ----
  function b64(bytes) {
    var b = new Uint8Array(bytes), s = "";
    for (var i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
    return btoaFn(s);
  }
  function ub64(str) {
    var s = atobFn(str), b = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) b[i] = s.charCodeAt(i);
    return b;
  }
  var btoaFn = (typeof btoa !== "undefined") ? btoa
    : function (s) { return Buffer.from(s, "binary").toString("base64"); };
  var atobFn = (typeof atob !== "undefined") ? atob
    : function (s) { return Buffer.from(s, "base64").toString("binary"); };

  var MAX_SKIP = 1000;                 // bound out-of-order key derivation (DoS guard)
  var INFO_RK = te.encode("SparrowRatchet");
  var INFO_MSG = te.encode("SparrowMsg");
  var ZERO32 = new Uint8Array(32);

  // ---- primitives ----
  async function genDH() {
    var kp = await subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
    var priv = await subtle.exportKey("jwk", kp.privateKey);
    var pub = new Uint8Array(await subtle.exportKey("raw", kp.publicKey));
    return { priv: priv, pub: b64(pub) };
  }
  async function dh(privJwk, pubRawB64) {
    var priv = await subtle.importKey("jwk", privJwk, { name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
    var pub = await subtle.importKey("raw", ub64(pubRawB64), { name: "ECDH", namedCurve: "P-256" }, false, []);
    return new Uint8Array(await subtle.deriveBits({ name: "ECDH", public: pub }, priv, 256));
  }
  async function hkdf(ikm, salt, info, len) {
    var k = await subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]);
    return new Uint8Array(await subtle.deriveBits(
      { name: "HKDF", hash: "SHA-256", salt: salt, info: info }, k, len * 8));
  }
  async function kdfRK(rk, dhOut) {
    var out = await hkdf(dhOut, rk, INFO_RK, 64);
    return [out.slice(0, 32), out.slice(32, 64)];   // [RK', CK]
  }
  async function hmac(keyBytes, dataByte) {
    var k = await subtle.importKey("raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    return new Uint8Array(await subtle.sign("HMAC", k, new Uint8Array([dataByte])));
  }
  async function kdfCK(ck) {
    var mk = await hmac(ck, 0x01);
    var ckNext = await hmac(ck, 0x02);
    return [ckNext, mk];                             // [CK', MK]
  }
  async function msgKeyIv(mk) {
    var b = await hkdf(mk, ZERO32, INFO_MSG, 44);
    return { key: b.slice(0, 32), iv: b.slice(32, 44) };
  }
  async function aeadEnc(mk, plaintextBytes, adBytes) {
    var ki = await msgKeyIv(mk);
    var key = await subtle.importKey("raw", ki.key, "AES-GCM", false, ["encrypt"]);
    var ct = await subtle.encrypt({ name: "AES-GCM", iv: ki.iv, additionalData: adBytes }, key, plaintextBytes);
    return b64(new Uint8Array(ct));
  }
  async function aeadDec(mk, ctB64, adBytes) {
    var ki = await msgKeyIv(mk);
    var key = await subtle.importKey("raw", ki.key, "AES-GCM", false, ["decrypt"]);
    var pt = await subtle.decrypt({ name: "AES-GCM", iv: ki.iv, additionalData: adBytes }, key, ub64(ctB64));
    return new Uint8Array(pt);
  }
  function adOf(header) {
    return te.encode(JSON.stringify({ dh: header.dh, pn: header.pn, n: header.n }));
  }

  // ---- session init ----
  // SK = a DH of the two long-term identity keys (X3DH-lite). Symmetric, so both
  // sides compute the same SK.
  async function rootFromIdentities(myIdPriv, theirIdPubB64) {
    var d = await dh(myIdPriv, theirIdPubB64);
    return await hkdf(d, ZERO32, te.encode("SparrowSK"), 32);
  }
  // The party that SENDS first. Uses the peer's identity key as the initial DHr.
  async function initSender(myIdentity, theirIdPubB64) {
    var RK = await rootFromIdentities(myIdentity.priv, theirIdPubB64);
    var DHs = await genDH();
    var pair = await kdfRK(RK, await dh(DHs.priv, theirIdPubB64));
    return { RK: b64(pair[0]), DHs: DHs, DHr: theirIdPubB64,
             CKs: b64(pair[1]), CKr: null, Ns: 0, Nr: 0, PN: 0, skipped: {} };
  }
  // The party that RECEIVES first. Its identity keypair IS the DHs the sender
  // used as DHr, so it can complete the ratchet on the first inbound message.
  async function initReceiver(myIdentity, theirIdPubB64) {
    var RK = await rootFromIdentities(myIdentity.priv, theirIdPubB64);
    return { RK: b64(RK), DHs: { priv: myIdentity.priv, pub: myIdentity.pub },
             DHr: null, CKs: null, CKr: null, Ns: 0, Nr: 0, PN: 0, skipped: {} };
  }

  // ---- ratchet steps ----
  async function dhRatchet(st, header) {
    st.PN = st.Ns; st.Ns = 0; st.Nr = 0; st.DHr = header.dh;
    var p1 = await kdfRK(ub64(st.RK), await dh(st.DHs.priv, st.DHr));
    st.RK = b64(p1[0]); st.CKr = b64(p1[1]);
    st.DHs = await genDH();
    var p2 = await kdfRK(ub64(st.RK), await dh(st.DHs.priv, st.DHr));
    st.RK = b64(p2[0]); st.CKs = b64(p2[1]);
  }
  async function skipMessageKeys(st, until) {
    if (st.Nr + MAX_SKIP < until) throw new Error("too many skipped messages");
    if (st.CKr) {
      while (st.Nr < until) {
        var pair = await kdfCK(ub64(st.CKr));
        st.CKr = b64(pair[0]);
        st.skipped[st.DHr + ":" + st.Nr] = b64(pair[1]);
        st.Nr++;
      }
      // Bound stored skipped keys.
      var keys = Object.keys(st.skipped);
      if (keys.length > MAX_SKIP) for (var i = 0; i < keys.length - MAX_SKIP; i++) delete st.skipped[keys[i]];
    }
  }

  // ---- public: encrypt / decrypt one message ----
  async function encrypt(st, plaintextBytes) {
    var pair = await kdfCK(ub64(st.CKs));
    st.CKs = b64(pair[0]);
    var header = { dh: st.DHs.pub, pn: st.PN, n: st.Ns };
    st.Ns++;
    var ct = await aeadEnc(pair[1], plaintextBytes, adOf(header));
    return { dh: header.dh, pn: header.pn, n: header.n, ct: ct };
  }
  async function decrypt(st, msg) {
    var header = { dh: msg.dh, pn: msg.pn, n: msg.n };
    // 1) a skipped key from an earlier out-of-order gap?
    var sk = header.dh + ":" + header.n;
    if (st.skipped[sk]) {
      var mk = ub64(st.skipped[sk]); delete st.skipped[sk];
      return await aeadDec(mk, msg.ct, adOf(header));
    }
    // 2) a new DH ratchet key from the peer?
    if (header.dh !== st.DHr) {
      await skipMessageKeys(st, header.pn);
      await dhRatchet(st, header);
    }
    // 3) advance the receiving chain to this message.
    await skipMessageKeys(st, header.n);
    var pair = await kdfCK(ub64(st.CKr));
    st.CKr = b64(pair[0]); st.Nr++;
    return await aeadDec(pair[1], msg.ct, adOf(header));
  }

  var API = {
    genDH: genDH, initSender: initSender, initReceiver: initReceiver,
    encrypt: encrypt, decrypt: decrypt, b64: b64, ub64: ub64, MAX_SKIP: MAX_SKIP
  };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else root.SparrowRatchet = API;
})(typeof self !== "undefined" ? self : this);
