/**
 * The auth gate — the layout shared by activation, sign-in and the forced
 * password change.
 *
 * These three screens render *instead of* the application shell: the router
 * marks them `chrome: 'auth'`, the shell hides its navigation, and this module
 * supplies the centred 420 px card with the wordmark above it.
 *
 * It also owns the cooldown countdown, because two of the three screens need
 * one and a shared implementation cannot drift: activation locks out after five
 * bad codes, sign-in after five bad passwords, and both answers carry
 * `details.retry_after_s`.
 */

import { t } from '../i18n.js';
import * as ui from '../ui.js';
import * as brand from '../brand.js';

/**
 * Build the gate layout.
 *
 * @param {Object} options
 * @param {string} options.titleKey Catalogue key for the heading.
 * @param {string} [options.subKey] Catalogue key for the sentence beneath it.
 * @param {HTMLElement|HTMLElement[]} [options.body] Form content.
 * @param {string} [options.footKey] Catalogue key for the footer line.
 * @param {Object} [options.footVars] Interpolation values for the footer.
 * @returns {{element: HTMLElement, body: HTMLElement, footer: HTMLElement,
 *            setSub: (key: ?string) => void, destroy: () => void}}
 */
export function gate(options) {
  const opts = options || {};

  const title = ui.el('h1', { class: 'gate__title' });
  const sub = ui.el('p', { class: 'gate__sub' });
  const foot = ui.el('p', { class: 'gate__foot' });

  const paint = () => {
    title.textContent = t(opts.titleKey);
    sub.textContent = opts.subKey ? t(opts.subKey) : '';
    sub.hidden = !opts.subKey;
    foot.textContent = opts.footKey ? t(opts.footKey, opts.footVars) : '';
    foot.hidden = !opts.footKey;
  };
  paint();

  const body = ui.el('div', { class: 'gate__form' }, opts.body);

  const card = ui.el('div', { class: 'card gate__card' }, title, sub, body);

  const brandLockup = ui.el('div', { class: 'gate__brand' },
    brand.mark(56, { class: 'gate__mark', label: null }),
    ui.el('span', { class: 'gate__wordmark', text: t('app.name') }),
    ui.el('span', { class: 'gate__tagline', text: t('app.tagline') }));

  const element = ui.el('div', { class: 'gate' }, brandLockup, card, foot);

  const onLang = () => paint();
  document.addEventListener('imri:lang', onLang);

  return {
    element,
    body,
    footer: foot,
    /**
     * Swap the sentence under the heading, or clear it.
     * @param {?string} key
     */
    setSub(key) {
      opts.subKey = key || null;
      paint();
    },
    destroy() {
      document.removeEventListener('imri:lang', onLang);
    },
  };
}

/**
 * A live countdown for a lockout.
 *
 * The server is the only authority on how long the wait is; this just renders
 * the number it sent. The timer is cleared by `stop()` (call it on unmount) and
 * automatically once it reaches zero.
 *
 * @param {number} seconds Seconds to count down from.
 * @param {(remaining: number) => void} onTick Called immediately and every second.
 * @param {() => void} [onDone] Called once, when the wait is over.
 * @returns {{stop: () => void, remaining: () => number}}
 */
export function startCooldown(seconds, onTick, onDone) {
  let remaining = Math.max(0, Math.ceil(Number(seconds) || 0));
  let timer = null;

  const tick = () => {
    if (typeof onTick === 'function') onTick(remaining);
    if (remaining <= 0) {
      if (timer) window.clearInterval(timer);
      timer = null;
      if (typeof onDone === 'function') onDone();
      return;
    }
    remaining -= 1;
  };

  tick();
  if (remaining >= 0 && !timer) {
    timer = window.setInterval(tick, 1000);
  }

  return {
    stop() {
      if (timer) window.clearInterval(timer);
      timer = null;
    },
    remaining() {
      return remaining;
    },
  };
}

/**
 * A password field with a reveal toggle inside its right edge.
 *
 * @param {Object} options
 * @param {string} [options.autocomplete]
 * @param {Function} [options.onInput]
 * @param {Function} [options.onEnter]
 * @param {string} [options.ariaLabel]
 * @returns {{element: HTMLElement, input: HTMLInputElement,
 *            setDisabled: (value: boolean) => void}}
 */export function passwordField(options) {
  const opts = options || {};
  const control = ui.input({
    type: 'password',
    autocomplete: opts.autocomplete || 'current-password',
    onInput: opts.onInput,
    onEnter: opts.onEnter,
    ariaLabel: opts.ariaLabel,
  });

  let visible = false;
  const toggle = ui.iconButton({
    icon: 'eye',
    label: t('auth.showPassword'),
    size: 'sm',
    className: 'pw__toggle',
    onClick: () => {
      visible = !visible;
      control.type = visible ? 'text' : 'password';
      const label = visible ? t('auth.hidePassword') : t('auth.showPassword');
      toggle.setAttribute('aria-label', label);
      toggle.setAttribute('title', label);
      toggle.setAttribute('aria-pressed', visible ? 'true' : 'false');
      control.focus();
    },
  });
  toggle.setAttribute('aria-pressed', 'false');

  const element = ui.el('div', { class: 'pw' }, control, toggle);
  return {
    element,
    input: control,
    setDisabled(value) {
      control.disabled = Boolean(value);
      toggle.disabled = Boolean(value);
    },
  };
}

/** Session-scoped key: this tab has been told the account is on the default password. */
const DEFAULT_PASSWORD_FLAG = 'imri.defaultPassword';

/**
 * Remember, for this tab only, that the account is still on the seeded
 * password. Session storage rather than local storage: the fact is about this
 * sitting, and a shared machine must not inherit it.
 *
 * @param {boolean} value
 */
export function rememberDefaultPassword(value) {
  try {
    if (value) window.sessionStorage.setItem(DEFAULT_PASSWORD_FLAG, '1');
    else window.sessionStorage.removeItem(DEFAULT_PASSWORD_FLAG);
  } catch (err) {
    /* private mode: the banner simply does not appear */
  }
}

/** @returns {boolean} Whether this tab has been told about the default password. */
export function forgotDefaultPassword() {
  try {
    return window.sessionStorage.getItem(DEFAULT_PASSWORD_FLAG) === '1';
  } catch (err) {
    return false;
  }
}

export default { gate, startCooldown, passwordField, rememberDefaultPassword, forgotDefaultPassword };
