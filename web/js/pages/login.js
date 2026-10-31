/**
 * Sign in.
 *
 * Username, password, Enter to submit, and the lockout countdown the server
 * asks for after five failures (`rate_limited`, `details.retry_after_s`).
 *
 * Two details worth knowing:
 *
 *   · A rejected password never clears the username. Retyping an identity
 *     because the password was wrong is the kind of small insult that makes a
 *     tool feel cheap.
 *   · The default-password banner is driven by a flag this tab remembers from
 *     the last sign-in. The server reports `default_password_warning` exactly
 *     when the account is still on the seeded password, and in that case it also
 *     forces the change — so the banner explains *why* the next screen appears,
 *     and stays visible if the operator signs out again before dealing with it.
 */

import { t, fmtNum } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';
import {
  gate,
  startCooldown,
  passwordField,
  rememberDefaultPassword,
  forgotDefaultPassword,
} from './_gate.js';

/**
 * Render the sign-in screen.
 *
 * @param {{navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  let busy = false;
  let cooling = null;

  const username = ui.input({
    autocomplete: 'username',
    ariaLabel: t('auth.username'),
    onInput: () => {
      if (!errorNode.hidden) clearError();
    },
    onEnter: () => submit(),
  });
  username.id = 'login-username';

  const password = passwordField({
    autocomplete: 'current-password',
    ariaLabel: t('auth.password'),
    onInput: () => {
      if (!errorNode.hidden) clearError();
    },
    onEnter: () => submit(),
  });
  password.input.id = 'login-password';

  const errorText = ui.el('span');
  const errorNode = ui.el('p', {
    class: 'field__error',
    attrs: { id: 'login-error', role: 'alert' },
    hidden: true,
  }, ui.icon('alertCircle', 14), errorText);

  const usernameField = ui.el('div', { class: 'field' },
    ui.el('label', { class: 'field__label', attrs: { for: username.id }, text: t('auth.username') }),
    username);

  const passwordFieldWrap = ui.el('div', { class: 'field' },
    ui.el('label', { class: 'field__label', attrs: { for: password.input.id }, text: t('auth.password') }),
    password.element);

  const submitButton = ui.button({
    label: t('auth.signIn'),
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
  }, usernameField, passwordFieldWrap, errorNode, submitButton);

  // --- default-password banner -------------------------------------------
  const banner = ui.el('div', { class: 'banner', attrs: { role: 'status' }, hidden: true });
  const paintBanner = () => {
    banner.textContent = '';
    banner.appendChild(ui.el('span', { class: 'banner__icon' }, ui.icon('alert', 18)));
    banner.appendChild(ui.el('div', { class: 'banner__text' },
      ui.el('strong', { text: t('auth.defaultBanner') }),
      ui.el('span', { text: ' ' }),
      ui.el('span', { text: t('auth.defaultBannerBody') })));
    banner.appendChild(ui.el('div', { class: 'banner__actions' },
      ui.button({
        label: t('auth.changeNow'),
        variant: 'secondary',
        size: 'sm',
        onClick: () => ctx.navigate('/change-password'),
      }),
      ui.button({
        label: t('auth.remindLater'),
        variant: 'ghost',
        size: 'sm',
        onClick: () => {
          rememberDefaultPassword(false);
          banner.hidden = true;
        },
      })));
  };
  if (forgotDefaultPassword() || store.mustChangePassword()) {
    paintBanner();
    banner.hidden = false;
  }

  const layout = gate({
    titleKey: 'auth.welcome',
    subKey: 'auth.subtitle',
    body: [banner, form],
    footKey: 'auth.footer',
    footVars: { version: store.state.version || '' },
  });

  /**
   * @param {string} message
   */
  function showError(message) {
    errorText.textContent = message;
    errorNode.hidden = false;
    username.setAttribute('aria-invalid', 'true');
    password.input.setAttribute('aria-invalid', 'true');
    if (!password.input.hasAttribute('aria-describedby')) {
      password.input.setAttribute('aria-describedby', 'login-error');
    }
  }

  function clearError() {
    errorNode.hidden = true;
    errorText.textContent = '';
    username.removeAttribute('aria-invalid');
    password.input.removeAttribute('aria-invalid');
  }

  function idleButton() {
    submitButton.disabled = false;
    submitButton.removeAttribute('aria-busy');
    submitButton.textContent = t('auth.signIn');
  }

  /**
   * @param {number} seconds
   */
  function beginCooldown(seconds) {
    if (cooling) cooling.stop();
    username.disabled = true;
    password.setDisabled(true);
    submitButton.disabled = true;
    cooling = startCooldown(seconds, (remaining) => {
      if (remaining > 0) showError(t('auth.lockout', { seconds: fmtNum(remaining) }));
    }, () => {
      cooling = null;
      username.disabled = false;
      password.setDisabled(false);
      idleButton();
      clearError();
      password.input.focus();
    });
  }

  async function submit() {
    if (busy || username.disabled) return;
    const name = username.value.trim();
    const secret = password.input.value;
    if (!name || !secret) {
      showError(t('err.validation_failed'));
      (name ? password.input : username).focus();
      return;
    }

    busy = true;
    clearError();
    submitButton.disabled = true;
    submitButton.setAttribute('aria-busy', 'true');
    submitButton.textContent = '';
    submitButton.appendChild(ui.el('span', { class: 'btn__spinner', attrs: { 'aria-hidden': 'true' } }));
    submitButton.appendChild(ui.el('span', { text: t('auth.signingIn') }));

    try {
      const result = await api.login(name, secret);
      const user = (result && result.user) || {};
      store.applySession({
        user,
        role: user.role || '',
        caps: Array.isArray(user.caps) ? user.caps : [],
        mustChangePassword: Boolean(result && result.must_change_password),
      });
      const onDefault = Boolean(result && (result.default_password_warning || result.must_change_password));
      rememberDefaultPassword(onDefault);
      password.input.value = '';
      if (onDefault) {
        ui.toast.info(t('auth.defaultBanner'));
        ctx.navigate('/change-password', { replace: true });
      } else {
        ctx.navigate('/dashboard', { replace: true });
      }
    } catch (error) {
      if (error && error.is && error.is('rate_limited')) {
        beginCooldown(Number(error.details && error.details.retry_after_s) || 30);
      } else if (error && error.is && error.is('invalid_credentials')) {
        showError(t('auth.failed'));
        password.input.value = '';
        password.input.focus();
      } else if (error && error.is && error.is('account_disabled')) {
        showError(t('err.account_disabled'));
      } else {
        showError(ui.formatError(error).message);
      }
    } finally {
      busy = false;
      if (!cooling) idleButton();
    }
  }

  window.requestAnimationFrame(() => {
    if (username.value) password.input.focus();
    else username.focus();
  });

  return {
    element: layout.element,
    destroy() {
      if (cooling) cooling.stop();
      layout.destroy();
    },
  };
}

export default { render };
