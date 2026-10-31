/**
 * Reports — the numbers a librarian hands to the head teacher.
 *
 * Every report arrives from the server in the same shape: `{title, columns,
 * rows}` plus whatever summary the kind provides. One renderer therefore draws
 * all five, and the CSV/PDF export is built from the same payload the table
 * shows — so the file and the screen cannot drift apart.
 */

import { t, fmtNum, fmtDate, fmtMoney, toISODate } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';
import * as charts from '../charts.js';

/** The five report kinds, in tab order. */
const KINDS = Object.freeze([
  { value: 'collection', labelKey: 'rep.collection' },
  { value: 'circulation', labelKey: 'rep.circulation' },
  { value: 'membership', labelKey: 'rep.membership' },
  { value: 'overdue-fines', labelKey: 'rep.overdueFines' },
  { value: 'trend', labelKey: 'rep.trend' },
]);

/** Period presets, in days (`all` means no lower bound). */
const PRESETS = Object.freeze([
  { value: '30', labelKey: 'rep.preset.30' },
  { value: '90', labelKey: 'rep.preset.90' },
  { value: 'year', labelKey: 'rep.preset.year' },
  { value: 'all', labelKey: 'rep.preset.all' },
]);

/** Columns whose values should be rendered as money. */
const MONEY_COLUMNS = new Set(['value', 'fine', 'fine_amount', 'accrued_fine', 'balance', 'paid', 'waived', 'outstanding', 'total_fines']);

/**
 * How many rows the on-screen table draws before it stops.
 *
 * The API returns the whole report because the CSV and PDF exports are built
 * from it. The screen is a different medium: past a few hundred rows a table
 * stops being readable, and building 200,000 DOM rows locks the tab. 500 is
 * more than fits on any monitor and still paints instantly.
 */
const PREVIEW_LIMIT = 500;

/**
 * ISO date `days` before today.
 * @param {number} days
 * @returns {string}
 */
function daysAgo(days) {
  const date = new Date();
  date.setDate(date.getDate() - days);
  // Local, not UTC: after 8pm in UTC+8 `toISOString()` returns yesterday.
  return toISODate(date);
}

