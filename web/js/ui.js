/**
 * IMRI — component kit.
 *
 * Hand-built DOM helpers, no framework and no build step. Every component
 * returns a real element the caller can append, keep a reference to, or throw
 * away. Nothing here fetches, nothing here knows about routes, and nothing here
 * contains a user-visible string: all copy comes from `t()` (I18N-04).
 *
 * Conventions
 *   el(tag, props, ...children)   the one DOM constructor everything uses
 *   icon(name, size)              inline 24×24 stroke SVG, `currentColor`
 *   button/input/…                thin, opinionated wrappers with a11y baked in
 *   modal/drawer/toast            layered UI with a shared focus-trap stack
 *
 * Accessibility rules enforced here rather than by the caller:
 *   · every dialog is `role="dialog" aria-modal="true"` with a focus trap
 *   · Esc closes the topmost layer only
 *   · focus returns to the element that opened the layer
 *   · toasts live in an `aria-live="polite"` region, capped at three
 *   · tabs use roving `tabindex` and respond to arrow keys
 *   · every icon-only control needs a `label` for `aria-label` + tooltip
 */

import { t } from './i18n.js';

// --------------------------------------------------------------------------- //
// DOM primitives
// --------------------------------------------------------------------------- //

const SVG_NS = 'http://www.w3.org/2000/svg';

/**
 * Create an element.
 *
 * Recognised props:
 *   class / className   string
 *   text                textContent (safe — never parsed as HTML)
 *   html                innerHTML — TRUSTED MARKUP ONLY, never user data
 *   attrs               object of attributes (null/false skipped)
 *   data                object → `dataset`
 *   style               object; camelCase or `kebab-case` keys
 *   on                  object of event listeners
 *   ref                 function called with the element
 *   everything else     set as a property when it exists on the element,
 *                       otherwise as an attribute (`type`, `value`, `href`, …)
 *
 * @param {string} tag
 * @param {?Object} [props]
 * @param {...(Node|string|number|Array|null|false|undefined)} children
 * @returns {HTMLElement}
 */
export function el(tag, props, ...children) {
  const node = document.createElement(tag);
  applyProps(node, props);
  appendChildren(node, children);
  return node;
}

/** Apply a props bag to an existing element. Shared by `el` and `svgEl`. */
function applyProps(node, props) {
  if (!props) return;
  for (const key of Object.keys(props)) {
    const value = props[key];
    if (value === undefined || value === null) continue;

    if (key === 'class' || key === 'className') {
      node.setAttribute('class', String(value));
      continue;
    }
    if (key === 'text') {
      node.textContent = String(value);
      continue;
    }
    if (key === 'html') {
      node.innerHTML = String(value);
      continue;
    }
    if (key === 'attrs') {
      for (const attr of Object.keys(value)) {
        const attrValue = value[attr];
        if (attrValue === null || attrValue === undefined || attrValue === false) continue;
        node.setAttribute(attr, attrValue === true ? '' : String(attrValue));
      }
      continue;
    }
    if (key === 'data') {
      for (const name of Object.keys(value)) {
        const dataValue = value[name];
        if (dataValue === null || dataValue === undefined) continue;
        node.dataset[name] = String(dataValue);
      }
      continue;
    }
    if (key === 'style') {
      for (const name of Object.keys(value)) {
        const cssValue = value[name];
        if (cssValue === null || cssValue === undefined) continue;
        if (name.includes('-')) node.style.setProperty(name, String(cssValue));
        else node.style[name] = String(cssValue);
      }
      continue;
    }
    if (key === 'on') {
      for (const type of Object.keys(value)) {
        const handler = value[type];
        if (typeof handler === 'function') node.addEventListener(type, handler);
      }
      continue;
    }
    if (key === 'ref') {
      if (typeof value === 'function') value(node);
      continue;
    }
    if (key in node) {
      try {
        node[key] = value;
        continue;
      } catch (err) {
        // Read-only property (e.g. `type` on an input after creation): fall through.
      }
    }
    node.setAttribute(key, value === true ? '' : String(value));
  }
}

/** Append children, flattening arrays and skipping falsy values. */
function appendChildren(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false || child === '') continue;
    if (Array.isArray(child)) {
      appendChildren(node, child);
      continue;
    }
    if (child instanceof Node) {
      node.appendChild(child);
      continue;
    }
    node.appendChild(document.createTextNode(String(child)));
  }
}

/**
 * Create an SVG element. SVG needs its own namespace, so `el` cannot be used.
 * @param {string} tag
 * @param {?Object} [attrs]
 * @param {...(Node|string|number|null)} children
 * @returns {SVGElement}
 */
export function svgEl(tag, attrs, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  if (attrs) {
    for (const key of Object.keys(attrs)) {
      const value = attrs[key];
      if (value === undefined || value === null || value === false) continue;
      node.setAttribute(key, String(value));
    }
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (child instanceof Node) node.appendChild(child);
    else node.appendChild(document.createTextNode(String(child)));
  }
  return node;
}

/** @returns {HTMLElement} A fragment-like `<div>` wrapper. */
export function frag(...children) {
  const node = document.createDocumentFragment();
  appendChildren(node, children);
  return node;
}

/**
 * Remove every child of a node.
 * @param {?Node} node
 */
export function clear(node) {
  if (!node) return;
  while (node.firstChild) node.removeChild(node.firstChild);
}

/** `document.querySelector`, typed for convenience. */
export function qs(selector, root) {
  return (root || document).querySelector(selector);
}

/** `document.querySelectorAll` as a real array. */
export function qsa(selector, root) {
  return Array.from((root || document).querySelectorAll(selector));
}

/**
 * Join class names, dropping falsy entries.
 * @param {...(string|false|null|undefined)} parts
 * @returns {string}
 */
export function cx(...parts) {
  return parts.filter(Boolean).join(' ');
}

// --------------------------------------------------------------------------- //
// Icons
// --------------------------------------------------------------------------- //

/**
 * Icon bodies on a 24×24 grid, drawn as strokes so they inherit `currentColor`
 * and line up with the 1.75 px stroke weight used across the product.
 */
