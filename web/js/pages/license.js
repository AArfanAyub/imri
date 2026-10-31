/**
 * Licence & about.
 *
 * One job: prove to the owner that this copy is activated for life, and give
 * support something concrete to work with. The activation code itself is never
 * displayed or echoed — the page shows the fingerprint the server stores, which
 * is enough to identify the install without publishing the key.
 */

import { t, fmtNum, fmtDate } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';

/**
 * Render the licence screen.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  let info = null;
  let diagnostics = null;
  let loading = true;
  let error = null;
  let destroyed = false;

  const title = ui.el('h1', { class: 'page__title', text: t('adm.license.title') });
  const subtitle = ui.el('p', { class: 'page__sub', text: t('app.tagline') });
  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle));

  const body = ui.el('div', { class: 'u-col', style: { gap: '16px' } });
  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } }, head, body);

  // -- data -------------------------------------------------------------- //
  async function load() {
    loading = true;
    error = null;
    paint();
    try {
      const [systemInfo, diag] = await Promise.all([
        api.get('/api/system/info'),
        api.get('/api/system/diagnostics').catch(() => null),
      ]);
      if (destroyed) return;
      info = systemInfo;
      diagnostics = diag;
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

  function copyDiagnostics() {
    if (!info) return;
    const payload = JSON.stringify({ info, diagnostics }, null, 2);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(payload).then(
        () => ui.toast.success(t('common.copied')),
        () => ui.toast.error(t('toast.error')),
      );
    } else {
      ui.toast.error(t('toast.error'));
    }
  }

  // -- paint ------------------------------------------------------------- //
  function paint() {
    ui.clear(body);

    if (loading) {
      body.appendChild(ui.card({ body: ui.skeleton({ rows: 5 }) }));
      return;
    }
    if (error) {
      body.appendChild(ui.errorState({ error, onRetry: () => load() }));
      return;
    }
    if (!info) return;

    const licence = info.license || {};
    const licensed = Boolean(licence.licensed);

    // The headline: licensed or not, in one glance.
    const statusCard = ui.card({
      body: ui.el('div', { class: 'licence-hero' },
        ui.el('div', { class: 'licence-hero__mark' }, ui.icon('check', 22)),
        ui.el('div', { class: 'licence-hero__body' },
          ui.el('h2', { class: 'licence-hero__title', text: licensed ? t('adm.license.active') : t('adm.license.inactive') }),
          ui.el('p', { class: 'licence-hero__text', text: licensed ? t('adm.license.note') : t('app.local') })),
        licensed ? ui.pill(licence.edition || 'perpetual', 'success') : null),
    });
    body.appendChild(statusCard);

    // Facts.
    const rows = [
      { label: t('adm.license.product'), value: 'IMRI' },
      { label: t('adm.license.edition'), value: licence.edition || t('common.na') },
      { label: t('adm.license.school'), value: licence.school_name || store.schoolName() || t('common.na') },
      { label: t('adm.license.activated'), value: licence.activated_at ? fmtDate(String(licence.activated_at).slice(0, 10)) : t('common.na') },
      { label: t('adm.license.version'), value: info.version },
      { label: t('adm.license.schema'), value: fmtNum(info.schema) },
      { label: t('adm.license.status'), value: licensed ? t('adm.license.active') : t('adm.license.inactive') },
    ];
    body.appendChild(ui.card({ title: t('adm.license.title'), body: ui.kv(rows) }));

    // Diagnostics — what support asks for first.
    const counts = info.row_counts || {};
    const objects = info.objects || {};
    const diagCard = ui.card({
      title: t('common.details'),
      actions: ui.button({
        label: t('common.copy'),
        icon: 'copy',
        size: 'sm',
        onClick: () => copyDiagnostics(),
      }),
      body: ui.el('div', { class: 'u-col', style: { gap: '12px' } },
        ui.kv([
          { label: t('adm.license.schema'), value: `${fmtNum(objects.tables)} · ${fmtNum(objects.indexes)}` },
          { label: t('cat.title'), value: fmtNum(counts.books) },
          { label: t('mem.title'), value: fmtNum(counts.members) },
          { label: t('circ.active'), value: fmtNum(counts.loans) },
          { label: 'Python', value: info.python },
        ]),
        ui.el('p', { class: 'u-xs u-muted u-mono', text: info.data_dir || '' })),
    });
    body.appendChild(diagCard);

    body.appendChild(ui.el('p', { class: 'u-xs u-muted', text: t('app.local') }));
  }

  const onLang = () => {
    title.textContent = t('adm.license.title');
    subtitle.textContent = t('app.tagline');
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
