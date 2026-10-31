/**
 * Reservations — the hold queue.
 *
 * The list is grouped by title rather than sorted by date, because the question
 * this screen answers is "who is next for this book", and a flat chronological
 * list makes that a manual scan. Positions come from the server, which
 * recomputes them gaplessly on every cancel, fulfil and expiry — the client
 * never renumbers anything.
 */

import { t, fmtNum, fmtDate } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';

/**
 * Render the reservations screen.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  let items = [];
  let loading = true;
  let error = null;
  let destroyed = false;
  let filter = (ctx.query && ctx.query.status) || '';

  const title = ui.el('h1', { class: 'page__title', text: t('res.title') });
  const subtitle = ui.el('p', { class: 'page__sub', text: t('res.subtitle') });

  const expireButton = ui.button({
    label: t('res.expireNow'),
    icon: 'refresh',
    onClick: () => releaseExpired(),
  });
  expireButton.hidden = !store.can('reservation.write');

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle),
    ui.el('div', { class: 'page__actions' }, expireButton));

  const chips = ui.chips({
    value: filter,
    items: [
      { value: 'open', label: t('status.open') },
      { value: 'ready', label: t('status.ready') },
      { value: 'fulfilled', label: t('status.fulfilled') },
      { value: 'expired', label: t('status.expired') },
    ],
    onChange: (value) => {
      filter = value;
      load();
    },
  });

  const summary = ui.el('div', { class: 'stat-row' });
  const body = ui.el('div', { class: 'u-col', style: { gap: '12px' } });
  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } },
    head, chips, summary, body);

  // -- data -------------------------------------------------------------- //
  async function load() {
    loading = true;
    error = null;
    paint();
    try {
      const params = { page: 1, page_size: 200 };
      if (filter) params.status = filter;
      const data = await api.get('/api/reservations', params);
      if (destroyed) return;
      items = data.items || [];
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
  async function releaseExpired() {
    try {
      const result = await api.post('/api/reservations/expire', {});
      const count = Number(result && result.expired) || 0;
      ui.toast.success(t('res.expireNow'), count ? `${fmtNum(count)}` : t('common.none'));
      load();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  async function fulfil(row) {
    try {
      await api.post(`/api/reservations/${encodeURIComponent(String(row.id))}/fulfil`, {});
      ui.toast.success(t('res.fulfil'), row.book_title || '');
      load();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  async function cancel(row) {
    const confirmed = await ui.confirmDialog({
      title: t('res.cancelConfirm'),
      body: t('res.cancelBody'),
      confirmLabel: t('res.cancel'),
      tone: 'danger',
    });
    if (!confirmed) return;
    try {
      await api.post(`/api/reservations/${encodeURIComponent(String(row.id))}/cancel`, {});
      ui.toast.success(t('toast.updated'), row.book_title || '');
      load();
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  // -- paint ------------------------------------------------------------- //
  function groupByTitle(rows) {
    const groups = new Map();
    for (const row of rows) {
      const key = row.book_id || row.accession_no || row.book_title;
      if (!groups.has(key)) groups.set(key, { title: row.book_title || '', rows: [] });
      groups.get(key).rows.push(row);
    }
    for (const group of groups.values()) {
      group.rows.sort((a, b) => (a.queue_position || 0) - (b.queue_position || 0));
    }
    return Array.from(groups.values());
  }

  function reservationRow(row) {
    const isReady = row.status === 'ready';
    const actions = ui.el('div', { class: 'u-row u-gap-4' });
    if (store.can('reservation.write')) {
      if (isReady || row.status === 'open') {
        actions.appendChild(ui.button({
          label: t('res.fulfil'),
          size: 'sm',
          onClick: (event) => {
            event.stopPropagation();
            fulfil(row);
          },
        }));
      }
      actions.appendChild(ui.iconButton({
        icon: 'x',
        label: t('res.cancel'),
        size: 'sm',
        onClick: (event) => {
          event.stopPropagation();
          cancel(row);
        },
      }));
    }

    return ui.el('div', { class: 'queue-row' },
      ui.el('span', { class: 'queue-row__pos u-num', text: fmtNum(row.queue_position || 0) }),
      ui.el('div', { class: 'queue-row__main' },
        ui.el('a', {
          class: 'queue-row__name',
          attrs: { href: `#/members/${row.member_id}` },
          text: row.member_name || row.member_code || '',
        }),
        ui.el('span', {
          class: 'queue-row__meta',
          text: [
            row.member_code,
            row.reserved_date ? fmtDate(row.reserved_date) : null,
            isReady && row.expires_at ? t('res.expires', { date: fmtDate(row.expires_at) }) : null,
          ].filter(Boolean).join(' · '),
        })),
      ui.statusPill(row.status),
      actions);
  }

  function paint() {
    ui.clear(body);
    ui.clear(summary);

    if (error) {
      body.appendChild(ui.errorState({ error, onRetry: () => load() }));
      return;
    }
    if (loading) {
      body.appendChild(ui.card({ body: ui.skeleton({ rows: 4 }) }));
      return;
    }

    const open = items.filter((row) => row.status === 'open').length;
    const ready = items.filter((row) => row.status === 'ready').length;
    summary.appendChild(ui.el('div', { class: 'stat-mini' },
      ui.el('span', { class: 'stat-mini__label', text: t('status.open') }),
      ui.el('span', { class: 'stat-mini__value u-num', text: fmtNum(open) })));
    summary.appendChild(ui.el('div', { class: 'stat-mini' },
      ui.el('span', { class: 'stat-mini__label', text: t('res.ready') }),
      ui.el('span', { class: 'stat-mini__value u-num', text: fmtNum(ready) })));

    if (!items.length) {
      body.appendChild(ui.card({
        flush: true,
        body: ui.emptyState({
          icon: 'book',
          title: t('res.empty.title'),
          body: t('res.empty.body'),
        }),
      }));
      return;
    }

    for (const group of groupByTitle(items)) {
      const card = ui.el('div', { class: 'card queue-card' });
      card.appendChild(ui.el('header', { class: 'queue-card__head' },
        ui.el('h2', { class: 'queue-card__title', text: group.title }),
        ui.el('span', { class: 'u-xs u-muted', text: t('res.queue') + ` · ${fmtNum(group.rows.length)}` })));
      const list = ui.el('div', { class: 'queue-card__body' });
      for (const row of group.rows) list.appendChild(reservationRow(row));
      card.appendChild(list);
      body.appendChild(card);
    }
  }

  const onLang = () => {
    title.textContent = t('res.title');
    subtitle.textContent = t('res.subtitle');
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