const ICONS = {
  dashboard:
    '<rect x="3.5" y="3.5" width="7" height="9" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="5" rx="1.5"/><rect x="13.5" y="11.5" width="7" height="9" rx="1.5"/><rect x="3.5" y="15.5" width="7" height="5" rx="1.5"/>',
  book:
    '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v18H6.5A2.5 2.5 0 0 1 4 18.5z"/><path d="M4 17.5A2.5 2.5 0 0 1 6.5 15H19"/>',
  bookOpen:
    '<path d="M12 6.5C10.5 5 8.5 4.5 4.5 4.5v13c4 0 6 .5 7.5 2 1.5-1.5 3.5-2 7.5-2v-13c-4 0-6 .5-7.5 2z"/><path d="M12 6.5v13"/>',
  books:
    '<path d="M5 4h4v16H5z"/><path d="M10 4h4v16h-4z"/><path d="M16.4 4.6l3.2 15.2"/>',
  users:
    '<path d="M16 20v-1.5a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4V20"/><circle cx="9.5" cy="7" r="3.5"/><path d="M21 20v-1.5a4 4 0 0 0-3-3.87"/><path d="M15.5 3.6a3.5 3.5 0 0 1 0 6.8"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 20v-1a5 5 0 0 1 5-5h6a5 5 0 0 1 5 5v1"/>',
  list:
    '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
  swap: '<path d="M4 8.5h13l-3.2-3.2"/><path d="M20 15.5H7l3.2 3.2"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  calendar:
    '<rect x="3.5" y="5.5" width="17" height="15" rx="1.5"/><path d="M3.5 10h17"/><path d="M8 3.5V7M16 3.5V7"/>',
  bookmark: '<path d="M6 4h12v17l-6-4.5L6 21z"/>',
  chart:
    '<path d="M4 20h16"/><path d="M7 20v-6"/><path d="M12 20V7"/><path d="M17 20v-9"/>',
  tag:
    '<path d="M20.4 13.2l-7.2 7.2a1.7 1.7 0 0 1-2.4 0L3.6 13.3V4.4h8.9l7.9 7.9a.9.9 0 0 1 0 1.3z"/><circle cx="7.6" cy="8.4" r="1.4"/>',
  settings:
    '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  shield: '<path d="M12 3l7.5 3v6c0 4.2-3 7.6-7.5 9-4.5-1.4-7.5-4.8-7.5-9V6z"/>',
  database:
    '<ellipse cx="12" cy="6" rx="7.5" ry="3"/><path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6"/><path d="M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3"/>',
  archive:
    '<rect x="3" y="4" width="18" height="4.5" rx="1"/><path d="M5 8.5V20h14V8.5"/><path d="M10 12.5h4"/>',
  help:
    '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.4 2.3c-.7.3-1 .9-1 1.6v.3"/><circle cx="12" cy="16.8" r="0.7" fill="currentColor" stroke="none"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.4 15.4L20 20"/>',
  bell:
    '<path d="M18 15.5V10a6 6 0 1 0-12 0v5.5L4.5 18h15z"/><path d="M9.8 21a2.4 2.4 0 0 0 4.4 0"/>',
  sun:
    '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4"/>',
  moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5z"/>',
  monitor:
    '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
  chevronDown: '<path d="M6 9.5l6 6 6-6"/>',
  chevronUp: '<path d="M6 14.5l6-6 6 6"/>',
  chevronLeft: '<path d="M14.5 6l-6 6 6 6"/>',
  chevronRight: '<path d="M9.5 6l6 6-6 6"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M4.5 12.5l5 5 10-11"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  more:
    '<circle cx="5.5" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none"/><circle cx="18.5" cy="12" r="1.3" fill="currentColor" stroke="none"/>',
  alert:
    '<path d="M12 4.2l8.6 15.3H3.4z"/><path d="M12 9.8v4.4"/><circle cx="12" cy="17" r="0.7" fill="currentColor" stroke="none"/>',
  alertCircle:
    '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.4V13"/><circle cx="12" cy="16.4" r="0.7" fill="currentColor" stroke="none"/>',
  info:
    '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.6"/><circle cx="12" cy="7.8" r="0.7" fill="currentColor" stroke="none"/>',
  checkCircle: '<circle cx="12" cy="12" r="8.5"/><path d="M8 12.3l2.7 2.7 5.3-5.6"/>',
  xCircle: '<circle cx="12" cy="12" r="8.5"/><path d="M9.2 9.2l5.6 5.6M14.8 9.2l-5.6 5.6"/>',
  download: '<path d="M12 4v10"/><path d="M8 10.5l4 4 4-4"/><path d="M5 19.5h14"/>',
  upload: '<path d="M12 15V5"/><path d="M8 8.5l4-4 4 4"/><path d="M5 19.5h14"/>',
  printer:
    '<path d="M7 8.5V4h10v4.5"/><rect x="4" y="8.5" width="16" height="7" rx="1.5"/><path d="M7 13.5h10V20H7z"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 4.5V9h-4.5"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  logout:
    '<path d="M14 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19H14"/><path d="M17 8.5l3.5 3.5L17 15.5"/><path d="M10 12h10"/>',
  lock:
    '<rect x="4.5" y="10.5" width="15" height="9.5" rx="1.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
  key:
    '<circle cx="8" cy="12" r="3.5"/><path d="M11.5 12H20"/><path d="M17 12v3M20 12v2.5"/>',
  globe:
    '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><path d="M12 3.5c2.2 2.4 3.3 5.3 3.3 8.5S14.2 18.1 12 20.5c-2.2-2.4-3.3-5.3-3.3-8.5S9.8 5.9 12 3.5z"/>',
  filter: '<path d="M4 5.5h16l-6.3 7.6V20l-3.4-2v-4.9z"/>',
  arrowUp: '<path d="M12 19V5"/><path d="M6.5 10.5L12 5l5.5 5.5"/>',
  arrowDown: '<path d="M12 5v14"/><path d="M6.5 13.5L12 19l5.5-5.5"/>',
  trash:
    '<path d="M4.5 7h15"/><path d="M9.5 7V4.5h5V7"/><path d="M6.5 7l1 12.5h9L17.5 7"/><path d="M10.5 11v5M13.5 11v5"/>',
  edit:
    '<path d="M4.5 19.5h4l10-10a2.1 2.1 0 0 0-3-3l-10 10z"/><path d="M14.5 6.5l3 3"/>',
  copy:
    '<rect x="8.5" y="8.5" width="11" height="11" rx="1.5"/><path d="M15.5 8.5v-3A1.5 1.5 0 0 0 14 4H6a1.5 1.5 0 0 0-1.5 1.5V14A1.5 1.5 0 0 0 6 15.5h2.5"/>',
  qr:
    '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><path d="M14 14h2.5v2.5H14zM18 18h2v2h-2zM14 19.5h1.5V21H14z"/>',
  barcode:
    '<path d="M4 6v12M7 6v12M10.5 6v12M13.5 6v12M16.5 6v12M20 6v12"/>',
  folder:
    '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2.5h7.5A1.5 1.5 0 0 1 20 10v7.5A1.5 1.5 0 0 1 18.5 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
  wifiOff:
    '<path d="M3 4l18 16"/><path d="M8.5 13.5a5 5 0 0 1 3-1.4"/><path d="M5.5 10a9 9 0 0 1 2.6-1.6"/><path d="M18.5 10a9 9 0 0 0-6.2-2.4"/><circle cx="12" cy="18" r="0.9" fill="currentColor" stroke="none"/>',
  eye:
    '<path d="M2.5 12S6 6.5 12 6.5 21.5 12 21.5 12 18 17.5 12 17.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.7"/>',
  save:
    '<path d="M5 4.5h11L19.5 8v11.5H5z"/><path d="M8.5 4.5v5h7v-5"/><rect x="8.5" y="13" width="7" height="6.5"/>',
  external:
    '<path d="M14 4.5h5.5V10"/><path d="M19 5l-8 8"/><path d="M18 14v5.5H4.5V6H10"/>',
  card:
    '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M2.5 10.5h19"/>',
  spark:
    '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/>',
};

/**
 * Build an inline icon.
 *
 * @param {string} name Key of {@link ICONS}.
 * @param {number} [size] Pixel size; defaults to 20, the sidebar/button size.
 * @param {string} [extraClass]
 * @returns {SVGElement}
 */
export function icon(name, size, extraClass) {
  const body = ICONS[name] || ICONS.info;
  const node = svgEl('svg', {
    viewBox: '0 0 24 24',
    width: size || 20,
    height: size || 20,
    fill: 'none',
    stroke: 'currentColor',
    'stroke-width': '1.75',
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
    'aria-hidden': 'true',
    focusable: 'false',
    class: cx('icon', extraClass),
  });
  // Constant, developer-authored markup — no interpolation, no user data.
  node.innerHTML = body;
  return node;
}

/** @returns {boolean} Whether an icon with this name exists. */
export function hasIcon(name) {
  return Object.prototype.hasOwnProperty.call(ICONS, name);
}

// --------------------------------------------------------------------------- //
// Layer management (modal, drawer, palette share one Esc stack)
// --------------------------------------------------------------------------- //

/** Open layers, topmost last. Esc closes only the last entry. */
const layerStack = [];

/** Global keydown handler, installed on the first layer. */
let layerKeyHandler = null;

/** Remember the element that had focus before a layer opened. */
function pushLayer(entry) {
  layerStack.push(entry);
  if (!layerKeyHandler) {
    layerKeyHandler = (event) => {
      if (event.key !== 'Escape') return;
      const top = layerStack[layerStack.length - 1];
      if (!top || !top.onEscape) return;
      event.preventDefault();
      event.stopPropagation();
      top.onEscape();
    };
    document.addEventListener('keydown', layerKeyHandler, true);
  }
}

/** Remove a layer from the stack, restoring focus when the stack empties. */
function popLayer(entry) {
  const index = layerStack.indexOf(entry);
  if (index >= 0) layerStack.splice(index, 1);
  if (!layerStack.length && layerKeyHandler) {
    document.removeEventListener('keydown', layerKeyHandler, true);
    layerKeyHandler = null;
  }
}

/** @returns {boolean} True when any layer is open. */
export function hasOpenLayer() {
  return layerStack.length > 0;
}

/** The focusable descendants of a container, in tab order. */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

/** @returns {HTMLElement[]} Focusable elements inside `root`, visible ones only. */
function focusables(root) {
  return qsa(FOCUSABLE, root).filter((node) => {
    if (node.hasAttribute('hidden')) return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 || rect.height > 0 || node === document.activeElement;
  });
}

/**
 * Trap Tab inside a container. Returns a release function.
 * @param {HTMLElement} root
 * @returns {() => void}
 */
export function trapFocus(root) {
  const handler = (event) => {
    if (event.key !== 'Tab') return;
    const items = focusables(root);
    if (!items.length) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !root.contains(active))) {
      event.preventDefault();
      last.focus();
      return;
    }
    if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };
  root.addEventListener('keydown', handler);
  return () => root.removeEventListener('keydown', handler);
}

/** Focus the first sensible control in a container. */
export function focusFirst(root) {
  const items = focusables(root);
  const target = items.find((node) => !node.dataset.noAutofocus) || items[0];
  if (target) target.focus();
}

// --------------------------------------------------------------------------- //
// Buttons
// --------------------------------------------------------------------------- //