/**
 * Render the reports screen.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  const query = ctx.query || {};
  let kind = KINDS.some((entry) => entry.value === query.kind) ? query.kind : 'collection';
  let preset = query.preset || '30';
  let report = null;
  let loading = true;
  let error = null;
  let destroyed = false;

  const title = ui.el('h1', { class: 'page__title', text: t('rep.title') });
  const subtitle = ui.el('p', { class: 'page__sub', text: t('rep.subtitle') });

  const csvButton = ui.button({
    label: t('rep.export.csv'),
    icon: 'download',
    onClick: () => exportReport('csv'),
  });
  const pdfButton = ui.button({
    label: t('rep.export.pdf'),
    icon: 'printer',
    variant: 'primary',
    onClick: () => exportReport('pdf'),
  });
  const printButton = ui.button({
    label: t('common.print'),
    icon: 'printer',
    onClick: () => window.print(),
  });

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle),
    ui.el('div', { class: 'page__actions' }, printButton, csvButton, pdfButton));

  const kindTabs = ui.tabs({
    value: kind,
    items: KINDS.map((entry) => ({ value: entry.value, label: t(entry.labelKey) })),
    onChange: (value) => {
      kind = value;
      load();
    },
  });

  const presetChips = ui.chips({
    value: preset,
    allowAll: false,
    items: PRESETS.map((entry) => ({ value: entry.value, label: t(entry.labelKey) })),
    onChange: (value) => {
      preset = value || '30';
      load();
    },
  });

  const metaBar = ui.el('div', { class: 'filterbar' },
    ui.el('div', { class: 'filterbar__group' },
      ui.el('span', { class: 'filterbar__label', text: t('rep.range') }), presetChips),
    ui.el('span', { class: 'filterbar__grow' }));

  const summary = ui.el('div', { class: 'stat-row' });
  const body = ui.el('div', { class: 'u-col', style: { gap: '16px' } });
  const chartSlot = ui.el('div');

  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } },
    head, kindTabs, metaBar, summary, chartSlot, body);

  // -- range ------------------------------------------------------------- //
  function range() {
    if (preset === 'all') return {};
    if (preset === 'year') {
      return { from: `${new Date().getFullYear()}-01-01`, to: daysAgo(0) };
    }
    return { from: daysAgo(Number(preset) || 30), to: daysAgo(0) };
  }

  // -- data -------------------------------------------------------------- //
  async function load() {
    loading = true;
    error = null;
    paint();
    try {
      const params = range();
      const data = await api.get(`/api/reports/${encodeURIComponent(kind)}`, params);
      if (destroyed) return;
      report = data;
    } catch (failure) {
      if (destroyed) return;
      error = failure;
      report = null;
    } finally {
      if (!destroyed) {
        loading = false;
        paint();
      }
    }
  }

  async function exportReport(format) {
    try {
      ui.toast.info(t('rep.preparing'));
      const params = Object.assign({ format }, range());
      const meta = await api.get(`/api/reports/${encodeURIComponent(kind)}/export`, params);
      const blob = await api.download(`/api/exports/${encodeURIComponent(meta.filename)}`);
      api.saveBlob(blob, meta.filename);
      ui.toast.success(t('toast.exported'), meta.filename);
    } catch (failure) {
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
    }
  }

  // -- rendering --------------------------------------------------------- //
  /** Human label for a column key, falling back to the key itself. */
  function columnLabel(key) {
    const known = {
      accession_no: 'cat.col.accession',
      title: 'circ.loan.title',
      authors: 'cat.col.author',
      material_type: 'cat.col.type',
      dewey: 'cat.col.dewey',
      call_no: 'cat.col.callNo',
      status: 'cat.col.status',
      copies: 'cat.col.copies',
      available: 'status.available',
      value: 'rep.value',
      issue_date: 'circ.loan.issued',
      due_date: 'circ.loan.due',
      return_date: 'circ.loan.returned',
      member_code: 'mem.col.code',
      member_name: 'mem.col.name',
      class_name: 'mem.col.class',
      member_type: 'mem.col.type',
      overdue_days: 'status.overdue',
      fine: 'circ.fine.title',
      accrued_fine: 'circ.fine.accrued',
      balance: 'circ.fine.balance',
      month: 'rep.month',
      issued: 'rep.issued',
      returned: 'rep.returned',
      category: 'rep.category',
      share: 'rep.share',
      count: 'rep.count',
      name: 'mem.col.name',
      joined: 'mem.col.joined',
    };
    return known[key] ? t(known[key]) : key.replace(/_/g, ' ');
  }

  function cellValue(key, value) {
    if (value === null || value === undefined || value === '') return t('common.na');
    if (MONEY_COLUMNS.has(key) && Number.isFinite(Number(value))) return fmtMoney(Number(value));
    if (key.endsWith('_date') || key === 'joined') return fmtDate(value);
    if (typeof value === 'number') return fmtNum(value);
    return String(value);
  }

  function renderSummary() {
    ui.clear(summary);
    if (!report) return;
    const totals = report.totals || report.summary || {};
    const entries = Object.entries(totals).slice(0, 4);
    for (const [key, value] of entries) {
      summary.appendChild(ui.el('div', { class: 'stat-mini' },
        ui.el('span', { class: 'stat-mini__label', text: columnLabel(key) }),
        ui.el('span', { class: 'stat-mini__value u-num', text: cellValue(key, value) })));
    }
  }

  function renderChart() {
    ui.clear(chartSlot);
    if (!report) return;

    // One chart per kind, each from data the report already carries.
    if (kind === 'collection' && Array.isArray(report.by_material) && report.by_material.length) {
      chartSlot.appendChild(ui.card({
        title: t('rep.collection'),
        body: charts.donut({
          data: report.by_material.map((row) => ({
            label: t(`type.${row.key}`),
            value: Number(row.titles) || 0,
          })),
          centerLabel: t('rep.total'),
        }),
      }));
      return;
    }

    if (kind === 'membership' && Array.isArray(report.by_type) && report.by_type.length) {
      chartSlot.appendChild(ui.card({
        title: t('rep.membership'),
        body: charts.donut({
          data: report.by_type.map((row) => ({
            label: t(`memberType.${row.key}`),
            value: Number(row.count) || 0,
          })),
          centerLabel: t('rep.total'),
        }),
      }));
      return;
    }

    if (kind === 'circulation' && Array.isArray(report.top_titles) && report.top_titles.length) {
      chartSlot.appendChild(ui.card({
        title: t('rep.circulation'),
        body: charts.barChart({
          data: report.top_titles.slice(0, 8).map((row) => ({
            label: row.title,
            value: Number(row.count) || 0,
          })),
        }),
      }));
      return;
    }

    if (kind === 'trend' && Array.isArray(report.months) && report.months.length) {
      chartSlot.appendChild(ui.card({
        title: t('rep.trend'),
        body: charts.barChart({
          data: report.months.map((row) => ({
            label: row.month,
            value: Number(row.issued) || 0,
          })),
        }),
      }));
      return;
    }

    if (kind === 'overdue-fines' && Array.isArray(report.by_bucket) && report.by_bucket.length) {
      chartSlot.appendChild(ui.card({
        title: t('rep.overdueFines'),
        body: charts.barChart({
          data: report.by_bucket.map((row) => ({
            label: String(row.key),
            value: Number(row.count) || 0,
          })),
        }),
      }));
    }
  }

  function renderTable() {
    ui.clear(body);
    if (!report) return;

    const columns = (report.columns || []).map((key) => ({
      key,
      label: columnLabel(key),
      align: typeof (report.rows && report.rows[0] && report.rows[0][key]) === 'number' ? 'right' : undefined,
      render: (row) => {
        const value = row[key];
        const money = MONEY_COLUMNS.has(key);
        return ui.el('span', {
          class: money || typeof value === 'number' ? 'u-num' : undefined,
          text: cellValue(key, value),
        });
      },
    }));

    // Rows arrive as arrays, in column order.
    const allRows = (report.rows || []).map((row) => {
      const record = {};
      (report.columns || []).forEach((key, index) => { record[key] = row[index]; });
      return record;
    });

    // A full collection report over a 200,000-title library is 200,000 rows.
    // Building that many DOM rows freezes the tab for far longer than the query
    // takes, so the screen renders a preview. Nothing is lost: CSV and PDF are
    // generated server-side from the whole result, not from this array.
    const shown = allRows.slice(0, PREVIEW_LIMIT);
    const truncated = allRows.length > shown.length;

    const card = ui.el('div', { class: 'card report-card' });
    card.appendChild(ui.el('header', { class: 'report-card__head' },
      ui.el('div', {},
        ui.el('h2', { class: 'report-card__title', text: report.title || t('rep.title') }),
        ui.el('p', { class: 'u-xs u-muted', text: t('rep.generated', { date: fmtDate(toISODate()) }) })),
      ui.el('span', { class: 'u-xs u-muted u-num', text: t('rep.rows', { count: fmtNum(allRows.length) }) })));

    card.appendChild(ui.table({
      columns,
      rows: shown,
      scroll: true,
      compact: true,
      empty: { icon: 'chart', title: t('rep.empty'), body: '' },
    }));

    if (truncated) {
      card.appendChild(ui.el('p', {
        class: 'u-xs u-muted u-gap-4',
        text: t('rep.previewNote', { shown: fmtNum(shown.length), total: fmtNum(allRows.length) }),
      }));
    }

    body.appendChild(card);
  }

  function paint() {
    renderSummary();
    renderChart();
    ui.clear(body);

    if (loading) {
      body.appendChild(ui.card({ body: ui.skeleton({ rows: 6 }) }));
      return;
    }
    if (error) {
      body.appendChild(ui.errorState({ error, onRetry: () => load() }));
      return;
    }
    renderTable();
  }

  const onLang = () => {
    title.textContent = t('rep.title');
    subtitle.textContent = t('rep.subtitle');
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
