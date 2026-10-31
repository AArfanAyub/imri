/**
 * The member editor.
 *
 * Shared by the member list ("Add member") and the member profile ("Edit"), the
 * same way the book editor serves the catalogue and the record drawer.
 *
 * Like the book editor, the server owns validation. A `422` comes back with a
 * `fields` map naming the exact input at fault, and each message is rendered
 * under that input rather than in one lump at the top — which is what makes the
 * form usable with a keyboard and a screen reader.
 */

import { t } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';

/** Member types the schema accepts. */
const MEMBER_TYPES = ['student', 'teacher', 'patron'];

/** Membership states. */
const MEMBER_STATUSES = ['active', 'suspended', 'inactive'];

/**
 * Open the member editor.
 *
 * @param {Object} options
 * @param {Object|null} [options.member] Existing record; omit to create.
 * @param {Function} [options.onSaved] Called with the saved member.
 * @returns {void}
 */
export function openMemberForm(options) {
  const opts = options || {};
  const editing = Boolean(opts.member && opts.member.id);
  const member = opts.member || {};

  /** @type {Object<string, {control: HTMLElement, wrap: HTMLElement}>} */
  const controls = {};
  let dirty = false;

  // -- controls ---------------------------------------------------------- //
  const nameInput = ui.input({
    value: member.name || '',
    autocomplete: 'off',
    onInput: () => { dirty = true; },
  });
  nameInput.id = 'mem-name';

  const codeInput = ui.input({
    value: member.member_code || '',
    mono: true,
    autocomplete: 'off',
    disabled: editing,
    onInput: () => { dirty = true; },
  });
  codeInput.id = 'mem-code';

  const typeSelect = ui.select({
    value: member.member_type || 'student',
    options: MEMBER_TYPES.map((value) => ({ value, label: t(`memberType.${value}`) })),
    onChange: () => { dirty = true; },
  });
  typeSelect.id = 'mem-type';

  const statusSelect = ui.select({
    value: member.status || 'active',
    options: MEMBER_STATUSES.map((value) => ({ value, label: t(`status.${value}`) })),
    onChange: () => { dirty = true; },
  });
  statusSelect.id = 'mem-status';

  const formInput = ui.input({
    value: member.form || '',
    placeholder: '5',
    onInput: () => { dirty = true; },
  });
  formInput.id = 'mem-form';

  const classInput = ui.input({
    value: member.class_name || '',
    placeholder: '5 Amanah',
    onInput: () => { dirty = true; },
  });
  classInput.id = 'mem-class';

  const icInput = ui.input({
    value: member.ic_number || '',
    mono: true,
    inputMode: 'numeric',
    onInput: () => { dirty = true; },
  });
  icInput.id = 'mem-ic';

  const emailInput = ui.input({
    value: member.email || '',
    type: 'email',
    autocomplete: 'off',
    onInput: () => { dirty = true; },
  });
  emailInput.id = 'mem-email';

  const phoneInput = ui.input({
    value: member.phone || '',
    type: 'tel',
    autocomplete: 'off',
    onInput: () => { dirty = true; },
  });
  phoneInput.id = 'mem-phone';

  const addressInput = ui.textarea({
    value: member.address || '',
    rows: 2,
    onInput: () => { dirty = true; },
  });
  addressInput.id = 'mem-address';

  const noteInput = ui.textarea({
    value: member.note || '',
    rows: 2,
    onInput: () => { dirty = true; },
  });
  noteInput.id = 'mem-note';

  // -- fields ------------------------------------------------------------ //
  const fieldSpecs = [
    ['name', ui.field({ label: t('mem.form.name'), control: nameInput, required: true })],
    ['member_code', ui.field({
      label: t('mem.form.code'),
      control: codeInput,
      hint: editing ? null : t('mem.form.codeHint'),
    })],
    ['member_type', ui.field({ label: t('mem.form.type'), control: typeSelect })],
    ['status', ui.field({ label: t('mem.form.status'), control: statusSelect })],
    ['form', ui.field({ label: t('mem.form.form'), control: formInput })],
    ['class_name', ui.field({ label: t('mem.form.class'), control: classInput })],
    ['ic_number', ui.field({ label: t('mem.form.ic'), control: icInput, hint: t('mem.form.icHint') })],
    ['email', ui.field({ label: t('mem.form.email'), control: emailInput })],
    ['phone', ui.field({ label: t('mem.form.phone'), control: phoneInput })],
    ['address', ui.field({ label: t('mem.form.address'), control: addressInput, wide: true })],
    ['note', ui.field({ label: t('mem.form.notes'), control: noteInput, wide: true })],
  ];

  for (const [key, wrap] of fieldSpecs) {
    controls[key] = { control: wrap.querySelector('input, select, textarea'), wrap };
  }

  const body = ui.el('div', { class: 'form-grid' });
  for (const [, wrap] of fieldSpecs) body.appendChild(wrap);

  // -- errors ------------------------------------------------------------ //
  /** Show or clear the inline error for one field. */
  function setFieldError(key, message) {
    const entry = controls[key];
    if (!entry) return;
    const existing = entry.wrap.querySelector('.field__error');
    if (existing) existing.remove();
    entry.wrap.classList.toggle('field--error', Boolean(message));
    entry.control.setAttribute('aria-invalid', message ? 'true' : 'false');
    if (!message) {
      entry.control.removeAttribute('aria-describedby');
      return;
    }
    const id = `mem-${key}-error`;
    entry.wrap.appendChild(ui.el('p', { class: 'field__error', attrs: { id, role: 'alert' } },
      ui.icon('alertCircle', 14), ui.el('span', { text: message })));
    entry.control.setAttribute('aria-describedby', id);
  }

  /** Paint the server's `fields` map. */
  function applyServerErrors(fields) {
    for (const key of Object.keys(controls)) setFieldError(key, null);
    if (!fields) return;
    let first = null;
    for (const key of Object.keys(fields)) {
      if (!controls[key]) continue;
      setFieldError(key, t(`err.${fields[key]}`));
      if (!first) first = controls[key].control;
    }
    if (first) first.focus();
  }

  // -- footer ------------------------------------------------------------ //
  const saveButton = ui.button({
    label: editing ? t('common.saveChanges') : t('common.create'),
    variant: 'primary',
    icon: 'check',
    onClick: () => { save(); },
  });

  const dialog = ui.modal({
    title: editing ? t('mem.edit') : t('mem.new'),
    wide: true,
    guard: () => dirty,
    body,
    footer: ui.el('div', { class: 'u-row u-gap-8' },
      ui.button({ label: t('common.cancel'), onClick: () => dialog.close() }),
      saveButton),
  });

  // -- save -------------------------------------------------------------- //
  async function save() {
    const payload = {
      name: nameInput.value.trim(),
      member_type: typeSelect.value,
      status: statusSelect.value,
      form: formInput.value.trim(),
      class_name: classInput.value.trim(),
      ic_number: icInput.value.trim(),
      email: emailInput.value.trim(),
      phone: phoneInput.value.trim(),
      address: addressInput.value.trim(),
      note: noteInput.value.trim(),
    };
    if (!editing) payload.member_code = codeInput.value.trim();
    else delete payload.member_code; // immutable once assigned

    if (!payload.name) {
      setFieldError('name', t('validate.required'));
      nameInput.focus();
      return;
    }

    saveButton.disabled = true;
    saveButton.setAttribute('aria-busy', 'true');
    dialog.setBusy(true);

    try {
      const saved = editing
        ? await api.patch(`/api/members/${encodeURIComponent(String(member.id))}`, payload)
        : await api.post('/api/members', payload);
      dirty = false;
      const record = (saved && saved.member) || saved || {};
      ui.toast.success(editing ? t('toast.updated') : t('toast.created'), record.name || payload.name);
      dialog.close();
      if (opts.onSaved) opts.onSaved(record);
    } catch (error) {
      if (error && error.isValidation) {
        applyServerErrors(error.fields);
        if (!error.fields) ui.toast.error(t('toast.error'), ui.formatError(error).message);
      } else if (error && error.is && error.is('duplicate_member_code')) {
        setFieldError('member_code', t('err.duplicate_member_code'));
        codeInput.focus();
      } else if (error && error.is && error.is('invalid_ic')) {
        setFieldError('ic_number', t('validate.ic'));
        icInput.focus();
      } else {
        ui.toast.error(t('toast.error'), ui.formatError(error).message);
      }
    } finally {
      saveButton.disabled = false;
      saveButton.removeAttribute('aria-busy');
      dialog.setBusy(false);
    }
  }

  window.requestAnimationFrame(() => nameInput.focus());
}

export default { openMemberForm };
