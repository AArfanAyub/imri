/**
 * The `/catalogue/:id` route.
 *
 * A record is a drawer over the list, not a page of its own — that is what makes
 * "next book" cheap: close, and the list is still there with its filters, sort
 * and scroll position intact. So this route paints the catalogue and then opens
 * the drawer on top of it, which also makes a record link deep-linkable and
 * reloadable.
 *
 * Closing the drawer rewrites the URL back to `/catalogue` with `replace`, so the
 * back button does not bounce the reader into a drawer they just dismissed.
 */

import { render as renderCatalogue } from './catalogue.js';
import { openBookDrawer } from './_book-drawer.js';

/**
 * @param {{params: Object, query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  const id = Number(ctx.params && ctx.params.id);
  const list = renderCatalogue(ctx);

  if (!Number.isFinite(id) || id <= 0) {
    // A non-numeric id lands on the list; the record cannot exist.
    ctx.navigate('/catalogue', { replace: true });
    return list;
  }

  const handle = openBookDrawer(id, {
    // An edit or a deleted copy changes the row behind the drawer.
    onChanged: () => {
      if (list.reload) list.reload();
    },
    onClose: () => {
      ctx.navigate('/catalogue', { replace: true });
    },
  });

  return {
    element: list.element,
    destroy() {
      // Closing the drawer here would trigger `onClose` and a navigation during
      // teardown; the router is already moving on, so just let it go.
      if (list.destroy) list.destroy();
      void handle;
    },
  };
}

export default { render };
