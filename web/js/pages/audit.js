/**
 * Audit log.
 *
 * Read-only by construction: the database has `BEFORE UPDATE` and `BEFORE
 * DELETE` triggers on this table, so nothing on this screen can rewrite
 * history — the page only filters and reads it.
 *
 * The `detail` column is JSON produced by the server. It is shown collapsed, in
 * a drawer, rather than in the row: an audit table whose columns jump around
 * because one action carries four fields and the next carries none is unreadable.
 */

import { t, fmtNum, fmtDate } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';

/**
 * Render the audit screen.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  const state = store.listState('audit');
  let items = [];
  let meta = { page: 1, page_size: state.pageSize, total: 0 };
  let loading = true;
  let error = null;
  let destroyed = false;
  let timer = null;

  const title = ui.el('h1', { class: 'page__title', text: t('adm.audit.title') });
  const subtitle = ui.el('p', { class: 'page__sub', text: t('adm.audit.subtitle') });

  const exportButton = ui.button({
    label: t('adm.audit.export'),
    icon: 'download',
    onClick: () => exportCsv(),
  });

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle),
    ui.el('div', { class: 'page__actions' }, exportButton));

  const actionInput = ui.input({
    value: (state.filters && state.filters.action) || '',
    placeholder: t('adm.audit.filter.action'),
    onInput: () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        state.filters.action = actionInput.value.trim() || undefined;
        state.page = 1;
        load();
      }, 180);
    },
  });
  actionInput.id = 'audit-action';

  const actorInput = ui.input({
    value: (state.filters && state.filters.actor) || '',
    placeholder: t('adm.audit.filter.actor'),
    onInput: () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        state.filters.actor = actorInput.value.trim() || undefined;
        state.page = 1;
        load();
      }, 180);
    },
  });
  actorInput.id = 'audit-actor';

  const filterBar = ui.el('div', { class: 'filterbar' },
    ui.el('div', { class: 'filterbar__group' },
      ui.el('span', { class: 'filterbar__label', text: t('adm.audit.filter.action') }), actionInput),
    ui.el('div', { class: 'filterbar__group' },
      ui.el('span', { class: 'filterbar__label', text: t('adm.audit.filter.actor') }), actorInput));

  const body = ui.el('div', { class: 'u-col' });
  const footer = ui.el('div', { class: 'tbl__foot' });

  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } },
    head, filterBar, body, footer);

  // -- data -------------------------------------------------------------- //
  async function load() {
    loading = true;
    error = null;
    paint();
    try {
      const params = { page: state.page, page_size: state.pageSize };
      if (state.filters.action) params.action = state.filters.action;
      if (state.filters.actor) params.actor = state.filters.actor;
      const page = await api.getWithMeta('/api/audit', params);
      if (destroyed) return;
      items = page.data.items || [];
      meta = page.meta || meta;
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

  async function exportCsv() {
    try {
      const params = {};
      if (state.filters.action) params.action = state.filters.action;
      if (state.filters.actor) params.actor = state.filters.actor;
      const result = await api.get('/api/audit/export', params);
      const blob = await api.download(`/api/exports/${encodeURIComponent(result.filename)}`);
      api.saveBlob(blob, result.filename);
      ui.toast.success(t('toast.exported'), result.filename);
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  function detail(row) {
    let payload = row.detail;
    try {
      payload = JSON.stringify(JSON.parse(row.detail), null, 2);
    } catch (error) {
      /* keep the raw string */
    }
    const dialog = ui.modal({
      title: row.action,
      body: ui.el('div', { class: 'u-col', style: { gap: '12px' } },
        ui.kv([
          { label: t('adm.audit.col.when'), value: `${fmtDate(String(row.ts).slice(0, 10))} · ${String(row.ts).slice(11, 19)}` },
          { label: t('adm.audit.col.actor'), value: row.actor || t('common.na') },
          { label: t('adm.audit.col.target'), value: row.target || t('common.na') },
          { label: t('adm.audit.col.action'), value: row.action },
        ]),
        ui.el('pre', { class: 'codebox codebox--block', text: payload || t('common.na') })),
      footer: ui.button({ label: t('common.close'), onClick: () => dialog.close() }),
    });
  }

  // -- paint ------------------------------------------------------------- //
  function paint() {
    ui.clear(body);
    ui.clear(footer);

    if (error) {
      body.appendChild(ui.errorState({ error, onRetry: () => load() }));
      return;
    }

    const columns = [
      {
        key: 'ts',
        label: t('adm.audit.col.when'),
        render: (row) => ui.el('div', { class: 'cell-stack' },
          ui.el('span', { class: 'cell-stack__main u-num', text: fmtDate(String(row.ts).slice(0, 10)) }),
          ui.el('span', { class: 'cell-stack__sub u-num', text: String(row.ts).slice(11, 19) })),
      },
      { key: 'actor', label: t('adm.audit.col.actor'), render: (row) => ui.el('span', { text: row.actor || t('common.na') }) },
      { key: 'action', label: t('adm.audit.col.action'), render: (row) => ui.pill(row.action, 'neutral') },
      { key: 'target', label: t('adm.audit.col.target'), render: (row) => ui.el('span', { class: 'u-mono u-xs', text: row.target || t('common.na') }) },
      {
        key: 'detail',
        label: t('adm.audit.col.detail'),
        render: (row) => {
          const text = String(row.detail || '');
          const short = text.length > 64 ? `${text.slice(0, 64)}…` : text;
          return ui.el('span', { class: 'u-mono u-xs u-muted', text: short || t('common.na') });
        },
      },
    ];

    body.appendChild(ui.table({
      columns,
      rows: items,
      loading,
      compact: true,
      empty: { icon: 'archive', title: t('adm.audit.empty'), body: '' },
      onRowClick: (row) => detail(row),
    }));

    if (items.length) {
      footer.appendChild(ui.pagination({
        page: Number(meta.page) || 1,
        pageSize: Number(meta.page_size) || state.pageSize,
        total: Number(meta.total) || 0,
        onChange: (page) => {
          state.page = page;
          load();
        },
      }));
    }
  }

  const onLang = () => {
    title.textContent = t('adm.audit.title');
    subtitle.textContent = t('adm.audit.subtitle');
    paint();
  };
  document.addEventListener('imri:lang', onLang);

  paint();
  load();

  return {
    element: root,
    destroy() {
      destroyed = true;
      window.clearTimeout(timer);
      document.removeEventListener('imri:lang', onLang);
    },
  };
}

export default { render };
