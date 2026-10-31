/**
 * The book record drawer.
 *
 * A drawer rather than a modal because the list behind it must stay readable:
 * a librarian checking one title usually wants the next one too. 560 px, so the
 * metadata grid keeps two columns and the copy rows keep their actions on one
 * line.
 *
 * Loan rows are rendered as compact list rows instead of a `ui.table`: inside a
 * 560 px panel a five-column table either wraps or truncates the member name,
 * and both read worse than two lines of text. The copy rows carry real actions,
 * so they are buttons and menus, not table cells.
 */

import { t, fmtDate, fmtNum } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import { openBookForm } from './_book-form.js';
import { openLabelPreview } from './_label.js';

/** Copy statuses a librarian may set by hand. `on_loan` is loan-driven. */
const COPY_STATUSES = ['available', 'reserved', 'lost', 'damaged', 'withdrawn'];
const COPY_CONDITIONS = ['new', 'good', 'fair', 'poor'];

/**
 * One labelled value in the metadata grid.
 * @param {string} labelKey
 * @param {(string|Node)} value
 * @returns {HTMLElement}
 */
function metaField(labelKey, value) {
  const dd = ui.el('span', { class: 'book__value' });
  if (value instanceof Node) dd.appendChild(value);
  else dd.textContent = value === null || value === undefined || value === '' ? t('common.na') : String(value);
  return ui.el('div', { class: 'book__field' },
    ui.el('span', { class: 'book__label', text: t(labelKey) }),
    dd);
}

/**
 * A copy row: identity on the left, state and actions on the right.
 *
 * @param {Object} copy
 * @param {Object} handlers
 * @returns {HTMLElement}
 */
function copyRow(copy, handlers) {
  const meta = [`${t('cat.copies.copyNo')} ${fmtNum(copy.copy_no)}`];
  if (copy.location) meta.push(copy.location);
  if (copy.condition) meta.push(t(`condition.${copy.condition}`));

  const main = ui.el('div', { class: 'copy-row__main' },
    ui.el('span', { class: 'copy-row__barcode', text: copy.barcode || t('common.na') }),
    ui.el('span', { class: 'copy-row__meta', text: meta.join(' · ') }));

  if (copy.current_loan) {
    main.appendChild(ui.el('span', { class: 'copy-row__meta' },
      t('cat.copies.onLoanTo', { name: copy.current_loan.member_name || copy.current_loan.member_code || '' }),
      ui.el('span', { text: ' · ' }),
      t('cat.copies.due', { date: fmtDate(copy.current_loan.due_date, 'short') })));
  }

  const actions = ui.el('div', { class: 'u-row' },
    ui.statusPill(copy.status),
    ui.iconButton({
      icon: 'more',
      label: t('cat.detail.copyMenu'),
      onClick: (event) => {
        ui.menu({
          anchor: event.currentTarget,
          align: 'right',
          items: [
            { label: t('common.edit'), icon: 'edit', onClick: () => handlers.onEdit(copy) },
            {
              label: t('cat.copy.markLost'),
              icon: 'alertCircle',
              disabled: copy.status === 'lost' || copy.status === 'on_loan',
              onClick: () => handlers.onMarkLost(copy),
            },
            '-',
            {
              label: t('common.delete'),
              icon: 'trash',
              danger: true,
              disabled: copy.status === 'on_loan',
              onClick: () => handlers.onDelete(copy),
            },
          ],
        });
      },
    }));

  return ui.el('div', { class: 'copy-row' }, main, actions);
}

/**
 * Open the drawer for one title.
 *
 * @param {number} bookId
 * @param {{onChanged?: () => void, onClose?: () => void}} [options]
 * @returns {{close: () => Promise<void>, reload: () => Promise<void>}}
 */
