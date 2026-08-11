/* Landing-page behaviour, kept OUT of the HTML so the deployed Cloudflare Pages
   site can ship a strict Content-Security-Policy (script-src 'self') via
   landing/_headers. Cloudflare Pages cannot add a per-response nonce, and an
   inline <script> would force either 'unsafe-inline' (which defeats the point)
   or a brittle hash that silently breaks the page the next time a byte changes.
   External + 'self' is the robust choice. */


/* ---- call-to-action buttons --------------------------------------------
   Fill either value in and its button appears; leave it empty and the button
   is simply not rendered, so the page never ships a dead link. */
const APP_URL = "https://map.sparrowmap.com/app";
const MAP_URL = "https://map.sparrowmap.com";
const GITHUB_URL = "https://github.com/SparrowMap/sparrowmap";
const INSTAGRAM_URL = "https://instagram.com/sparrowmap";
const CONTACT_EMAIL = "sparrowmap@icloud.com";

(function () {
  const host = document.getElementById("actions");
  if (!host) return;
  const out = [];
  const link = (cls, href, text) => {
    const a = document.createElement("a");
    a.className = cls; a.href = href; a.rel = "noopener"; a.textContent = text;
    out.push(a);
  };
  if (APP_URL) link("btn", APP_URL, "Add a camera");
  if (MAP_URL) link("btn ghost", MAP_URL, "View the live map");
  if (GITHUB_URL) link("btn ghost", GITHUB_URL, "Read the code on GitHub");
  if (INSTAGRAM_URL) link("btn ghost", INSTAGRAM_URL, "Follow on Instagram");
  if (CONTACT_EMAIL) {
    const a = document.createElement("a");
    a.className = "btn ghost";
    a.href = "mailto:" + CONTACT_EMAIL + "?subject=I%20want%20to%20run%20a%20camera";
    a.textContent = "Ask about running a camera";
    out.push(a);
  }
  if (!out.length) {
    const p = document.createElement("p");
    p.style.cssText = "font-family:var(--mono);font-size:13px;color:var(--steel);margin:0";
    p.textContent = "Opening to volunteers soon.";
    out.push(p);
  }
  host.replaceChildren(...out);
})();
