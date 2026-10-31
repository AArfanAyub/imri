/**
 * Label preview.
 *
 * The label is rendered by the server (`GET /api/labels/book/{copy_id}.svg`) so
 * the printed sticker never depends on a canvas, a font or a browser that is not
 * Edge. This module only fetches the markup, shows it, and prints it.
 *
 * Printing moves the SVG into a `<div class="label-print">` attached to `<body>`;
 * the print stylesheet hides every other child of `<body>`, which is the only
 * reliable way to print one element from inside a modal.
 */

import { t } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';

/**
 * Open the preview for one copy.
 *
 * @param {Object} options
 * @param {number} options.copyId
 * @param {string} [options.title] Used for the accessible name.
 * @returns {Promise<void>}
 */
export async function openLabelPreview(options) {
  const opts = options || {};

  const holder = ui.el('div', { class: 'label-preview' }, ui.skeleton({ variant: 'text', rows: 3 }));

  const printButton = ui.button({
    label: t('cat.detail.print'),
    variant: 'primary',
    icon: 'printer',
    disabled: true,
  });

  const dialog = ui.modal({
    title: t('cat.detail.labelTitle'),
    body: [
      ui.el('p', { class: 'u-sm u-muted', text: t('cat.detail.labelHint') }),
      holder,
    ],
    footer: [
      ui.button({ label: t('common.close'), onClick: () => dialog.close() }),
      printButton,
    ],
  });

  let markup = '';
  try {
    markup = await api.svg(`/api/labels/book/${encodeURIComponent(String(opts.copyId))}.svg`, { scale: 4 });
  } catch (error) {
    holder.textContent = '';
    holder.appendChild(ui.errorState({ error }));
    return;
  }

  holder.textContent = '';
  // Server-generated SVG from our own origin; the CSP forbids anything remote.
  holder.innerHTML = markup;
  printButton.disabled = false;
  printButton.addEventListener('click', () => {
    const sheet = ui.el('div', { class: 'label-print' });
    sheet.innerHTML = markup;
    document.body.appendChild(sheet);
    document.body.classList.add('is-printing-label');

    const cleanup = () => {
      document.body.classList.remove('is-printing-label');
      sheet.remove();
      window.removeEventListener('afterprint', cleanup);
    };
    window.addEventListener('afterprint', cleanup);
    try {
      window.print();
    } finally {
      // Some engines never fire afterprint for a cancelled dialog.
      window.setTimeout(cleanup, 2000);
    }
  });
}

export default { openLabelPreview };
