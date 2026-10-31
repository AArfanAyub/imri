/**
 * Member profile — the 360° view of one borrower.
 *
 * A full page rather than a drawer, because this is where a librarian answers
 * "may I lend to this person, and what do they owe" — a question that needs the
 * loans, the ledger, the holds and the reading list on screen at once.
 *
 * Everything on this page comes from a single `GET /api/members/{id}` call: the
 * server assembles the profile in one round trip so the four panels cannot
 * disagree with each other.
 */

import { t, fmtNum, fmtDate, fmtMoney } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';
import { openMemberForm } from './_member-form.js';

/**
 * Render the member profile.
 *
 * @param {{params: Object, query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  const memberId = ctx.params && ctx.params.id;
  let profile = null;
  let loading = true;
  let error = null;
  let destroyed = false;
  let activeTab = (ctx.query && ctx.query.tab) || 'loans';

  const title = ui.el('h1', { class: 'page__title', text: t('mem.profile.title') });
  const subtitle = ui.el('p', { class: 'page__sub', text: '' });
  const backButton = ui.button({
    label: t('common.back'),
    variant: 'ghost',
    icon: 'chevronLeft',
    onClick: () => ctx.navigate('/members'),
  });

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, backButton, title, subtitle));

  const body = ui.el('div', { class: 'u-col', style: { gap: '16px' } });
  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } }, head, body);

  // -- data -------------------------------------------------------------- //
  async function load() {
    loading = true;
    error = null;
    paint();
    try {
      const data = await api.get(`/api/members/${encodeURIComponent(String(memberId))}`);
      if (destroyed) return;
      profile = data;
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

  // -- panels ------------------------------------------------------------ //
  function statTile(label, value, tone) {
    return ui.el('div', { class: 'stat-mini' },
      ui.el('span', { class: 'stat-mini__label', text: label }),
      ui.el('span', { class: `stat-mini__value${tone ? ` ${tone}` : ''}`, text: value }));
  }

  function identityCard(member) {
    const rows = [
      [t('mem.form.code'), member.member_code],
      [t('mem.form.type'), member.member_type ? t(`memberType.${member.member_type}`) : null],
      [t('mem.form.class'), member.class_name],
      [t('mem.form.form'), member.form],
      [t('mem.form.ic'), member.ic_number],
      [t('mem.form.email'), member.email],
      [t('mem.form.phone'), member.phone],
      [t('mem.form.address'), member.address],
      [t('mem.col.joined'), member.membership_date ? fmtDate(member.membership_date) : null],
      [t('mem.form.notes'), member.note],
    ].filter(([, value]) => value);

    return ui.card({
      title: t('mem.section.identity'),
      body: ui.kv(rows.map(([label, value]) => ({ label, value }))),
    });
  }

  function loansPanel(loans) {
    const current = (loans && loans.current) || [];
    const history = (loans && loans.history) || [];
    const rows = activeTab === 'history' ? history : current;

    const columns = activeTab === 'history'
      ? [
        { key: 'book_title', label: t('circ.loan.title'), render: (row) => ui.el('span', { text: row.book_title || t('common.na') }) },
        { key: 'issue_date', label: t('circ.loan.issued'), render: (row) => ui.el('span', { class: 'u-num u-xs', text: fmtDate(row.issue_date) }) },
        { key: 'return_date', label: t('circ.loan.returned'), render: (row) => ui.el('span', { class: 'u-num u-xs', text: row.return_date ? fmtDate(row.return_date) : t('common.na') }) },
        { key: 'fine_amount', label: t('circ.fine.title'), align: 'right', render: (row) => ui.el('span', { class: 'u-num u-xs', text: Number(row.fine_amount) ? fmtMoney(row.fine_amount) : t('common.na') }) },
        { key: 'status', label: t('mem.col.status'), render: (row) => ui.statusPill(row.status) },
      ]
      : [
        { key: 'book_title', label: t('circ.loan.title'), render: (row) => ui.el('span', { text: row.book_title || t('common.na') }) },
        { key: 'copy_barcode', label: t('circ.loan.copy'), render: (row) => ui.el('span', { class: 'u-mono u-xs', text: row.copy_barcode || row.accession_no || '' }) },
        { key: 'issue_date', label: t('circ.loan.issued'), render: (row) => ui.el('span', { class: 'u-num u-xs', text: fmtDate(row.issue_date) }) },
        { key: 'due_date', label: t('circ.loan.due'), render: (row) => dueCell(row) },
        { key: 'renewal_count', label: t('circ.loan.renewals'), align: 'right', render: (row) => ui.el('span', { class: 'u-num u-xs', text: fmtNum(row.renewal_count || 0) }) },
        {
          key: 'actions',
          label: '',
          align: 'right',
          render: (row) => (store.can('circ.write')
            ? ui.button({
              label: t('circ.renew'),
              size: 'sm',
              onClick: (event) => {
                event.stopPropagation();
                renew(row);
              },
            })
            : null),
        },
      ];

    return ui.table({
      columns,
      rows,
      compact: true,
      empty: {
        icon: 'book',
        title: activeTab === 'history' ? t('mem.profile.noLoans') : t('circ.empty.title'),
        body: activeTab === 'history' ? '' : t('circ.empty.body'),
      },
    });
  }

  function dueCell(row) {
    const due = row.due_date ? new Date(`${row.due_date}T00:00:00`) : null;
    if (!due) return ui.el('span', { class: 'u-muted', text: t('common.na') });
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const days = Math.round((due - today) / 86400000);
    if (days < 0) {
      return ui.el('span', { class: 'u-warn-text u-xs u-num', text: t('circ.loan.overdueBy', { days: Math.abs(days) }) });
    }
    if (days === 0) return ui.el('span', { class: 'u-xs u-num', text: t('circ.loan.dueToday') });
    return ui.el('span', { class: 'u-xs u-num u-muted', text: t('circ.loan.dueIn', { days }) });
  }

  function finesPanel(fines) {
    const items = (fines && fines.items) || [];
    const totals = (fines && fines.totals) || {};
    const summary = ui.el('div', { class: 'stat-row' },
      statTile(t('circ.fine.outstanding'), fmtMoney(totals.outstanding || 0), Number(totals.outstanding) ? 'u-warn-text' : ''),
      statTile(t('circ.fine.paid'), fmtMoney(totals.paid || 0)),
      statTile(t('circ.fine.waived'), fmtMoney(totals.waived || 0)));

    const columns = [
      { key: 'book_title', label: t('circ.loan.title'), render: (row) => ui.el('span', { text: row.book_title || t('common.na') }) },
      { key: 'due_date', label: t('circ.loan.due'), render: (row) => ui.el('span', { class: 'u-num u-xs', text: fmtDate(row.due_date) }) },
      { key: 'fine_amount', label: t('circ.fine.accrued'), align: 'right', render: (row) => ui.el('span', { class: 'u-num u-xs', text: fmtMoney(row.fine_amount) }) },
      { key: 'paid_amount', label: t('circ.fine.paid'), align: 'right', render: (row) => ui.el('span', { class: 'u-num u-xs', text: fmtMoney(row.paid_amount) }) },
      { key: 'balance', label: t('circ.fine.balance'), align: 'right', render: (row) => ui.el('span', { class: 'u-num u-xs u-warn-text', text: fmtMoney(row.balance) }) },
    ];

    return ui.el('div', { class: 'u-col', style: { gap: '12px' } },
      summary,
      ui.table({
        columns,
        rows: items,
        compact: true,
        empty: { icon: 'box', title: t('circ.fine.empty'), body: '' },
      }));
  }

  function reservationsPanel(reservations) {
    const items = reservations || [];
    const columns = [
      { key: 'book_title', label: t('circ.loan.title'), render: (row) => ui.el('span', { text: row.book_title || t('common.na') }) },
      { key: 'queue_position', label: t('res.position'), align: 'right', render: (row) => ui.el('span', { class: 'u-num', text: fmtNum(row.queue_position || 0) }) },
      { key: 'reserved_date', label: t('res.reserved'), render: (row) => ui.el('span', { class: 'u-num u-xs', text: fmtDate(row.reserved_date) }) },
      { key: 'expires_at', label: t('res.expires', { date: '' }).trim(), render: (row) => ui.el('span', { class: 'u-num u-xs', text: row.expires_at ? fmtDate(row.expires_at) : t('common.na') }) },
      { key: 'status', label: t('mem.col.status'), render: (row) => ui.statusPill(row.status) },
    ];
    return ui.table({
      columns,
      rows: items,
      compact: true,
      empty: { icon: 'box', title: t('res.empty.title'), body: t('res.empty.body') },
    });
  }

  function readingListPanel(list) {
    const items = list || [];
    const addButton = store.can('member.write')
      ? ui.button({
        label: t('common.add'),
        size: 'sm',
        icon: 'plus',
        onClick: () => addToReadingList(),
      })
      : null;

    const columns = [
      { key: 'title', label: t('circ.loan.title'), render: (row) => ui.el('div', { class: 'cell-stack' },
        ui.el('span', { class: 'cell-stack__main', text: (row.book && row.book.title) || '' }),
        ui.el('span', { class: 'cell-stack__sub', text: (row.book && row.book.authors) || '' })) },
      { key: 'call_no', label: t('cat.col.callNo'), render: (row) => ui.el('span', { class: 'u-mono u-xs', text: (row.book && row.book.call_no) || t('common.na') }) },
      {
        key: 'actions',
        label: '',
        align: 'right',
        render: (row) => ui.el('div', { class: 'u-row u-gap-4' },
          store.can('reservation.write')
            ? ui.button({
              label: t('res.reserved'),
              size: 'sm',
              onClick: (event) => {
                event.stopPropagation();
                reserveItem(row);
              },
            })
            : null,
          store.can('member.write')
            ? ui.iconButton({
              icon: 'trash',
              label: t('common.remove'),
              size: 'sm',
              onClick: (event) => {
                event.stopPropagation();
                removeItem(row);
              },
            })
            : null),
      },
    ];

    const tableNode = ui.table({
      columns,
      rows: items,
      compact: true,
      empty: { icon: 'book', title: t('mem.profile.noList'), body: '' },
    });

    return ui.el('div', { class: 'u-col', style: { gap: '12px' } },
      ui.el('div', { class: 'u-row u-row--between' },
        ui.el('span', { class: 'u-sm u-muted', text: t('mem.profile.readingList') }), addButton),
      tableNode);
  }

  // -- actions ----------------------------------------------------------- //
  async function renew(row) {
    try {
      await api.post('/api/circulation/renew', { loan_id: row.id });
      ui.toast.success(t('toast.updated'), t('circ.renew'));
      load();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  async function addToReadingList() {
    const bookInput = ui.input({ placeholder: t('cat.search'), large: true });
    const resultsBox = ui.el('div', { class: 'u-col', style: { gap: '4px', maxHeight: '260px', overflow: 'auto' } });
    let timer = null;

    const dialog = ui.modal({
      title: t('mem.profile.readingList'),
      body: ui.el('div', { class: 'u-col', style: { gap: '12px' } },
        ui.field({ label: t('circ.loan.title'), control: bookInput }),
        resultsBox),
      footer: ui.el('div', { class: 'u-row u-gap-8' },
        ui.button({ label: t('common.close'), onClick: () => dialog.close() })),
    });

    const search = async () => {
      const term = bookInput.value.trim();
      if (!term) {
        ui.clear(resultsBox);
        return;
      }
      try {
        const page = await api.get('/api/books', { q: term, page: 1, page_size: 8 });
        ui.clear(resultsBox);
        const items = page.items || [];
        if (!items.length) {
          resultsBox.appendChild(ui.el('p', { class: 'u-sm u-muted', text: t('common.noResults') }));
          return;
        }
        for (const book of items) {
          resultsBox.appendChild(ui.el('button', {
            class: 'pickrow',
            attrs: { type: 'button' },
            on: {
              click: async () => {
                try {
                  await api.post(`/api/members/${encodeURIComponent(String(memberId))}/reading-list`, { book_id: book.id });
                  ui.toast.success(t('toast.created'), book.title);
                  dialog.close();
                  load();
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
      window.clearTimeout(timer);
      timer = window.setTimeout(search, 180);
    });
    window.requestAnimationFrame(() => bookInput.focus());
  }

  async function reserveItem(row) {
    try {
      await api.post(`/api/members/${encodeURIComponent(String(memberId))}/reading-list/${encodeURIComponent(String(row.id))}/reserve`, {});
      ui.toast.success(t('toast.created'), t('res.reserved'));
      load();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  async function removeItem(row) {
    try {
      await api.del(`/api/members/${encodeURIComponent(String(memberId))}/reading-list/${encodeURIComponent(String(row.id))}`);
      ui.toast.success(t('toast.deleted'));
      load();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  async function removeMember() {
    const member = profile.member;
    const confirmed = await ui.confirmDialog({
      title: t('mem.deleteTitle'),
      body: t('mem.deleteBody', { name: member.name }),
      confirmLabel: t('common.yesDelete'),
      tone: 'danger',
    });
    if (!confirmed) return;
    try {
      await api.del(`/api/members/${encodeURIComponent(String(memberId))}`);
      ui.toast.success(t('toast.deleted'), member.name);
      ctx.navigate('/members');
    } catch (failure) {
      const message = failure && failure.is && failure.is('member_has_loans')
        ? t('mem.deleteBlocked')
        : ui.formatError(failure).message;
      ui.toast.error(t('toast.error'), message);
    }
  }

  function printCard() {
    window.open(`/api/members/${encodeURIComponent(String(memberId))}/card.svg?scale=4`, '_blank', 'noopener');
  }

  // -- paint ------------------------------------------------------------- //
  function paint() {
    ui.clear(body);

    if (loading) {
      body.appendChild(ui.card({ body: ui.skeleton({ rows: 5 }) }));
      return;
    }
    if (error) {
      body.appendChild(ui.errorState({
        error,
        title: t('errstate.title'),
        onRetry: () => load(),
      }));
      return;
    }
    if (!profile || !profile.member) return;

    const member = profile.member;
    const counts = profile.counts || {};

    title.textContent = member.name;
    subtitle.textContent = [member.member_code, member.member_type ? t(`memberType.${member.member_type}`) : null, member.class_name]
      .filter(Boolean).join(' · ');

    // Action bar.
    const actions = ui.el('div', { class: 'page__actions' },
      ui.button({ label: t('mem.card.print'), icon: 'printer', onClick: () => printCard() }));
    if (store.can('member.write')) {
      actions.appendChild(ui.button({
        label: t('common.edit'),
        icon: 'edit',
        variant: 'primary',
        onClick: () => openMemberForm({ member, onSaved: () => load() }),
      }));
    }
    if (store.can('member.delete')) {
      actions.appendChild(ui.button({
        label: t('common.delete'),
        variant: 'danger',
        icon: 'trash',
        onClick: () => removeMember(),
      }));
    }
    head.appendChild(actions);

    // Status + counts.
    const summary = ui.el('div', { class: 'stat-row' },
      statTile(t('mem.profile.activeLoans'), fmtNum(counts.active_loans || 0)),
      statTile(t('mem.profile.history'), fmtNum(counts.history || 0)),
      statTile(t('mem.profile.outstanding'), fmtMoney(counts.outstanding || 0), Number(counts.outstanding) ? 'u-warn-text' : ''),
      statTile(t('mem.profile.reservations'), fmtNum(counts.reservations || 0)));

    const statusRow = ui.el('div', { class: 'u-row u-gap-8' },
      ui.statusPill(member.status),
      member.ic_warning ? ui.pill(t('validate.ic'), 'warning') : null);

    // Tabs.
    const tabs = ui.tabs({
      value: activeTab,
      items: [
        { value: 'loans', label: t('mem.profile.loans'), count: (profile.loans && profile.loans.current || []).length },
        { value: 'history', label: t('mem.profile.history'), count: (profile.loans && profile.loans.history || []).length },
        { value: 'fines', label: t('mem.profile.fines'), count: (profile.fines && profile.fines.items || []).length },
        { value: 'reservations', label: t('mem.profile.reservations'), count: (profile.reservations || []).length },
        { value: 'reading', label: t('mem.profile.readingList'), count: (profile.reading_list || []).length },
      ],
      onChange: (value) => {
        activeTab = value;
        paint();
      },
    });

    let panel;
    if (activeTab === 'loans' || activeTab === 'history') panel = loansPanel(profile.loans);
    else if (activeTab === 'fines') panel = finesPanel(profile.fines);
    else if (activeTab === 'reservations') panel = reservationsPanel(profile.reservations);
    else panel = readingListPanel(profile.reading_list);

    body.appendChild(summary);
    body.appendChild(statusRow);
    body.appendChild(tabs);
    body.appendChild(panel);
    body.appendChild(identityCard(member));
  }

  const onLang = () => paint();
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
