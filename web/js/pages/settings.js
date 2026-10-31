/**
 * Settings.
 *
 * The form is generated from the server's own rule map (`GET /api/settings`
 * returns `validation` alongside the values), so a numeric field can never be
 * given a range the API would reject, and adding a setting on the server needs
 * no change here.
 *
 * The save bar only appears when something has actually changed — a settings
 * screen that always shows "Save" invites accidental writes.
 */

import { t } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';

/** Groups, in the order they are shown, with the keys each one owns. */
const GROUPS = Object.freeze([
  { key: 'school', labelKey: 'adm.settings.title', fields: ['school_name', 'currency', 'currency_space', 'default_locale', 'theme'] },
  { key: 'circulation', labelKey: 'circ.title', fields: ['loan_days', 'max_loans', 'max_renewals', 'hold_days', 'fine_per_day'] },
  { key: 'interface', labelKey: 'theme.label', fields: ['page_size_default', 'idle_lock_minutes'] },
  { key: 'data', labelKey: 'adm.backup.title', fields: ['label_prefix', 'backup_on_exit', 'backup_keep'] },
]);

/** Enum option labels. */
function enumOptions(key, values) {
  if (key === 'theme') return values.map((value) => ({ value, label: t(`theme.${value}`) }));
  if (key === 'default_locale') return values.map((value) => ({ value, label: t(`lang.${value}`) }));
  return values.map((value) => ({ value, label: value }));
}

