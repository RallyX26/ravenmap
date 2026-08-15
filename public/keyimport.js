/* Getting a credential OFF a phone and INTO a box, once, for both gates.
 *
 * 🚨 WHY THIS IS A SHARED FILE AND NOT COPIED INTO THE SECOND PAGE.
 *
 * /signin grew file-and-QR import so somebody who had saved their camera key
 * could get back in without typing 60 characters on a phone keyboard. The
 * reviewer gate on /rv never got it - it has a paste box and nothing else - so
 * a reviewer whose token lives in the file we gave them had no way in at all.
 * Reported: "cant upload token now", on a page whose only control is "Add
 * token" and whose only answer is "Paste a token first."
 *
 * The obvious fix is to copy the importer into /rv. This codebase has been
 * bitten by that specific move over and over: is_operator_addr, the
 * government-vehicle call, the full-resolution photo swap - each was fixed in
 * one copy, left broken in the other, and found later by a user. So the
 * importer moved out here instead, and both pages call it.
 *
 * What differs between the two callers is only what counts as a valid string,
 * so that is the one thing passed in. Everything else - the picker, the size
 * cap, the two QR decoders, the downscale, the error wording - is identical by
 * construction rather than by somebody remembering to update both.
 */
window.sparrowKeyImport = function (opts) {
  var parse  = opts.parse;
  var onValue = opts.onValue;
  var say    = opts.say || function () {};
  var input  = opts.file;

  /* jsQR is vendored (MIT, pure JS, no network of its own) because
   * BarcodeDetector does not exist on iOS Safari or Firefox - and the path it
   * was guarding, "a photo of the QR you cannot scan because it is on this
   * same phone's screen", is exactly the path most people need. Loaded only
   * when somebody actually picks an image. */
  var jsqrLoading = null;
  function loadJsQR() {
    if (window.jsQR) return Promise.resolve(window.jsQR);
    if (jsqrLoading) return jsqrLoading;
    jsqrLoading = new Promise(function (res, rej) {
      var sc = document.createElement("script");
      sc.src = "/vendor/jsqr.min.js";
      sc.onload = function () { res(window.jsQR); };
      sc.onerror = function () { rej(new Error("decoder")); };
      document.head.appendChild(sc);
    });
    return jsqrLoading;
  }

  function decodeWithJsQR(file) {
    return loadJsQR().then(function (jsQR) {
      return new Promise(function (res, rej) {
        var url = URL.createObjectURL(file);
        var img = new Image();
        img.onload = function () {
          // ⚠️ CAP THE PIXELS. A phone photo is 12 megapixels and walking that
          // whole grid locks the tab for seconds on the exact device this is
          // for. A QR survives a downscale to 1600px on the long edge.
          var s = Math.min(1, 1600 / Math.max(img.width, img.height));
          var w = Math.max(1, Math.round(img.width * s));
          var h = Math.max(1, Math.round(img.height * s));
          var cv = document.createElement("canvas");
          cv.width = w; cv.height = h;
          var cx = cv.getContext("2d", { willReadFrequently: true });
          cx.drawImage(img, 0, 0, w, h);
          URL.revokeObjectURL(url);
          var px;
          try { px = cx.getImageData(0, 0, w, h); }
          catch (e) { rej(e); return; }
          // attemptBoth: a QR photographed off a dark screen, or a screenshot
          // of this site's own dark key page, arrives inverted.
          var r = jsQR(px.data, w, h, { inversionAttempts: "attemptBoth" });
          res(r && r.data ? r.data : null);
        };
        img.onerror = function () { URL.revokeObjectURL(url); rej(new Error("image")); };
        img.src = url;
      });
    });
  }

  function useDecoded(raw) {
    var v = raw && parse(raw);
    if (!v) {
      say("No SparrowMap code found in that picture. Make sure the whole QR "
        + "code is in the shot and not cropped or blurred.", false);
      return;
    }
    onValue(v);
  }

  function fromImage(file) {
    say("Reading the QR code…", true);
    var native = ("BarcodeDetector" in window)
      ? createImageBitmap(file)
          .then(function (bmp) {
            return new window.BarcodeDetector({ formats: ["qr_code"] }).detect(bmp);
          })
          .then(function (codes) {
            var hit = null;
            (codes || []).forEach(function (c) { if (!hit && parse(c.rawValue)) hit = c.rawValue; });
            return hit;
          })
          .catch(function () { return null; })
      : Promise.resolve(null);

    native.then(function (hit) {
      if (hit) { useDecoded(hit); return; }
      return decodeWithJsQR(file).then(useDecoded);
    }).catch(function () {
      say("Could not read that image. Try the file instead, or paste it above.",
          false);
    });
  }

  input.addEventListener("change", function () {
    var f = this.files && this.files[0];
    if (!f) return;
    // Reset, so choosing the SAME file twice after a failure fires again.
    var self = this;
    var clear = function () { try { self.value = ""; } catch (e) {} };
    if (/^image\//.test(f.type)) { fromImage(f); clear(); return; }
    /* 🚨 A CAP, BECAUSE THIS IS A FILE A STRANGER CHOSE. The real file is
     * under a kilobyte; anything large is the wrong file, and reading it as
     * text would hang the tab for no possible benefit. */
    if (f.size > 512 * 1024) {
      say("That file is far too big (the real one is under a kilobyte). Pick "
        + "the file SparrowMap gave you, or a photo of the QR code.", false);
      clear(); return;
    }
    f.text().then(function (txt) {
      var v = parse(txt);
      if (!v) {
        say("Nothing from SparrowMap was found in that file. It should be the "
          + "file we gave you, a photo of your QR code, or a note with the "
          + "code in it.", false);
        return;
      }
      onValue(v);
    }).catch(function () {
      say("Could not read that file.", false);
    }).then(clear, clear);
  });

  return { pick: function () { input.click(); } };
};
