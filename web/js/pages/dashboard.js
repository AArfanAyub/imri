/**
 * Dashboard — the screen the owner judges the product on.
 *
 * Everything here comes from one request (`GET /api/dashboard`), which the
 * reporting service assembles from roughly ten aggregate queries. The page does
 * no arithmetic the server did not already do, with one exception: the KPI delta
 * is expressed as a percentage of the implied baseline (`value - delta`), which
 * is derived from the same two numbers rather than a second request.
 *
 * Deltas are coloured by *consequence*, not by direction. More titles is good;
 * more overdue loans is not. Treating every rise as green — the stock-ticker
 * reflex — would tell a librarian that things are going well while they are
 * going wrong.
 */

import { t, fmtDate, fmtNum, fmtMoney, fmtPct } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';
import * as charts from '../charts.js';
import { openBookDrawer } from './_book-drawer.js';

/** Dismissal lasts for the visit only: a real problem comes back on reload. */
let alertsDismissed = false;

/**
 * The six KPI tiles, in the order the brief asks for.
 *
 * `goodUp` decides the colour of the delta; `route` is where a click lands,
 * with the filter that makes the destination match the number.
 */
const KPIS = Object.freeze([
  { key: 'titles', labelKey: 'dash.kpi.titles', route: '/catalogue', goodUp: true, money: false },
  { key: 'copies', labelKey: 'dash.kpi.copies', route: '/catalogue', goodUp: true, money: false },
  { key: 'on_loan', labelKey: 'dash.kpi.onLoan', route: '/catalogue', query: { status: 'on_loan' }, goodUp: true, money: false },
  { key: 'overdue', labelKey: 'dash.kpi.overdue', route: '/overdue', goodUp: false, money: false },
  { key: 'active_members', labelKey: 'dash.kpi.activeMembers', route: '/members', query: { status: 'active' }, goodUp: true, money: false },
  { key: 'fines_outstanding', labelKey: 'dash.kpi.finesOutstanding', route: '/overdue', goodUp: false, money: true },
]);

/** The quick-action tiles, in the order a librarian works. */
const QUICK = Object.freeze([
  { icon: 'swap', labelKey: 'dash.quickIssue', path: '/counter', query: { intent: 'issue' } },
  { icon: 'plus', labelKey: 'dash.quickAdd', path: '/catalogue', query: { intent: 'new' } },
  { icon: 'users', labelKey: 'dash.quickMember', path: '/members', query: { intent: 'new' } },
  { icon: 'chart', labelKey: 'dash.quickReport', path: '/reports' },
]);

/**
 * @param {number} hour 0–23
 * @returns {string} Catalogue key for the greeting.
 */
function greetingKey(hour) {
  if (hour < 12) return 'dash.greeting.morning';
  if (hour < 18) return 'dash.greeting.afternoon';
  return 'dash.greeting.evening';
}

/**
 * Turn a KPI's `value` / `delta_30d` pair into a stat-card delta.
 *
 * @param {{value: number|string, delta_30d?: number}} kpi
 * @param {boolean} goodUp
 * @param {boolean} money
 * @returns {{value: string, tone: string, label: string}}
 */
function deltaOf(kpi, goodUp, money) {
  const value = Number(kpi.value) || 0;
  const change = Number(kpi.delta_30d) || 0;
  if (!change) {
    return { value: t('dash.deltaFlat'), tone: 'flat', label: t('dash.deltaAbs', { value: '0' }) };
  }
  const sign = change > 0 ? '+' : '−';
  const magnitude = Math.abs(change);
  const base = value - change;
  const tone = (change > 0) === goodUp ? 'up' : 'down';

  if (base > 0) {
    const pct = Math.max(1, Math.round((magnitude / base) * 100));
    return {
      value: `${sign}${fmtNum(pct)}%`,
      tone,
      label: t('dash.deltaPct', { value: `${sign}${fmtNum(pct)}` }),
    };
  }
  // No baseline to divide by (the figure started at zero), so show the count.
  const text = money ? `${sign}${fmtMoney(magnitude)}` : `${sign}${fmtNum(magnitude)}`;
  return { value: text, tone, label: t('dash.deltaAbs', { value: text }) };
}

/**
 * Resolve a loan row to its book and open the drawer.
 *
 * `/api/dashboard` returns the accession number rather than the book id — the
 * loan row does not carry one — so the barcode lookup is the honest way back to
 * the record. It is one request, on click, and it reuses the same resolver the
 * circulation counter uses.
 *
 * @param {{accession_no?: string}} row
 * @param {() => void} onChanged
 */
async function openFromLoanRow(row, onChanged) {
  const accession = String((row && row.accession_no) || '');
  if (!accession) return;
  try {
    const found = await api.get('/api/books/lookup', { barcode: accession });
    const book = found && found.book;
    if (!book || book.id === undefined) {
      ui.toast.warning(t('err.not_found'));
      return;
    }
    openBookDrawer(book.id, { onChanged });
  } catch (error) {
    ui.toast.error(t('toast.error'), ui.formatError(error).message);
  }
}