/**
 * A button.
 *
 * @param {Object} options
 * @param {string} [options.label] Visible text; omit for icon-only.
 * @param {string} [options.icon] Icon name drawn before the label.
 * @param {string} [options.iconRight] Icon name drawn after the label.
 * @param {'primary'|'secondary'|'ghost'|'danger'|'danger-solid'} [options.variant]
 * @param {'sm'} [options.size]
 * @param {Function} [options.onClick]
 * @param {boolean} [options.disabled]
 * @param {boolean} [options.loading] Shows a spinner and blocks clicks.
 * @param {boolean} [options.block] Full width.
 * @param {string} [options.ariaLabel] Required when there is no visible label.
 * @param {string} [options.title] Native tooltip; defaults to the label.
 * @param {string} [options.type] Defaults to `'button'` so forms never submit by
 *   accident — the classic bug where Enter in a search box reloads the page.
 * @returns {HTMLButtonElement}
 */
export function button(options) {
  const opts = options || {};
  const variant = opts.variant || 'secondary';
  const classes = cx(
    'btn',
    `btn--${variant}`,
    opts.size === 'sm' && 'btn--sm',
    !opts.label && 'btn--icon',
    opts.block && 'btn--block',
    opts.className,
  );
  const node = el('button', {
    class: classes,
    attrs: {
      type: opts.type || 'button',
      'aria-label': opts.label ? null : opts.ariaLabel || opts.title || null,
      title: opts.title || (opts.label ? null : opts.ariaLabel) || null,
      'aria-busy': opts.loading ? 'true' : null,
      'aria-disabled': opts.disabled && opts.loading ? 'true' : null,
    },
    disabled: Boolean(opts.disabled) || Boolean(opts.loading),
    on: { click: opts.onClick },
  });
  if (opts.loading) node.appendChild(el('span', { class: 'btn__spinner', attrs: { 'aria-hidden': 'true' } }));
  else if (opts.icon) node.appendChild(icon(opts.icon, 16));
  if (opts.label) node.appendChild(el('span', { text: opts.label }));
  if (opts.iconRight && !opts.loading) node.appendChild(icon(opts.iconRight, 16));
  return node;
}

/**
 * An icon-only button.
 *
 * `label` becomes the accessible name *and* the tooltip, so an icon-only
 * control can never ship without one.
 *
 * @param {Object} options Same as {@link button}, but `label` is not rendered.
 * @returns {HTMLButtonElement}
 */
export function iconButton(options) {
  const opts = options || {};
  const accessible = opts.ariaLabel || opts.label || opts.title || '';
  return button(Object.assign({}, opts, {
    variant: opts.variant || 'ghost',
    label: '',
    ariaLabel: accessible,
    title: opts.title || accessible,
  }));
}

/** A link styled as a button. */
export function linkButton(options) {
  const opts = options || {};
  const node = el('a', {
    class: cx('btn', `btn--${opts.variant || 'ghost'}`, opts.size === 'sm' && 'btn--sm', !opts.label && 'btn--icon'),
    href: opts.href || '#',
    attrs: { 'aria-label': opts.label ? null : opts.ariaLabel || null },
    on: { click: opts.onClick },
  });
  if (opts.icon) node.appendChild(icon(opts.icon, 16));
  if (opts.label) node.appendChild(el('span', { text: opts.label }));
  return node;
}

// --------------------------------------------------------------------------- //
// Form controls
// --------------------------------------------------------------------------- //

/** Monotonic counter for `id`/`for` pairs. */
let uid = 0;

/** @returns {string} A unique element id. */
export function nextId(prefix) {
  uid += 1;
  return `${prefix || 'f'}${uid}`;
}

/**
 * A labelled form row.
 *
 * Wires `for`/`id`, `aria-describedby` and `aria-invalid` so the error is
 * announced with the field rather than floating next to it.
 *
 * @param {Object} options
 * @param {string} [options.label]
 * @param {string} [options.hint] Helper text under the control.
 * @param {string} [options.error] Error text; switches the row to the error style.
 * @param {boolean} [options.required]
 * @param {HTMLElement} options.control
 * @param {string} [options.id]
 * @param {boolean} [options.wide]
 * @returns {HTMLElement}
 */
export function field(options) {
  const opts = options || {};
  const control = opts.control;
  const id = opts.id || (control && control.id) || nextId('field');
  if (control && !control.id) control.id = id;

  const describedBy = [];
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;

  const wrap = el('div', { class: cx('field', opts.error && 'field--error', opts.wide && 'field--wide') });

  if (opts.label) {
    wrap.appendChild(el('label', {
      class: 'field__label',
      attrs: { for: id },
      text: opts.label,
    }, opts.required ? el('span', { class: 'field__req', text: '*', attrs: { 'aria-hidden': 'true' } }) : null));
  }

  wrap.appendChild(control);

  if (opts.hint) {
    describedBy.push(hintId);
    wrap.appendChild(el('p', { class: 'field__hint', attrs: { id: hintId }, text: opts.hint }));
  }
  if (opts.error) {
    describedBy.push(errorId);
    wrap.appendChild(el('p', {
      class: 'field__error',
      attrs: { id: errorId, role: 'alert' },
    }, icon('alertCircle', 14), el('span', { text: opts.error })));
  }

  if (describedBy.length) control.setAttribute('aria-describedby', describedBy.join(' '));
  if (opts.error) control.setAttribute('aria-invalid', 'true');
  if (opts.required) control.setAttribute('aria-required', 'true');
  return wrap;
}

/**
 * A text input.
 *
 * @param {Object} options
 * @param {string} [options.value]
 * @param {string} [options.placeholder]
 * @param {string} [options.type]
 * @param {string} [options.mono] Monospaced (barcodes, codes).
 * @param {boolean} [options.large]
 * @param {Function} [options.onInput]
 * @param {Function} [options.onChange]
 * @param {Function} [options.onEnter]
 * @param {Function} [options.onBlur]
 * @param {string} [options.autocomplete]
 * @returns {HTMLInputElement}
 */
export function input(options) {
  const opts = options || {};
  const node = el('input', {
    class: cx('input', opts.mono && 'input--mono', opts.large && 'input--lg', opts.className),
    attrs: {
      type: opts.type || 'text',
      placeholder: opts.placeholder || null,
      inputmode: opts.inputMode || null,
      autocomplete: opts.autocomplete || 'off',
      spellcheck: opts.spellcheck === false ? 'false' : null,
      maxlength: opts.maxLength || null,
      min: opts.min !== undefined ? opts.min : null,
      max: opts.max !== undefined ? opts.max : null,
      step: opts.step !== undefined ? opts.step : null,
      name: opts.name || null,
      'aria-label': opts.ariaLabel || null,
      readonly: opts.readOnly || null,
    },
    value: opts.value === undefined || opts.value === null ? '' : String(opts.value),
    disabled: Boolean(opts.disabled),
    on: {
      input: opts.onInput,
      change: opts.onChange,
      blur: opts.onBlur,
      keydown: opts.onEnter
        ? (event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            opts.onEnter(event);
          }
        }
        : null,
    },
  });
  return node;
}

/**
 * A `<select>`.
 * @param {Object} options
 * @param {Array<{value: string, label: string, disabled?: boolean}>} options.options
 * @param {string} [options.value]
 * @param {Function} [options.onChange]
 * @param {string} [options.placeholder] Renders a disabled empty first option.
 * @returns {HTMLSelectElement}
 */
export function select(options) {
  const opts = options || {};
  const node = el('select', {
    class: cx('select', opts.className),
    attrs: { 'aria-label': opts.ariaLabel || null },
    disabled: Boolean(opts.disabled),
    on: { change: opts.onChange },
  });
  if (opts.placeholder) {
    node.appendChild(el('option', {
      attrs: { value: '', disabled: true, selected: !opts.value },
      text: opts.placeholder,
    }));
  }
  for (const item of opts.options || []) {
    node.appendChild(el('option', {
      attrs: {
        value: String(item.value),
        disabled: item.disabled || null,
        selected: String(item.value) === String(opts.value === undefined ? '' : opts.value),
      },
      text: item.label,
    }));
  }
  return node;
}

/**
 * A textarea.
 * @param {Object} options
 * @returns {HTMLTextAreaElement}
 */
export function textarea(options) {
  const opts = options || {};
  return el('textarea', {
    class: cx('textarea', opts.className),
    attrs: {
      rows: opts.rows || 3,
      placeholder: opts.placeholder || null,
      maxlength: opts.maxLength || null,
      'aria-label': opts.ariaLabel || null,
    },
    value: opts.value === undefined || opts.value === null ? '' : String(opts.value),
    disabled: Boolean(opts.disabled),
    on: { input: opts.onInput, change: opts.onChange, blur: opts.onBlur },
  });
}

/**
 * A checkbox with a clickable label.
 * @param {Object} options
 * @param {string} [options.label]
 * @param {boolean} [options.checked]
 * @param {Function} [options.onChange] Receives the new boolean.
 * @returns {HTMLElement} The `<label class="check">` wrapper.
 */
