/**
 * Change password — forced after the first sign-in, voluntary afterwards.
 *
 * Three fields, a live checklist, and no way out. When the server has flagged
 * the account (`must_change_password`), the router already refuses every other
 * route; this screen simply does not offer a cancel, a back link or a close
 * button, so there is nothing to click that looks like an escape hatch.
 *
 * The checklist is client-side guidance only. The server re-validates
 * (`weak_password`, `details.reasons`) and its answer wins: a rule the client
 * is happy with but the server rejects is shown under the field, not swallowed.
 */

import { t } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';
import { gate, passwordField, rememberDefaultPassword } from './_gate.js';

/** Shortest password the server accepts (`_MIN_PASSWORD_LEN`). */
const MIN_LENGTH = 8;

/**
 * One row of the requirement list.
 *
 * @param {string} labelKey
 * @returns {{element: HTMLElement, set: (state: 'idle'|'ok'|'bad') => void}}
 */
function rule(labelKey) {
  const mark = ui.el('span', { class: 'rule__mark', attrs: { 'aria-hidden': 'true' } });
  const text = ui.el('span', { text: t(labelKey) });
  const element = ui.el('div', { class: 'rule' }, mark, text);

  return {
    element,
    /**
     * @param {'idle'|'ok'|'bad'} state
     */
    set(state) {
      element.classList.toggle('rule--ok', state === 'ok');
      element.classList.toggle('rule--bad', state === 'bad');
      mark.textContent = '';
      if (state === 'ok') mark.appendChild(ui.icon('check', 11));
      else if (state === 'bad') mark.appendChild(ui.icon('x', 11));
    },
  };
}

