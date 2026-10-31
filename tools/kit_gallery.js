/*
 * Component-kit gallery, evaluated inside the running app by
 * `tools/proof_t4_ui.py`.
 *
 * Renders one of every component into a full-screen layer, returns a JSON
 * census of what appeared, and leaves the layer in place so the proof can take
 * a screenshot before removing it.
 *
 * Kept in its own file so the proof reads as Python, not as a quoted script.
 */
(async () => {
  const ui = await import('/js/ui.js');
  const charts = await import('/js/charts.js');
  const i18n = await import('/js/i18n.js');
  const t = i18n.t;

  const host = document.createElement('div');
  host.id = 'kit-gallery';
  host.style.cssText = 'position:fixed;inset:0;z-index:1000;overflow:auto;'
    + 'background:var(--bg);padding:24px;display:block;';

  // Block flow, not a flex column: flex children shrink to fit the viewport and
  // a table would collapse to a hairline.
  const space = (node) => {
    node.style.marginBottom = '16px';
    return node;
  };
  const row = (...nodes) => ui.el('div', { class: 'u-row u-wrap' }, nodes);

  // --- buttons ---------------------------------------------------------- //
  host.appendChild(space(row(
    ui.button({ label: t('common.save'), variant: 'primary' }),
    ui.button({ label: t('common.cancel') }),
    ui.button({ label: t('common.delete'), variant: 'danger', icon: 'trash' }),
    ui.button({ label: t('common.loading'), loading: true }),
    ui.button({ label: t('common.save'), variant: 'primary', size: 'sm' }),
    ui.iconButton({ icon: 'settings', label: t('nav.settings') }),
  )));

  // --- form controls ---------------------------------------------------- //
  const grid = ui.el('div', { class: 'grid grid--2' });
  grid.appendChild(ui.field({
    label: t('cat.form.title'),
    required: true,
    hint: t('common.optional'),
    control: ui.input({ value: 'The Quiet Library', placeholder: t('common.search') }),
  }));
  grid.appendChild(ui.field({
    label: t('cat.form.title'),
    error: t('validate.required'),
    control: ui.input({ value: '' }),
  }));
  grid.appendChild(ui.field({
    label: t('mem.form.address'),
    control: ui.select({
      value: 'a',
      options: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }],
    }),
  }));
  grid.appendChild(ui.field({
    label: t('common.details'),
    control: ui.textarea({ value: '', rows: 2 }),
  }));
  host.appendChild(space(grid));

  host.appendChild(space(row(
    ui.checkbox({ label: t('common.enabled'), checked: true }),
    ui.switchControl({ label: t('theme.dark'), checked: false }),
    ui.kbd(['Ctrl', 'K']),
  )));

  // --- table ------------------------------------------------------------ //
  host.appendChild(space(ui.table({
    columns: [
      { key: 'title', label: t('cat.col.title'), sortable: true },
      { key: 'copies', label: t('cat.copies.title'), align: 'right' },
      { key: 'status', label: t('common.summary'), render: (r) => ui.statusPill(r.status) },
    ],
    rows: [
      { title: 'A Short History of Nearly Everything', copies: 3, status: 'available' },
      { title: 'The Left Hand of Darkness', copies: 1, status: 'on_loan' },
      { title: 'Pulang', copies: 2, status: 'lost' },
    ],
    sort: { key: 'title', dir: 'asc' },
    onSort: () => {},
  })));

  // --- pills, chips, tabs ----------------------------------------------- //
  host.appendChild(space(row(
    ui.pill(t('status.available'), 'success'),
    ui.pill(t('status.onLoan'), 'primary'),
    ui.pill(t('status.overdue'), 'danger'),
    ui.pill(t('status.reserved'), 'warning'),
    ui.statusPill('withdrawn'),
    ui.statusPill('on_loan'),
  )));

  host.appendChild(space(ui.chips({
    items: [
      { value: 'book', label: t('type.book'), count: 12 },
      { value: 'av', label: t('type.av'), count: 3 },
    ],
    value: 'book',
    onChange: () => {},
  })));

  host.appendChild(space(ui.tabs({
    items: [
      { value: 'a', label: t('adm.tab.license') },
      { value: 'b', label: t('adm.tab.settings') },
    ],
    value: 'a',
    onChange: () => {},
  })));

  // --- KPI tiles -------------------------------------------------------- //
  const stats = ui.el('div', { class: 'grid grid--kpi' });
  stats.appendChild(ui.statCard({
    label: t('dash.kpi.titles'),
    value: '1,248',
    delta: { value: '+12', tone: 'up', label: t('dash.delta', { value: 12 }) },
    spark: charts.sparkline({ values: [3, 6, 4, 9, 7, 12, 10, 14] }),
  }));
  stats.appendChild(ui.statCard({
    label: t('dash.kpi.onLoan'),
    value: '37',
    delta: { value: '—', tone: 'flat' },
  }));
  stats.appendChild(ui.statCard({
    label: t('dash.kpi.overdue'),
    value: '7',
    delta: { value: '-2', tone: 'down' },
  }));
  host.appendChild(space(stats));

  // --- charts ----------------------------------------------------------- //
  const chartsRow = ui.el('div', { class: 'grid grid--2' });
  chartsRow.appendChild(ui.card({
    title: t('dash.chart.loansByMonth'),
    body: charts.barChart({
      data: [
        { label: 'Jan', value: 12 }, { label: 'Feb', value: 19 },
        { label: 'Mar', value: 8 }, { label: 'Apr', value: 24 },
        { label: 'May', value: 17 }, { label: 'Jun', value: 30 },
      ],
      ariaLabel: 'Loans per month',
    }),
  }));
  chartsRow.appendChild(ui.card({
    title: t('dash.chart.collectionByType'),
    body: charts.donut({
      data: [
        { label: t('type.book'), value: 820 },
        { label: t('type.av'), value: 140 },
        { label: t('type.magazine'), value: 288 },
      ],
      centerValue: '1,248',
      centerLabel: t('dash.kpi.copies'),
      ariaLabel: 'Collection by type',
    }),
  }));
  host.appendChild(space(chartsRow));

  host.appendChild(space(ui.banner({ text: t('dash.alert.backup') })));

  // --- states ----------------------------------------------------------- //
  host.appendChild(space(ui.card({
    flush: true,
    foot: ui.pagination({ page: 2, pageSize: 25, total: 248, onChange: () => {} }),
    body: ui.emptyState({
      icon: 'search',
      title: t('cat.empty.title'),
      body: t('cat.empty.body'),
      action: ui.button({ label: t('cat.new'), variant: 'primary', icon: 'plus' }),
    }),
  })));

  host.appendChild(space(ui.errorState({
    error: { code: 'db_busy', status: 503, message: 'busy' },
    onRetry: () => {},
  })));

  host.appendChild(space(ui.skeleton({ rows: 2, columns: 3 })));

  document.body.appendChild(host);

  return JSON.stringify({
    buttons: host.querySelectorAll('.btn').length,
    fields: host.querySelectorAll('.field').length,
    rows: host.querySelectorAll('.tbl tbody tr').length,
    pills: host.querySelectorAll('.pill').length,
    chips: host.querySelectorAll('.chip').length,
    tabs: host.querySelectorAll('.tab').length,
    stats: host.querySelectorAll('.stat').length,
    bars: host.querySelectorAll('.chart__bar').length,
    arcs: host.querySelectorAll('.chart__arc').length,
    sparks: host.querySelectorAll('.spark').length,
    pager: host.querySelectorAll('.pager__btn').length,
    empty: host.querySelectorAll('.empty').length,
    errors: host.querySelectorAll('.errstate').length,
    skeleton: host.querySelectorAll('.sk').length,
  });
})()