/**
 * Render the dashboard.
 *
 * @param {{navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  let destroyed = false;
  let current = null;

  const name = (store.user() && (store.user().display_name || store.user().username)) || '';
  const greeting = ui.el('h1', { class: 'page__title' });
  const dateLine = ui.el('p', { class: 'dash__date' });

  const paintHead = () => {
    greeting.textContent = t(greetingKey(new Date().getHours()), { name });
    dateLine.textContent = t('dash.dateLine', {
      date: fmtDate(new Date(), 'long'),
      school: store.schoolName() || '',
    });
  };
  paintHead();

  const refreshButton = ui.iconButton({
    icon: 'refresh',
    label: t('common.refresh'),
    onClick: () => load(),
  });

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'dash__greeting' }, greeting, dateLine),
    ui.el('div', { class: 'page__actions' }, refreshButton));

  const body = ui.el('div', { class: 'u-col', style: { gap: '16px' } });
  const element = ui.el('div', { class: 'u-col', style: { gap: '16px' } }, head, body);

  /** Replace the body with a layout-matched skeleton. */
  function showLoading() {
    body.textContent = '';
    body.appendChild(ui.skeleton({ variant: 'kpi', columns: 6 }));
    body.appendChild(ui.el('div', { class: 'grid grid--2' },
      ui.el('div', { class: 'card card--pad' }, ui.skeleton({ variant: 'text', rows: 5 })),
      ui.el('div', { class: 'card card--pad' }, ui.skeleton({ variant: 'text', rows: 5 }))));
    body.appendChild(ui.el('div', { class: 'grid grid--2' },
      ui.el('div', { class: 'card card--pad' }, ui.skeleton({ rows: 4, columns: 3 })),
      ui.el('div', { class: 'card card--pad' }, ui.skeleton({ rows: 4, columns: 3 }))));
  }

  /**
   * The amber strip. Rendered only when the server reported something.
   * @param {Array<{kind: string, count: number}>} alerts
   * @returns {?HTMLElement}
   */
  function buildAlerts(alerts) {
    if (alertsDismissed) return null;
    const entries = [];
    for (const alert of alerts || []) {
      if (!alert || !alert.count) continue;
      if (alert.kind === 'no_available_with_holds') {
        entries.push({
          text: t('dash.alerts.holds', { count: fmtNum(alert.count) }),
          path: '/reservations',
          labelKey: 'dash.alerts.fixHolds',
        });
      } else if (alert.kind === 'over_loan_limit') {
        entries.push({
          text: t('dash.alerts.limit', { count: fmtNum(alert.count) }),
          path: '/members',
          labelKey: 'dash.alerts.fixLimit',
        });
      }
    }
    if (!entries.length) return null;

    const list = ui.el('div', { class: 'alerts__list' });
    for (const entry of entries) {
      list.appendChild(ui.el('div', { class: 'alerts__item' },
        ui.el('span', { text: entry.text }),
        ui.el('a', {
          class: 'alerts__link',
          href: `#${entry.path}`,
          on: {
            click: (event) => {
              event.preventDefault();
              ctx.navigate(entry.path);
            },
          },
        }, ui.el('span', { text: t(entry.labelKey) }), ui.icon('chevronRight', 12))));
    }

    return ui.el('div', { class: 'alerts' },
      ui.el('span', { class: 'alerts__icon' }, ui.icon('alert', 18)),
      ui.el('div', { class: 'alerts__body' },
        ui.el('p', { class: 'alerts__title', text: t('dash.alerts') }),
        list),
      ui.iconButton({
        icon: 'x',
        label: t('common.dismiss'),
        onClick: (event) => {
          alertsDismissed = true;
          const strip = event && event.target && event.target.closest
            ? event.target.closest('.alerts')
            : null;
          if (strip) strip.remove();
        },
      }));
  }

  /**
   * @param {{kpis: Object, charts: Object, alerts: Array}} data
   */
  function buildKpis(data) {
    const grid = ui.el('div', { class: 'grid grid--kpi' });
    for (const definition of KPIS) {
      const kpi = (data.kpis && data.kpis[definition.key]) || { value: 0, delta_30d: 0 };
      const raw = Number(kpi.value) || 0;
      grid.appendChild(ui.statCard({
        label: t(definition.labelKey),
        value: definition.money ? fmtMoney(raw) : fmtNum(raw),
        delta: deltaOf(kpi, definition.goodUp, definition.money),
        onClick: () => ctx.navigate(definition.route, { query: definition.query }),
      }));
      const tile = grid.lastElementChild;
      tile.setAttribute('aria-label', `${t(definition.labelKey)}: ${t('dash.openKpi', { label: t(definition.labelKey) })}`);
    }
    return grid;
  }

  /**
   * @param {{loans_by_month: Array, collection_by_type: Array}} chartsData
   */
  function buildCharts(chartsData) {
    const months = (chartsData.loans_by_month || []).slice(-6);
    const types = (chartsData.collection_by_type || []).slice(0, 6);

    const bar = charts.barChart({
      data: months.map((row) => ({
        label: fmtDate(`${row.month}-01`, 'month'),
        value: Number(row.count) || 0,
      })),
      height: 200,
      ariaLabel: t('dash.chart.loansByMonth'),
      emptyText: t('dash.emptyChart'),
    });

    const totalTitles = (chartsData.collection_by_type || [])
      .reduce((sum, row) => sum + (Number(row.count) || 0), 0);
    const donut = charts.donut({
      data: types.map((row) => {
        const count = Number(row.count) || 0;
        const share = totalTitles > 0 ? count / totalTitles : 0;
        return {
          label: t(`type.${row.type}`),
          value: count,
          valueLabel: `${fmtNum(count)} · ${fmtPct(share)}`,
        };
      }),
      size: 168,
      centerValue: fmtNum(totalTitles),
      centerLabel: t('dash.donutCentre'),
      ariaLabel: t('dash.chart.collectionByType'),
      emptyText: t('dash.emptyCollection'),
    });

    return ui.el('div', { class: 'grid grid--2' },
      ui.card({ title: t('dash.chart.loansByMonth'), subtitle: t('dash.chart.window'), body: bar }),
      ui.card({ title: t('dash.chart.collectionByType'), body: donut }));
  }

  /**
   * One of the two loan lists.
   *
   * @param {string} titleKey
   * @param {Array} rows
   * @param {string} emptyKey
   * @param {boolean} withLateness
   */
  function buildList(titleKey, rows, emptyKey, withLateness) {
    const columns = [
      {
        key: 'book_title',
        label: t('dash.col.title'),
        render: (row) => ui.el('span', { class: 'u-truncate', text: row.book_title || t('common.na') }),
      },
      {
        key: 'member_name',
        label: t('dash.col.member'),
        render: (row) => ui.el('span', { class: 'u-truncate', text: row.member_name || t('common.na') }),
      },
      {
        key: 'due_date',
        label: t('dash.col.due'),
        render: (row) => ui.el('span', { class: 'u-num', text: fmtDate(row.due_date, 'short') }),
      },
    ];
    if (withLateness) {
      columns.push({
        key: 'overdue_days',
        label: '',
        align: 'right',
        render: (row) => ui.pill(t('dash.col.overdueBy', { count: fmtNum(row.overdue_days || 0) }), 'danger'),
      });
    }

    const toolbar = ui.el('div', { class: 'cat__toolbar' },
      ui.el('h2', { class: 'card__title', text: t(titleKey) }),
      ui.button({
        label: t('dash.viewAll'),
        variant: 'ghost',
        size: 'sm',
        iconRight: 'chevronRight',
        onClick: () => ctx.navigate('/overdue'),
      }));

    return ui.table({
      columns,
      rows,
      compact: true,
      toolbar,
      empty: { icon: withLateness ? 'check' : 'calendar', title: t(emptyKey) },
      onRowClick: (row) => openFromLoanRow(row, () => load()),
    });
  }

  /** The four quick actions. */
  function buildQuick() {
    const grid = ui.el('div', { class: 'quick' });
    for (const action of QUICK) {
      grid.appendChild(ui.el('button', {
        class: 'quick__item',
        attrs: { type: 'button' },
        on: { click: () => ctx.navigate(action.path, { query: action.query }) },
      },
      ui.el('span', { class: 'quick__icon' }, ui.icon(action.icon, 18)),
      ui.el('span', { text: t(action.labelKey) })));
    }
    return ui.el('div', { class: 'u-col', style: { gap: '8px' } },
      ui.el('h2', { class: 'section__title', text: t('dash.quick.title') }),
      grid);
  }

  /**
   * @param {Object} data
   */
  function showData(data) {
    current = data;
    body.textContent = '';

    const strip = buildAlerts(data.alerts);
    if (strip) body.appendChild(strip);

    body.appendChild(buildKpis(data));
    body.appendChild(buildCharts(data.charts || {}));
    body.appendChild(ui.el('div', { class: 'grid grid--2' },
      buildList('dash.dueToday', data.due_today || [], 'dash.emptyDue', false),
      buildList('dash.overdue', data.overdue || [], 'dash.emptyOverdue', true)));
    body.appendChild(buildQuick());
  }

  /**
   * @param {*} error
   */
  function showError(error) {
    body.textContent = '';
    body.appendChild(ui.el('div', { class: 'card card--pad' },
      ui.errorState({ error, onRetry: () => load() })));
  }

  /** Fetch and paint. */
  async function load() {
    showLoading();
    try {
      const data = await api.get('/api/dashboard');
      if (destroyed) return;
      showData(data);
    } catch (error) {
      if (destroyed) return;
      if (error && error.is && error.is('aborted')) return;
      showError(error);
    }
  }

  const onLang = () => {
    paintHead();
    if (current) showData(current);
    else load();
  };
  document.addEventListener('imri:lang', onLang);

  load();

  return {
    element,
    destroy() {
      destroyed = true;
      document.removeEventListener('imri:lang', onLang);
    },
  };
}

export default { render };