/**
 * Render the change-password screen.
 *
 * @param {{navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  let busy = false;
  const forced = store.mustChangePassword();

  const current = passwordField({ autocomplete: 'current-password', ariaLabel: t('auth.currentPassword') });
  current.input.id = 'pw-current';

  const next = passwordField({
    autocomplete: 'new-password',
    ariaLabel: t('auth.newPassword'),
    onInput: () => evaluate(),
    onEnter: () => submit(),
  });
  next.input.id = 'pw-new';

  const confirm = passwordField({
    autocomplete: 'new-password',
    ariaLabel: t('auth.confirmPassword'),
    onInput: () => evaluate(),
    onEnter: () => submit(),
  });
  confirm.input.id = 'pw-confirm';

  const ruleLength = rule('auth.ruleLength');
  const ruleMatches = rule('auth.ruleMatches');
  const ruleDiffers = rule('auth.ruleDiffers');

  const currentError = ui.el('span');
  const currentErrorNode = ui.el('p', {
    class: 'field__error',
    attrs: { id: 'pw-current-error', role: 'alert' },
    hidden: true,
  }, ui.icon('alertCircle', 14), currentError);

  const newError = ui.el('span');
  const newErrorNode = ui.el('p', {
    class: 'field__error',
    attrs: { id: 'pw-new-error', role: 'alert' },
    hidden: true,
  }, ui.icon('alertCircle', 14), newError);

  const currentWrap = ui.el('div', { class: 'field' },
    ui.el('label', { class: 'field__label', attrs: { for: current.input.id }, text: t('auth.currentPassword') }),
    current.element,
    currentErrorNode);

  const newWrap = ui.el('div', { class: 'field' },
    ui.el('label', { class: 'field__label', attrs: { for: next.input.id }, text: t('auth.newPassword') }),
    next.element,
    ui.el('p', { class: 'field__hint', text: t('auth.passwordHint') }),
    newErrorNode);

  const confirmWrap = ui.el('div', { class: 'field' },
    ui.el('label', { class: 'field__label', attrs: { for: confirm.input.id }, text: t('auth.confirmPassword') }),
    confirm.element);

  const rules = ui.el('div', { class: 'rules', attrs: { role: 'list' } },
    ruleLength.element, ruleMatches.element, ruleDiffers.element);

  const submitButton = ui.button({
    label: t('auth.changeNow'),
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
  }, currentWrap, newWrap, confirmWrap, rules, submitButton);

  const layout = gate({
    titleKey: 'auth.mustChangeTitle',
    subKey: forced ? 'auth.forced' : 'auth.mustChangeBody',
    body: form,
    footKey: 'auth.footer',
    footVars: { version: store.state.version || '' },
  });

  /** @returns {{length: boolean, matches: boolean, differs: boolean}} */
  function checks() {
    const value = next.input.value;
    return {
      length: value.length >= MIN_LENGTH,
      matches: value.length > 0 && value === confirm.input.value,
      differs: value.length > 0 && value !== current.input.value,
    };
  }

  /** Repaint the checklist and enable or disable the button. */
  function evaluate() {
    const state = checks();
    ruleLength.set(state.length ? 'ok' : (next.input.value ? 'bad' : 'idle'));
    ruleMatches.set(state.matches ? 'ok' : (confirm.input.value ? 'bad' : 'idle'));
    ruleDiffers.set(state.differs ? 'ok' : (next.input.value && current.input.value ? 'bad' : 'idle'));
    submitButton.disabled = busy || !(state.length && state.matches && state.differs);
    return state;
  }

  /**
   * @param {HTMLElement} node
   * @param {HTMLElement} textNode
   * @param {string} message
   */
  function showFieldError(node, textNode, message) {
    textNode.textContent = message;
    node.hidden = false;
  }

  function clearFieldErrors() {
    currentErrorNode.hidden = true;
    newErrorNode.hidden = true;
    currentError.textContent = '';
    newError.textContent = '';
    currentWrap.classList.remove('field--error');
    newWrap.classList.remove('field--error');
    current.input.removeAttribute('aria-invalid');
    next.input.removeAttribute('aria-invalid');
  }

  function idleButton() {
    submitButton.textContent = t('auth.changeNow');
    submitButton.removeAttribute('aria-busy');
    evaluate();
  }

  async function submit() {
    if (busy) return;
    const state = evaluate();
    if (!state.length || !state.matches || !state.differs) {
      if (!state.length) showFieldError(newErrorNode, newError, t('validate.passwordShort'));
      else if (!state.matches) showFieldError(newErrorNode, newError, t('auth.passwordMismatch'));
      else showFieldError(newErrorNode, newError, t('auth.ruleDiffers'));
      next.input.focus();
      return;
    }
    if (!current.input.value) {
      showFieldError(currentErrorNode, currentError, t('validate.required'));
      currentWrap.classList.add('field--error');
      current.input.focus();
      return;
    }

    busy = true;
    clearFieldErrors();
    submitButton.disabled = true;
    submitButton.setAttribute('aria-busy', 'true');
    submitButton.textContent = '';
    submitButton.appendChild(ui.el('span', { class: 'btn__spinner', attrs: { 'aria-hidden': 'true' } }));
    submitButton.appendChild(ui.el('span', { text: t('common.loading') }));

    try {
      await api.post('/api/auth/password', {
        current_password: current.input.value,
        new_password: next.input.value,
      });
      const session = store.state.session;
      store.applySession({
        user: session.user,
        role: session.role,
        caps: session.caps,
        mustChangePassword: false,
      });
      rememberDefaultPassword(false);
      ui.toast.success(t('auth.passwordChanged'), t('auth.passwordChangedBody'));
      ctx.navigate('/dashboard', { replace: true });
    } catch (error) {
      if (error && error.is && error.is('wrong_password')) {
        showFieldError(currentErrorNode, currentError, t('err.wrong_password'));
        currentWrap.classList.add('field--error');
        current.input.setAttribute('aria-invalid', 'true');
        current.input.select();
      } else if (error && error.is && error.is('weak_password')) {
        const reasons = (error.details && error.details.reasons) || [];
        const message = reasons.indexOf('min_length') !== -1
          ? t('validate.passwordShort')
          : t('err.weak_password');
        showFieldError(newErrorNode, newError, message);
        newWrap.classList.add('field--error');
        next.input.setAttribute('aria-invalid', 'true');
        next.input.focus();
      } else {
        showFieldError(newErrorNode, newError, ui.formatError(error).message);
        newWrap.classList.add('field--error');
      }
    } finally {
      busy = false;
      idleButton();
    }
  }

  evaluate();
  window.requestAnimationFrame(() => current.input.focus());

  return {
    element: layout.element,
    destroy() {
      layout.destroy();
    },
  };
}

export default { render };
