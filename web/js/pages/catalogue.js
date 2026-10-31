/**
 * The catalogue — search first, filters second, table third.
 *
 * State lives in `store.listState('catalogue')`, not in this module, so coming
 * back from a record restores the search term, the filters, the sort, the page
 * *and* the scroll offset. The router re-renders the page from scratch on every
 * navigation; anything held in a closure here would be lost.
 *
 * All filtering, sorting and paging happen on the server. The client sends the
 * filter set and renders what comes back, which is why "Export" can reuse the
 * exact same object: the file matches the view by construction rather than by
 * two implementations agreeing.
 */

import { t, fmtNum, fmtDate } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';
import { openBookForm } from './_book-form.js';
import { openBookDrawer } from './_book-drawer.js';
import { openImportWizard } from './_import-wizard.js';

/** List-state key; also the route name. */
const KEY = 'catalogue';

/** Material types, in catalogue order. */
const MATERIAL_TYPES = ['book', 'textbook', 'reference', 'magazine', 'av', 'kit', 'thesis', 'other'];

/** Languages offered by the server. */
const LANGUAGE_CODES = ['en', 'ms', 'zh', 'ta', 'ar', 'fr', 'de', 'es', 'id', 'ja', 'ko', 'other'];

/** Sort options offered in the toolbar, mapped to the server's sort keys. */
const SORTS = Object.freeze([
  { value: 'title:asc', labelKey: 'cat.sort.title' },
  { value: 'authors:asc', labelKey: 'cat.sort.author' },
  { value: 'pub_year:desc', labelKey: 'cat.sort.year' },
  { value: 'created_at:desc', labelKey: 'cat.sort.recent' },
]);

/** Availability toggle values, mapped to the query parameters they set. */
const AVAILABILITY = Object.freeze([
  { value: '', labelKey: 'common.all' },
  { value: 'available', labelKey: 'status.available' },
  { value: 'on_loan', labelKey: 'status.onLoan' },
]);

/** Title counts per material type, fetched once per session. */
let typeCounts = null;

/**
 * Whether the search box has already taken focus once this session.
 *
 * The catalogue is search-first, so arriving here should put the caret in the
 * search box — but only the first time. Stealing focus on every return from a
 * record would fight the reader who came back to click the next row.
 */
let searchFocusedOnce = false;

/**
 * Counts for the material chips.
 *
 * `GET /api/books` returns one page, so it cannot answer "how many magazines are
 * there" without a second request per type. The collection report already
 * aggregates by material type in one query; it needs `report.read`, so an
 * assistant simply sees chips without counts rather than an error.
 *
 * @returns {Promise<void>}
 */
async function loadTypeCounts() {
  if (typeCounts || !store.can('report.read')) return;
  try {
    const report = await api.get('/api/reports/collection');
    const counts = {};
    for (const row of report.by_material || []) counts[row.key] = Number(row.titles) || 0;
    typeCounts = counts;
  } catch (error) {
    typeCounts = {};
  }
}

/**
 * Wrap every case-insensitive occurrence of `term` in a `<mark>`.
 *
 * @param {string} text
 * @param {string} term
 * @returns {DocumentFragment}
 */
function highlight(text, term) {
  const fragment = document.createDocumentFragment();
  const source = String(text === undefined || text === null ? '' : text);
  const needle = String(term || '').trim();
  if (!needle) {
    fragment.appendChild(document.createTextNode(source));
    return fragment;
  }
  const haystack = source.toLowerCase();
  const lower = needle.toLowerCase();
  let cursor = 0;
  let found = haystack.indexOf(lower);
  while (found !== -1) {
    if (found > cursor) fragment.appendChild(document.createTextNode(source.slice(cursor, found)));
    fragment.appendChild(ui.el('mark', { class: 'hl', text: source.slice(found, found + needle.length) }));
    cursor = found + needle.length;
    found = haystack.indexOf(lower, cursor);
  }
  if (cursor < source.length) fragment.appendChild(document.createTextNode(source.slice(cursor)));
  return fragment;
}

