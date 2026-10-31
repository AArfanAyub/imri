/**
 * IMRI — router.
 *
 * Hash-based, because the app is served from a single `index.html` inside a
 * desktop shell where a deep link never leaves the machine: `#/catalogue?q=orge`
 * is bookmarkable, survives F5, and needs no server-side rewrite.
 *
 * Each route loads its page module with a dynamic `import()`, so the first paint
 * ships the shell plus one screen instead of every screen at once. Modules are
 * cached by the browser after the first visit.
 *
 * Guards run in a fixed order and can bounce the user:
 *   licence gate → sign-in → forced password change → capability
 *
 * A leave guard runs before navigation away, so a half-filled form asks before
 * it is thrown away (UX-12). Because the hash has already changed by the time
 * `hashchange` fires, a cancelled navigation restores the previous hash with a
 * suppression flag rather than fighting the event.
 */

import * as i18n from './i18n.js';
import * as store from './store.js';

/**
 * Route table.
 *
 * `pattern` segments starting with `:` capture a parameter. Literal routes are
 * declared before parameterised ones so `/catalogue/new` wins over
 * `/catalogue/:id`.
 */
export const ROUTES = Object.freeze([
  // Unauthenticated chrome.
  { name: 'activate', pattern: '/activate', titleKey: 'activate.title', chrome: 'auth', load: () => import('./pages/activate.js') },
  { name: 'login', pattern: '/login', titleKey: 'auth.signIn', chrome: 'auth', load: () => import('./pages/login.js') },
  { name: 'change-password', pattern: '/change-password', titleKey: 'auth.mustChangeTitle', chrome: 'auth', load: () => import('./pages/change-password.js') },

  // Application chrome.
  { name: 'dashboard', pattern: '/dashboard', titleKey: 'nav.dashboard', load: () => import('./pages/dashboard.js'), cap: 'dashboard.read' },
  { name: 'catalogue', pattern: '/catalogue', titleKey: 'cat.title', load: () => import('./pages/catalogue.js'), cap: 'book.read' },
  { name: 'book', pattern: '/catalogue/:id', titleKey: 'cat.detail.title', load: () => import('./pages/book.js'), cap: 'book.read' },
  { name: 'members', pattern: '/members', titleKey: 'nav.members', load: () => import('./pages/members.js'), cap: 'member.read' },
  { name: 'member', pattern: '/members/:id', titleKey: 'mem.profile.title', load: () => import('./pages/member.js'), cap: 'member.read' },
  { name: 'reading-lists', pattern: '/reading-lists', titleKey: 'nav.readingLists', load: () => import('./pages/reading-lists.js'), cap: 'member.read' },
  { name: 'counter', pattern: '/counter', titleKey: 'nav.counter', load: () => import('./pages/counter.js'), cap: 'circ.read' },
  { name: 'overdue', pattern: '/overdue', titleKey: 'nav.overdue', load: () => import('./pages/overdue.js'), cap: 'circ.read' },
  { name: 'reservations', pattern: '/reservations', titleKey: 'nav.reservations', load: () => import('./pages/reservations.js'), cap: 'reservation.read' },
  { name: 'reports', pattern: '/reports', titleKey: 'rep.title', load: () => import('./pages/reports.js'), cap: 'report.read' },
  { name: 'labels', pattern: '/labels', titleKey: 'label.title', load: () => import('./pages/labels.js'), cap: 'label.read' },
  { name: 'admin', pattern: '/admin', titleKey: 'adm.users.title', load: () => import('./pages/users.js'), cap: 'user.read' },
  { name: 'settings', pattern: '/settings', titleKey: 'adm.settings.title', load: () => import('./pages/settings.js'), cap: 'settings.read' },
  { name: 'backup', pattern: '/backup', titleKey: 'adm.backup.title', load: () => import('./pages/backup.js'), cap: 'backup.create' },
  { name: 'audit', pattern: '/audit', titleKey: 'nav.audit', load: () => import('./pages/audit.js'), cap: 'audit.read' },
  { name: 'license', pattern: '/license', titleKey: 'adm.license.title', load: () => import('./pages/license.js'), cap: 'license.read' },

  // Catch-all.
  { name: 'not-found', pattern: '*', titleKey: 'errstate.title', chrome: 'auth', load: () => import('./pages/not-found.js') },
]);

/** Compiled matchers, built once. */
const COMPILED = ROUTES.map((route) => {
  if (route.pattern === '*') return { route, regex: null, keys: [] };
  const keys = [];
  const source = route.pattern
    .split('/')
    .filter(Boolean)
    .map((segment) => {
      if (segment.startsWith(':')) {
        keys.push(segment.slice(1));
        return '([^/]+)';
      }
      return segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    })
    .join('/');
  return { route, regex: new RegExp(`^/${source}/?$`), keys };
});

