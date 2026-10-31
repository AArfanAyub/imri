/**
 * The IMRI mark.
 *
 * One drawing, used everywhere: the sidebar, the boot splash, the sign-in and
 * activation screens, and the browser tab. Keeping it in one module is the
 * only way those places can never drift apart.
 *
 * The idea is an open book seen from above, with a bookmark hanging past the
 * bottom edge of the page. A book plus a bookmark is the smallest set of
 * shapes that says "resource centre" without spelling anything, and the
 * silhouette stays readable at 16 px because it is only four masses: a
 * squircle, two page planes, and a ribbon. The indigo-to-violet body and the
 * amber ribbon give it a colour signature the wordmark alone cannot.
 *
 * Geometry notes, because the coordinates look arbitrary and are not:
 *   - The squircle is 2..46 with r=13; everything else lives inside 8..40 so
 *     the mark never crowds its own edge.
 *   - The book is wider than it is tall (31 x 22) because that is what an open
 *     book looks like from above. Two tall pages read as a closed book.
 *   - Each page's spine edge is 18.8 tall and its outer edge only 16.8. That
 *     pinch is what makes the pair read as fanning open rather than as two
 *     tilted rectangles — it is the single most important number here, and
 *     shrinking it to 1 px turns the mark back into a pair of boxes.
 *   - The pages stop 1.1 px short of x=24 on either side. That 2.2 px gutter
 *     shows the gradient through, which reads as a spine without a drawn line,
 *     and it survives down to 16 px.
 *   - The ribbon is drawn *before* the right page, so its top end tucks under
 *     the paper and only the 7 px that overhang the page edge is visible.
 *
 * Four drawings were compared side by side before this one was chosen: a
 * gentler dome, a curled page with a bulging outer edge, and a version with a
 * dark hardback cover behind the pages. The cover lost because at 28 px the
 * cover and the body became the same tone; the curl lost because a bulging
 * outer edge reads as a shield rather than as paper.
 *
 * @module brand
 */

import { svgEl } from './ui.js';

/** Gradient stops. Shared with the favicon so the tab matches the UI. */
const FROM = '#4F46E5';
const VIA = '#6D5BE8';
const TO = '#8B5CF6';
const RIBBON_FROM = '#FCD34D';
const RIBBON_TO = '#F59E0B';

let seq = 0;

/**
 * The mark as raw SVG markup.
 *
 * @param {string} id Unique suffix for the gradient defs. Two marks on one page
 *   must not share an id, or the second silently borrows the first's paint.
 * @returns {string}
 */
function markup(id) {
  return `
    <defs>
      <linearGradient id="imri-bg-${id}" x1="8" y1="0" x2="40" y2="48" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="${FROM}"/>
        <stop offset="0.52" stop-color="${VIA}"/>
        <stop offset="1" stop-color="${TO}"/>
      </linearGradient>
      <linearGradient id="imri-shine-${id}" x1="24" y1="2" x2="24" y2="28" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#ffffff" stop-opacity="0.30"/>
        <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
      </linearGradient>
      <linearGradient id="imri-ribbon-${id}" x1="29" y1="17" x2="29" y2="42" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="${RIBBON_FROM}"/>
        <stop offset="1" stop-color="${RIBBON_TO}"/>
      </linearGradient>
    </defs>

    <rect x="2" y="2" width="44" height="44" rx="13" fill="url(#imri-bg-${id})"/>
    <rect x="2" y="2" width="44" height="44" rx="13" fill="url(#imri-shine-${id})"/>
    <rect x="3.4" y="3.4" width="41.2" height="41.2" rx="11.8"
          fill="none" stroke="#ffffff" stroke-opacity="0.16" stroke-width="1.2"/>

    <path d="M26.4 17.4H30.2V42.2L28.3 39.6L26.4 42.2Z" fill="url(#imri-ribbon-${id})"/>

    <path d="M22.9 17.2Q13.2 12.2 9.6 14.4Q8.2 15.1 8.2 16.8V28.8Q8.2 30.5 9.6 31.2Q13.2 33.4 22.9 36Z"
          fill="#ffffff" fill-opacity="0.88"/>
    <path d="M25.1 17.2Q34.8 12.2 38.4 14.4Q39.8 15.1 39.8 16.8V28.8Q39.8 30.5 38.4 31.2Q34.8 33.4 25.1 36Z"
          fill="#ffffff"/>
  `.trim();
}

/**
 * Build the mark as a live SVG element.
 *
 * @param {number} [size] Pixel size for the square. Defaults to 28.
 * @param {{label?: string|null, class?: string}} [options] `label: null` marks
 *   the drawing decorative, for the cases where the wordmark sits right beside
 *   it and a screen reader would otherwise announce "IMRI" twice.
 * @returns {SVGElement}
 */
export function mark(size = 28, options = {}) {
  const opts = options || {};
  const px = Math.max(12, Number(size) || 28);
  const id = `m${(seq += 1)}`;
  const decorative = opts.label === null;

  const node = svgEl('svg', {
    viewBox: '0 0 48 48',
    width: px,
    height: px,
    class: opts.class ? `brandmark ${opts.class}` : 'brandmark',
    role: decorative ? 'presentation' : 'img',
    'aria-label': decorative ? null : opts.label || 'IMRI',
    'aria-hidden': decorative ? 'true' : null,
    focusable: 'false',
  });
  // Constant, developer-authored markup — no interpolation of user data.
  node.innerHTML = markup(id);
  return node;
}

/**
 * The mark as a `data:` URI, for the favicon.
 *
 * A tab icon cannot load a module, so this is what `app.js` assigns to the
 * `<link rel="icon">` at boot. A gradient needs `<defs>`, which is why the
 * icon is a full SVG rather than a single path.
 *
 * @returns {string}
 */
export function faviconUri() {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">${markup('f')}</svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

export default { mark, faviconUri };
