/**
 * Not found — placeholder.
 *
 * Reached by any hash that matches no route. The sidebar stays available, so
 * the user can get back without retyping a URL.
 */

import { pageStub } from './_stub.js';

/**
 * @param {Object} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  return pageStub('errstate.title', {
    variant: 'auth',
    subtitleKey: 'errstate.body',
    icon: 'alert',
    ctx,
  });
}

export default { render };
