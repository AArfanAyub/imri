/**
 * The three-step import wizard.
 *
 * The server does the parsing and the validating, and hands back a preview plus
 * a `batch_token` that lives for ten minutes (`_IMPORT_TTL_SECONDS`). This
 * module never re-reads the file: step 3 posts the token, so what gets imported
 * is exactly what step 2 showed. If the token has expired by then the server
 * says `import_expired` and the wizard returns to step 1 rather than pretending
 * anything was written.
 *
 * Dismissing the wizard cancels the cached batch (`DELETE /api/import/{token}`)
 * so an abandoned preview does not sit in memory until it times out.
 */

import { t, fmtNum } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';

/** How many preview rows the server sends. */
const PREVIEW_ROWS = 20;
/** How many rejection rows the server sends. */
const PREVIEW_ERRORS = 50;

/**
 * Build a CSV blob from rejected rows and hand it to the browser.
 *
 * @param {Array<{row_no: number, code: string, message: string}>} rows
 */
function downloadRejected(rows) {
  const header = ['row', 'code', 'message'];
  const lines = [header.join(',')];
  for (const row of rows) {
    const cells = [row.row_no, row.code, row.message].map((value) => {
      const text = value === undefined || value === null ? '' : String(value);
      return `"${text.replace(/"/g, '""')}"`;
    });
    lines.push(cells.join(','));
  }
  const blob = new Blob([`\ufeff${lines.join('\r\n')}`], { type: 'text/csv;charset=utf-8' });
  api.saveBlob(blob, 'imri-rejected-rows.csv');
}

/**
 * Open the wizard.
 *
 * @param {{onDone?: (result: Object) => void}} [options]
 */
