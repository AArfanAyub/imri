/**
 * Members — the list of borrowers.
 *
 * Search first, then filters, then the table, exactly like the catalogue, and
 * for the same reason: a librarian almost always arrives knowing a name or a
 * class, not a filter combination.
 *
 * The row itself is not the editor. Clicking a row opens the member's profile
 * (`/members/:id`), because the useful question is "what does this person have
 * out", not "what is their phone number". Editing lives on that profile.
 */

import { t, fmtNum } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';
import { openMemberForm } from './_member-form.js';

const KEY = 'members';

/** Status filter values. */
const STATUSES = ['active', 'suspended', 'inactive'];

/** Type filter values. */
const TYPES = ['student', 'teacher', 'patron'];

/**
 * Render the member list.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  const state = store.listState(KEY);
  let rows = [];
  let meta = { page: 1, page_size: state.pageSize, total: 0, pages: 0 };
  let loading = true;
  let error = null;
  let destroyed = false;
  let searchTimer = null;
  let seq = 0;

  const query = ctx.query || {};
  if (query.q !== undefined) state.q = query.q;
  if (query.status !== undefined) state.filters.status = query.status;
  if (query.member_type !== undefined) state.filters.member_type = query.member_type;

  // -- shell ------------------------------------------------------------- //
  const title = ui.el('h1', { class: 'page__title', text: t('mem.title') });
  const subtitle = ui.el('p', { class: 'page__sub', text: t('mem.subtitle') });

  const addButton = ui.button({
    label: t('mem.new'),
    variant: 'primary',
    icon: 'plus',
    onClick: () => openMemberForm({ onSaved: () => load() }),
  });
  addButton.hidden = !store.can('member.write');

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle),
    ui.el('div', { class: 'page__actions' }, addButton));

  const searchInput = ui.input({
    value: state.q || '',
    placeholder: t('mem.search'),
    large: true,
    onInput: () => {
      state.q = searchInput.value;
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        state.page = 1;
        load();
      }, 150);
    },
    onEnter: () => {
      window.clearTimeout(searchTimer);
      state.page = 1;
      load();
    },
  });
  searchInput.id = 'members-search';
  searchInput.setAttribute('aria-label', t('mem.search'));

  const searchWrap = ui.el('div', { class: 'searchfield' },
    ui.el('span', { class: 'searchfield__icon' }, ui.icon('search', 18)),
    searchInput);

  const statusChips = ui.chips({
    value: state.filters.status || '',
    items: STATUSES.map((value) => ({ value, label: t(`status.${value}`) })),
    onChange: (value) => {
      state.filters.status = value || undefined;
      state.page = 1;
      paintFilters();
      load();
    },
  });

  const typeChips = ui.chips({
    value: state.filters.member_type || '',
    items: TYPES.map((value) => ({ value, label: t(`memberType.${value}`) })),
    onChange: (value) => {
      state.filters.member_type = value || undefined;
      state.page = 1;
      paintFilters();
      load();
    },
  });

  const clearButton = ui.button({
    label: t('common.clearAll'),
    variant: 'ghost',
    size: 'sm',
    icon: 'x',
    onClick: () => clearAll(),
  });

  const filterBar = ui.el('div', { class: 'filterbar' },
    ui.el('div', { class: 'filterbar__group' },
      ui.el('span', { class: 'filterbar__label', text: t('mem.filter.status') }), statusChips),
    ui.el('div', { class: 'filterbar__group' },
      ui.el('span', { class: 'filterbar__label', text: t('mem.filter.type') }), typeChips),
    ui.el('span', { class: 'filterbar__grow' }), clearButton);

  /** Show "Clear all" only when something is actually filtered. */
  function paintFilters() {
    clearButton.hidden = !(state.q || Object.keys(state.filters || {}).length);
  }

  /** Reset every filter and reload. */
  function clearAll() {
    state.q = '';
    state.filters = {};
    state.page = 1;
    searchInput.value = '';
    paintFilters();
    load();
  }
  paintFilters();

  const results = ui.el('div', { class: 'u-col' });
  const footer = ui.el('div', { class: 'tbl__foot' });

  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } },
    head, searchWrap, filterBar, results, footer);

  // -- data -------------------------------------------------------------- //
  async function load() {
    const mine = ++seq;
    loading = true;
    error = null;
    paint();

    const params = {
      page: state.page,
      page_size: state.pageSize,
      sort: state.sort.key || 'name',
      order: state.sort.dir || 'asc',
    };
    if (state.q) params.q = state.q;
    const filters = state.filters || {};
    if (filters.status) params.status = filters.status;
    if (filters.member_type) params.member_type = filters.member_type;

    try {
      const page = await api.getWithMeta('/api/members', params);
      if (destroyed || mine !== seq) return;
      rows = page.data.items || [];
      meta = page.meta || meta;
    } catch (failure) {
      if (destroyed || mine !== seq) return;
      error = failure;
      rows = [];
    } finally {
      if (!destroyed && mine === seq) {
        loading = false;
        paint();
      }
    }
  }

  // -- cells ------------------------------------------------------------- //
  function nameCell(row) {
    const secondary = [row.class_name, row.form ? `${t('mem.form.form')} ${row.form}` : null]
      .filter(Boolean).join(' · ');
    return ui.el('div', { class: 'cell-stack' },
      ui.el('span', { class: 'cell-stack__main', text: row.name }),
      secondary ? ui.el('span', { class: 'cell-stack__sub', text: secondary }) : null);
  }

  function loansCell(row) {
    const count = Number(row.active_loans) || 0;
    const max = store.maxLoans();
    return ui.el('span', {
      class: `u-num${count >= max ? ' u-warn-text' : ''}`,
      text: `${fmtNum(count)}/${fmtNum(max)}`,
      attrs: { title: t('mem.col.loans') },
    });
  }

  function owedCell(row) {
    const value = Number(row.outstanding) || 0;
    if (!value) return ui.el('span', { class: 'u-muted', text: t('common.na') });
    return ui.el('span', {
      class: 'u-num u-warn-text',
      text: `${store.state.settings.currency || ''} ${value.toFixed(2)}`,
    });
  }

  // -- empty states ------------------------------------------------------ //
  function emptyFor() {
    if (error) {
      return {
        icon: 'alertTriangle',
        title: t('errstate.title'),
        body: ui.formatError(error).message,
        action: ui.button({ label: t('common.retry'), variant: 'primary', icon: 'refresh', onClick: () => load() }),
      };
    }
    if (state.q || Object.keys(state.filters || {}).length) {
      return {
        icon: 'search',
        title: t('common.noResults'),
        body: t('mem.empty.filtered'),
        action: ui.button({ label: t('common.clearAll'), onClick: () => clearAll() }),
      };
    }
    return {
      icon: 'users',
      title: t('mem.empty.title'),
      body: t('mem.empty.body'),
      action: store.can('member.write')
        ? ui.button({
          label: t('mem.new'),
          variant: 'primary',
          icon: 'plus',
          onClick: () => openMemberForm({ onSaved: () => load() }),
        })
        : null,
    };
  }

  // -- paint ------------------------------------------------------------- //
  function paint() {
    ui.clear(results);
    ui.clear(footer);

    const columns = [
      { key: 'member_code', label: t('mem.col.code'), sortable: true, render: (row) => ui.el('span', { class: 'u-mono u-xs', text: row.member_code || '' }) },
      { key: 'name', label: t('mem.col.name'), sortable: true, render: nameCell },
      { key: 'member_type', label: t('mem.col.type'), sortable: true, render: (row) => ui.el('span', { text: row.member_type ? t(`memberType.${row.member_type}`) : t('common.na') }) },
      { key: 'class_name', label: t('mem.col.class'), sortable: true, render: (row) => ui.el('span', { text: row.class_name || t('common.na') }) },
      { key: 'active_loans', label: t('mem.col.loans'), align: 'right', render: loansCell },
      { key: 'outstanding', label: t('mem.col.outstanding'), align: 'right', render: owedCell },
      { key: 'status', label: t('mem.col.status'), render: (row) => ui.statusPill(row.status) },
    ];

    results.appendChild(ui.table({
      columns,
      rows,
      loading,
      empty: emptyFor(),
      sort: { key: state.sort.key || 'name', dir: state.sort.dir || 'asc' },
      onSort: (key) => {
        state.sort = { key, dir: state.sort.key === key && state.sort.dir === 'asc' ? 'desc' : 'asc' };
        state.page = 1;
        load();
      },
      onRowClick: (row) => ctx.navigate(`/members/${row.id}`),
    }));

    if (rows.length) {
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
    title.textContent = t('mem.title');
    subtitle.textContent = t('mem.subtitle');
    addButton.textContent = '';
    addButton.appendChild(ui.icon('plus', 16));
    addButton.appendChild(ui.el('span', { text: t('mem.new') }));
    searchInput.setAttribute('placeholder', t('mem.search'));
    paint();
  };
  document.addEventListener('imri:lang', onLang);

  paint();
  load();

  return {
    element: root,
    destroy() {
      destroyed = true;
      window.clearTimeout(searchTimer);
      document.removeEventListener('imri:lang', onLang);
    },
  };
}

export default { render };