export function checkbox(options) {
  const opts = options || {};
  const box = el('input', {
    attrs: { type: 'checkbox', 'aria-label': opts.label || opts.ariaLabel || null },
    checked: Boolean(opts.checked),
    disabled: Boolean(opts.disabled),
    on: {
      change: opts.onChange ? (event) => opts.onChange(event.target.checked, event) : null,
    },
  });
  return el('label', { class: cx('check', opts.className) }, box,
    opts.label ? el('span', { text: opts.label }) : null);
}

/**
 * A switch, for settings that take effect immediately.
 * @param {Object} options
 * @returns {HTMLElement} The `<label class="switch">` wrapper.
 */
export function switchControl(options) {
  const opts = options || {};
  const box = el('input', {
    attrs: {
      type: 'checkbox',
      role: 'switch',
      'aria-label': opts.label || opts.ariaLabel || null,
    },
    checked: Boolean(opts.checked),
    disabled: Boolean(opts.disabled),
    on: { change: opts.onChange ? (event) => opts.onChange(event.target.checked, event) : null },
  });
  return el('label', { class: cx('switch', opts.className) }, box,
    opts.label ? el('span', { text: opts.label }) : null);
}

/**
 * An input with a trailing button, e.g. a search box with a clear control.
 * @param {Object} options
 * @param {HTMLElement} options.control
 * @param {HTMLElement} [options.action]
 * @returns {HTMLElement}
 */
export function inputGroup(options) {
  return el('div', { class: cx('input-group', options && options.className) },
    options.control, options.action || null);
}

// --------------------------------------------------------------------------- //
// Surfaces
// --------------------------------------------------------------------------- //

/**
 * A card surface: 1 px hairline, 12 px radius, no shadow at rest.
 *
 * @param {Object} options
 * @param {string} [options.title]
 * @param {string} [options.subtitle]
 * @param {HTMLElement|HTMLElement[]} [options.actions]
 * @param {HTMLElement|HTMLElement[]} [options.body]
 * @param {HTMLElement|HTMLElement[]} [options.foot]
 * @param {boolean} [options.flush] Body with no padding (for tables).
 * @param {boolean} [options.pad] Card itself padded, no header.
 * @param {boolean} [options.hover]
 * @param {string} [options.className]
 * @returns {HTMLElement}
 */
export function card(options) {
  const opts = options || {};
  const node = el('section', {
    class: cx('card', opts.pad && 'card--pad', opts.hover && 'card--hover', opts.className),
  });
  if (opts.title || opts.actions) {
    node.appendChild(el('header', { class: 'card__head' },
      el('div', {},
        opts.title ? el('h2', { class: 'card__title', text: opts.title }) : null,
        opts.subtitle ? el('p', { class: 'card__sub', text: opts.subtitle }) : null),
      opts.actions ? el('div', { class: 'u-row' }, opts.actions) : null));
  }
  if (opts.body) {
    node.appendChild(el('div', { class: cx('card__body', opts.flush && 'card__body--flush') }, opts.body));
  }
  if (opts.foot) {
    node.appendChild(el('footer', { class: 'card__foot' }, opts.foot));
  }
  return node;
}

/**
 * A KPI tile.
 *
 * @param {Object} options
 * @param {string} options.label
 * @param {string} options.value Pre-formatted (money, count, percentage).
 * @param {{value: string, tone: 'up'|'down'|'flat', label?: string}} [options.delta]
 * @param {HTMLElement} [options.spark] A `charts.sparkline()` node.
 * @param {Function} [options.onClick] Makes the whole tile a button.
 * @returns {HTMLElement}
 */
export function statCard(options) {
  const opts = options || {};
  const node = el(opts.onClick ? 'button' : 'div', {
    class: cx('stat', opts.onClick && 'stat--link'),
    attrs: opts.onClick ? { type: 'button' } : null,
    on: { click: opts.onClick },
  });
  node.appendChild(el('span', { class: 'stat__label', text: opts.label }));
  const row = el('div', { class: 'stat__row' });
  row.appendChild(el('span', { class: 'stat__value', text: opts.value }));
  if (opts.delta) {
    const tone = opts.delta.tone || 'flat';
    row.appendChild(el('span', {
      class: `stat__delta stat__delta--${tone}`,
      attrs: { title: opts.delta.label || null },
    }, icon(tone === 'up' ? 'arrowUp' : tone === 'down' ? 'arrowDown' : 'minus', 12),
    el('span', { text: opts.delta.value })));
  }
  node.appendChild(row);
  if (opts.spark) {
    const holder = el('div', { class: 'stat__spark' });
    holder.appendChild(opts.spark);
    node.appendChild(holder);
  }
  return node;
}

/**
 * A two-column definition list.
 * @param {Array<{label: string, value: (string|Node)}>} rows
 * @returns {HTMLElement}
 */
export function kv(rows) {
  const node = el('dl', { class: 'kv' });
  for (const row of rows || []) {
    node.appendChild(el('dt', { text: row.label }));
    const dd = el('dd');
    if (row.value instanceof Node) dd.appendChild(row.value);
    else dd.textContent = row.value === null || row.value === undefined ? t('common.na') : String(row.value);
    node.appendChild(dd);
  }
  return node;
}

/**
 * A page section with a title and optional actions.
 * @param {Object} options
 * @returns {HTMLElement}
 */
export function section(options) {
  const opts = options || {};
  const node = el('section', { class: 'section' });
  node.appendChild(el('div', { class: 'section__head' },
    el('h2', { class: 'section__title', text: opts.title }),
    opts.actions ? el('div', { class: 'u-row' }, opts.actions) : null));
  if (opts.body) node.appendChild(opts.body);
  return node;
}

// --------------------------------------------------------------------------- //
// Status vocabulary
// --------------------------------------------------------------------------- //

/** Status → pill tone. Unknown statuses fall back to muted. */
const STATUS_TONES = Object.freeze({
  available: 'success',
  active: 'success',
  returned: 'success',
  paid: 'success',
  fulfilled: 'success',
  ready: 'success',
  complete: 'success',
  on_loan: 'primary',
  issued: 'primary',
  reserved: 'primary',
  pending: 'primary',
  open: 'primary',
  overdue: 'danger',
  lost: 'danger',
  damaged: 'danger',
  suspended: 'danger',
  failed: 'danger',
  held: 'warning',
  expiring: 'warning',
  renewed: 'warning',
  withdrawn: 'muted',
  expired: 'muted',
  cancelled: 'muted',
  waived: 'muted',
  inactive: 'muted',
  disabled: 'muted',
});

/**
 * A status pill.
 *
 * @param {string} status Raw server value, e.g. `'on_loan'`.
 * @param {{label?: string, tone?: string, large?: boolean, plain?: boolean}} [options]
 * @returns {HTMLElement}
 */
export function statusPill(status, options) {
  const opts = options || {};
  const key = String(status || '').toLowerCase();
  // The server speaks snake_case; the catalogue is camelCase. Translating the
  // key here keeps every catalogue readable instead of littered with
  // `status.on_loan`-style names.
  const labelKey = key.replace(/_([a-z0-9])/g, (whole, letter) => letter.toUpperCase());
  const tone = opts.tone || STATUS_TONES[key] || 'muted';
  const label = opts.label || (key ? t(`status.${labelKey}`) : t('common.unknown'));
  return el('span', {
    class: cx('pill', `pill--${tone}`, opts.large && 'pill--lg', opts.plain && 'pill--plain'),
    attrs: { title: label },
    text: label,
  });
}

/**
 * A generic pill.
 * @param {string} label
 * @param {string} [tone] `'primary' | 'success' | 'warning' | 'danger' | 'muted'`
 * @param {Object} [options]
 * @returns {HTMLElement}
 */
export function pill(label, tone, options) {
  const opts = options || {};
  return el('span', {
    class: cx('pill', `pill--${tone || 'muted'}`, opts.large && 'pill--lg', opts.plain && 'pill--plain'),
    attrs: { title: opts.title || null },
    text: label,
  });
}

// --------------------------------------------------------------------------- //
// Filter chips
// --------------------------------------------------------------------------- //

/**
 * A single-select or multi-select filter chip bar.
 *
 * @param {Object} options
 * @param {Array<{value: string, label: string, count?: number}>} options.items
 * @param {string|string[]} [options.value] Active value(s); `''` means "all".
 * @param {boolean} [options.multiple]
 * @param {boolean} [options.allowAll] Render an "All" chip; default true for single.
 * @param {(next: string|string[]) => void} options.onChange
 * @returns {HTMLElement}
 */
