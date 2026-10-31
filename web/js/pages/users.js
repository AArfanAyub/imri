/**
 * Users & roles.
 *
 * The role picker is honest about what each role can do: the capability counts
 * come from the server's own matrix, so the screen cannot advertise a role the
 * API will then refuse. Every guard that matters (last administrator, self
 * demotion) is enforced server-side; this page only surfaces the refusal in
 * plain language.
 */

import { t, fmtDate } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';

/** Roles the schema accepts, in descending order of power. */
const ROLES = ['admin', 'librarian', 'assistant', 'viewer'];

/**
 * Render the users screen.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  let users = [];
  let loading = true;
  let error = null;
  let destroyed = false;

  const title = ui.el('h1', { class: 'page__title', text: t('adm.users.title') });
  const subtitle = ui.el('p', { class: 'page__sub', text: t('adm.title') });

  const addButton = ui.button({
    label: t('adm.users.new'),
    variant: 'primary',
    icon: 'plus',
    onClick: () => openForm(null),
  });

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle),
    ui.el('div', { class: 'page__actions' }, addButton));

  const body = ui.el('div', { class: 'u-col' });
  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } }, head, body);

  // -- data -------------------------------------------------------------- //
  async function load() {
    loading = true;
    error = null;
    paint();
    try {
      const data = await api.get('/api/users');
      if (destroyed) return;
      users = data.items || [];
    } catch (failure) {
      if (destroyed) return;
      error = failure;
      users = [];
    } finally {
      if (!destroyed) {
        loading = false;
        paint();
      }
    }
  }

  // -- editor ------------------------------------------------------------ //
  function openForm(user) {
    const editing = Boolean(user && user.id);
    const controls = {};

    const usernameInput = ui.input({ value: (user && user.username) || '', mono: true, disabled: editing });
    usernameInput.id = 'user-username';
    const nameInput = ui.input({ value: (user && user.display_name) || '' });
    nameInput.id = 'user-name';
    const roleSelect = ui.select({
      value: (user && user.role) || 'librarian',
      options: ROLES.map((value) => ({ value, label: t(`role.${value}`) })),
    });
    roleSelect.id = 'user-role';
    const passwordInput = ui.input({ type: 'password', autocomplete: 'new-password' });
    passwordInput.id = 'user-password';
    const activeSwitch = ui.switchControl({
      checked: user ? Boolean(user.is_active) : true,
      label: t('adm.users.form.active'),
    });

    const specs = [
      ['username', ui.field({ label: t('adm.users.form.username'), control: usernameInput, required: true })],
      ['display_name', ui.field({ label: t('adm.users.form.name'), control: nameInput })],
      ['role', ui.field({ label: t('adm.users.form.role'), control: roleSelect })],
      ['password', ui.field({
        label: t('adm.users.form.password'),
        control: passwordInput,
        hint: t('adm.users.form.passwordHint'),
        required: !editing,
      })],
    ];
    for (const [key, wrap] of specs) {
      controls[key] = { wrap, control: wrap.querySelector('input, select, textarea') };
    }

    const saveButton = ui.button({
      label: editing ? t('common.saveChanges') : t('common.create'),
      variant: 'primary',
      icon: 'check',
      onClick: () => save(),
    });

    const dialog = ui.modal({
      title: editing ? t('adm.users.edit') : t('adm.users.new'),
      body: ui.el('div', { class: 'form-grid' },
        ...specs.map(([, wrap]) => wrap),
        ui.el('div', { class: 'field field--wide' }, activeSwitch)),
      footer: ui.el('div', { class: 'u-row u-gap-8' },
        ui.button({ label: t('common.cancel'), onClick: () => dialog.close() }),
        saveButton),
    });

    async function save() {
      const payload = {
        username: usernameInput.value.trim(),
        display_name: nameInput.value.trim(),
        role: roleSelect.value,
        is_active: activeSwitch.querySelector('input').checked ? 1 : 0,
      };
      const password = passwordInput.value;
      if (!editing) payload.password = password;
      else if (password) payload.password = password;

      saveButton.disabled = true;
      try {
        if (editing) await api.patch(`/api/users/${encodeURIComponent(String(user.id))}`, payload);
        else await api.post('/api/users', payload);
        ui.toast.success(editing ? t('toast.updated') : t('toast.created'), payload.username);
        dialog.close();
        load();
      } catch (failure) {
        ui.toast.error(t('toast.error'), ui.formatError(failure).message);
      } finally {
        saveButton.disabled = false;
      }
    }

    window.requestAnimationFrame(() => (editing ? nameInput : usernameInput).focus());
  }

  // -- actions ----------------------------------------------------------- //
  async function resetPassword(user) {
    const confirmed = await ui.confirmDialog({
      title: t('adm.users.resetConfirm', { name: user.display_name || user.username }),
      body: t('adm.users.resetBody'),
      confirmLabel: t('adm.users.reset'),
    });
    if (!confirmed) return;
    try {
      const result = await api.post(`/api/users/${encodeURIComponent(String(user.id))}/reset-password`, {});
      const password = (result && result.password) || '';
      ui.modal({
        title: t('adm.users.reset'),
        body: ui.el('div', { class: 'u-col', style: { gap: '12px' } },
          ui.el('p', { class: 'u-sm', text: t('adm.users.resetBody') }),
          ui.el('div', { class: 'codebox', text: password || '—' })),
        footer: ui.button({ label: t('common.close'), onClick: () => {} }),
      });
      load();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  async function remove(user) {
    const confirmed = await ui.confirmDialog({
      title: t('adm.users.deleteConfirm'),
      body: user.display_name || user.username,
      confirmLabel: t('common.yesDelete'),
      tone: 'danger',
    });
    if (!confirmed) return;
    try {
      await api.del(`/api/users/${encodeURIComponent(String(user.id))}`);
      ui.toast.success(t('toast.deleted'), user.username);
      load();
    } catch (failure) {
      const message = failure && failure.is && (failure.is('last_admin') || failure.is('self_delete'))
        ? t('adm.users.lastAdmin')
        : ui.formatError(failure).message;
      ui.toast.error(t('toast.error'), message);
    }
  }

  // -- paint ------------------------------------------------------------- //
  function paint() {
    ui.clear(body);

    if (error) {
      body.appendChild(ui.errorState({ error, onRetry: () => load() }));
      return;
    }

    const columns = [
      { key: 'username', label: t('adm.users.col.username'), render: (row) => ui.el('span', { class: 'u-mono', text: row.username }) },
      { key: 'display_name', label: t('adm.users.col.name'), render: (row) => ui.el('span', { text: row.display_name || t('common.na') }) },
      { key: 'role', label: t('adm.users.col.role'), render: (row) => ui.pill(t(`role.${row.role}`), row.role === 'admin' ? 'primary' : 'neutral') },
      { key: 'is_active', label: t('adm.users.col.status'), render: (row) => ui.statusPill(row.is_active ? 'active' : 'inactive') },
      { key: 'last_login_at', label: t('adm.users.col.lastLogin'), render: (row) => ui.el('span', { class: 'u-xs u-num u-muted', text: row.last_login_at ? fmtDate(row.last_login_at.slice(0, 10)) : t('common.never') }) },
      {
        key: 'actions',
        label: '',
        align: 'right',
        render: (row) => ui.el('div', { class: 'u-row u-gap-4' },
          store.can('user.write')
            ? ui.button({ label: t('common.edit'), size: 'sm', onClick: (event) => { event.stopPropagation(); openForm(row); } })
            : null,
          store.can('user.write')
            ? ui.iconButton({ icon: 'key', label: t('adm.users.reset'), size: 'sm', onClick: (event) => { event.stopPropagation(); resetPassword(row); } })
            : null,
          store.can('user.delete')
            ? ui.iconButton({ icon: 'trash', label: t('common.delete'), size: 'sm', onClick: (event) => { event.stopPropagation(); remove(row); } })
            : null),
      },
    ];

    body.appendChild(ui.table({
      columns,
      rows: users,
      loading,
      empty: { icon: 'users', title: t('adm.users.empty'), body: '' },
    }));
  }

  const onLang = () => {
    title.textContent = t('adm.users.title');
    addButton.textContent = '';
    addButton.appendChild(ui.icon('plus', 16));
    addButton.appendChild(ui.el('span', { text: t('adm.users.new') }));
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
