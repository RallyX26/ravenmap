/* One-tap copy, because "select these 32 characters" is not a thing people do
 * on a phone.
 *
 * 🚨 THE TOKEN IS THE WHOLE ONBOARDING, AND IT WAS THE HARDEST PART OF IT.
 * A volunteer enrols a camera, gets a reviewer token, and has to get that token
 * into the review page. On the SAME browser there is already a one-tap link
 * (/rv#t=...) and the wallet remembers it afterwards, so that path is fine.
 * Every other path was manual: camera app on the phone and review on the
 * laptop, an installed PWA whose storage the browser tab cannot see, a second
 * phone, a browser whose site data got cleared. In all of those the only
 * instruction the project could offer was "select the token and paste it",
 * one-handed, from a small grey monospace run of characters. Reported by a real
 * volunteer mid-setup: "the key is difficult to use".
 *
 * Shared rather than written twice. There are three places a credential is
 * shown - the camera app's review banner, the review page's token list, and the
 * key page - and three copies of a clipboard helper is three chances for one of
 * them to be the broken one.
 *
 * ⚠️ TWO MECHANISMS, DELIBERATELY. navigator.clipboard needs a secure context,
 * so it is simply absent on the plain-http LAN address a node is often set up
 * from - which is exactly the moment somebody is holding a token they need to
 * move. The execCommand fallback is deprecated and still works everywhere, and
 * a deprecated call that copies beats a modern one that is undefined.
 */
(function () {
  "use strict";

  function legacy(text) {
    // Off-screen rather than display:none - a hidden element cannot be selected,
    // so the copy silently does nothing.
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    var ok = false;
    try {
      ta.select();
      ta.setSelectionRange(0, text.length);   // iOS ignores select() alone
      ok = document.execCommand("copy");
    } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }

  /* Copy `text`. Resolves true on success, false if the browser refused -
   * never rejects, because every caller wants to show a message either way. */
  function copy(text) {
    text = String(text == null ? "" : text);
    if (!text) return Promise.resolve(false);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text)
        .then(function () { return true; })
        // Permission denied, or not a real user gesture. Fall back rather than
        // report failure - the old API often still works when the new one will
        // not.
        .catch(function () { return legacy(text); });
    }
    return Promise.resolve(legacy(text));
  }

  /* A button that copies `getText()` and SAYS SO.
   *
   * The confirmation is the point, not decoration. A clipboard write is
   * invisible: without it somebody taps, sees nothing happen, taps again, and
   * concludes the button is broken - which is the same failure as the silent
   * empty states this codebase keeps finding. It also reports a REFUSAL rather
   * than claiming success, so "Copied" always means copied.
   */
  function button(getText, opts) {
    opts = opts || {};
    var b = document.createElement("button");
    b.type = "button";
    b.className = "copybtn" + (opts.className ? " " + opts.className : "");
    var idle = opts.label || "Copy";
    b.textContent = idle;
    b.setAttribute("aria-label", opts.aria || "copy to clipboard");
    if (!opts.bare) {
      b.style.cssText =
        "background:#1b2432;border:1px solid #2b3a4f;color:#cfe3f5;" +
        "border-radius:8px;padding:7px 12px;font:600 12px system-ui,sans-serif;" +
        "cursor:pointer;-webkit-tap-highlight-color:transparent;white-space:nowrap";
    }
    var timer = null;
    b.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      var text = typeof getText === "function" ? getText() : getText;
      copy(text).then(function (ok) {
        b.textContent = ok ? (opts.done || "✓ Copied") : "Press and hold to copy";
        if (ok && navigator.vibrate) { try { navigator.vibrate(15); } catch (e) {} }
        if (timer) clearTimeout(timer);
        timer = setTimeout(function () { b.textContent = idle; }, ok ? 1600 : 3000);
      });
    });
    return b;
  }

  window.sparrowCopy = copy;
  window.sparrowCopyButton = button;
})();