export function chips(options) {
  const opts = options || {};
  const multiple = Boolean(opts.multiple);
  const allowAll = opts.allowAll !== false && !multiple;
  const active = multiple
    ? new Set(Array.isArray(opts.value) ? opts.value : [])
    : new Set([opts.value || '']);

  const node = el('div', { class: 'chips', attrs: { role: 'group' } });

  const makeChip = (value, label, count) => {
    const pressed = active.has(value);
    const chip = el('button', {
      class: 'chip',
      attrs: { type: 'button', 'aria-pressed': pressed ? 'true' : 'false' },
      on: {
        click: () => {
          if (multiple) {
            const next = new Set(active);
            if (next.has(value)) next.delete(value);
            else next.add(value);
            opts.onChange(Array.from(next));
            return;
          }
          opts.onChange(value);
        },
      },
    }, el('span', { text: label }));
    if (typeof count === 'number') {
      chip.appendChild(el('span', { class: 'chip__count u-num', text: String(count) }));
    }
    return chip;
  };

  if (allowAll) node.appendChild(makeChip('', t('common.all')));
  for (const item of opts.items || []) {
    node.appendChild(makeChip(String(item.value), item.label, item.count));
  }
  return node;
}

// --------------------------------------------------------------------------- //
// Table
// --------------------------------------------------------------------------- //

/**
 * A data table.
 *
 * The header is `position: sticky` and the wrapper scrolls, so a 500-row list
 * keeps its column labels while the body moves. Sorting is announced through
 * `aria-sort`; the caret is pure CSS off that attribute.
 *
 * @param {Object} options
 * @param {Array<Object>} options.columns
 *   `{key, label, align?: 'right'|'center', sortable?: boolean, width?: string,
 *     className?: string, render?: (row, index) => (Node|string)}`
 * @param {Array<Object>} options.rows
 * @param {(row: Object, index: number) => string} [options.rowKey]
 * @param {{key: string, dir: 'asc'|'desc'}} [options.sort]
 * @param {(key: string) => void} [options.onSort]
 * @param {(row: Object) => void} [options.onRowClick]
 * @param {boolean} [options.loading]
 * @param {number} [options.skeletonRows]
 * @param {Object} [options.empty] `{title, body, action, icon}`
 * @param {boolean} [options.scroll] Cap the height so the header sticks.
 * @param {boolean} [options.compact]
 * @param {string} [options.caption]
 * @param {HTMLElement} [options.toolbar] Rendered above the table inside the card.
 * @returns {HTMLElement}
 */
export function table(options) {
  const opts = options || {};
  const columns = opts.columns || [];
  const rows = opts.rows || [];
  const sort = opts.sort || { key: '', dir: 'asc' };
  const skeletonRows = Number.isInteger(opts.skeletonRows) ? opts.skeletonRows : 6;

  const cardNode = el('div', { class: 'card' });
  if (opts.toolbar) cardNode.appendChild(opts.toolbar);

  if (opts.loading && !rows.length) {
    const body = el('div', { attrs: { 'aria-busy': 'true', 'aria-live': 'polite' } });
    for (let i = 0; i < skeletonRows; i += 1) {
      const row = el('div', { class: 'sk-row' });
      for (let c = 0; c < Math.min(columns.length || 4, 6); c += 1) {
        row.appendChild(el('div', { class: `sk sk--cell${c === 0 ? ' sk--wide' : ''}` }));
      }
      body.appendChild(row);
    }
    cardNode.appendChild(body);
    return cardNode;
  }

  if (!rows.length) {
    cardNode.appendChild(emptyState(opts.empty || {}));
    return cardNode;
  }

  const wrap = el('div', { class: cx('tbl-wrap', opts.scroll && 'tbl-wrap--scroll') });
  const tableNode = el('table', { class: cx('tbl', opts.compact && 'tbl--compact') });
  if (opts.caption) tableNode.appendChild(el('caption', { class: 'u-sr', text: opts.caption }));

  const thead = el('thead');
  const headRow = el('tr');
  for (const column of columns) {
    const sortable = Boolean(column.sortable && opts.onSort);
    const isSorted = sort.key === column.key;
    const th = el('th', {
      class: cx(
        column.align === 'right' && 'tbl__num',
        column.align === 'center' && 'tbl__center',
        column.className,
      ),
      attrs: {
        scope: 'col',
        style: column.width ? `width:${column.width}` : null,
        'aria-sort': isSorted ? (sort.dir === 'desc' ? 'descending' : 'ascending') : (sortable ? 'none' : null),
      },
    });
    if (sortable) {
      const sortButton = el('button', {
        class: 'tbl__sort',
        attrs: { type: 'button' },
        on: { click: () => opts.onSort(column.key) },
      }, el('span', { text: column.label }), el('span', { class: 'tbl__caret', attrs: { 'aria-hidden': 'true' } }));
      th.appendChild(sortButton);
    } else {
      th.textContent = column.label;
    }
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  tableNode.appendChild(thead);

  const tbody = el('tbody');
  rows.forEach((row, index) => {
    const tr = el('tr', {
      attrs: { tabindex: opts.onRowClick ? '0' : null },
      on: opts.onRowClick
        ? {
          click: (event) => {
            // Let real controls inside the row handle their own clicks.
            if (event.target.closest('button, a, input, select, label')) return;
            opts.onRowClick(row, index);
          },
          keydown: (event) => {
            if (event.key !== 'Enter' || event.target !== tr) return;
            event.preventDefault();
            opts.onRowClick(row, index);
          },
        }
        : null,
    });
    if (opts.onRowClick) tr.classList.add('tbl__row--clickable');
    for (const column of columns) {
      const td = el('td', {
        class: cx(
          column.align === 'right' && 'tbl__num',
          column.align === 'center' && 'tbl__center',
          column.cellClass,
        ),
      });
      const value = column.render ? column.render(row, index) : row[column.key];
      if (value instanceof Node) td.appendChild(value);
      else if (value === null || value === undefined || value === '') td.appendChild(document.createTextNode(t('common.na')));
      else td.textContent = String(value);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  });
  tableNode.appendChild(tbody);
  wrap.appendChild(tableNode);
  cardNode.appendChild(wrap);
  return cardNode;
}

// --------------------------------------------------------------------------- //
// Pagination
// --------------------------------------------------------------------------- //

/**
 * Build the page-number sequence: first, last, current ±1, with gaps.
 * @param {number} page
 * @param {number} pages
 * @returns {Array<number|'gap'>}
 */
function pageSequence(page, pages) {
  const wanted = new Set([1, pages, page, page - 1, page + 1]);
  const numbers = Array.from(wanted).filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
  const out = [];
  let previous = 0;
  for (const number of numbers) {
    if (previous && number - previous > 1) out.push('gap');
    out.push(number);
    previous = number;
  }
  return out;
}

/**
 * A pager: previous, numbered pages, next, plus a range summary.
 *
 * @param {Object} options
 * @param {number} options.page 1-based.
 * @param {number} options.pageSize
 * @param {number} options.total Total rows across all pages.
 * @param {(page: number) => void} options.onChange
 * @returns {HTMLElement}
 */
export function pagination(options) {
  const opts = options || {};
  const pageSize = Math.max(1, Number(opts.pageSize) || 25);
  const total = Math.max(0, Number(opts.total) || 0);
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(Math.max(1, Number(opts.page) || 1), pages);
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);

  const node = el('div', { class: 'u-row u-row--between u-grow' });

  node.appendChild(el('span', {
    class: 'u-xs u-muted u-num',
    text: t('common.showing', { from, to, total }),
  }));

  const pager = el('nav', { class: 'pager', attrs: { 'aria-label': t('common.page') } });

  pager.appendChild(el('button', {
    class: 'pager__btn',
    attrs: { type: 'button', 'aria-label': t('common.previous') },
    disabled: page <= 1,
    on: { click: () => opts.onChange(page - 1) },
  }, icon('chevronLeft', 16)));

  for (const entry of pageSequence(page, pages)) {
    if (entry === 'gap') {
      pager.appendChild(el('span', { class: 'pager__gap', text: '…' }));
      continue;
    }
    pager.appendChild(el('button', {
      class: 'pager__btn',
      attrs: {
        type: 'button',
        'aria-current': entry === page ? 'true' : null,
        'aria-label': `${t('common.page')} ${entry}`,
      },
      on: { click: () => opts.onChange(entry) },
    }, el('span', { text: String(entry) })));
  }

  pager.appendChild(el('button', {
    class: 'pager__btn',
    attrs: { type: 'button', 'aria-label': t('common.next') },
    disabled: page >= pages,
    on: { click: () => opts.onChange(page + 1) },
  }, icon('chevronRight', 16)));

  node.appendChild(pager);
  return node;
}

// --------------------------------------------------------------------------- //
// Tabs
// --------------------------------------------------------------------------- //

/**
 * A tab strip with roving tabindex and arrow-key navigation.
 *
 * @param {Object} options
 * @param {Array<{value: string, label: string, count?: number}>} options.items
 * @param {string} options.value
 * @param {(value: string) => void} options.onChange
 * @param {string} [options.ariaLabel]
 * @returns {HTMLElement}
 */
export function tabs(options) {
  const opts = options || {};
  const items = opts.items || [];
  const strip = el('div', {
    class: 'tabs',
    attrs: { role: 'tablist', 'aria-label': opts.ariaLabel || null },
  });

  const buttons = items.map((item, index) => {
    const selected = item.value === opts.value;
    const tab = el('button', {
      class: 'tab',
      attrs: {
        type: 'button',
        role: 'tab',
        'aria-selected': selected ? 'true' : 'false',
        tabindex: selected ? '0' : '-1',
        'data-value': item.value,
      },
      on: {
        click: () => opts.onChange(item.value),
        keydown: (event) => {
          const forward = event.key === 'ArrowRight' || event.key === 'ArrowDown';
          const back = event.key === 'ArrowLeft' || event.key === 'ArrowUp';
          if (!forward && !back) return;
          event.preventDefault();
          const next = (index + (forward ? 1 : -1) + items.length) % items.length;
          const target = buttons[next];
          buttons.forEach((node) => node.setAttribute('tabindex', '-1'));
          target.setAttribute('tabindex', '0');
          target.focus();
          opts.onChange(items[next].value);
        },
      },
    }, el('span', { text: item.label }));
    if (typeof item.count === 'number') {
      tab.appendChild(el('span', { class: 'tab__count u-num', text: String(item.count) }));
    }
    return tab;
  });

  buttons.forEach((node) => strip.appendChild(node));
  return strip;
}

// --------------------------------------------------------------------------- //
// Menu
// --------------------------------------------------------------------------- //

/** The currently open menu, if any. */
let openMenu = null;

/**
 * Open a dropdown menu anchored to an element.
 *
 * Positioned `fixed` so it escapes any `overflow: hidden` ancestor, and flipped
 * up when there is not enough room below.
 *
 * @param {Object} options
 * @param {HTMLElement} options.anchor
 * @param {Array<Object|string>} options.items
 *   Item: `{label, icon?, onClick?, danger?, disabled?, meta?, checked?}`;
 *   `'-'` for a separator; `{group: string}` for a section label.
 * @param {'left'|'right'} [options.align]
 * @param {() => void} [options.onClose]
 * @returns {{close: () => void, element: HTMLElement}}
 */
export function menu(options) {
  const opts = options || {};
  if (openMenu) openMenu.close();

  const node = el('div', { class: 'menu', attrs: { role: 'menu' } });
  const close = () => {
    node.remove();
    document.removeEventListener('mousedown', onOutside, true);
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('resize', close);
    window.removeEventListener('scroll', close, true);
    if (openMenu && openMenu.element === node) openMenu = null;
    if (opts.onClose) opts.onClose();
  };

  const onOutside = (event) => {
    if (node.contains(event.target) || opts.anchor.contains(event.target)) return;
    close();
  };
  const onKey = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      opts.anchor.focus();
    }
  };

  for (const item of opts.items || []) {
    if (item === '-') {
      node.appendChild(el('div', { class: 'menu__sep', attrs: { role: 'separator' } }));
      continue;
    }
    if (item.group) {
      node.appendChild(el('div', { class: 'menu__label', text: item.group }));
      continue;
    }
    const buttonNode = el('button', {
      class: cx('menu__item', item.danger && 'menu__item--danger'),
      attrs: { type: 'button', role: 'menuitem', disabled: item.disabled || null },
      on: {
        click: () => {
          close();
          if (item.onClick) item.onClick();
        },
      },
    });
    if (item.icon) buttonNode.appendChild(icon(item.icon, 16));
    else if (item.checked) buttonNode.appendChild(icon('check', 16));
    buttonNode.appendChild(el('span', { text: item.label }));
    if (item.meta) buttonNode.appendChild(el('span', { class: 'menu__meta', text: item.meta }));
    node.appendChild(buttonNode);
  }

  document.body.appendChild(node);

  // Position under the anchor, flipping up if the menu would overflow.
  const rect = opts.anchor.getBoundingClientRect();
  const menuRect = node.getBoundingClientRect();
  const alignRight = opts.align === 'right';
  let left = alignRight ? rect.right - menuRect.width : rect.left;
  left = Math.max(8, Math.min(left, window.innerWidth - menuRect.width - 8));
  let top = rect.bottom + 6;
  if (top + menuRect.height > window.innerHeight - 8) {
    top = Math.max(8, rect.top - menuRect.height - 6);
  }
  node.style.left = `${Math.round(left)}px`;
  node.style.top = `${Math.round(top)}px`;

  document.addEventListener('mousedown', onOutside, true);
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('resize', close);
  window.addEventListener('scroll', close, true);

  const first = node.querySelector('.menu__item');
  if (first) first.focus();

  openMenu = { element: node, close };
  return { close, element: node };
}