export function openImportWizard(options) {
  const opts = options || {};
  let step = 1;
  let file = null;
  let preview = null;
  let result = null;
  let busy = false;
  let cancelled = false;

  const body = ui.el('div', { class: 'u-col', style: { gap: '16px' } });
  const footer = ui.el('div', { class: 'u-row u-row--between u-grow' });
  const stepBar = ui.el('div', { class: 'steps' });

  const dialog = ui.modal({
    title: t('cat.import.title'),
    body: [stepBar, body],
    footer,
    wide: true,
    guard: () => false,
    onClose: () => {
      if (!cancelled && preview && !result) {
        // Best effort: the token may already have expired.
        api.del(`/api/import/${encodeURIComponent(preview.batch_token)}`).catch(() => {});
      }
    },
  });

  /** Repaint the three-dot progress strip. */
  function paintSteps() {
    stepBar.textContent = '';
    const labels = ['cat.import.step.pick', 'cat.import.step.check', 'cat.import.step.commit'];
    labels.forEach((key, index) => {
      const number = index + 1;
      const state = number === step ? 'step--active' : (number < step ? 'step--done' : '');
      stepBar.appendChild(ui.el('div', { class: `step ${state}`.trim() },
        ui.el('span', { class: 'step__num' }, number < step
          ? ui.icon('check', 11)
          : ui.el('span', { text: String(number) })),
        ui.el('span', { text: t(key) })));
      if (number < labels.length) stepBar.appendChild(ui.el('span', { class: 'step__sep' }));
    });
  }

  /** Repaint the footer for the current step. */
  function paintFooter() {
    footer.textContent = '';
    footer.appendChild(ui.button({
      label: step === 3 ? t('common.done') : t('common.cancel'),
      onClick: () => {
        cancelled = true;
        dialog.close();
      },
    }));

    if (step === 2) {
      footer.appendChild(ui.el('div', { class: 'u-row' },
        ui.button({
          label: t('common.back'),
          onClick: () => {
            preview = null;
            goTo(1);
          },
        }),
        ui.button({
          label: t('cat.import.commit', { count: fmtNum(preview ? preview.ok_count : 0) }),
          variant: 'primary',
          icon: 'upload',
          disabled: !preview || !preview.ok_count,
          onClick: () => commit(),
        })));
    } else if (step === 3 && result) {
      const actions = ui.el('div', { class: 'u-row' });
      const rejected = (result.errors && result.errors.length)
        ? result.errors
        : ((preview && preview.errors) || []);
      if (rejected.length) {
        actions.appendChild(ui.button({
          label: t('cat.import.downloadRejected'),
          icon: 'download',
          onClick: () => downloadRejected(rejected),
        }));
      }
      actions.appendChild(ui.button({
        label: t('cat.import.another'),
        variant: 'primary',
        onClick: () => {
          preview = null;
          result = null;
          file = null;
          goTo(1);
        },
      }));
      footer.appendChild(actions);
    }
  }

  /**
   * @param {number} next
   */
  function goTo(next) {
    step = next;
    paintSteps();
    paintFooter();
    if (next === 1) paintPick();
    else if (next === 2) paintCheck();
    else paintResult();
  }

  // ----------------------------------------------------------------------- //
  // Step 1 — choose a file
  // ----------------------------------------------------------------------- //

  function paintPick() {
    body.textContent = '';

    const input = ui.el('input', {
      attrs: { type: 'file', accept: '.csv,.xlsx,text/csv', hidden: 'hidden' },
      on: { change: (event) => chooseFile(event.target.files && event.target.files[0]) },
    });
    input.id = `import-file-${ui.nextId('f')}`;

    const browse = ui.button({
      label: t('cat.import.browse'),
      variant: 'secondary',
      icon: 'folder',
      onClick: () => input.click(),
    });

    const zone = ui.el('div', {
      class: 'drop',
      on: {
        dragover: (event) => {
          event.preventDefault();
          zone.classList.add('drop--over');
        },
        dragleave: () => zone.classList.remove('drop--over'),
        drop: (event) => {
          event.preventDefault();
          zone.classList.remove('drop--over');
          const dropped = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
          if (dropped) chooseFile(dropped);
        },
      },
    },
    ui.icon('upload', 28),
    ui.el('p', { text: t('cat.import.drop') }),
    browse,
    ui.el('p', { class: 'drop__hint', text: t('cat.import.formats') }));

    body.appendChild(zone);
    body.appendChild(input);

    if (file) {
      body.appendChild(ui.el('p', { class: 'u-sm u-muted' },
        `${t('cat.import.file')}: ${file.name}`));
    }
    window.requestAnimationFrame(() => browse.focus());
  }

  /**
   * Upload the file and move to the preview.
   * @param {?File} chosen
   */
  async function chooseFile(chosen) {
    if (!chosen || busy) return;
    file = chosen;
    busy = true;
    body.textContent = '';
    body.appendChild(ui.el('div', { class: 'u-col', style: { gap: '8px' } },
      ui.skeleton({ variant: 'text', rows: 3 }),
      ui.el('p', { class: 'u-sm u-muted', text: t('cat.import.reading') })));

    try {
      preview = await api.upload('/api/books/import/preview', chosen);
      busy = false;
      goTo(2);
    } catch (error) {
      busy = false;
      preview = null;
      goTo(1);
      if (error && error.is && error.isAny('import_empty', 'import_bad_encoding')) {
        ui.toast.error(ui.formatError(error).message);
      } else {
        ui.toast.error(t('toast.error'), ui.formatError(error).message);
      }
    }
  }

  // ----------------------------------------------------------------------- //
  // Step 2 — check the rows
  // ----------------------------------------------------------------------- //

  function paintCheck() {
    body.textContent = '';
    if (!preview) return;

    const summary = ui.el('div', { class: 'import-summary' });
    const cell = (labelKey, value, tone) => ui.el('div', { class: 'import-summary__item' },
      ui.el('span', { class: `import-summary__num${tone ? ` u-${tone}` : ''}`, text: fmtNum(value) }),
      ui.el('span', { class: 'import-summary__label', text: t(labelKey) }));
    summary.appendChild(cell('cat.import.total', preview.total));
    summary.appendChild(cell('cat.import.ready', preview.ok_count));
    summary.appendChild(cell('cat.import.rejected', preview.error_count));
    body.appendChild(summary);

    body.appendChild(ui.el('p', { class: 'u-sm u-muted' },
      `${t('cat.import.file')}: ${file ? file.name : ''}`));

    // --- rows that will be imported ------------------------------------- //
    const columns = preview.columns || {};
    const headers = Object.keys(columns).slice(0, 4);
    if (preview.preview && preview.preview.length) {
      body.appendChild(ui.el('h3', { class: 'book__section-title', text: t('cat.import.readyTable') }));
      const rows = preview.preview.map((row) => Object.assign({}, row));
      body.appendChild(ui.el('div', { class: 'import-table' }, ui.table({
        compact: true,
        columns: headers.map((header) => ({
          key: columns[header],
          label: header,
          render: (row) => ui.el('span', { class: 'u-truncate', text: row[columns[header]] === undefined ? '' : String(row[columns[header]]) }),
        })).concat([{
          key: '__status',
          label: '',
          align: 'right',
          render: () => ui.pill(t('status.ok'), 'success'),
        }]),
        rows,
      })));
      if (preview.ok_count > PREVIEW_ROWS) {
        body.appendChild(ui.el('p', { class: 'u-xs u-muted' },
          t('cat.import.more', { count: fmtNum(preview.ok_count - PREVIEW_ROWS) })));
      }
    }

    // --- rows that will be skipped -------------------------------------- //
    if (preview.errors && preview.errors.length) {
      body.appendChild(ui.el('h3', { class: 'book__section-title', text: t('cat.import.rejectedTable') }));
      body.appendChild(ui.el('div', { class: 'import-table' }, ui.table({
        compact: true,
        columns: [
          {
            key: 'row_no',
            label: t('cat.import.rowNo'),
            align: 'right',
            render: (row) => ui.el('span', { class: 'u-num', text: fmtNum(row.row_no) }),
          },
          {
            key: 'code',
            label: t('cat.import.reason'),
            render: (row) => ui.el('span', { text: t(`err.${row.code}`) }),
          },
          { key: '__status', label: '', align: 'right', render: () => ui.pill(t('status.error'), 'danger') },
        ],
        rows: preview.errors,
      })));
      if (preview.error_count > PREVIEW_ERRORS) {
        body.appendChild(ui.el('p', { class: 'u-xs u-muted' },
          t('cat.import.more', { count: fmtNum(preview.error_count - PREVIEW_ERRORS) })));
      }
    }

    if (!preview.ok_count) {
      body.appendChild(ui.el('p', { class: 'u-sm u-muted', text: t('cat.import.noRows') }));
    }
  }

  // ----------------------------------------------------------------------- //
  // Step 3 — commit
  // ----------------------------------------------------------------------- //

  async function commit() {
    if (busy || !preview) return;
    busy = true;
    body.textContent = '';
    body.appendChild(ui.el('div', { class: 'u-col', style: { gap: '8px' } },
      ui.skeleton({ variant: 'text', rows: 3 }),
      ui.el('p', { class: 'u-sm u-muted', text: t('cat.import.committing') })));
    paintFooter();

    try {
      result = await api.post('/api/books/import/commit', { batch_token: preview.batch_token });
      busy = false;
      goTo(3);
      if (opts.onDone) opts.onDone(result);
    } catch (error) {
      busy = false;
      if (error && error.is && error.is('import_expired')) {
        preview = null;
        goTo(1);
        ui.toast.warning(t('cat.import.expired'));
      } else {
        goTo(2);
        ui.toast.error(t('toast.error'), ui.formatError(error).message);
      }
    }
  }

  function paintResult() {
    body.textContent = '';
    if (!result) return;
    body.appendChild(ui.el('p', { class: 'u-md', text: t('cat.import.done') }));
    body.appendChild(ui.el('p', { class: 'u-sm u-muted' },
      t('cat.import.result', {
        imported: fmtNum(result.imported || 0),
        rejected: fmtNum(result.rejected || 0),
      })));
    if (result.errors && result.errors.length) {
      body.appendChild(ui.el('div', { class: 'import-table' }, ui.table({
        compact: true,
        columns: [
          {
            key: 'row_no',
            label: t('cat.import.rowNo'),
            align: 'right',
            render: (row) => ui.el('span', { class: 'u-num', text: fmtNum(row.row_no) }),
          },
          { key: 'code', label: t('cat.import.reason'), render: (row) => ui.el('span', { text: t(`err.${row.code}`) }) },
        ],
        rows: result.errors,
      })));
    }
  }

  goTo(1);
}

export default { openImportWizard };
