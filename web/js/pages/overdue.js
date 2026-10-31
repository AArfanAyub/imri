/**
 * Overdue & fines.
 *
 * Two questions, one screen: who is late, and who owes money. The overdue table
 * is the chase list (days late, accrued fine, contact details), and the fines
 * ledger is the cash book (accrued, paid, waived, outstanding).
 *
 * Both totals come from the server. The client never adds up money itself —
 * a fine total that disagrees with the receipt drawer is the one bug a school
 * will not forgive.
 */

import { t, fmtNum, fmtDate, fmtMoney } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';

/**
 * Render the overdue screen.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
/** The fine ledger keys its rows by `loan_id`; the overdue list uses `id`. */
function loanIdOf(row) {
  return row.id !== undefined && row.id !== null ? row.id : row.loan_id;
}

export function render(ctx) {
  let activeTab = (ctx.query && ctx.query.tab) === 'fines' ? 'fines' : 'overdue';
  let overdue = { items: [], totals: { count: 0, fine_accrued: 0 } };
  let fines = { items: [], totals: { accrued: 0, paid: 0, waived: 0, outstanding: 0 } };
  let loading = true;
  let error = null;
  let destroyed = false;
  let page = 1;
  let pageSize = store.pageSize();

  const title = ui.el('h1', { class: 'page__title', text: t('circ.overdue.title') });
  const subtitle = ui.el('p', { class: 'page__sub', text: t('circ.overdue.subtitle') });

  const exportButton = ui.button({
    label: t('common.export'),
    icon: 'download',
    onClick: () => exportCsv(),
  });
  exportButton.hidden = !store.can('export.run');

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle),
    ui.el('div', { class: 'page__actions' }, exportButton));

  const tabs = ui.tabs({
    value: activeTab,
    items: [
      { value: 'overdue', label: t('circ.overdue.title') },
      { value: 'fines', label: t('circ.fine.title') },
    ],
    onChange: (value) => {
      activeTab = value;
      page = 1;
      paint();
      load();
    },
  });

  const summary = ui.el('div', { class: 'stat-row' });
  const body = ui.el('div', { class: 'u-col' });
  const footer = ui.el('div', { class: 'tbl__foot' });

  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } },
    head, tabs, summary, body, footer);

  // -- data -------------------------------------------------------------- //
  async function load() {
    loading = true;
    error = null;
    paint();
    try {
      if (activeTab === 'overdue') {
        const data = await api.get('/api/circulation/overdue', { page, page_size: pageSize });
        if (destroyed) return;
        overdue = { items: data.items || [], totals: data.totals || overdue.totals };
      } else {
        const data = await api.get('/api/fines', { page, page_size: pageSize });
        if (destroyed) return;
        fines = { items: data.items || [], totals: data.totals || fines.totals };
      }
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

  // -- actions ----------------------------------------------------------- //
  async function exportCsv() {
    try {
      const meta = await api.get('/api/circulation/overdue/export');
      const blob = await api.download(`/api/exports/${encodeURIComponent(meta.filename)}`);
      api.saveBlob(blob, meta.filename);
      ui.toast.success(t('toast.exported'), meta.filename);
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  function payDialog(row) {
    const balance = Number(row.balance) || Math.max(0, Number(row.accrued_fine || row.fine_amount || 0) - Number(row.paid_amount || 0) - Number(row.waived_amount || 0));
    const amountInput = ui.input({
      value: balance.toFixed(2),
      type: 'number',
      min: '0',
      step: '0.01',
      mono: true,
    });
    const methodSelect = ui.select({
      value: 'cash',
      options: [
        { value: 'cash', label: t('circ.fine.methodCash') },
        { value: 'other', label: t('circ.fine.methodOther') },
      ],
    });

    const payButton = ui.button({
      label: t('circ.fine.pay'),
      variant: 'primary',
      icon: 'check',
      onClick: async () => {
        const amount = Number(amountInput.value);
        if (!Number.isFinite(amount) || amount <= 0) {
          ui.toast.error(t('toast.error'), t('validate.number'));
          return;
        }
        payButton.disabled = true;
        try {
          await api.post(`/api/loans/${encodeURIComponent(String(loanIdOf(row)))}/payments`, {
            amount,
            method: methodSelect.value,
          });
          ui.toast.success(t('circ.fine.paid'), fmtMoney(amount));
          dialog.close();
          load();
        } catch (failure) {
          ui.toast.error(t('toast.error'), ui.formatError(failure).message);
        } finally {
          payButton.disabled = false;
        }
      },
    });

    const dialog = ui.modal({
      title: t('circ.fine.pay'),
      body: ui.el('div', { class: 'u-col', style: { gap: '14px' } },
        ui.el('div', { class: 'stat-mini' },
          ui.el('span', { class: 'stat-mini__label', text: t('circ.loan.title') }),
          ui.el('span', { class: 'stat-mini__value', text: row.book_title || '' })),
        ui.field({ label: t('circ.fine.amount'), control: amountInput }),
        ui.field({ label: t('circ.fine.method'), control: methodSelect })),
      footer: ui.el('div', { class: 'u-row u-gap-8' },
        ui.button({ label: t('common.cancel'), onClick: () => dialog.close() }),
        payButton),
    });
    window.requestAnimationFrame(() => amountInput.select());
  }

  function waiveDialog(row) {
    const reasonInput = ui.input({ placeholder: t('circ.fine.reason') });
    const confirmButton = ui.button({
      label: t('circ.fine.waive'),
      variant: 'danger',
      onClick: async () => {
        const reason = reasonInput.value.trim();
        if (!reason) {
          ui.toast.error(t('toast.error'), t('validate.required'));
          reasonInput.focus();
          return;
        }
        confirmButton.disabled = true;
        try {
          const balance = Number(row.balance) || Math.max(0, Number(row.accrued_fine || row.fine_amount || 0) - Number(row.paid_amount || 0) - Number(row.waived_amount || 0));
          await api.post(`/api/loans/${encodeURIComponent(String(loanIdOf(row)))}/waive`, {
            amount: balance,
            reason,
          });
          ui.toast.success(t('circ.fine.waived'), row.book_title || '');
          dialog.close();
          load();
        } catch (failure) {
          ui.toast.error(t('toast.error'), ui.formatError(failure).message);
        } finally {
          confirmButton.disabled = false;
        }
      },
    });

    const dialog = ui.modal({
      title: t('circ.fine.waive'),
      body: ui.el('div', { class: 'u-col', style: { gap: '14px' } },
        ui.field({ label: t('circ.fine.reason'), control: reasonInput, required: true })),
      footer: ui.el('div', { class: 'u-row u-gap-8' },
        ui.button({ label: t('common.cancel'), onClick: () => dialog.close() }),
        confirmButton),
    });
    window.requestAnimationFrame(() => reasonInput.focus());
  }

  // -- paint ------------------------------------------------------------- //
  function paintSummary() {
    ui.clear(summary);
    if (activeTab === 'overdue') {
      summary.appendChild(ui.el('div', { class: 'stat-mini' },
        ui.el('span', { class: 'stat-mini__label', text: t('status.overdue') }),
        ui.el('span', { class: 'stat-mini__value u-num', text: fmtNum(overdue.totals.count || 0) })));
      summary.appendChild(ui.el('div', { class: 'stat-mini' },
        ui.el('span', { class: 'stat-mini__label', text: t('circ.fine.accrued') }),
        ui.el('span', { class: 'stat-mini__value u-num u-warn-text', text: fmtMoney(overdue.totals.fine_accrued || 0) })));
    } else {
      summary.appendChild(ui.el('div', { class: 'stat-mini' },
        ui.el('span', { class: 'stat-mini__label', text: t('circ.fine.outstanding') }),
        ui.el('span', { class: 'stat-mini__value u-num u-warn-text', text: fmtMoney(fines.totals.outstanding || 0) })));
      summary.appendChild(ui.el('div', { class: 'stat-mini' },
        ui.el('span', { class: 'stat-mini__label', text: t('circ.fine.paid') }),
        ui.el('span', { class: 'stat-mini__value u-num', text: fmtMoney(fines.totals.paid || 0) })));
      summary.appendChild(ui.el('div', { class: 'stat-mini' },
        ui.el('span', { class: 'stat-mini__label', text: t('circ.fine.waived') }),
        ui.el('span', { class: 'stat-mini__value u-num', text: fmtMoney(fines.totals.waived || 0) })));
    }
  }

  function overdueTable() {
    const columns = [
      {
        key: 'book_title',
        label: t('circ.loan.title'),
        render: (row) => ui.el('div', { class: 'cell-stack' },
          ui.el('span', { class: 'cell-stack__main', text: row.book_title || '' }),
          ui.el('span', { class: 'cell-stack__sub', text: row.accession_no || '' })),
      },
      {
        key: 'member_name',
        label: t('circ.loan.member'),
        render: (row) => ui.el('div', { class: 'cell-stack' },
          ui.el('span', { class: 'cell-stack__main', text: row.member_name || '' }),
          ui.el('span', { class: 'cell-stack__sub', text: [row.member_code, row.class_name].filter(Boolean).join(' · ') })),
      },
      { key: 'due_date', label: t('circ.loan.due'), render: (row) => ui.el('span', { class: 'u-num u-xs', text: fmtDate(row.due_date) }) },
      {
        key: 'overdue_days',
        label: t('status.overdue'),
        align: 'right',
        render: (row) => ui.pill(`${fmtNum(row.overdue_days)} ${t('common.days')}`, 'danger'),
      },
      { key: 'accrued_fine', label: t('circ.fine.accrued'), align: 'right', render: (row) => ui.el('span', { class: 'u-num u-warn-text', text: fmtMoney(row.accrued_fine) }) },
      {
        key: 'actions',
        label: '',
        align: 'right',
        render: (row) => ui.el('div', { class: 'u-row u-gap-4' },
          store.can('fine.pay')
            ? ui.button({ label: t('circ.fine.pay'), size: 'sm', onClick: (event) => { event.stopPropagation(); payDialog(row); } })
            : null,
          store.can('fine.waive')
            ? ui.iconButton({ icon: 'x', label: t('circ.fine.waive'), size: 'sm', onClick: (event) => { event.stopPropagation(); waiveDialog(row); } })
            : null),
      },
    ];

    return ui.table({
      columns,
      rows: overdue.items,
      loading,
      empty: {
        icon: 'book',
        title: t('circ.overdue.empty'),
        body: '',
      },
      onRowClick: (row) => ctx.navigate(`/members/${row.member_id}`),
    });
  }

  function finesTable() {
    const columns = [
      {
        key: 'book_title',
        label: t('circ.loan.title'),
        render: (row) => ui.el('span', { text: row.book_title || '' }),
      },
      {
        key: 'member_name',
        label: t('circ.loan.member'),
        render: (row) => ui.el('span', { text: row.member_name || '' }),
      },
      { key: 'fine_amount', label: t('circ.fine.accrued'), align: 'right', render: (row) => ui.el('span', { class: 'u-num', text: fmtMoney(row.fine_amount) }) },
      { key: 'paid_amount', label: t('circ.fine.paid'), align: 'right', render: (row) => ui.el('span', { class: 'u-num', text: fmtMoney(row.paid_amount) }) },
      { key: 'balance', label: t('circ.fine.balance'), align: 'right', render: (row) => ui.el('span', { class: 'u-num u-warn-text', text: fmtMoney(row.balance) }) },
      {
        key: 'actions',
        label: '',
        align: 'right',
        render: (row) => (store.can('fine.pay') && Number(row.balance) > 0
          ? ui.button({ label: t('circ.fine.pay'), size: 'sm', onClick: (event) => { event.stopPropagation(); payDialog(row); } })
          : null),
      },
    ];

    return ui.table({
      columns,
      rows: fines.items,
      loading,
      empty: { icon: 'box', title: t('circ.fine.empty'), body: '' },
      onRowClick: (row) => ctx.navigate(`/members/${row.member_id}`),
    });
  }

  function paint() {
    paintSummary();
    ui.clear(body);
    ui.clear(footer);

    if (error) {
      body.appendChild(ui.errorState({ error, onRetry: () => load() }));
      return;
    }

    body.appendChild(activeTab === 'overdue' ? overdueTable() : finesTable());

    const items = activeTab === 'overdue' ? overdue.items : fines.items;
    if (items.length) {
      const total = activeTab === 'overdue' ? (overdue.totals.count || 0) : (fines.totals.count || 0);
      footer.appendChild(ui.pagination({
        page,
        pageSize,
        total: total || items.length,
        onChange: (next) => {
          page = next;
          load();
        },
      }));
    }
  }

  const onLang = () => {
    title.textContent = t('circ.overdue.title');
    subtitle.textContent = t('circ.overdue.subtitle');
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
