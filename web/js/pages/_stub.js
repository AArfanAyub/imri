/**
 * Placeholder page.
 *
 * T4 pass A builds the shell, not the screens. Every route module returns one
 * of these so the router, the guards, the sidebar and the topbar can be tested
 * end to end against a real destination.
 *
 * Each stub still does the things a real page must do, and is therefore a
 * worked example for pass B:
 *   · prints its own title from the catalogue (never a literal)
 *   · repaints when the language changes, without a re-fetch
 *   · returns a `destroy()` so the router can detach its listener
 *
 * @param {string} titleKey Catalogue key for the page title.
 * @param {Object} [options]
 * @param {string} [options.subtitleKey]
 * @param {string} [options.icon] Empty-state illustration.
 * @param {'app'|'auth'} [options.variant]
 * @param {Object} [options.ctx] Router context, echoed in a small caption.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */

import { t } from '../i18n.js';
import * as ui from '../ui.js';

export function pageStub(titleKey, options) {
  const opts = options || {};
  const ctx = opts.ctx || {};

  const title = ui.el('h1', { class: 'page__title' });
  const subtitle = ui.el('p', { class: 'page__sub' });

  const paint = () => {
    title.textContent = t(titleKey);
    if (opts.subtitleKey) {
      subtitle.textContent = t(opts.subtitleKey);
      subtitle.hidden = false;
    } else {
      subtitle.textContent = '';
      subtitle.hidden = true;
    }
  };
  paint();

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle));

  const body = ui.card({
    flush: true,
    body: ui.emptyState({
      icon: opts.icon || 'box',
      title: t('empty.generic.title'),
      body: t('empty.generic.body'),
    }),
  });

  // What the router actually resolved — useful while the pages are still stubs.
  const bits = [ctx.path || ''];
  if (ctx.params && Object.keys(ctx.params).length) bits.push(JSON.stringify(ctx.params));
  if (ctx.query && Object.keys(ctx.query).length) bits.push(JSON.stringify(ctx.query));
  const routeInfo = ui.el('p', { class: 'u-xs u-muted u-mono', text: bits.join('  ') });

  const onLang = () => paint();
  document.addEventListener('imri:lang', onLang);
  const destroy = () => document.removeEventListener('imri:lang', onLang);

  if (opts.variant === 'auth') {
    const cardNode = ui.el('div', { class: 'card card--pad', style: { width: 'min(440px, 100%)' } },
      head, body, routeInfo);
    const wrap = ui.el('div', { class: 'boot' }, cardNode);
    return { element: wrap, destroy };
  }

  const node = ui.el('div', { class: 'u-col', style: { gap: '16px' } }, head, body, routeInfo);
  return { element: node, destroy };
}

export default { pageStub };