/** Close any open menu. */
export function closeMenu() {
  if (openMenu) openMenu.close();
}

// --------------------------------------------------------------------------- //
// Modal
// --------------------------------------------------------------------------- //

/**
 * Open a modal dialog.
 *
 * @param {Object} options
 * @param {string} options.title
 * @param {HTMLElement|HTMLElement[]} [options.body]
 * @param {HTMLElement|HTMLElement[]} [options.footer]
 * @param {boolean} [options.wide] 720 px instead of 560 px.
 * @param {boolean} [options.dismissible] Backdrop click closes; default true.
 * @param {() => (boolean|Promise<boolean>)} [options.guard]
 *   Called before closing. Return `true` to mean "there are unsaved changes" —
 *   the user is asked to confirm before the dialog disappears.
 * @param {() => void} [options.onClose]
 * @returns {{element: HTMLElement, body: HTMLElement, footer: HTMLElement,
 *            close: () => Promise<void>, setBusy: (busy: boolean) => void}}
 */
export function modal(options) {
  const opts = options || {};
  const opener = document.activeElement;

  const scrim = el('div', { class: 'scrim', attrs: { 'data-layer': 'scrim' } });
  const panel = el('div', {
    class: cx('modal', opts.wide && 'modal--720'),
    attrs: {
      role: 'dialog',
      'aria-modal': 'true',
      'aria-labelledby': 'modal-title',
    },
  });

  const title = el('h2', { class: 'modal__title', attrs: { id: 'modal-title' }, text: opts.title });
  const closeButton = iconButton({
    icon: 'x',
    label: t('common.close'),
    className: 'modal__close',
    onClick: () => {
      close();
    },
  });

  const body = el('div', { class: 'modal__body' }, opts.body);
  const footer = el('div', { class: 'modal__foot' }, opts.footer);

  panel.appendChild(el('header', { class: 'modal__head' }, title, closeButton));
  panel.appendChild(body);
  if (opts.footer) panel.appendChild(footer);

  document.body.appendChild(scrim);
  document.body.appendChild(panel);

  const releaseTrap = trapFocus(panel);
  let busy = false;
  let closed = false;

  const close = async () => {
    if (closed || busy) return;
    if (opts.guard) {
      let dirty = false;
      try {
        dirty = await opts.guard();
      } catch (err) {
        dirty = false;
      }
      if (dirty) {
        const leave = await confirmDialog({
          title: t('confirm.discardTitle'),
          body: t('confirm.discardBody'),
          confirmLabel: t('confirm.discard'),
          cancelLabel: t('confirm.keepEditing'),
          tone: 'danger',
        });
        if (!leave) return;
      }
    }
    closed = true;
    releaseTrap();
    popLayer(entry);
    scrim.remove();
    panel.remove();
    if (opener && typeof opener.focus === 'function') opener.focus();
    if (opts.onClose) opts.onClose();
  };

  const entry = { element: panel, onEscape: () => { close(); } };
  pushLayer(entry);

  scrim.addEventListener('mousedown', (event) => {
    if (event.target !== scrim) return;
    if (opts.dismissible === false) return;
    close();
  });

  const setBusy = (value) => {
    busy = Boolean(value);
    panel.setAttribute('aria-busy', busy ? 'true' : 'false');
  };

  // Focus the first control once the entry animation has settled enough.
  window.requestAnimationFrame(() => focusFirst(panel));

  return { element: panel, body, footer, close, setBusy };
}

// --------------------------------------------------------------------------- //
// Drawer
// --------------------------------------------------------------------------- //

/**
 * Open a right-hand drawer (480 px, 220 ms slide).
 *
 * Same contract as {@link modal}: returns handles, traps focus, honours Esc and
 * the optional unsaved-changes guard.
 *
 * @param {Object} options
 * @param {string} options.title
 * @param {HTMLElement|HTMLElement[]} [options.body]
 * @param {HTMLElement|HTMLElement[]} [options.footer]
 * @param {boolean} [options.wide] 560 px instead of 480 px.
 * @param {boolean} [options.dismissible] Backdrop click closes; default true.
 * @param {() => (boolean|Promise<boolean>)} [options.guard]
 * @param {() => void} [options.onClose]
 * @returns {{element: HTMLElement, body: HTMLElement, footer: HTMLElement,
 *            close: () => Promise<void>, setBusy: (busy: boolean) => void}}
 */