/**
 * Render the catalogue.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  const state = store.listState(KEY);
  const selected = new Set();
  let rows = [];
  let meta = { page: 1, page_size: state.pageSize, total: 0, pages: 0 };
  let loading = false;
  let error = null;
  let destroyed = false;
  let searchTimer = null;

  // --- route query seeds the filters ------------------------------------- //
  const query = ctx.query || {};
  if (query.q !== undefined) state.q = query.q;
  if (query.material_type !== undefined) state.filters.material_type = query.material_type;
  if (query.language !== undefined) state.filters.language = query.language;
  if (query.status !== undefined) state.filters.status = query.status;
  if (query.available_only !== undefined) state.filters.available_only = query.available_only;
  if (query.page !== undefined) state.page = Math.max(1, Number(query.page) || 1);
  if (query.sort !== undefined) {
    state.sort = { key: query.sort, dir: query.order === 'desc' ? 'desc' : 'asc' };
  }

  /** @returns {Object} The query the server understands. */
  function listQuery() {
    const filters = state.filters || {};
    const out = {
      page: state.page,
      page_size: state.pageSize,
      sort: state.sort.key || 'title',
      order: state.sort.dir || 'asc',
    };
    if (state.q) out.q = state.q;
    if (filters.material_type) out.material_type = filters.material_type;
    if (filters.language) out.language = filters.language;
    if (filters.status) out.status = filters.status;
    if (filters.available_only) out.available_only = 1;
    return out;
  }

  /** @returns {boolean} */
  function isFiltered() {
    const filters = state.filters || {};
    return Boolean(state.q || filters.material_type || filters.language || filters.status || filters.available_only);
  }

  // --- chrome ------------------------------------------------------------ //
  const searchInput = ui.input({
    large: true,
    placeholder: t('cat.search'),
    ariaLabel: t('cat.search'),
    value: state.q,
    onInput: (event) => {
      state.q = event.target.value;
      if (searchTimer) window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        state.page = 1;
        load();
      }, 150);
    },
    onEnter: () => {
      if (searchTimer) window.clearTimeout(searchTimer);
      state.page = 1;
      load();
    },
  });
  searchInput.id = 'catalogue-search';

  const searchBox = ui.el('div', { class: 'search-box' },
    ui.el('span', { class: 'search-box__icon' }, ui.icon('search', 18)),
    searchInput);

  const filterBar = ui.el('div', { class: 'cat__filters' });
  const activeBar = ui.el('div', { class: 'cat__active' });
  const resultsHost = ui.el('div');

  const importButton = ui.button({
    label: t('common.import'),
    icon: 'upload',
    onClick: () => openImportWizard({ onDone: (result) => { if (result && result.imported) load(); } }),
  });
  const exportButton = ui.button({
    label: t('common.export'),
    icon: 'download',
    onClick: () => runExport(),
  });
  const addButton = ui.button({
    label: t('cat.new'),
    variant: 'primary',
    icon: 'plus',
    onClick: () => openEditor(),
  });

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' },
      ui.el('h1', { class: 'page__title', text: t('cat.title') }),
      ui.el('p', { class: 'page__sub', text: t('cat.subtitle') })),
    ui.el('div', { class: 'page__actions' },
      store.can('import.run') ? importButton : null,
      store.can('export.run') ? exportButton : null,
      store.can('book.write') ? addButton : null));

  const element = ui.el('div', { class: 'u-col', style: { gap: '12px' } },
    head, searchBox, filterBar, activeBar, resultsHost);

  // --- filter bar -------------------------------------------------------- //
  function paintFilters() {
    filterBar.textContent = '';
    const filters = state.filters || {};

    const materialItems = MATERIAL_TYPES
      .filter((code) => !typeCounts || typeCounts[code] === undefined || typeCounts[code] > 0 || filters.material_type === code)
      .map((code) => ({
        value: code,
        label: t(`type.${code}`),
        count: typeCounts && typeCounts[code] !== undefined ? typeCounts[code] : undefined,
      }));

    filterBar.appendChild(ui.el('div', { class: 'cat__filter-group' },
      ui.el('span', { class: 'cat__filter-label', text: t('cat.filter.material') }),
      ui.chips({
        items: materialItems,
        value: filters.material_type || '',
        onChange: (value) => {
          state.filters.material_type = value;
          state.page = 1;
          load();
        },
      })));

    filterBar.appendChild(ui.el('div', { class: 'cat__filter-group' },
      ui.el('span', { class: 'cat__filter-label', text: t('cat.filter.availability') }),
      ui.chips({
        items: AVAILABILITY.filter((entry) => entry.value).map((entry) => ({
          value: entry.value,
          label: t(entry.labelKey),
        })),
        value: filters.available_only ? 'available' : (filters.status || ''),
        onChange: (value) => {
          state.filters.available_only = value === 'available' ? 1 : '';
          state.filters.status = value === 'on_loan' ? 'on_loan' : '';
          state.page = 1;
          load();
        },
      })));

    const language = ui.select({
      value: filters.language || '',
      placeholder: t('common.all'),
      options: LANGUAGE_CODES.map((code) => ({ value: code, label: t(`lang.${code}`) })),
      onChange: (event) => {
        state.filters.language = event.target.value;
        state.page = 1;
        load();
      },
    });
    language.setAttribute('aria-label', t('cat.filter.language'));

    filterBar.appendChild(ui.el('div', { class: 'cat__filter-group' },
      ui.el('span', { class: 'cat__filter-label', text: t('cat.filter.language') }),
      language));

    if (isFiltered()) {
      filterBar.appendChild(ui.button({
        label: t('cat.filter.clear'),
        variant: 'ghost',
        size: 'sm',
        icon: 'x',
        onClick: () => {
          state.q = '';
          state.filters = {};
          state.page = 1;
          searchInput.value = '';
          load();
        },
      }));
    }

    paintActive();
  }

  /** The removable tags under the filter bar. */
  function paintActive() {
    activeBar.textContent = '';
    if (!isFiltered()) {
      activeBar.hidden = true;
      return;
    }
    activeBar.hidden = false;
    activeBar.appendChild(ui.el('span', { class: 'cat__filter-label', text: t('cat.filter.applied') }));

    const tag = (label, onRemove) => ui.el('span', { class: 'tag' },
      ui.el('span', { text: label }),
      ui.el('button', {
        class: 'tag__x',
        attrs: { type: 'button', 'aria-label': t('cat.filter.remove', { label }) },
        on: { click: onRemove },
      }, ui.icon('x', 12)));

    if (state.q) {
      activeBar.appendChild(tag(`“${state.q}”`, () => {
        state.q = '';
        searchInput.value = '';
        state.page = 1;
        load();
      }));
    }
    const filters = state.filters || {};
    if (filters.material_type) {
      activeBar.appendChild(tag(t(`type.${filters.material_type}`), () => {
        delete state.filters.material_type;
        state.page = 1;
        load();
      }));
    }
    if (filters.status) {
      activeBar.appendChild(tag(t(`status.${filters.status === 'on_loan' ? 'onLoan' : filters.status}`), () => {
        delete state.filters.status;
        state.page = 1;
        load();
      }));
    }
    if (filters.available_only) {
      activeBar.appendChild(tag(t('status.available'), () => {
        delete state.filters.available_only;
        state.page = 1;
        load();
      }));
    }
    if (filters.language) {
      activeBar.appendChild(tag(t(`lang.${filters.language}`), () => {
        delete state.filters.language;
        state.page = 1;
        load();
      }));
    }
  }

  // --- results ----------------------------------------------------------- //
  /**
   * @param {Object} row
   * @returns {HTMLElement}
   */
  function titleCell(row) {
    const main = ui.el('span', { class: 'cat__title-main' });
    main.appendChild(highlight(row.title || '', state.q));
    const cell = ui.el('span', { class: 'cat__title' }, main);
    if (row.authors) cell.appendChild(ui.el('span', { class: 'cat__title-sub', text: row.authors }));
    return cell;
  }

  /**
   * Available / total, with a bar when some copies are out.
   * @param {Object} row
   * @returns {HTMLElement}
   */
  function copiesCell(row) {
    const total = Number(row.total_copies) || 0;
    const available = Number(row.available) || 0;
    const wrapper = ui.el('span', { class: 'copies' });
    if (total > 0 && available < total) {
      const fill = ui.el('span', { class: 'copies__fill', style: { width: `${Math.round((available / total) * 100)}%` } });
      wrapper.appendChild(ui.el('span', { class: 'copies__bar' }, fill));
    }
    wrapper.appendChild(ui.el('span', {
      class: 'copies__num',
      text: `${fmtNum(available)}/${fmtNum(total)}`,
      attrs: { title: t('cat.col.copies') },
    }));
    return wrapper;
  }

  /** The toolbar above the table. */
  function buildToolbar() {
    const total = Number(meta.total) || 0;
    const countText = total === 1 ? t('cat.countOne') : t('cat.count', { count: fmtNum(total) });

    const sortSelect = ui.select({
      value: `${state.sort.key}:${state.sort.dir}`,
      options: SORTS.map((entry) => ({ value: entry.value, label: t(entry.labelKey) })),
      onChange: (event) => {
        const parts = String(event.target.value).split(':');
        state.sort = { key: parts[0], dir: parts[1] || 'asc' };
        state.page = 1;
        load();
      },
    });
    sortSelect.setAttribute('aria-label', t('cat.sortBy'));

    return ui.el('div', { class: 'cat__toolbar' },
      ui.el('span', { class: 'cat__count', text: countText }),
      ui.el('div', { class: 'cat__sort' },
        ui.el('span', { class: 'cat__filter-label', text: t('cat.sortBy') }),
        sortSelect));
  }

  /** The bulk-action strip, shown only when something is ticked. */
  function buildSelectionBar() {
    if (!selected.size) return null;
    return ui.el('div', { class: 'selbar' },
      ui.el('span', { class: 'selbar__count', text: t('cat.selected', { count: fmtNum(selected.size) }) }),
      ui.el('span', { class: 'selbar__grow' }),
      ui.button({
        label: t('cat.bulk.withdraw'),
        variant: 'secondary',
        size: 'sm',
        icon: 'archive',
        disabled: !store.can('book.write'),
        onClick: () => bulkWithdraw(),
      }),
      ui.button({
        label: t('cat.bulk.clear'),
        variant: 'ghost',
        size: 'sm',
        onClick: () => {
          selected.clear();
          paintResults();
        },
      }));
  }

  /** The table plus its footer. */
  function buildTable() {
    const columns = [
      {
        key: '__check',
        label: '',
        className: 'tbl__check',
        cellClass: 'tbl__check',
        width: '40px',
        render: (row) => ui.checkbox({
          checked: selected.has(row.id),
          ariaLabel: row.title,
          onChange: (checked) => {
            if (checked) selected.add(row.id);
            else selected.delete(row.id);
            paintResults();
          },
        }),
      },
      { key: 'title', label: t('cat.col.title'), sortable: true, render: titleCell },
      {
        key: 'accession_no',
        label: t('cat.col.accession'),
        sortable: true,
        render: (row) => ui.el('span', { class: 'u-mono u-xs', text: row.accession_no || '' }),
      },
      { key: 'call_no', label: t('cat.col.callNo'), sortable: true, render: (row) => ui.el('span', { class: 'u-mono u-xs', text: row.call_no || t('common.na') }) },
      {
        key: 'material_type',
        label: t('cat.col.type'),
        sortable: true,
        render: (row) => ui.el('span', { text: row.material_type ? t(`type.${row.material_type}`) : t('common.na') }),
      },
      { key: 'copies', label: t('cat.col.copies'), align: 'right', render: copiesCell },
      { key: 'status', label: t('cat.col.status'), render: (row) => ui.statusPill(row.status) },
    ];

    const tableNode = ui.table({
      columns,
      rows,
      loading,
      compact: false,
      empty: emptyFor(),
      sort: { key: state.sort.key, dir: state.sort.dir },
      onSort: (key) => {
        state.sort = { key, dir: state.sort.key === key && state.sort.dir === 'asc' ? 'desc' : 'asc' };
        state.page = 1;
        load();
      },
      onRowClick: (row) => openRecord(row.id),
    });

    // Header checkbox: ui.table renders a text label, so the control is placed
    // once the header exists.
    const headerCell = tableNode.querySelector('thead th');
    if (headerCell && rows.length) {
      headerCell.textContent = '';
      const all = rows.every((row) => selected.has(row.id));
      headerCell.appendChild(ui.checkbox({
        checked: all,
        ariaLabel: t('common.select'),
        onChange: (checked) => {
          for (const row of rows) {
            if (checked) selected.add(row.id);
            else selected.delete(row.id);
          }
          paintResults();
        },
      }));
    }

    // Up / down move between rows; Enter is handled by ui.table itself.
    const tbody = tableNode.querySelector('tbody');
    if (tbody) {
      tbody.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        const cells = Array.from(tbody.querySelectorAll('tr[tabindex="0"]'));
        const index = cells.indexOf(document.activeElement);
        if (index === -1) return;
        event.preventDefault();
        const next = cells[index + (event.key === 'ArrowDown' ? 1 : -1)];
        if (next) next.focus();
      });
    }

    return tableNode;
  }

  /** @returns {Object} The right empty state for the situation. */
  function emptyFor() {
    if (error) {
      return {
        icon: 'alert',
        title: t('errstate.title'),
        body: ui.formatError(error).message,
        action: ui.button({ label: t('common.retry'), icon: 'refresh', onClick: () => load() }),
      };
    }
    if (state.q) {
      return {
        icon: 'search',
        title: t('cat.empty.search.title', { term: state.q }),
        body: t('cat.empty.search.body'),
        action: ui.button({
          label: t('cat.filter.clear'),
          icon: 'x',
          onClick: () => {
            state.q = '';
            state.filters = {};
            state.page = 1;
            searchInput.value = '';
            load();
          },
        }),
      };
    }
    if (isFiltered()) {
      return {
        icon: 'filter',
        title: t('cat.empty.filtered'),
        body: t('cat.empty.search.body'),
        action: ui.button({
          label: t('cat.filter.clear'),
          icon: 'x',
          onClick: () => {
            state.filters = {};
            state.page = 1;
            load();
          },
        }),
      };
    }
    return {
      icon: 'book',
      title: t('cat.empty.title'),
      body: t('cat.empty.body'),
      action: ui.el('div', { class: 'u-row' },
        store.can('book.write')
          ? ui.button({ label: t('cat.empty.addFirst'), variant: 'primary', icon: 'plus', onClick: () => openEditor() })
          : null,
        store.can('import.run')
          ? ui.button({ label: t('cat.empty.importAction'), icon: 'upload', onClick: () => openImportWizard({ onDone: () => load() }) })
          : null),
    };
  }

  /** The pager plus the page-size control. */
  function buildFoot() {
    if (!rows.length) return null;

    const sizeSelect = ui.select({
      value: String(state.pageSize),
      options: [25, 50, 100].map((size) => ({ value: String(size), label: `${size}` })),
      onChange: (event) => {
        state.pageSize = Number(event.target.value) || 25;
        state.page = 1;
        load();
      },
    });
    sizeSelect.setAttribute('aria-label', t('common.rows'));

    return ui.el('div', { class: 'cat__foot' },
      ui.pagination({
        page: Number(meta.page) || 1,
        pageSize: Number(meta.page_size) || state.pageSize,
        total: Number(meta.total) || 0,
        onChange: (page) => {
          state.page = page;
          selected.clear();
          load();
        },
      }),
      sizeSelect);
  }

  /** Rebuild the results card from `rows` / `meta` / `loading` / `error`. */
  function paintResults() {
    resultsHost.textContent = '';

    const cardNode = ui.el('div', { class: 'card' });
    cardNode.appendChild(buildToolbar());
    const bar = buildSelectionBar();
    if (bar) cardNode.appendChild(bar);
    cardNode.appendChild(buildTable());
    const foot = buildFoot();
    if (foot) cardNode.appendChild(foot);
    resultsHost.appendChild(cardNode);
  }

  // --- actions ----------------------------------------------------------- //

  /**
   * @param {number} id
   */
  function openRecord(id) {
    openBookDrawer(id, {
      onChanged: () => load(),
      onClose: () => {
        // Returning to the list keeps the scroll offset the reader had.
        const content = document.querySelector('.content');
        if (content) state.scrollTop = content.scrollTop;
      },
    });
  }

  function openEditor() {
    openBookForm({
      mode: 'create',
      onSaved: () => {
        state.page = 1;
        load();
      },
    });
  }

  /** Withdraw every ticked title, then refresh only the list. */
  async function bulkWithdraw() {
    const ids = Array.from(selected);
    if (!ids.length) return;
    const ok = await ui.confirmDialog({
      title: t('cat.bulk.title', { count: fmtNum(ids.length) }),
      body: t('cat.bulk.body'),
      confirmLabel: t('cat.bulk.withdraw'),
      tone: 'danger',
    });
    if (!ok) return;
    try {
      const result = await api.post('/api/books/bulk', { ids, patch: { status: 'withdrawn' } });
      const updated = Number(result && result.updated) || 0;
      if (updated) ui.toast.success(t('cat.bulk.done', { count: fmtNum(updated) }));
      else ui.toast.info(t('cat.bulk.none'));
      selected.clear();
      load();
    } catch (error) {
      ui.toast.error(t('toast.error'), ui.formatError(error).message);
    }
  }

  /**
   * Export the current view.
   *
   * The API writes the file and returns its metadata (the export is a server-side
   * artefact so it can be re-downloaded from the exports folder), so this is two
   * steps: build it, then fetch it.
   */
  async function runExport() {
    exportButton.disabled = true;
    exportButton.setAttribute('aria-busy', 'true');
    try {
      const built = await api.get('/api/books/export', Object.assign(listQuery(), { format: 'csv' }));
      if (!built || !built.rows) {
        ui.toast.info(t('cat.export.empty'));
        return;
      }
      await api.download(`/api/exports/${encodeURIComponent(built.filename)}`, null, built.filename);
      ui.toast.success(t('cat.export.done', { count: fmtNum(built.rows) }));
    } catch (error) {
      ui.toast.error(t('toast.error'), ui.formatError(error).message);
    } finally {
      exportButton.disabled = false;
      exportButton.removeAttribute('aria-busy');
    }
  }

  // --- data -------------------------------------------------------------- //

  /** Fetch one page and repaint. */
  async function load() {
    loading = true;
    error = null;
    paintResults();
    try {
      const [page] = await Promise.all([
        api.getWithMeta('/api/books', listQuery()),
        loadTypeCounts(),
      ]);
      if (destroyed) return;
      rows = (page.data && page.data.items) || [];
      meta = page.meta || { page: 1, page_size: state.pageSize, total: rows.length, pages: 1 };
      // A page beyond the end (after a filter narrowed the set) falls back to 1.
      if (!rows.length && Number(meta.total) > 0 && Number(state.page) > 1) {
        state.page = 1;
        loading = false;
        await load();
        return;
      }
      loading = false;
      paintFilters();
      paintResults();
    } catch (err) {
      if (destroyed) return;
      if (err && err.is && err.is('aborted')) return;
      loading = false;
      rows = [];
      error = err;
      paintResults();
    }
  }

  // --- lifecycle --------------------------------------------------------- //

  const content = document.querySelector('.content');
  if (state.scrollTop) {
    const restore = state.scrollTop;
    window.requestAnimationFrame(() => {
      if (content) content.scrollTop = restore;
    });
  }

  // First visit only, and never when something else on this route wants the
  // caret: a record (`/catalogue/:id`) traps focus in its drawer, and
  // `?intent=new` opens the editor, which focuses its own title field.
  const openingRecord = Boolean(ctx.params && ctx.params.id);
  if (!searchFocusedOnce && !openingRecord && query.intent !== 'new') {
    searchFocusedOnce = true;
    window.requestAnimationFrame(() => searchInput.focus());
  }

  const onLang = () => {
    searchInput.placeholder = t('cat.search');
    searchInput.setAttribute('aria-label', t('cat.search'));
    head.querySelector('.page__title').textContent = t('cat.title');
    head.querySelector('.page__sub').textContent = t('cat.subtitle');
    paintFilters();
    paintResults();
  };
  document.addEventListener('imri:lang', onLang);

  paintFilters();
  load();

  // `?intent=new` opens the editor on arrival (the dashboard quick action).
  if (query.intent === 'new' && store.can('book.write')) {
    window.requestAnimationFrame(() => openEditor());
  }

  return {
    element,
    /** Re-fetch the current page — used by the record drawer after an edit. */
    reload: () => load(),
    destroy() {
      destroyed = true;
      if (searchTimer) window.clearTimeout(searchTimer);
      if (content) state.scrollTop = content.scrollTop;
      document.removeEventListener('imri:lang', onLang);
    },
  };
}

export default { render };
