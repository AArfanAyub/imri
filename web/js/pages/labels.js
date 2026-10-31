/**
 * Labels & cards.
 *
 * Both encoders are pure Python on the server and return SVG, so the preview is
 * an `<img>` pointing at `/api/labels/...`. That keeps the QR matrix and the
 * Code 39 bar widths out of the browser entirely — one implementation, printed
 * and previewed from the same source, which is the only way the label on the
 * spine can be guaranteed to scan like the one on screen.
 */

import { t, fmtNum } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';

/**
 * Render the labels screen.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  let mode = (ctx.query && ctx.query.mode) === 'card' ? 'card' : 'book';
  let searchTerm = '';
  let candidates = [];
  let selected = new Set();
  let previewItem = null;
  let scale = 4;
  let code39 = true;
  let destroyed = false;
  let timer = null;

  const title = ui.el('h1', { class: 'page__title', text: t('label.title') });
  const subtitle = ui.el('p', { class: 'page__sub', text: t('label.subtitle') });
  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle));

  const modeTabs = ui.tabs({
    value: mode,
    items: [
      { value: 'book', label: t('label.book') },
      { value: 'card', label: t('label.card') },
    ],
    onChange: (value) => {
      mode = value;
      selected.clear();
      previewItem = null;
      load();
      paintPreview();
    },
  });

  // -- picker ------------------------------------------------------------ //
  const searchInput = ui.input({
    placeholder: mode === 'card' ? t('mem.search') : t('cat.search'),
    large: true,
    onInput: () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        searchTerm = searchInput.value.trim();
        load();
      }, 160);
    },
  });
  searchInput.id = 'labels-search';

  const listBox = ui.el('div', { class: 'picklist picklist--tall' });
  const pickerCard = ui.el('div', { class: 'card card--pad u-col', style: { gap: '12px' } },
    ui.el('div', { class: 'searchfield' },
      ui.el('span', { class: 'searchfield__icon' }, ui.icon('search', 18)),
      searchInput),
    listBox);

  // -- preview ----------------------------------------------------------- //
  const previewBox = ui.el('div', { class: 'label-preview' });

  const scaleSelect = ui.select({
    value: String(scale),
    options: [2, 3, 4, 6, 8].map((value) => ({ value: String(value), label: `${value}×` })),
    onChange: (event) => {
      scale = Number(event.target.value) || 4;
      paintPreview();
    },
  });

  const barcodeSwitch = ui.switchControl({
    checked: code39,
    label: t('label.barcode'),
    onChange: (checked) => {
      code39 = checked;
      paintPreview();
    },
  });

  const printButton = ui.button({
    label: t('label.print'),
    variant: 'primary',
    icon: 'printer',
    onClick: () => printPreview(),
  });

  const sheetButton = ui.button({
    label: t('label.sheet'),
    icon: 'grid',
    onClick: () => buildSheet(),
  });

  const controls = ui.el('div', { class: 'label-controls' },
    ui.field({ label: t('label.scale'), control: scaleSelect }),
    mode === 'book' ? barcodeSwitch : null,
    ui.el('div', { class: 'u-row u-gap-8' }, sheetButton, printButton));

  const previewCard = ui.el('div', { class: 'card card--pad u-col', style: { gap: '14px' } },
    ui.el('div', { class: 'u-row u-row--between' },
      ui.el('h2', { class: 'section__title', text: t('label.preview') }),
      ui.el('span', { class: 'u-xs u-muted', id: 'label-count' })),
    previewBox,
    controls);

  const grid = ui.el('div', { class: 'split split--wide' }, pickerCard, previewCard);
  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } }, head, modeTabs, grid);

  // -- data -------------------------------------------------------------- //
  async function load() {
    try {
      if (mode === 'card') {
        const data = await api.get('/api/members', { q: searchTerm, page: 1, page_size: 40 });
        candidates = (data.items || []).map((member) => ({
          id: member.id,
          main: member.name,
          sub: [member.member_code, member.class_name].filter(Boolean).join(' · '),
        }));
      } else {
        const data = await api.get('/api/books', { q: searchTerm, page: 1, page_size: 40 });
        candidates = (data.items || []).map((book) => ({
          id: book.id,
          main: book.title,
          sub: [book.accession_no, book.authors].filter(Boolean).join(' · '),
          copies: book.total_copies || book.copies || 0,
        }));
      }
      paintList();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  /** Copies of one title, fetched when a book is picked for preview. */
  async function copiesOf(bookId) {
    const data = await api.get(`/api/books/${encodeURIComponent(String(bookId))}/copies`);
    return data.items || data.copies || [];
  }

  // -- list -------------------------------------------------------------- //
  function paintList() {
    ui.clear(listBox);
    if (!candidates.length) {
      listBox.appendChild(ui.el('p', { class: 'u-sm u-muted', text: t('common.noResults') }));
      return;
    }
    for (const item of candidates) {
      const active = previewItem && previewItem.id === item.id;
      listBox.appendChild(ui.el('button', {
        class: `pickrow${active ? ' pickrow--active' : ''}`,
        attrs: { type: 'button' },
        on: { click: () => pick(item) },
      },
      ui.el('span', { class: 'pickrow__main', text: item.main }),
      ui.el('span', { class: 'pickrow__sub', text: item.sub })));
    }
  }

  async function pick(item) {
    previewItem = item;
    paintList();
    paintPreview();

    // Selecting a title queues all of its copies for the sheet.
    if (mode === 'book') {
      try {
        const copies = await copiesOf(item.id);
        for (const copy of copies) selected.add(copy.id);
        updateCount();
      } catch (failure) {
        ui.toast.error(t('toast.error'), ui.formatError(failure).message);
      }
    } else {
      selected.add(item.id);
      updateCount();
    }
  }

  function updateCount() {
    const node = document.getElementById('label-count');
    if (node) node.textContent = `${fmtNum(selected.size)} ${t('common.selected').toLowerCase()}`;
  }

  // -- preview ----------------------------------------------------------- //
  function paintPreview() {
    ui.clear(previewBox);
    if (!previewItem) {
      previewBox.appendChild(ui.emptyState({
        icon: 'box',
        title: t('label.empty.title'),
        body: t('label.empty.body'),
      }));
      updateCount();
      return;
    }

    let src;
    if (mode === 'card') {
      src = `/api/members/${encodeURIComponent(String(previewItem.id))}/card.svg?scale=${scale}`;
    } else {
      // The label endpoint takes a copy id, so the preview resolves the first
      // copy of the picked title before drawing.
      src = previewItem.copyId
        ? `/api/labels/book/${encodeURIComponent(String(previewItem.copyId))}.svg?scale=${scale}&code39=${code39 ? 1 : 0}`
        : null;
    }

    if (!src) {
      previewBox.appendChild(ui.el('p', { class: 'u-sm u-muted', text: t('common.loading') }));
      resolvePreviewCopy();
      updateCount();
      return;
    }

    previewBox.appendChild(ui.el('img', {
      class: 'label-preview__img',
      attrs: { src, alt: previewItem.main, loading: 'eager' },
    }));
    updateCount();
  }

  async function resolvePreviewCopy() {
    if (!previewItem || mode !== 'book') return;
    try {
      const copies = await copiesOf(previewItem.id);
      const first = copies[0];
      if (first && !destroyed) {
        previewItem.copyId = first.id;
        for (const copy of copies) selected.add(copy.id);
        paintPreview();
      }
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  // -- actions ----------------------------------------------------------- //
  function printPreview() {
    if (!previewItem) return;
    const src = mode === 'card'
      ? `/api/members/${encodeURIComponent(String(previewItem.id))}/card.svg?scale=${scale}`
      : (previewItem.copyId ? `/api/labels/book/${encodeURIComponent(String(previewItem.copyId))}.svg?scale=${scale}&code39=${code39 ? 1 : 0}` : null);
    if (!src) return;
    const frame = window.open('', '_blank');
    if (!frame) return;
    frame.document.write(`<!doctype html><title>${previewItem.main}</title>
      <style>body{margin:0;display:grid;place-items:center;min-height:100vh;font-family:system-ui}
      img{max-width:100%}@media print{body{min-height:auto}}</style>
      <img src="${src}" onload="window.print()">`);
    frame.document.close();
  }

  async function buildSheet() {
    if (mode !== 'book' || !selected.size) {
      ui.toast.warning(t('label.empty.title'), t('label.empty.body'));
      return;
    }
    try {
      const result = await api.post('/api/labels/sheet', {
        copy_ids: Array.from(selected),
        layout: '3x8',
        paper: 'A4',
      });
      window.open(`/api/exports/${encodeURIComponent(result.filename)}`, '_blank', 'noopener');
      ui.toast.success(t('toast.exported'), `${fmtNum(result.count)} · ${fmtNum(result.pages)} ${t('common.page')}`);
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  const onLang = () => {
    title.textContent = t('label.title');
    subtitle.textContent = t('label.subtitle');
    paintPreview();
  };
  document.addEventListener('imri:lang', onLang);

  paintPreview();
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