export function openBookDrawer(bookId, options) {
  const opts = options || {};
  let record = null;
  let activeTab = 'current';
  let destroyed = false;

  const body = ui.el('div', { class: 'u-col', style: { gap: '20px' } },
    ui.skeleton({ variant: 'text', rows: 6 }));

  const editButton = ui.button({
    label: t('common.edit'),
    icon: 'edit',
    disabled: true,
    onClick: () => openEditor(),
  });
  const labelButton = ui.button({
    label: t('cat.detail.printLabel'),
    icon: 'printer',
    disabled: true,
    onClick: () => openLabel(),
  });
  const deleteButton = ui.button({
    label: t('common.delete'),
    variant: 'ghost',
    icon: 'trash',
    disabled: true,
    onClick: () => removeBook(),
  });

  const dialog = ui.drawer({
    title: t('cat.detail.title'),
    body,
    footer: [deleteButton, ui.el('span', { class: 'u-grow' }), labelButton, editButton],
    wide: true,
    onClose: () => {
      destroyed = true;
      if (opts.onClose) opts.onClose();
    },
  });

  /** @returns {?Object} The first copy, which is what the label prints. */
  function firstCopy() {
    return record && record.copies && record.copies.length ? record.copies[0] : null;
  }

  function openEditor() {
    if (!record) return;
    openBookForm({
      mode: 'edit',
      book: record.book,
      subjects: record.subjects,
      onSaved: () => {
        reload();
        if (opts.onChanged) opts.onChanged();
      },
    });
  }

  function openLabel() {
    const copy = firstCopy();
    if (!copy) {
      ui.toast.warning(t('cat.label.none'));
      return;
    }
    openLabelPreview({ copyId: copy.id, title: record.book.title });
  }

  /**
   * Patch one copy, then refresh.
   * @param {Object} copy
   * @param {Object} patch
   */
  async function patchCopy(copy, patch) {
    try {
      await api.patch(`/api/copies/${encodeURIComponent(String(copy.id))}`, patch);
      ui.toast.success(t('cat.copy.saved'));
      await reload();
      if (opts.onChanged) opts.onChanged();
    } catch (error) {
      ui.toast.error(t('toast.error'), ui.formatError(error).message);
    }
  }

  /** The small modal behind "Edit copy". */
  function editCopy(copy) {
    const location = ui.input({ value: copy.location || '' });
    const condition = ui.select({
      value: copy.condition || '',
      placeholder: t('common.none'),
      options: COPY_CONDITIONS.map((code) => ({ value: code, label: t(`condition.${code}`) })),
    });
    // `on_loan` is not a hand-settable state, but a copy that is out must show
    // its real status in the select rather than an empty box.
    const statusOptions = COPY_STATUSES.map((code) => ({
      value: code,
      label: t(`status.${code.replace(/_([a-z])/g, (whole, letter) => letter.toUpperCase())}`),
    }));
    if (copy.status === 'on_loan') {
      statusOptions.unshift({ value: 'on_loan', label: t('status.onLoan') });
    }
    const status = ui.select({
      value: copy.status,
      options: statusOptions,
      disabled: copy.status === 'on_loan',
    });
    const note = ui.textarea({ value: copy.note || '', rows: 2 });

    const save = ui.button({
      label: t('common.save'),
      variant: 'primary',
      onClick: async () => {
        save.disabled = true;
        await patchCopy(copy, {
          location: location.value.trim(),
          condition: condition.value,
          status: status.value,
          note: note.value.trim(),
        });
        copyDialog.close();
      },
    });

    const copyDialog = ui.modal({
      title: t('cat.copy.editTitle'),
      body: ui.el('div', { class: 'u-col', style: { gap: '16px' } },
        ui.field({ label: t('cat.copies.location'), control: location }),
        ui.field({ label: t('cat.copies.condition'), control: condition }),
        ui.field({ label: t('cat.copies.status'), control: status, hint: copy.status === 'on_loan' ? t('cat.copy.onLoan') : '' }),
        ui.field({ label: t('cat.copy.note'), control: note })),
      footer: [ui.button({ label: t('common.cancel'), onClick: () => copyDialog.close() }), save],
    });
    window.requestAnimationFrame(() => location.focus());
  }

  /**
   * @param {Object} copy
   */
  async function markLost(copy) {
    const ok = await ui.confirmDialog({
      title: t('cat.copy.markLostTitle'),
      body: t('cat.copy.markLostBody'),
      confirmLabel: t('cat.copy.markLost'),
      tone: 'danger',
    });
    if (!ok) return;
    await patchCopy(copy, { status: 'lost' });
  }

  /**
   * @param {Object} copy
   */
  async function deleteCopy(copy) {
    const ok = await ui.confirmDialog({
      title: t('cat.copy.deleteTitle'),
      body: t('cat.copy.deleteBody', { barcode: copy.barcode || '' }),
      confirmLabel: t('common.yesDelete'),
      tone: 'danger',
    });
    if (!ok) return;
    try {
      await api.del(`/api/copies/${encodeURIComponent(String(copy.id))}`);
      ui.toast.success(t('toast.deleted'), copy.barcode || '');
      await reload();
      if (opts.onChanged) opts.onChanged();
    } catch (error) {
      if (error && error.is && error.is('copy_on_loan')) {
        ui.toast.error(t('cat.copy.onLoan'));
      } else if (error && error.is && error.is('last_copy')) {
        ui.toast.error(t('cat.copy.lastCopy'));
      } else {
        ui.toast.error(t('toast.error'), ui.formatError(error).message);
      }
    }
  }

  /** Delete the whole title, refusing with the server's reason when it must. */
  async function removeBook() {
    if (!record) return;
    const book = record.book;
    const count = (record.copies || []).length;
    const ok = await ui.confirmDialog({
      title: t('cat.deleteTitle'),
      body: t('cat.deleteBody', { title: book.title, count: fmtNum(count) }),
      confirmLabel: t('common.yesDelete'),
      tone: 'danger',
    });
    if (!ok) return;
    try {
      await api.del(`/api/books/${encodeURIComponent(String(book.id))}`);
      ui.toast.success(t('cat.deleted'), book.title);
      await dialog.close();
      if (opts.onChanged) opts.onChanged();
    } catch (error) {
      if (error && error.is && error.is('book_has_active_loans')) {
        ui.toast.error(t('cat.deleteBlockedLoans'));
      } else if (error && error.is && error.is('book_has_reservations')) {
        ui.toast.error(t('cat.deleteBlockedHolds'));
      } else {
        ui.toast.error(t('toast.error'), ui.formatError(error).message);
      }
    }
  }

  /** The MARC block: a definition list, plus the raw JSON behind a toggle. */
  function marcSection(marc) {
    const entries = Object.keys(marc || {}).sort();
    if (!entries.length) {
      return ui.el('div', { class: 'book__section' },
        ui.el('h3', { class: 'book__section-title', text: t('cat.marc.title') }),
        ui.el('p', { class: 'u-sm u-muted', text: t('cat.marc.empty') }));
    }

    const list = ui.el('div', { class: 'marc' });
    for (const tag of entries) {
      list.appendChild(ui.el('span', { class: 'marc__tag', text: tag }));
      list.appendChild(ui.el('span', { text: String(marc[tag]) }));
    }

    const raw = ui.el('pre', { class: 'marc__raw', text: JSON.stringify(marc, null, 2), hidden: true });
    const toggle = ui.button({
      label: t('cat.detail.marcRaw'),
      variant: 'ghost',
      size: 'sm',
      onClick: () => {
        raw.hidden = !raw.hidden;
        toggle.textContent = raw.hidden ? t('cat.detail.marcRaw') : t('cat.detail.marcHide');
      },
    });

    const details = ui.el('details', { class: 'disclosure' },
      ui.el('summary', { text: t('cat.marc.title') }),
      ui.el('div', { class: 'u-col', style: { gap: '8px', marginTop: '8px' } }, list, toggle, raw));

    return details;
  }

  /**
   * The current-loans / history tabs.
   * @param {{current: Array, history: Array}} loans
   */
  function loansSection(loans) {
    const currentRows = loans.current || [];
    const historyRows = loans.history || [];

    const strip = ui.tabs({
      value: activeTab,
      ariaLabel: t('cat.detail.loans'),
      items: [
        { value: 'current', label: t('cat.detail.tabCurrent'), count: currentRows.length },
        { value: 'history', label: t('cat.detail.loans'), count: historyRows.length },
      ],
      onChange: (value) => {
        activeTab = value;
        paint();
      },
    });

    const rows = activeTab === 'current' ? currentRows : historyRows;
    const holder = ui.el('div', { class: 'u-col', style: { gap: '2px' } });

    if (!rows.length) {
      holder.appendChild(ui.el('p', { class: 'u-sm u-muted', text: t(activeTab === 'current' ? 'cat.detail.noCurrent' : 'cat.detail.noLoans') }));
    } else {
      for (const loan of rows) {
        const meta = activeTab === 'current'
          ? t('cat.copies.due', { date: fmtDate(loan.due_date, 'short') })
          : [loan.issue_date ? fmtDate(loan.issue_date, 'short') : '', loan.return_date ? fmtDate(loan.return_date, 'short') : '']
            .filter(Boolean).join(' → ');
        holder.appendChild(ui.el('div', { class: 'copy-row' },
          ui.el('div', { class: 'copy-row__main' },
            ui.el('span', { text: loan.member_name || loan.member_code || t('common.na') }),
            ui.el('span', { class: 'copy-row__meta', text: meta })),
          ui.statusPill(loan.status)));
      }
    }

    return ui.el('div', { class: 'book__section' }, strip, holder);
  }

  /** Rebuild the whole body from `record`. */
  function paint() {
    if (!record || destroyed) return;
    const book = record.book;
    body.textContent = '';

    const available = Number(record.available) || 0;
    const total = (record.copies || []).length;

    // --- header ---------------------------------------------------------- //
    const head = ui.el('div', { class: 'book__head' },
      ui.el('h2', { class: 'book__title', text: book.title || t('common.na') }));
    if (book.subtitle) head.appendChild(ui.el('p', { class: 'book__sub', text: book.subtitle }));
    if (book.authors) head.appendChild(ui.el('p', { class: 'book__authors', text: book.authors }));
    head.appendChild(ui.el('div', { class: 'u-row' },
      ui.statusPill(book.status),
      ui.pill(`${t('cat.detail.copiesCount')} ${fmtNum(available)}/${fmtNum(total)}`, 'muted')));
    body.appendChild(head);

    // --- metadata -------------------------------------------------------- //
    const isbnValue = book.isbn
      ? ui.el('span', { class: 'u-row' },
        ui.el('span', { class: 'u-mono', text: book.isbn }),
        ui.iconButton({
          icon: 'copy',
          label: t('common.copy'),
          onClick: async () => {
            try {
              await navigator.clipboard.writeText(String(book.isbn));
              ui.toast.success(t('toast.copied'));
            } catch (error) {
              ui.toast.error(t('toast.error'), ui.formatError(error).message);
            }
          },
        }))
      : null;

    body.appendChild(ui.el('div', { class: 'book__meta' },
      metaField('cat.detail.isbn', isbnValue),
      metaField('cat.detail.publisher', book.publisher),
      metaField('cat.detail.year', book.pub_year),
      metaField('cat.detail.edition', book.edition),
      metaField('cat.detail.language', book.language ? t(`lang.${book.language}`) : ''),
      metaField('cat.detail.dewey', book.dewey),
      metaField('cat.detail.callNo', book.call_no),
      metaField('cat.detail.material', book.material_type ? t(`type.${book.material_type}`) : ''),
      metaField('cat.detail.accession', book.accession_no),
      metaField('cat.form.location', book.location)));

    // --- copies ---------------------------------------------------------- //
    const copiesPanel = ui.el('div', { class: 'book__section' },
      ui.el('h3', { class: 'book__section-title', text: t('cat.copies.title') }));
    if (!total) {
      copiesPanel.appendChild(ui.el('p', { class: 'u-sm u-muted', text: t('cat.copies.empty') }));
    } else {
      for (const copy of record.copies) {
        copiesPanel.appendChild(copyRow(copy, {
          onEdit: editCopy,
          onMarkLost: markLost,
          onDelete: deleteCopy,
        }));
      }
    }
    body.appendChild(copiesPanel);

    // --- loans ----------------------------------------------------------- //
    body.appendChild(loansSection(record.loans || { current: [], history: [] }));

    // --- vocabulary ------------------------------------------------------ //
    const subjects = record.subjects || [];
    const languages = record.languages || [];
    if (subjects.length || languages.length) {
      const vocab = ui.el('div', { class: 'book__section' },
        ui.el('h3', { class: 'book__section-title', text: t('cat.detail.subjects') }));
      const chipRow = ui.el('div', { class: 'u-row u-wrap', style: { gap: '6px' } });
      if (subjects.length) {
        for (const heading of subjects) chipRow.appendChild(ui.pill(heading, 'muted'));
      } else {
        chipRow.appendChild(ui.el('span', { class: 'u-sm u-muted', text: t('cat.detail.noSubjects') }));
      }
      vocab.appendChild(chipRow);
      if (languages.length) {
        vocab.appendChild(ui.el('div', { class: 'u-row u-wrap', style: { gap: '6px' } },
          languages.map((code) => ui.pill(t(`lang.${code}`), 'primary'))));
      }
      body.appendChild(vocab);
    }

    // --- MARC ------------------------------------------------------------ //
    body.appendChild(marcSection(record.marc || {}));

    editButton.disabled = false;
    labelButton.disabled = !total;
    deleteButton.disabled = false;
  }

  /** Fetch the record and repaint. */
  async function reload() {
    try {
      const data = await api.get(`/api/books/${encodeURIComponent(String(bookId))}`);
      if (destroyed) return;
      record = data;
      paint();
    } catch (error) {
      if (destroyed) return;
      body.textContent = '';
      body.appendChild(ui.errorState({ error, onRetry: () => reload() }));
    }
  }

  reload();

  return { close: () => dialog.close(), reload };
}

export default { openBookDrawer };
