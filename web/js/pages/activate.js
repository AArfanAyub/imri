/**
 * Activation gate.
 *
 * Renders instead of the shell — `chrome: 'auth'` — because an unlicensed
 * install has no navigation to offer. One field, one button, and a countdown if
 * the operator guesses wrong five times in a row: the server owns the lockout
 * (`license_cooldown`, `details.retry_after_s`), this screen only renders it.
 *
 * The code is sent exactly as typed. The server compares `strip().lower()`
 * against the constant, so "ARFANAYUB0" and " arfanayub0 " both work and the
 * client never has to guess at normalisation.
 */

import { t, fmtNum } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';
import { gate, startCooldown } from './_gate.js';

/** After this many wrong codes the server starts returning a cooldown. */
const MAX_ATTEMPTS_HINT = 5;

/**
 * Render the activation screen.
 *
 * @param {{navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  let busy = false;
  let cooling = null;

  const code = ui.input({
    mono: true,
    autocomplete: 'off',
    spellcheck: false,
    className: 'gate__code',
    ariaLabel: t('activate.code'),
    onInput: () => {
      // Typing again clears a stale rejection; the value itself is untouched.
      if (!errorNode.hidden) clearError();
    },
    onEnter: () => submit(),
  });
  code.id = 'activation-code';

  const errorText = ui.el('span');
  const errorNode = ui.el('p', {
    class: 'field__error',
    attrs: { id: 'activation-error', role: 'alert' },
    hidden: true,
  }, ui.icon('alertCircle', 14), errorText);

  const hint = ui.el('p', { class: 'field__hint', text: t('activate.codeHint') });

  const fieldWrap = ui.el('div', { class: 'field' },
    ui.el('label', { class: 'field__label', attrs: { for: code.id }, text: t('activate.code') }),
    code,
    hint,
    errorNode);

  const submitButton = ui.button({
    label: t('activate.action'),
    variant: 'primary',
    block: true,
    type: 'submit',
    onClick: () => submit(),
  });

  const form = ui.el('form', {
    class: 'gate__form',
    attrs: { novalidate: 'novalidate' },
    on: {
      submit: (event) => {
        event.preventDefault();
        submit();
      },
    },
  }, fieldWrap, submitButton);

  const layout = gate({
    titleKey: 'activate.title',
    subKey: 'activate.oneTime',
    body: [form, ui.el('p', { class: 'gate__foot', text: t('activate.footer') })],
    footKey: 'activate.footerVersion',
    footVars: { version: store.state.version || '' },
  });

  /**
   * Show a message under the field.
   * @param {string} message
   */
  function showError(message) {
    errorText.textContent = message;
    errorNode.hidden = false;
    fieldWrap.classList.add('field--error');
    code.setAttribute('aria-invalid', 'true');
    if (!code.hasAttribute('aria-describedby')) {
      code.setAttribute('aria-describedby', 'activation-error');
    }
  }

  /** Clear the rejection without touching what was typed. */
  function clearError() {
    errorNode.hidden = true;
    errorText.textContent = '';
    fieldWrap.classList.remove('field--error');
    code.removeAttribute('aria-invalid');
  }

  /** Put the submit button back to its resting state. */
  function idleButton() {
    submitButton.disabled = false;
    submitButton.removeAttribute('aria-busy');
    submitButton.textContent = t('activate.action');
  }

  /**
   * Disable the form and count down.
   * @param {number} seconds
   */
  function beginCooldown(seconds) {
    if (cooling) cooling.stop();
    code.disabled = true;
    submitButton.disabled = true;
    cooling = startCooldown(seconds, (remaining) => {
      if (remaining > 0) showError(t('activate.cooldown', { seconds: fmtNum(remaining) }));
    }, () => {
      cooling = null;
      code.disabled = false;
      idleButton();
      clearError();
      code.focus();
    });
  }

  /** Validate, post, react. */
  async function submit() {
    if (busy || code.disabled) return;
    const value = code.value.trim();
    if (!value) {
      showError(t('err.validation_failed'));
      code.focus();
      return;
    }

    busy = true;
    clearError();
    submitButton.disabled = true;
    submitButton.setAttribute('aria-busy', 'true');
    submitButton.textContent = '';
    submitButton.appendChild(ui.el('span', { class: 'btn__spinner', attrs: { 'aria-hidden': 'true' } }));
    submitButton.appendChild(ui.el('span', { text: t('activate.working') }));

    try {
      await api.activate(value);
      // Re-read the licence from the server rather than assuming: it also
      // refreshes the school name the next screen prints.
      const payload = await api.bootstrap();
      store.applyBootstrap(payload);
      ui.toast.success(t('activate.success'));
      ctx.navigate('/login', { replace: true });
    } catch (error) {
      if (error && error.is && error.is('license_cooldown')) {
        const wait = Number(error.details && error.details.retry_after_s) || 30;
        beginCooldown(wait);
      } else if (error && error.is && error.is('invalid_license_code')) {
        showError(t('activate.invalid'));
        const left = Number(error.details && error.details.attempts_left);
        const wait = Number(error.details && error.details.retry_after_s);
        if (wait > 0) {
          beginCooldown(wait);
        } else if (Number.isFinite(left) && left > 0 && left < MAX_ATTEMPTS_HINT) {
          hint.textContent = t('activate.attemptsLeft', { count: fmtNum(left) });
        }
        code.select();
      } else {
        showError(ui.formatError(error).message);
      }
    } finally {
      busy = false;
      if (!cooling) idleButton();
    }
  }

  // Autofocus on mount: this screen has exactly one thing to do.
  window.requestAnimationFrame(() => code.focus());

  return {
    element: layout.element,
    destroy() {
      if (cooling) cooling.stop();
      layout.destroy();
    },
  };
}

export default { render };
