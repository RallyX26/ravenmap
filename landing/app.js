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

/* First-visit nudge to add a camera - people were not finding the button. Shown
   once per browser, dismissible, built with DOM calls (style-src allows inline
   styles here; there is still no inline <script>). */
(function () {
  try { if (localStorage.getItem("sparrow.introSeen")) return; } catch (e) { return; }
  const mk = (tag, text, css) => {
    const el = document.createElement(tag);
    if (text) el.textContent = text;
    if (css) el.style.cssText = css;
    return el;
  };
  const ov = mk("div", "", "position:fixed;inset:0;z-index:3000;display:flex;"
    + "align-items:center;justify-content:center;background:rgba(0,0,0,.6);padding:20px");
  const card = mk("div", "", "max-width:380px;width:100%;background:#0d1219;"
    + "border:1px solid #22303c;border-radius:16px;padding:26px;text-align:center;"
    + "color:#c7d2dc;font:15px/1.55 system-ui,sans-serif;box-shadow:0 24px 70px rgba(0,0,0,.6)");
  const h = mk("div", "Watch the watchers",
    "font-size:21px;font-weight:700;color:#fff;margin-bottom:10px");
  const p = mk("div", "SparrowMap runs on volunteer cameras. Point a spare phone "
    + "at a street and it maps the patrols that pass — plates destroyed on the "
    + "device, never uploaded.", "color:#93a3b3;margin-bottom:20px");
  const add = mk("a", "Add a camera", "display:block;padding:14px;border-radius:11px;"
    + "background:#3b82f6;color:#fff;font-weight:600;text-decoration:none;margin-bottom:10px");
  add.href = APP_URL || "https://map.sparrowmap.com/app"; add.rel = "noopener";
  const skip = mk("button", "Just browsing", "display:block;width:100%;padding:12px;"
    + "border-radius:11px;background:transparent;border:1px solid #22303c;color:#7f93a6;"
    + "cursor:pointer;font:inherit");
  const done = () => { try { localStorage.setItem("sparrow.introSeen", "1"); } catch (e) {} ov.remove(); };
  skip.addEventListener("click", done);
  add.addEventListener("click", done);
  ov.addEventListener("click", (e) => { if (e.target === ov) done(); });
  card.append(h, p, add, skip);
  ov.appendChild(card);
  document.body.appendChild(ov);
})();
