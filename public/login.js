/* Operator sign-in. POSTs the token to /api/operator/login as application/json
   (which the hub's CSRF guard requires) and, on success, follows the
   Set-Cookie the server sends back. External file so the page needs no inline
   script under a strict CSP. */
(function () {
  const tok = document.getElementById('tok');
  const btn = document.getElementById('go');
  const msg = document.getElementById('msg');

  function say(text, cls) { msg.textContent = text; msg.className = 'msg ' + (cls || ''); }

  async function submit() {
    const value = (tok.value || '').trim();
    if (!value) { say('Enter the token.', 'err'); tok.focus(); return; }
    btn.disabled = true; say('Signing in…');
    try {
      const r = await fetch('/api/operator/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: value }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        if (d.note) { say('Auth is off on this deployment — no sign-in needed.', 'ok'); }
        else { say('Signed in. Opening review…', 'ok'); }
        setTimeout(() => { location.href = '/review'; }, 500);
        return;
      }
      say(r.status === 401 ? 'Wrong token.' : ('Sign-in failed (' + r.status + ').'), 'err');
    } catch (e) {
      say('Could not reach the hub.', 'err');
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener('click', submit);
  tok.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
  tok.focus();
})();
