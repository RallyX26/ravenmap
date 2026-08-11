/* A refresh button, on every SparrowMap page.
 *
 * ONE implementation shared by both apps rather than a copy per page. Five
 * copies of fifteen lines is five places to fix the day one of them is wrong,
 * and this codebase has already been bitten twice by a control that existed in
 * one file and not the other.
 *
 * It prefers a SOFT refresh: if a page defines `window.sparrowRefresh` it is
 * called instead of reloading, so the map keeps the spot you panned to and the
 * camera page keeps its stream. A full reload throws away exactly the state a
 * person was looking at, which is the thing they were trying to keep.
 *
 * Hold it (or shift-click) to force a hard reload anyway - the escape hatch for
 * when the page itself is what is wrong.
 */
(function () {
  const css = `
    .swrefresh{position:fixed;right:12px;top:60px;z-index:9000;
      width:44px;height:44px;border-radius:50%;cursor:pointer;
      display:flex;align-items:center;justify-content:center;
      background:#111621ee;border:1px solid #2a3547;color:#8794a8;
      backdrop-filter:blur(6px);box-shadow:0 3px 14px #0008;
      -webkit-tap-highlight-color:transparent;transition:color .15s,border-color .15s}
    .swrefresh:hover{color:#e6ecf5;border-color:#3d8cff}
    .swrefresh svg{width:19px;height:19px;transition:transform .5s ease}
    .swrefresh.spin svg{transform:rotate(360deg)}
    .swrefresh.hard{border-color:#ffb547;color:#ffb547}
    @media print{.swrefresh{display:none}}
  `;
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  const btn = document.createElement('button');
  btn.className = 'swrefresh';
  btn.title = 'Refresh  (hold or shift-click to reload the whole page)';
  btn.setAttribute('aria-label', 'Refresh');
  btn.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/></svg>';

  let held = false, timer = null;

  function spin() {
    btn.classList.remove('spin');
    void btn.offsetWidth;          // restart the transition
    btn.classList.add('spin');
  }

  async function refresh(hard) {
    spin();
    if (hard || typeof window.sparrowRefresh !== 'function') {
      location.reload();
      return;
    }
    try {
      await window.sparrowRefresh();
    } catch (e) {
      location.reload();           // a soft refresh that throws is a hard one
    }
  }

  btn.addEventListener('click', (e) => {
    if (held) { held = false; return; }
    refresh(e.shiftKey);
  });

  // Press and hold for a hard reload, so the escape hatch works on a phone
  // where there is no shift key and no address bar in the cockpit view.
  const start = () => {
    timer = setTimeout(() => {
      held = true;
      btn.classList.add('hard');
      refresh(true);
    }, 600);
  };
  const cancel = () => { clearTimeout(timer); btn.classList.remove('hard'); };
  btn.addEventListener('mousedown', start);
  btn.addEventListener('touchstart', start, { passive: true });
  ['mouseup', 'mouseleave', 'touchend', 'touchcancel']
    .forEach((ev) => btn.addEventListener(ev, cancel));

  // Top-right, below the header. It used to sit bottom-right where it covered
  // the fixed bottom nav's "Add a camera" button (swallowing the tap, since its
  // z-index outranks the bar) and, once lifted, the map's own footer text. The
  // top-right corner below the header is clear on every page.
  function place() { document.body.appendChild(btn); }
  document.addEventListener('DOMContentLoaded', place);
  if (document.readyState !== 'loading') place();
})();