export function drawer(options) {
  const opts = options || {};
  const opener = document.activeElement;

  const scrim = el('div', { class: 'scrim scrim--drawer' });
  const panel = el('aside', {
    class: cx('drawer', opts.wide && 'drawer--wide'),
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'drawer-title' },
  });

  const title = el('h2', { class: 'drawer__title', attrs: { id: 'drawer-title' }, text: opts.title });
  const closeButton = iconButton({
    icon: 'x',
    label: t('common.close'),
    onClick: () => { close(); },
  });

  const body = el('div', { class: 'drawer__body' }, opts.body);
  const footer = el('div', { class: 'drawer__foot' }, opts.footer);

  panel.appendChild(el('header', { class: 'drawer__head' }, title, closeButton));
  panel.appendChild(body);
  if (opts.footer) panel.appendChild(footer);

  document.body.appendChild(scrim);
  document.body.appendChild(panel);

  const releaseTrap = trapFocus(panel);
  let busy = false;
  let closed = false;

  const close = async () => {
    if (closed || busy) return;
    if (opts.guard) {
      let dirty = false;
      try {
        dirty = await opts.guard();
      } catch (err) {
        dirty = false;
      }
      if (dirty) {
        const leave = await confirmDialog({
          title: t('confirm.discardTitle'),
          body: t('confirm.discardBody'),
          confirmLabel: t('confirm.discard'),
          cancelLabel: t('confirm.keepEditing'),
          tone: 'danger',
        });
        if (!leave) return;
      }
    }
    closed = true;
    releaseTrap();
    popLayer(entry);
    panel.classList.add('drawer--closing');
    scrim.remove();
    const done = () => {
      panel.remove();
      if (opener && typeof opener.focus === 'function') opener.focus();
      if (opts.onClose) opts.onClose();
    };
    // Match --dur-panel; fall back to a timer if the animation never fires.
    let settled = false;
    const settle = () => {
      if (settled) return;
      settled = true;
      done();
    };
    panel.addEventListener('animationend', settle, { once: true });
    window.setTimeout(settle, 260);
  };

  const entry = { element: panel, onEscape: () => { close(); } };
  pushLayer(entry);

  scrim.addEventListener('mousedown', (event) => {
    if (event.target !== scrim) return;
    if (opts.dismissible === false) return;
    close();
  });

  const setBusy = (value) => {
    busy = Boolean(value);
    panel.setAttribute('aria-busy', busy ? 'true' : 'false');
  };

  window.requestAnimationFrame(() => focusFirst(panel));
  return { element: panel, body, footer, close, setBusy };
}

// --------------------------------------------------------------------------- //
// Confirm dialog
// --------------------------------------------------------------------------- //

/**
 * Ask a yes/no question. Resolves `true` when confirmed.
 *
 * @param {Object} options
 * @param {string} options.title
 * @param {string} [options.body]
 * @param {string} [options.confirmLabel]
 * @param {string} [options.cancelLabel]
 * @param {'primary'|'danger'} [options.tone]
 * @returns {Promise<boolean>}
 */
export function confirmDialog(options) {
  const opts = options || {};
  return new Promise((resolve) => {
    let settled = false;
    const settle = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };

    const cancel = button({
      label: opts.cancelLabel || t('common.cancel'),
      onClick: () => {
        settle(false);
        dialog.close();
      },
    });
    const confirm = button({
      label: opts.confirmLabel || t('common.confirm'),
      variant: opts.tone === 'danger' ? 'danger-solid' : 'primary',
      onClick: () => {
        settle(true);
        dialog.close();
      },
    });

    const dialog = modal({
      title: opts.title,
      body: opts.body ? el('p', { class: 'u-sm', text: opts.body }) : null,
      footer: [cancel, confirm],
      dismissible: true,
      onClose: () => settle(false),
    });

    // Enter confirms — but never when focus is on Cancel, so a reflexive Enter
    // on a destructive dialog does not delete anything.
    dialog.element.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      if (event.target.closest('button')) return;
      event.preventDefault();
      settle(true);
      dialog.close();
    });

    window.requestAnimationFrame(() => confirm.focus());
  });
}

// --------------------------------------------------------------------------- //
// Toasts
// --------------------------------------------------------------------------- //

/** Cap: three at a time; the oldest is retired to make room. */
const TOAST_LIMIT = 3;

/** @returns {HTMLElement} The toast stack, created on first use. */
function toastHost() {
  let host = document.getElementById('toasts');
  if (!host) {
    host = el('div', {
      class: 'toasts',
      attrs: { id: 'toasts', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'false' },
    });
    document.body.appendChild(host);
  }
  return host;
}

/** Icon name per tone. */
const TOAST_ICONS = { success: 'checkCircle', error: 'xCircle', warning: 'alert', info: 'info' };

/**
 * Show a toast.
 *
 * @param {Object|string} options A string is treated as the title.
 * @param {string} [options.title]
 * @param {string} [options.text] Secondary line.
 * @param {'success'|'error'|'warning'|'info'} [options.tone]
 * @param {number} [options.timeout] Milliseconds; errors default to 8 s.
 * @param {{label: string, onClick: Function}} [options.action]
 * @returns {{close: () => void, element: HTMLElement}}
 */
export function toast(options) {
  const opts = typeof options === 'string' ? { title: options } : (options || {});
  const tone = opts.tone || 'info';
  const host = toastHost();

  // Retire the oldest so the stack never exceeds three.
  while (host.children.length >= TOAST_LIMIT) {
    host.firstElementChild.remove();
  }

  const node = el('div', {
    class: `toast toast--${tone}`,
    attrs: { role: tone === 'error' ? 'alert' : 'status' },
  });
  node.appendChild(el('span', { class: 'toast__icon' }, icon(TOAST_ICONS[tone] || 'info', 18)));

  const body = el('div', { class: 'toast__body' });
  if (opts.title) body.appendChild(el('p', { class: 'toast__title', text: opts.title }));
  if (opts.text) body.appendChild(el('p', { class: 'toast__text', text: opts.text }));
  if (opts.action) {
    body.appendChild(button({
      label: opts.action.label,
      variant: 'ghost',
      size: 'sm',
      onClick: () => {
        opts.action.onClick();
        close();
      },
    }));
  }
  node.appendChild(body);

  let timer = null;
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    if (timer) window.clearTimeout(timer);
    node.classList.add('toast--leaving');
    let removed = false;
    const remove = () => {
      if (removed) return;
      removed = true;
      node.remove();
    };
    node.addEventListener('animationend', remove, { once: true });
    window.setTimeout(remove, 220);
  };

  node.appendChild(iconButton({
    icon: 'x',
    label: t('common.dismiss'),
    className: 'toast__close',
    onClick: close,
  }));

  const timeout = Number.isFinite(opts.timeout)
    ? opts.timeout
    : (tone === 'error' ? 8000 : 5000);

  const startTimer = () => {
    if (!timeout) return;
    timer = window.setTimeout(close, timeout);
  };
  node.addEventListener('mouseenter', () => {
    if (timer) window.clearTimeout(timer);
    timer = null;
  });
  node.addEventListener('mouseleave', startTimer);
  node.addEventListener('focusin', () => {
    if (timer) window.clearTimeout(timer);
    timer = null;
  });

  host.appendChild(node);
  startTimer();
  return { close, element: node };
}

/** Shorthand toasts. Each takes `(title, text?, options?)`. */
toast.success = (title, text, extra) => toast(Object.assign({ title, text, tone: 'success' }, extra));
toast.error = (title, text, extra) => toast(Object.assign({ title, text, tone: 'error' }, extra));
toast.warning = (title, text, extra) => toast(Object.assign({ title, text, tone: 'warning' }, extra));
toast.info = (title, text, extra) => toast(Object.assign({ title, text, tone: 'info' }, extra));

// --------------------------------------------------------------------------- //
// States
// --------------------------------------------------------------------------- //

/** Line-art illustrations for empty states. 96×64, single stroke weight. */
const EMPTY_ART = {
  book:
    '<path d="M14 16h20a6 6 0 0 1 6 6v26H14z"/><path d="M46 48V22h20a6 6 0 0 1 6 6v20z"/><path d="M48 12v40"/><path d="M24 26h8M24 34h8"/>',
  search:
    '<circle cx="38" cy="30" r="13"/><path d="M47 39l9 9"/><path d="M20 52h28"/>',
  users:
    '<circle cx="34" cy="24" r="7"/><path d="M22 48c0-6.6 5.4-12 12-12s12 5.4 12 12"/><circle cx="52" cy="28" r="5"/><path d="M46 48c0-4.4 2.7-8 6-8"/>',
  chart:
    '<path d="M18 14v36h60"/><path d="M30 50V36"/><path d="M44 50V26"/><path d="M58 50V40"/><path d="M72 50V20"/>',
  box: '<path d="M24 22l24-8 24 8v24l-24 8-24-8z"/><path d="M24 22l24 8 24-8"/><path d="M48 30v24"/>',
  alert: '<path d="M48 14l30 40H18z"/><path d="M48 30v12"/><circle cx="48" cy="48" r="1.6"/>',
};

