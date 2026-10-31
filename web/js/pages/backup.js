/**
 * Backup & restore.
 *
 * Restore is the most destructive action in the product, so it is gated by a
 * typed confirmation rather than a click: the user has to type the backup's own
 * date to proceed. That is deliberately slower than a checkbox — the failure
 * mode here is replacing a term's worth of circulation history with an empty
 * database.
 *
 * The server takes a pre-restore snapshot before swapping, and this page tells
 * the user that, so nobody hesitates to restore out of fear of losing today.
 */

import { t, fmtNum, fmtDate } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';

/**
 * Human-readable byte size.
 * @param {number} bytes
 * @returns {string}
 */
function sizeLabel(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Render the backup screen.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  let items = [];
  let directory = '';
  let loading = true;
  let busy = false;
  let error = null;
  let destroyed = false;

  const title = ui.el('h1', { class: 'page__title', text: t('adm.backup.title') });
  const subtitle = ui.el('p', { class: 'page__sub', text: t('adm.backup.subtitle') });

  const createButton = ui.button({
    label: t('adm.backup.create'),
    variant: 'primary',
    icon: 'database',
    onClick: () => create(),
  });
  createButton.hidden = !store.can('backup.create');

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle),
    ui.el('div', { class: 'page__actions' }, createButton));

  const body = ui.el('div', { class: 'u-col' });
  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } }, head, body);

  // -- data -------------------------------------------------------------- //
  async function load() {
    loading = true;
    error = null;
    paint();
    try {
      const data = await api.get('/api/backup');
      if (destroyed) return;
      items = data.items || [];
      directory = data.dir || '';
    } catch (failure) {
      if (destroyed) return;
      error = failure;
      items = [];
    } finally {
      if (!destroyed) {
        loading = false;
        paint();
      }
    }
  }

  // -- actions ----------------------------------------------------------- //
  async function create() {
    if (busy) return;
    busy = true;
    createButton.disabled = true;
    createButton.setAttribute('aria-busy', 'true');
    try {
      const result = await api.post('/api/backup', {});
      ui.toast.success(t('toast.backupDone'), (result && result.name) || '');
      load();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    } finally {
      busy = false;
      createButton.disabled = false;
      createButton.removeAttribute('aria-busy');
    }
  }

  async function verify(item) {
    try {
      const result = await api.get(`/api/backup/${encodeURIComponent(item.name)}/verify`);
      if (result && result.ok) ui.toast.success(t('adm.backup.verified'), item.name);
      else ui.toast.error(t('adm.backup.corrupt'), item.name);
      load();
    } catch (failure) {
      ui.toast.error(t('adm.backup.corrupt'), ui.formatError(failure).message);
    }
  }

  function restore(item) {
    const stamp = String(item.created || item.name).slice(0, 10);
    const input = ui.input({ placeholder: stamp, mono: true });

    const confirmButton = ui.button({
      label: t('adm.backup.restore'),
      variant: 'danger-solid',
      disabled: true,
      onClick: async () => {
        confirmButton.disabled = true;
        try {
          await api.post('/api/backup/restore', { name: item.name, confirm: true });
          ui.toast.success(t('toast.restored'), item.name);
          dialog.close();
          load();
        } catch (failure) {
          ui.toast.error(t('toast.error'), ui.formatError(failure).message);
          confirmButton.disabled = false;
        }
      },
    });

    input.addEventListener('input', () => {
      confirmButton.disabled = input.value.trim() !== stamp;
    });

    const dialog = ui.modal({
      title: t('adm.backup.restoreConfirm'),
      body: ui.el('div', { class: 'u-col', style: { gap: '14px' } },
        ui.el('p', { class: 'u-sm', text: t('adm.backup.restoreBody') }),
        ui.el('div', { class: 'callout callout--danger', text: item.name }),
        ui.field({
          label: stamp,
          control: input,
          hint: t('common.required'),
        })),
      footer: ui.el('div', { class: 'u-row u-gap-8' },
        ui.button({ label: t('common.cancel'), onClick: () => dialog.close() }),
        confirmButton),
    });
    window.requestAnimationFrame(() => input.focus());
  }

  async function remove(item) {
    const confirmed = await ui.confirmDialog({
      title: t('adm.backup.deleteConfirm'),
      body: item.name,
      confirmLabel: t('common.yesDelete'),
      tone: 'danger',
    });
    if (!confirmed) return;
    try {
      await api.del(`/api/backup/${encodeURIComponent(item.name)}`);
      ui.toast.success(t('toast.deleted'), item.name);
      load();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  // -- paint ------------------------------------------------------------- //
  function paint() {
    ui.clear(body);

    if (error) {
      body.appendChild(ui.errorState({ error, onRetry: () => load() }));
      return;
    }

    if (directory) {
      body.appendChild(ui.el('div', { class: 'callout' },
        ui.el('span', { class: 'u-xs u-muted', text: t('adm.backup.subtitle') }),
        ui.el('span', { class: 'u-mono u-xs', text: directory })));
    }

    const columns = [
      {
        key: 'name',
        label: t('adm.backup.col.name'),
        render: (row) => ui.el('div', { class: 'cell-stack' },
          ui.el('span', { class: 'cell-stack__main u-mono', text: row.name }),
          row.label ? ui.el('span', { class: 'cell-stack__sub', text: row.label }) : null),
      },
      { key: 'created', label: t('adm.backup.col.created'), render: (row) => ui.el('span', { class: 'u-num u-xs', text: row.created ? fmtDate(String(row.created).slice(0, 10)) : t('common.na') }) },
      { key: 'files', label: t('adm.backup.col.files'), align: 'right', render: (row) => ui.el('span', { class: 'u-num u-xs', text: fmtNum(row.files) }) },
      { key: 'size', label: t('adm.backup.col.size'), align: 'right', render: (row) => ui.el('span', { class: 'u-num u-xs', text: sizeLabel(row.size) }) },
      {
        key: 'verified',
        label: t('common.verify'),
        render: (row) => (row.verified
          ? ui.pill(t('adm.backup.verified'), 'success')
          : ui.pill(t('adm.backup.corrupt'), 'danger')),
      },
      {
        key: 'actions',
        label: '',
        align: 'right',
        render: (row) => ui.el('div', { class: 'u-row u-gap-4' },
          ui.button({ label: t('common.verify'), size: 'sm', onClick: (event) => { event.stopPropagation(); verify(row); } }),
          store.can('backup.restore')
            ? ui.button({ label: t('adm.backup.restore'), size: 'sm', onClick: (event) => { event.stopPropagation(); restore(row); } })
            : null,
          store.can('backup.create')
            ? ui.iconButton({ icon: 'trash', label: t('common.delete'), size: 'sm', onClick: (event) => { event.stopPropagation(); remove(row); } })
            : null),
      },
    ];

    body.appendChild(ui.table({
      columns,
      rows: items,
      loading,
      empty: {
        icon: 'box',
        title: t('adm.backup.empty'),
        body: t('adm.backup.subtitle'),
        action: store.can('backup.create')
          ? ui.button({ label: t('adm.backup.create'), variant: 'primary', icon: 'database', onClick: () => create() })
          : null,
      },
    }));
  }

  const onLang = () => {
    title.textContent = t('adm.backup.title');
    subtitle.textContent = t('adm.backup.subtitle');
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
