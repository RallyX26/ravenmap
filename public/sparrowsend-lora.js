/* Sparrow Send - the LoRa wire format, shared so the internet app and the
 * off-grid board app speak the SAME bytes on the mesh lane.
 *
 * A LoRa packet is tiny (<=250 B), so the internet {from,mid,r} JSON envelope
 * does not fit. On the mesh we use the board's compact BINARY frame instead, and
 * both ends trial-decrypt it against their contacts (no sender field - that would
 * be metadata on a broadcast). This module is pure framing: no keys, no crypto.
 * It MUST stay byte-identical to sealAndSend()/tryDecrypt() in sendlora.html.
 *
 *   frame:  [MAGIC 'S'][VER 1][T_MSG 1][flags 0] [n:2 BE][pn:2 BE][dh:65][ct]
 *   plain:  [kind:1][mid:4][body...]            kind: 1=text, 2=read-receipt
 */
(function (root) {
  "use strict";
  var MAGIC = 0x53, VER = 1, T_MSG = 1, HDR = 4, MAX_FRAME = 250;
  var K_TEXT = 1, K_ACK = 2, K_REKEY = 3;   // 3 = "I started a fresh session, adopt it and resend"

  function b64(u8){ var s=""; for(var i=0;i<u8.length;i++) s+=String.fromCharCode(u8[i]);
    return (typeof btoa!=="undefined"?btoa(s):Buffer.from(s,"binary").toString("base64")); }
  function ub64(s){ var r=(typeof atob!=="undefined"?atob(s):Buffer.from(s,"base64").toString("binary"));
    var u=new Uint8Array(r.length); for(var i=0;i<r.length;i++) u[i]=r.charCodeAt(i); return u; }

  // A ratchet message {dh(b64),pn,n,ct(b64)} -> the raw binary frame bytes.
  function msgToFrame(m){
    var dh = ub64(m.dh), ct = ub64(m.ct);            // dh is the 65-byte P-256 pub
    var pl = new Uint8Array(4 + 65 + ct.length);
    pl[0]=(m.n>>8)&255; pl[1]=m.n&255; pl[2]=(m.pn>>8)&255; pl[3]=m.pn&255;
    pl.set(dh, 4); pl.set(ct, 69);
    var f = new Uint8Array(HDR + pl.length);
    f[0]=MAGIC; f[1]=VER; f[2]=T_MSG; f[3]=0; f.set(pl, HDR);
    return f;
  }
  // Raw frame bytes -> a ratchet message, or null if it is not a T_MSG frame.
  function frameToMsg(f){
    if(!f || f.length < HDR+4+65 || f[0]!==MAGIC || f[1]!==VER || f[2]!==T_MSG) return null;
    var pl = f.subarray(HDR);
    return { dh: b64(pl.subarray(4,69)), n: (pl[0]<<8)|pl[1], pn: (pl[2]<<8)|pl[3], ct: b64(pl.subarray(69)) };
  }

  // plaintext [kind][mid:4][body]
  function packPlain(kind, mid4, body){
    var pt = new Uint8Array(5 + (body?body.length:0));
    pt[0]=kind; pt.set(mid4, 1); if(body) pt.set(body, 5); return pt;
  }
  function parsePlain(pt){
    return { kind: pt[0], mid: pt.subarray(1,5), body: pt.subarray(5) };
  }
  // hex of the 4-byte mid, for de-dupe keys
  function midHex(mid4){ var s=""; for(var i=0;i<4;i++) s+=(mid4[i]+256).toString(16).slice(1); return s; }

  var API = { MAGIC:MAGIC, VER:VER, T_MSG:T_MSG, HDR:HDR, MAX_FRAME:MAX_FRAME,
    K_TEXT:K_TEXT, K_ACK:K_ACK, K_REKEY:K_REKEY, b64:b64, ub64:ub64,
    msgToFrame:msgToFrame, frameToMsg:frameToMsg, packPlain:packPlain,
    parsePlain:parsePlain, midHex:midHex };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else root.SparrowLora = API;
})(typeof self !== "undefined" ? self : this);