/** The route currently on screen. */
let activeRoute = null;

/** The mounted page's teardown function, if it returned one. */
let activeTeardown = null;

/** The element pages are mounted into. */
let mountPoint = null;

/** Async guard: `(route) => null | string` where a string is a redirect path. */
let routeGuard = null;

/** Leave guard: `() => boolean | Promise<boolean>`; false cancels navigation. */
let leaveGuard = null;

/** The last hash we successfully rendered. */
let lastHash = '';

/** Called after each successful mount, with the route. */
let afterMount = null;

// --------------------------------------------------------------------------- //
// Parsing
// --------------------------------------------------------------------------- //

/**
 * Split a hash into path and query.
 *
 * @param {string} hash e.g. `'#/catalogue?q=orge&page=2'`.
 * @returns {{path: string, query: Object<string, string>}}
 */
export function parseHash(hash) {
  let raw = String(hash || '').replace(/^#/, '');
  if (!raw) raw = '/dashboard';
  if (!raw.startsWith('/')) raw = `/${raw}`;
  const question = raw.indexOf('?');
  const path = question >= 0 ? raw.slice(0, question) : raw;
  const search = question >= 0 ? raw.slice(question + 1) : '';

  const query = {};
  if (search) {
    const params = new URLSearchParams(search);
    for (const [key, value] of params.entries()) query[key] = value;
  }
  return { path: path || '/dashboard', query };
}

/**
 * Build a hash string from a path and query object.
 * @param {string} path
 * @param {?Object<string, (string|number|boolean|null|undefined)>} [query]
 * @returns {string} e.g. `'#/catalogue?q=orge'`.
 */
export function buildHash(path, query) {
  const target = String(path || '/dashboard').replace(/^#/, '');
  const params = new URLSearchParams();
  if (query) {
    for (const key of Object.keys(query)) {
      const value = query[key];
      if (value === null || value === undefined || value === '') continue;
      params.set(key, String(value));
    }
  }
  const search = params.toString();
  return `#${target}${search ? `?${search}` : ''}`;
}

/**
 * Match a path against the route table.
 * @param {string} path
 * @returns {{route: Object, params: Object<string, string>}}
 */
export function match(path) {
  for (const entry of COMPILED) {
    if (!entry.regex) continue;
    const found = entry.regex.exec(path);
    if (!found) continue;
    const params = {};
    entry.keys.forEach((key, index) => {
      params[key] = decodeURIComponent(found[index + 1]);
    });
    return { route: entry.route, params };
  }
  const fallback = ROUTES[ROUTES.length - 1];
  return { route: fallback, params: {} };
}

/**
 * The href for a path, ready for an `<a>`.
 * @param {string} path
 * @param {?Object} [query]
 * @returns {string}
 */
export function href(path, query) {
  return buildHash(path, query);
}

// --------------------------------------------------------------------------- //
// Navigation
// --------------------------------------------------------------------------- //

/**
 * Navigate.
 *
 * @param {string} path e.g. `'/catalogue'` or `'#/catalogue'`.
 * @param {{query?: Object, replace?: boolean}} [options]
 */
export function navigate(path, options) {
  const opts = options || {};
  const target = buildHash(path, opts.query);
  if (target === window.location.hash) {
    // Same destination: re-render, so a "retry" click always does something.
    render();
    return;
  }
  if (opts.replace) {
    // Replace the history entry, then paint explicitly. `location.replace`
    // also emits `hashchange`; the listener ignores a hash that is already on
    // screen, so the explicit render and the event cannot cancel each other.
    // (Suppressing the event instead — the previous behaviour — left the user
    // on the old screen after signing in, because nothing else rendered.)
    window.location.replace(target);
    render();
    return;
  }
  window.location.hash = target;
}

/**
 * Re-render the current route without touching the hash.
 * @returns {Promise<void>}
 */
export function reload() {
  return render();
}

/** @returns {?Object} The active route descriptor. */
export function current() {
  return activeRoute;
}

/**
 * Install the guards.
 * @param {{guard?: Function, leave?: Function, after?: Function}} hooks
 */
export function setGuards(hooks) {
  const next = hooks || {};
  if ('guard' in next) routeGuard = next.guard;
  if ('leave' in next) leaveGuard = next.leave;
  if ('after' in next) afterMount = next.after;
}

// --------------------------------------------------------------------------- //
// Rendering
// --------------------------------------------------------------------------- //

/**
 * Replace the mounted page.
 * @param {Node} node
 */
function mount(node) {
  if (!mountPoint) return;
  mountPoint.textContent = '';
  mountPoint.appendChild(node);
}

/** Tear the current page down, if it asked for it. */
function teardown() {
  if (typeof activeTeardown === 'function') {
    try {
      activeTeardown();
    } catch (err) {
      console.error('[router] page teardown failed', err);
    }
  }
  activeTeardown = null;
}

/**
 * Render the route named by `location.hash`.
 * @returns {Promise<void>}
 */
async function render() {
  const hash = window.location.hash || '#/dashboard';
  const { path, query } = parseHash(hash);
  const { route, params } = match(path);

  // A guard may bounce us somewhere else before anything is painted.
  if (routeGuard) {
    let redirect = null;
    try {
      redirect = await routeGuard({ route, params, query, path });
    } catch (err) {
      console.error('[router] guard failed', err);
    }
    if (redirect) {
      const target = buildHash(redirect);
      if (target !== hash) {
        // Rewrite the hash and render the destination here. The `hashchange`
        // that follows is ignored by the listener below, because by then
        // `lastHash` already equals the destination.
        window.location.replace(target);
        await render();
        return;
      }
    }
  }

  activeRoute = { name: route.name, path, params, query, route };
  lastHash = hash;
  store.setRoute(activeRoute);

  const title = route.titleKey ? i18n.t(route.titleKey) : i18n.t('app.name');
  document.title = `${title} · ${i18n.t('app.name')}`;

  teardown();

  let node;
  try {
    const module = await route.load();
    const renderPage = module.render || (module.default && module.default.render);
    if (typeof renderPage !== 'function') {
      throw new Error(`page module for "${route.name}" has no render()`);
    }
    const result = await renderPage({
      params,
      query,
      route: activeRoute,
      navigate,
      href,
      reload,
    });

    if (result && typeof result === 'object' && result.element) {
      node = result.element;
      activeTeardown = result.destroy || null;
    } else {
      node = result;
    }
    if (!node) node = document.createDocumentFragment();
  } catch (err) {
    console.error('[router] failed to render', route.name, err);
    node = errorPage(err);
  }

  // Wrap in the standard page shell so every screen gets the same width,
  // padding and 120 ms fade.
  const page = document.createElement('div');
  page.className = 'page page--fade';
  page.appendChild(node);
  mount(page);

  const content = document.querySelector('.content');
  if (content) content.scrollTop = 0;

  if (afterMount) {
    try {
      afterMount(activeRoute);
    } catch (err) {
      console.error('[router] after-mount hook failed', err);
    }
  }
}

/**
 * The last-resort error page. Uses plain DOM so it cannot itself throw.
 * @param {*} error
 * @returns {HTMLElement}
 */
function errorPage(error) {
  const wrap = document.createElement('div');
  wrap.className = 'errstate';
  wrap.setAttribute('role', 'alert');

  const head = document.createElement('div');
  head.className = 'errstate__head';
  head.textContent = i18n.t('shell.error');
  wrap.appendChild(head);

  const body = document.createElement('p');
  body.className = 'errstate__body';
  body.textContent = (error && error.message) || i18n.t('errstate.body');
  wrap.appendChild(body);

  const reloadButton = document.createElement('button');
  reloadButton.className = 'btn btn--secondary btn--sm';
  reloadButton.type = 'button';
  reloadButton.textContent = i18n.t('shell.reload');
  reloadButton.addEventListener('click', () => window.location.reload());

  const actions = document.createElement('div');
  actions.className = 'errstate__actions';
  actions.appendChild(reloadButton);
  wrap.appendChild(actions);

  return wrap;
}

// --------------------------------------------------------------------------- //
// Start
// --------------------------------------------------------------------------- //

/**
 * Start routing.
 *
 * @param {Object} options
 * @param {HTMLElement} options.mount Container the page is rendered into.
 * @returns {Promise<void>}
 */
export async function start(options) {
  const opts = options || {};
  mountPoint = opts.mount || document.getElementById('view');
  lastHash = window.location.hash;

  window.addEventListener('hashchange', async () => {
    if (window.location.hash === lastHash) {
      // Already painted — this is the echo of a `location.replace()` we
      // performed ourselves.
      return;
    }
    if (leaveGuard) {
      let allowed = true;
      try {
        allowed = await leaveGuard();
      } catch (err) {
        allowed = true;
      }
      if (!allowed) {
        // Put the hash back; the listener ignores it because it matches the
        // route that is already painted.
        window.location.replace(lastHash || '#/dashboard');
        return;
      }
    }
    await render();
  });

  await render();
}

/** Default export. */
export default {
  ROUTES,
  start,
  navigate,
  reload,
  current,
  href,
  match,
  parseHash,
  buildHash,
  setGuards,
};