/**
 * Render the settings screen.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  let settings = {};
  let rules = {};
  let readOnly = [];
  let loading = true;
  let error = null;
  let destroyed = false;
  let dirty = false;

  /** @type {Object<string, {control: HTMLElement, wrap: HTMLElement, get: Function}>} */
  const controls = {};

  const title = ui.el('h1', { class: 'page__title', text: t('adm.settings.title') });
  const subtitle = ui.el('p', { class: 'page__sub', text: t('adm.title') });

  const saveButton = ui.button({
    label: t('common.save'),
    variant: 'primary',
    icon: 'check',
    onClick: () => save(),
  });

  const saveBar = ui.el('div', { class: 'savebar' },
    ui.el('span', { class: 'savebar__text', text: t('common.unsaved') }),
    ui.el('span', { class: 'savebar__grow' }),
    ui.button({
      label: t('common.reset'),
      onClick: () => {
        dirty = false;
        load();
      },
    }),
    saveButton);
  saveBar.hidden = true;

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle));

  const body = ui.el('div', { class: 'u-col', style: { gap: '16px' } });
  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } }, head, body, saveBar);

  // -- data -------------------------------------------------------------- //
  async function load() {
    loading = true;
    error = null;
    paint();
    try {
      const data = await api.get('/api/settings');
      if (destroyed) return;
      settings = data.settings || {};
      rules = data.validation || {};
      readOnly = data.read_only || [];
    } catch (failure) {
      if (destroyed) return;
      error = failure;
    } finally {
      if (!destroyed) {
        loading = false;
        paint();
      }
    }
  }

  async function save() {
    const payload = {};
    for (const [key, entry] of Object.entries(controls)) {
      if (readOnly.includes(key)) continue;
      payload[key] = entry.get();
    }
    saveButton.disabled = true;
    saveButton.setAttribute('aria-busy', 'true');
    try {
      const data = await api.put('/api/settings', payload);
      if (data && data.settings) settings = data.settings;
      dirty = false;
      saveBar.hidden = true;
      ui.toast.success(t('adm.settings.saved'));
      // The shell reads settings from the store, so refresh it in place.
      if (store.state.settings) Object.assign(store.state.settings, settings);
      if (settings.theme) store.setTheme(settings.theme);
      if (settings.default_locale) document.dispatchEvent(new CustomEvent('imri:lang'));
      paint();
    } catch (failure) {
      if (failure && failure.isValidation && failure.fields) {
        for (const [key, code] of Object.entries(failure.fields)) {
          const entry = controls[key];
          if (!entry) continue;
          entry.wrap.classList.add('field--error');
          const existing = entry.wrap.querySelector('.field__error');
          if (existing) existing.remove();
          entry.wrap.appendChild(ui.el('p', { class: 'field__error', attrs: { role: 'alert' } },
            ui.icon('alertCircle', 14), ui.el('span', { text: t(`err.${code}`) })));
        }
        ui.toast.error(t('toast.error'), ui.formatError(failure).message);
      } else {
        ui.toast.error(t('toast.error'), ui.formatError(failure).message);
      }
    } finally {
      saveButton.disabled = false;
      saveButton.removeAttribute('aria-busy');
    }
  }

  function markDirty() {
    dirty = true;
    saveBar.hidden = false;
  }

  // -- fields ------------------------------------------------------------ //
  function buildControl(key, value, rule) {
    const label = t(`set.${key}.label`);
    const hint = t(`set.${key}.hint`);
    const kind = (rule && rule.kind) || 'text';

    if (kind === 'bool') {
      const control = ui.switchControl({
        checked: value === '1' || value === true,
        label,
        onChange: () => markDirty(),
      });
      const wrap = ui.el('div', { class: 'field field--wide' }, control,
        hint ? ui.el('p', { class: 'field__hint', text: hint }) : null);
      return { wrap, control: control.querySelector('input'), get: () => (control.querySelector('input').checked ? '1' : '0') };
    }

    if (kind === 'enum') {
      const control = ui.select({
        value,
        options: enumOptions(key, rule.values || []),
        onChange: () => markDirty(),
      });
      const wrap = ui.field({ label, control, hint });
      return { wrap, control, get: () => control.value };
    }

    const numeric = kind === 'int' || kind === 'float';
    const control = ui.input({
      value,
      type: numeric ? 'number' : 'text',
      mono: numeric,
      min: rule && rule.min !== undefined ? rule.min : undefined,
      max: rule && rule.max !== undefined ? rule.max : undefined,
      step: kind === 'float' ? '0.01' : undefined,
      onInput: () => markDirty(),
    });
    const rangeHint = numeric && rule
      ? `${hint || ''}${hint ? ' · ' : ''}${rule.min}–${rule.max}`.trim()
      : hint;
    const wrap = ui.field({ label, control, hint: rangeHint });
    return { wrap, control, get: () => control.value };
  }

  // -- paint ------------------------------------------------------------- //
  function paint() {
    ui.clear(body);
    Object.keys(controls).forEach((key) => delete controls[key]);

    if (loading) {
      body.appendChild(ui.card({ body: ui.skeleton({ rows: 6 }) }));
      return;
    }
    if (error) {
      body.appendChild(ui.errorState({ error, onRetry: () => load() }));
      return;
    }

    for (const group of GROUPS) {
      const card = ui.card({ title: t(group.labelKey) });
      const grid = ui.el('div', { class: 'form-grid' });
      for (const key of group.fields) {
        if (!(key in settings)) continue;
        const entry = buildControl(key, settings[key], rules[key] || {});
        controls[key] = entry;
        grid.appendChild(entry.wrap);
      }
      card.appendChild(grid);
      body.appendChild(card);
    }

    // Licence-managed values are shown, not edited.
    const locked = readOnly.filter((key) => key in settings);
    if (locked.length) {
      const card = ui.card({ title: t('adm.license.title') });
      card.appendChild(ui.el('p', { class: 'u-xs u-muted', text: t('adm.settings.readOnly') }));
      card.appendChild(ui.kv(locked.map((key) => ({
        label: t(`set.${key}.label`),
        value: key === 'license_key' ? '••••••••' : settings[key],
      }))));
      body.appendChild(card);
    }
  }

  const onLang = () => {
    title.textContent = t('adm.settings.title');
    saveButton.textContent = '';
    saveButton.appendChild(ui.icon('check', 16));
    saveButton.appendChild(ui.el('span', { text: t('common.save') }));
    paint();
  };
  document.addEventListener('imri:lang', onLang);

  paint();
  load();

  return {
    element: root,
    destroy() {
      destroyed = true;
      document.removeEventListener('imri:lang', onLang);
    },
  };
}

export default { render };
