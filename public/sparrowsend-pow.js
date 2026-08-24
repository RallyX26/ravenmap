/* Sparrow Send — a hashcash-style proof-of-work stamped on every SEND.
 *
 * The relay takes mail from anyone who knows your mailbox id (that is how you
 * receive without an account), which left three cheap floods open: evicting a
 * victim's queued mail, filling the global cap to refuse mail network-wide, and
 * monopolising the shared rate bucket. A small per-message cost closes all three
 * without adding accounts: the SENDER must find a nonce so that
 *
 *     SHA-256( "SP1|" + mailbox + "|" + ts + "|" + sha256(envelope) + "|" + nonce )
 *
 * has POW_BITS leading zero bits. A human sending a handful of messages spends a
 * few hundred ms total and never notices; flooding 60,000 messages costs hours
 * of CPU. The relay verifies with ONE SHA-256. `ts` (accepted within a few
 * minutes of now) stops a solution being precomputed far ahead or replayed, and
 * binding the envelope hash means a solved stamp can't be moved to another
 * message. Pure Web Crypto is async and too slow for a tight loop, so this ships
 * a small synchronous SHA-256 just for the search.
 *
 * ⚠️ POW_BITS here MUST equal POW_BITS in send_relay.py or valid mail is refused.
 */
(function (root) {
  "use strict";
  var POW_BITS = 17;   // ~131k hashes expected; must match send_relay.POW_BITS
  var VERSION = "SP1";

  var K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2];

  // SHA-256 of a byte array -> 32-byte Uint8Array. Standard, no external lib.
  function sha256(bytes) {
    var l = bytes.length;
    // padded length in 512-bit blocks
    var withOne = l + 1;
    var k = (56 - (withOne % 64) + 64) % 64;
    var total = withOne + k + 8;
    var m = new Uint8Array(total);
    m.set(bytes);
    m[l] = 0x80;
    var bitLen = l * 8;
    // 64-bit big-endian length (high word is 0 for our small inputs)
    m[total - 4] = (bitLen >>> 24) & 0xff;
    m[total - 3] = (bitLen >>> 16) & 0xff;
    m[total - 2] = (bitLen >>> 8) & 0xff;
    m[total - 1] = bitLen & 0xff;

    var h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a,
        h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
    var w = new Int32Array(64);
    for (var off = 0; off < total; off += 64) {
      for (var i = 0; i < 16; i++) {
        w[i] = (m[off + i * 4] << 24) | (m[off + i * 4 + 1] << 16) | (m[off + i * 4 + 2] << 8) | (m[off + i * 4 + 3]);
      }
      for (i = 16; i < 64; i++) {
        var x = w[i - 15], y = w[i - 2];
        var s0 = ((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3);
        var s1 = ((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      var a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, hh = h7;
      for (i = 0; i < 64; i++) {
        var S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
        var ch = (e & f) ^ (~e & g);
        var t1 = (hh + S1 + ch + K[i] + w[i]) | 0;
        var S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
        var maj = (a & b) ^ (a & c) ^ (b & c);
        var t2 = (S0 + maj) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
      h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + hh) | 0;
    }
    var out = new Uint8Array(32), hs = [h0, h1, h2, h3, h4, h5, h6, h7];
    for (i = 0; i < 8; i++) {
      out[i * 4] = (hs[i] >>> 24) & 0xff; out[i * 4 + 1] = (hs[i] >>> 16) & 0xff;
      out[i * 4 + 2] = (hs[i] >>> 8) & 0xff; out[i * 4 + 3] = hs[i] & 0xff;
    }
    return out;
  }

  var HEX = "0123456789abcdef";
  function toHex(bytes) {
    var s = "";
    for (var i = 0; i < bytes.length; i++) s += HEX[(bytes[i] >> 4) & 0xf] + HEX[bytes[i] & 0xf];
    return s;
  }
  // UTF-8 bytes. Our preimages are ASCII, but env can hold any codepoint; use the
  // platform encoder for the one env hash and a fast path for the ASCII preimage.
  var TE = (typeof TextEncoder !== "undefined") ? new TextEncoder() : null;
  function utf8(str) {
    if (TE) return TE.encode(str);
    var b = []; for (var i = 0; i < str.length; i++) { var c = str.charCodeAt(i); if (c < 128) b.push(c); else b.push(63); }
    return new Uint8Array(b);
  }
  function ascii(str) {   // fast: assumes charCode < 256 (true for the preimage)
    var b = new Uint8Array(str.length);
    for (var i = 0; i < str.length; i++) b[i] = str.charCodeAt(i) & 0xff;
    return b;
  }
  function leadingZeroBits(d) {
    var bits = 0;
    for (var i = 0; i < d.length; i++) {
      var v = d[i];
      if (v === 0) { bits += 8; continue; }
      var c = 0, t = v;
      while (t < 128) { c++; t <<= 1; }
      return bits + c;
    }
    return bits;
  }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  // Find a nonce so SHA-256(preimage) has >= bits leading zeros. Yields to the
  // event loop periodically so the UI never fully freezes on a slow phone.
  async function stamp(mailbox, envStr, bits) {
    bits = bits || POW_BITS;
    var now = (typeof Date !== "undefined") ? Math.floor(Date.now() / 1000) : 0;
    var ch = toHex(sha256(utf8(envStr)));
    var prefix = VERSION + "|" + mailbox + "|" + now + "|" + ch + "|";
    var nonce = 0, batch = 0;
    for (;;) {
      var d = sha256(ascii(prefix + nonce));
      if (leadingZeroBits(d) >= bits) return { ts: now, nonce: nonce };
      nonce++;
      if ((++batch & 8191) === 0) await sleep(0);   // breathe every 8192 tries
    }
  }

  var API = { stamp: stamp, sha256: sha256, toHex: toHex, utf8: utf8, POW_BITS: POW_BITS, VERSION: VERSION };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else root.SparrowPow = API;
})(typeof self !== "undefined" ? self : this);
