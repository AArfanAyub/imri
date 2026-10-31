/**
 * Reading lists — what each reader wants to read next.
 *
 * A list belongs to a member, so the screen is a two-pane picker: search for a
 * reader on the left, see their list on the right. That avoids inventing a
 * "all lists" endpoint the API does not have, and matches how the feature is
 * actually used — one reader at a time, usually while they stand at the counter.
 */

import { t, fmtNum } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';

/**
 * Render the reading-lists screen.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  let selected = null;
  let list = [];
  let candidates = [];
  let loadingList = false;
  let destroyed = false;
  let timer = null;

  const title = ui.el('h1', { class: 'page__title', text: t('nav.readingLists') });
  const subtitle = ui.el('p', { class: 'page__sub', text: t('mem.profile.readingList') });
  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle));

  const searchInput = ui.input({
    placeholder: t('mem.search'),
    large: true,
    onInput: () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(search, 160);
    },
  });
  searchInput.id = 'reading-search';

  const resultsBox = ui.el('div', { class: 'picklist' });
  const panel = ui.el('div', { class: 'card card--pad' });

  const left = ui.el('div', { class: 'card card--pad u-col', style: { gap: '12px' } },
    ui.el('div', { class: 'searchfield' },
      ui.el('span', { class: 'searchfield__icon' }, ui.icon('search', 18)),
      searchInput),
    resultsBox);

  const grid = ui.el('div', { class: 'split' }, left, panel);
  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } }, head, grid);

  // -- member picker ----------------------------------------------------- //
  async function search() {
    const term = searchInput.value.trim();
    try {
      const data = await api.get('/api/members', { q: term, page: 1, page_size: 12 });
      if (destroyed) return;
      candidates = data.items || [];
      paintCandidates();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  function paintCandidates() {
    ui.clear(resultsBox);
    if (!candidates.length) {
      resultsBox.appendChild(ui.el('p', { class: 'u-sm u-muted', text: t('common.noResults') }));
      return;
    }
    for (const member of candidates) {
      const active = selected && selected.id === member.id;
      resultsBox.appendChild(ui.el('button', {
        class: `pickrow${active ? ' pickrow--active' : ''}`,
        attrs: { type: 'button', 'aria-pressed': active ? 'true' : 'false' },
        on: { click: () => select(member) },
      },
      ui.el('span', { class: 'pickrow__main', text: member.name }),
      ui.el('span', { class: 'pickrow__sub', text: [member.member_code, member.class_name].filter(Boolean).join(' · ') })));
    }
  }

  function select(member) {
    selected = member;
    paintCandidates();
    loadList();
  }

  // -- list -------------------------------------------------------------- //
  async function loadList() {
    if (!selected) return;
    loadingList = true;
    paintPanel();
    try {
      const data = await api.get(`/api/members/${encodeURIComponent(String(selected.id))}/reading-list`);
      if (destroyed) return;
      list = data.items || [];
    } catch (failure) {
      if (destroyed) return;
      list = [];
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    } finally {
      if (!destroyed) {
        loadingList = false;
        paintPanel();
      }
    }
  }

  async function addBook() {
    const bookInput = ui.input({ placeholder: t('cat.search'), large: true });
    const box = ui.el('div', { class: 'u-col', style: { gap: '4px', maxHeight: '280px', overflow: 'auto' } });
    let searchTimer = null;

    const dialog = ui.modal({
      title: t('common.add'),
      body: ui.el('div', { class: 'u-col', style: { gap: '12px' } },
        ui.field({ label: t('circ.loan.title'), control: bookInput }),
        box),
      footer: ui.el('div', { class: 'u-row u-gap-8' },
        ui.button({ label: t('common.close'), onClick: () => dialog.close() })),
    });

    const run = async () => {
      const term = bookInput.value.trim();
      if (!term) {
        ui.clear(box);
        return;
      }
      try {
        const data = await api.get('/api/books', { q: term, page: 1, page_size: 8 });
        ui.clear(box);
        const rows = data.items || [];
        if (!rows.length) {
          box.appendChild(ui.el('p', { class: 'u-sm u-muted', text: t('common.noResults') }));
          return;
        }
        for (const book of rows) {
          box.appendChild(ui.el('button', {
            class: 'pickrow',
            attrs: { type: 'button' },
            on: {
              click: async () => {
                try {
                  await api.post(`/api/members/${encodeURIComponent(String(selected.id))}/reading-list`, { book_id: book.id });
                  ui.toast.success(t('toast.created'), book.title);
                  dialog.close();
                  loadList();
                } catch (failure) {
                  ui.toast.error(t('toast.error'), ui.formatError(failure).message);
                }
              },
            },
          },
          ui.el('span', { class: 'pickrow__main', text: book.title }),
          ui.el('span', { class: 'pickrow__sub', text: book.authors || '' })));
        }
      } catch (failure) {
        ui.toast.error(t('toast.error'), ui.formatError(failure).message);
      }
    };

    bookInput.addEventListener('input', () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(run, 180);
    });
    window.requestAnimationFrame(() => bookInput.focus());
  }

  async function reserve(item) {
    try {
      await api.post(`/api/members/${encodeURIComponent(String(selected.id))}/reading-list/${encodeURIComponent(String(item.id))}/reserve`, {});
      ui.toast.success(t('res.reserved'), (item.book && item.book.title) || '');
      loadList();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  async function remove(item) {
    try {
      await api.del(`/api/members/${encodeURIComponent(String(selected.id))}/reading-list/${encodeURIComponent(String(item.id))}`);
      ui.toast.success(t('toast.deleted'));
      loadList();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  // -- panel ------------------------------------------------------------- //
  function paintPanel() {
    ui.clear(panel);

    if (!selected) {
      panel.appendChild(ui.emptyState({
        icon: 'users',
        title: t('label.select'),
        body: t('mem.profile.readingList'),
      }));
      return;
    }

    const header = ui.el('div', { class: 'u-row u-row--between' },
      ui.el('div', { class: 'u-col' },
        ui.el('h2', { class: 'section__title', text: selected.name }),
        ui.el('span', { class: 'u-xs u-muted', text: [selected.member_code, selected.class_name].filter(Boolean).join(' · ') })),
      ui.el('div', { class: 'u-row u-gap-8' },
        ui.button({ label: t('mem.profile.title'), size: 'sm', onClick: () => ctx.navigate(`/members/${selected.id}`) }),
        store.can('member.write')
          ? ui.button({ label: t('common.add'), size: 'sm', variant: 'primary', icon: 'plus', onClick: () => addBook() })
          : null));
    panel.appendChild(header);

    if (loadingList) {
      panel.appendChild(ui.skeleton({ rows: 3 }));
      return;
    }

    if (!list.length) {
      panel.appendChild(ui.emptyState({
        icon: 'book',
        title: t('mem.profile.noList'),
        body: '',
        action: store.can('member.write')
          ? ui.button({ label: t('common.add'), variant: 'primary', icon: 'plus', onClick: () => addBook() })
          : null,
      }));
      return;
    }

    const rows = ui.el('div', { class: 'u-col', style: { gap: '6px' } });
    for (const item of list) {
      const book = item.book || {};
      rows.appendChild(ui.el('div', { class: 'queue-row' },
        ui.el('div', { class: 'queue-row__main' },
          ui.el('a', { class: 'queue-row__name', attrs: { href: `#/catalogue/${book.id}` }, text: book.title || '' }),
          ui.el('span', { class: 'queue-row__meta', text: [book.authors, book.call_no].filter(Boolean).join(' · ') })),
        ui.el('div', { class: 'u-row u-gap-4' },
          store.can('reservation.write')
            ? ui.button({ label: t('res.reserved'), size: 'sm', onClick: () => reserve(item) })
            : null,
          store.can('member.write')
            ? ui.iconButton({ icon: 'trash', label: t('common.remove'), size: 'sm', onClick: () => remove(item) })
            : null)));
    }
    panel.appendChild(rows);
    panel.appendChild(ui.el('p', { class: 'u-xs u-muted', text: t('common.results', { count: fmtNum(list.length) }) }));
  }

  const onLang = () => {
    title.textContent = t('nav.readingLists');
    paintPanel();
    paintCandidates();
  };
  document.addEventListener('imri:lang', onLang);

  paintPanel();
  search();

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
