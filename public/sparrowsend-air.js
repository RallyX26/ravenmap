/* Sparrow Send - routing-tag derivation for the internet air relay.
 *
 * The air relay (air_relay.py) routes an opaque, sealed frame to a recipient by
 * an unlinkable ROUTING TAG instead of a mailbox id. This module computes that
 * tag from the pairwise ECDH secret two Sparrow identities share:
 *
 *     S   = ECDH(my_ecdh_private, their_ecdh_public)      // symmetric
 *     tag = HMAC-SHA256(S, "sparrow-air-v1" | epoch(4B BE) | recipient_pub)[:16]
 *
 * Both parties (and only they) know S, so both can compute the tag; the hub
 * cannot. Direction is separated by whose public key is the recipient, so a
 * sender never polls its own frames: to SEND to a contact the recipient is the
 * CONTACT (sendTag); to RECEIVE from a contact the recipient is ME (recvTag).
 * The tag rotates each epoch, so poll the current AND previous epoch to cover a
 * boundary crossing or modest clock skew.
 *
 * Web Crypto only (P-256 ECDH + HMAC-SHA256). Runs in a browser and under Node
 * 18+ (globalThis.crypto). No external dependency, so the LoRa app can serve it
 * from flash and the online app from the hub, byte-identical.
 */
(function (root) {
  "use strict";
  var subtle = globalThis.crypto && globalThis.crypto.subtle;
  var TE = (typeof TextEncoder !== "undefined") ? TextEncoder
    : require("util").TextEncoder;
  var te = new TE();

  var EPOCH_SECS = 3600;                 // tag lifetime; MUST match on both ends
  var LABEL = te.encode("sparrow-air-v1");

  function ub64u(s) {                     // base64url (or base64) -> Uint8Array
    s = String(s).replace(/-/g, "+").replace(/_/g, "/").replace(/\s+/g, "");
    s += "=".repeat((4 - (s.length % 4)) % 4);
    var bin = (typeof atob !== "undefined") ? atob(s)
      : Buffer.from(s, "base64").toString("binary");
    var u = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return u;
  }
  function hex(u8) {
    var s = "";
    for (var i = 0; i < u8.length; i++) s += (u8[i] + 0x100).toString(16).slice(1);
    return s;
  }
  function u32be(n) {
    n = n >>> 0;
    return new Uint8Array([(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]);
  }
  function cat() {
    var n = 0, i;
    for (i = 0; i < arguments.length; i++) n += arguments[i].length;
    var out = new Uint8Array(n), o = 0;
    for (i = 0; i < arguments.length; i++) { out.set(arguments[i], o); o += arguments[i].length; }
    return out;
  }

  function epochNow() { return Math.floor(Date.now() / 1000 / EPOCH_SECS); }

  // S = ECDH(myPriv, peerPub). myPriv may be a JWK or an already-imported
  // CryptoKey (the online app keeps its identity ECDH key as a CryptoKey).
  async function ecdhSecret(myPriv, peerPubRaw) {
    var priv = myPriv;
    var isKey = (typeof CryptoKey !== "undefined") && (myPriv instanceof CryptoKey);
    if (!isKey) {
      priv = await subtle.importKey("jwk", myPriv,
        { name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
    }
    var pub = await subtle.importKey("raw", peerPubRaw,
      { name: "ECDH", namedCurve: "P-256" }, false, []);
    return new Uint8Array(await subtle.deriveBits({ name: "ECDH", public: pub }, priv, 256));
  }

  // The tag for a frame whose recipient owns `toPubRaw`, on the shared secret
  // between me (myPriv) and the other party (peerPubRaw).
  async function tag(myPriv, peerPubRaw, toPubRaw, epoch) {
    var S = await ecdhSecret(myPriv, peerPubRaw);
    var key = await subtle.importKey("raw", S, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    var mac = new Uint8Array(await subtle.sign("HMAC", key, cat(LABEL, u32be(epoch), toPubRaw)));
    return hex(mac.subarray(0, 16));
  }

  // Accept raw Uint8Array or a base64/base64url string for the public keys.
  function raw(pub) { return (pub instanceof Uint8Array) ? pub : ub64u(pub); }

  // The tag I SEND to a contact (recipient = the contact).
  async function sendTag(myPriv, contactPub, epoch) {
    var c = raw(contactPub);
    return tag(myPriv, c, c, (epoch == null ? epochNow() : epoch));
  }
  // The tag I POLL for a contact's mail to me (recipient = ME).
  async function recvTag(myPriv, contactPub, myPub, epoch) {
    return tag(myPriv, raw(contactPub), raw(myPub), (epoch == null ? epochNow() : epoch));
  }
  // The two epochs worth polling right now (current + previous).
  function pollEpochs() { var e = epochNow(); return [e, e - 1]; }

  var API = {
    EPOCH_SECS: EPOCH_SECS, epochNow: epochNow, pollEpochs: pollEpochs,
    tag: tag, sendTag: sendTag, recvTag: recvTag, ecdhSecret: ecdhSecret,
    ub64u: ub64u, hex: hex
  };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else root.SparrowAir = API;
})(typeof self !== "undefined" ? self : this);