/**
 * An empty state: line art, one sentence, one action.
 *
 * @param {Object} options
 * @param {string} [options.title]
 * @param {string} [options.body]
 * @param {string} [options.icon] Key of the built-in art set.
 * @param {HTMLElement} [options.action]
 * @returns {HTMLElement}
 */
export function emptyState(options) {
  const opts = options || {};
  const art = EMPTY_ART[opts.icon || 'box'] || EMPTY_ART.box;
  const node = el('div', { class: 'empty' });
  const artNode = svgEl('svg', {
    viewBox: '0 0 96 64',
    width: '96',
    height: '64',
    fill: 'none',
    stroke: 'currentColor',
    'stroke-width': '1.5',
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
    'aria-hidden': 'true',
    class: 'empty__icon',
  });
  artNode.innerHTML = art;
  node.appendChild(artNode);
  node.appendChild(el('p', { class: 'empty__title', text: opts.title || t('empty.generic.title') }));
  if (opts.body) node.appendChild(el('p', { class: 'empty__body', text: opts.body }));
  if (opts.action) {
    const holder = el('div', { class: 'empty__action' });
    holder.appendChild(opts.action);
    node.appendChild(holder);
  }
  return node;
}

/**
 * Turn an error into display strings.
 *
 * `ApiError` carries a stable machine code; the catalogue has a sentence for
 * every one of them, so the UI never shows a raw server message.
 *
 * @param {*} error
 * @returns {{title: string, message: string, detail: string}}
 */
export function formatError(error) {
  const fallbackTitle = t('errstate.title');
  const fallbackBody = t('errstate.body');
  if (!error) return { title: fallbackTitle, message: fallbackBody, detail: '' };

  const code = String(error.code || '');
  const known = code ? t(`err.${code}`) : '';
  const hasSentence = known && !known.startsWith('⟨');

  const message = hasSentence ? known : (error.message || fallbackBody);
  const detailLines = [];
  if (code) detailLines.push(`code: ${code}`);
  if (error.status) detailLines.push(`status: ${error.status}`);
  if (error.details && typeof error.details === 'object') {
    detailLines.push(`details: ${JSON.stringify(error.details)}`);
  } else if (error.details) {
    detailLines.push(`details: ${String(error.details)}`);
  }

  return {
    title: error.isNetwork ? t('shell.offline') : fallbackTitle,
    message,
    detail: detailLines.join('\n'),
  };
}

/**
 * An error state with a retry action and collapsible technical detail.
 *
 * @param {Object} options
 * @param {*} options.error
 * @param {() => void} [options.onRetry]
 * @param {string} [options.title]
 * @returns {HTMLElement}
 */
export function errorState(options) {
  const opts = options || {};
  const info = formatError(opts.error);
  const node = el('div', { class: 'errstate', attrs: { role: 'alert' } });

  node.appendChild(el('div', { class: 'errstate__head' },
    icon('alertCircle', 18),
    el('span', { text: opts.title || info.title })));

  node.appendChild(el('p', { class: 'errstate__body', text: info.message }));

  const actions = el('div', { class: 'errstate__actions' });
  if (opts.onRetry) {
    actions.appendChild(button({
      label: t('errstate.retry'),
      variant: 'secondary',
      size: 'sm',
      icon: 'refresh',
      onClick: opts.onRetry,
    }));
  }
  node.appendChild(actions);

  if (info.detail) {
    const details = el('details');
    details.appendChild(el('summary', { text: t('errstate.details') }));
    details.appendChild(el('pre', { text: info.detail }));
    node.appendChild(details);
  }
  return node;
}

/**
 * A warning banner with optional actions.
 * @param {Object} options
 * @param {string} options.text
 * @param {HTMLElement[]} [options.actions]
 * @returns {HTMLElement}
 */
export function banner(options) {
  const opts = options || {};
  return el('div', { class: 'banner', attrs: { role: 'status' } },
    el('span', { class: 'banner__icon' }, icon('alert', 18)),
    el('p', { class: 'banner__text', text: opts.text }),
    opts.actions ? el('div', { class: 'banner__actions' }, opts.actions) : null);
}

// --------------------------------------------------------------------------- //
// Skeletons
// --------------------------------------------------------------------------- //

/**
 * A loading skeleton.
 * @param {Object} options
 * @param {number} [options.rows] Row count for the table variant.
 * @param {'table'|'kpi'|'text'} [options.variant]
 * @param {number} [options.columns]
 * @returns {HTMLElement}
 */
export function skeleton(options) {
  const opts = options || {};
  const variant = opts.variant || 'table';
  const rows = Number.isInteger(opts.rows) ? opts.rows : 6;

  if (variant === 'kpi') {
    const grid = el('div', { class: 'grid grid--kpi' });
    for (let i = 0; i < (opts.columns || 4); i += 1) {
      grid.appendChild(el('div', { class: 'stat' },
        el('div', { class: 'sk sk--text', style: { width: '45%' } }),
        el('div', { class: 'sk sk--kpi' })));
    }
    return grid;
  }

  if (variant === 'text') {
    const wrap = el('div', { class: 'u-col' });
    for (let i = 0; i < rows; i += 1) {
      wrap.appendChild(el('div', { class: 'sk sk--text', style: { width: `${90 - i * 7}%` } }));
    }
    return wrap;
  }

  const wrap = el('div', { attrs: { 'aria-busy': 'true' } });
  for (let i = 0; i < rows; i += 1) {
    const row = el('div', { class: 'sk-row' });
    for (let c = 0; c < (opts.columns || 4); c += 1) {
      row.appendChild(el('div', { class: `sk sk--cell${c === 0 ? ' sk--wide' : ''}` }));
    }
    wrap.appendChild(row);
  }
  return wrap;
}

// --------------------------------------------------------------------------- //
// Tooltip
// --------------------------------------------------------------------------- //

/** The single visible tooltip, if any. */
let activeTip = null;

/**
 * Attach a tooltip to an element. Shows on hover and on keyboard focus, hides on
 * Esc, and never renders on a touch device (there is no hover to leave).
 *
 * @param {HTMLElement} anchor
 * @param {string} text
 * @returns {() => void} Detach.
 */
export function attachTooltip(anchor, text) {
  if (!anchor || !text) return () => {};
  let tip = null;
  let timer = null;

  const hide = () => {
    if (timer) {
      window.clearTimeout(timer);
      timer = null;
    }
    if (tip) {
      tip.remove();
      if (activeTip === tip) activeTip = null;
      tip = null;
    }
  };

  const show = () => {
    if (tip) return;
    if (activeTip) activeTip.remove();
    tip = el('div', { class: 'tip', attrs: { role: 'tooltip' }, text });
    document.body.appendChild(tip);
    const rect = anchor.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - tipRect.width / 2;
    left = Math.max(6, Math.min(left, window.innerWidth - tipRect.width - 6));
    let top = rect.top - tipRect.height - 6;
    if (top < 6) top = rect.bottom + 6;
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(top)}px`;
    activeTip = tip;
  };

  const onEnter = () => {
    hide();
    timer = window.setTimeout(show, 420);
  };

  anchor.addEventListener('mouseenter', onEnter);
  anchor.addEventListener('mouseleave', hide);
  anchor.addEventListener('focus', onEnter);
  anchor.addEventListener('blur', hide);
  anchor.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') hide();
  });

  return () => {
    hide();
    anchor.removeEventListener('mouseenter', onEnter);
    anchor.removeEventListener('mouseleave', hide);
    anchor.removeEventListener('focus', onEnter);
    anchor.removeEventListener('blur', hide);
  };
}

/**
 * A keyboard hint, e.g. `Ctrl` `K`.
 * @param {string[]} keys
 * @returns {HTMLElement}
 */
export function kbd(keys) {
  const node = el('span', { class: 'sheet__keys' });
  for (const key of keys || []) node.appendChild(el('kbd', { class: 'kbd', text: key }));
  return node;
}

/** Default export grouping the kit, for call sites that prefer `ui.el(...)`. */
export default {
  el,
  svgEl,
  frag,
  clear,
  qs,
  qsa,
  cx,
  icon,
  hasIcon,
  button,
  iconButton,
  linkButton,
  field,
  input,
  select,
  textarea,
  checkbox,
  switchControl,
  inputGroup,
  card,
  statCard,
  kv,
  section,
  statusPill,
  pill,
  chips,
  table,
  pagination,
  tabs,
  menu,
  closeMenu,
  modal,
  drawer,
  confirmDialog,
  toast,
  emptyState,
  errorState,
  formatError,
  banner,
  skeleton,
  attachTooltip,
  kbd,
  trapFocus,
  focusFirst,
  hasOpenLayer,
  nextId,
};
