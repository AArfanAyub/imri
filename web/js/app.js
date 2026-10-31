/**
 * IMRI — boot.
 *
 * The whole start-up sequence, in order:
 *
 *   1. read persisted preferences (theme, sidebar width)
 *   2. ask the server what it is: licensed? who is signed in? what settings?
 *   3. decide which screen the user is allowed to see
 *   4. mount the shell, then start the router
 *
 * Nothing renders before step 3, so an unlicensed install never flashes a
 * dashboard it is about to replace with the activation screen.
 *
 * If the server cannot be reached at all, the boot card is replaced by a plain
 * explanation and a retry button — no half-built shell.
 */

import * as i18n from './i18n.js';
import * as store from './store.js';
import * as ui from './ui.js';
import * as router from './router.js';
import * as shell from './shell.js';
import * as api from './api.js';
import * as brand from './brand.js';

const { t } = i18n;

/** Optional probe a page can install: returns true when the form is dirty. */
let dirtyProbe = null;

/** How often the page tells the server it is still alive. */
const PING_MS = 20_000;

/**
 * Tell the server this page still exists, from the moment it loads.
 *
 * The server shuts itself down when it stops hearing from the page, because a
 * crashed renderer cannot say goodbye. That is only safe if the page has been
 * talking since the beginning — and this used to start only after sign-in,
 * which meant the activation and login screens were silent. A librarian who
 * opened IMRI and spent two minutes reading the activation screen came back to
 * "Cannot reach the IMRI service": the server had killed itself underneath the
 * form they were about to type into.
 *
 * So the ping starts at boot, keeps running when the window is hidden or
 * minimised (a background window is still a live window), and fires again the
 * moment the window comes back — which also covers a laptop waking from sleep.
 */
function startLiveness() {
  const ping = async () => {
    // `heartbeat()` never throws: a failure is the answer we are asking for.
    const ok = await api.heartbeat();
    store.setConnection(ok ? 'online' : 'offline');
  };

  ping();
  window.setInterval(ping, PING_MS);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) ping();
  });
}

/**
 * Register the "this screen has unsaved changes" probe.
 *
 * Pages call this while they are mounted and clear it on teardown. Returning
 * `true` makes the next navigation ask before it discards the form.
 *
 * @param {?(() => (boolean|Promise<boolean>))} probe
 */
export function setLeaveGuard(probe) {
  dirtyProbe = typeof probe === 'function' ? probe : null;
}

/**
 * Decide where a route may go.
 *
 * Order matters and mirrors the server: licence, then identity, then the forced
 * password change, then capability. A user who cannot pass an earlier gate is
 * never shown a later screen, even for one frame.
 *
 * @param {{route: Object, params: Object, query: Object, path: string}} context
 * @returns {?string} A redirect path, or `null` to allow.
 */
function guard(context) {
  const route = context.route;
  const licensed = store.state.licensed;
  const authed = store.isAuthed();

  if (!licensed) return route.name === 'activate' ? null : '/activate';

  // Activated installs have no use for the activation screen.
  if (route.name === 'activate') return authed ? '/dashboard' : '/login';

  if (!authed) return route.chrome === 'auth' ? null : '/login';

  if (store.mustChangePassword()) {
    return route.name === 'change-password' ? null : '/change-password';
  }
  if (route.name === 'change-password' || route.chrome === 'auth') return '/dashboard';

  if (route.cap && !store.can(route.cap)) return '/dashboard';
  return null;
}

/**
 * Ask before throwing away a dirty form.
 * @returns {Promise<boolean>} True when navigation may proceed.
 */
async function leave() {
  if (!dirtyProbe) return true;
  let dirty = false;
  try {
    dirty = await dirtyProbe();
  } catch (err) {
    dirty = false;
  }
  if (!dirty) return true;
  return ui.confirmDialog({
    title: t('confirm.leaveTitle'),
    body: t('confirm.leaveBody'),
    confirmLabel: t('confirm.leave'),
    cancelLabel: t('confirm.keepEditing'),
    tone: 'danger',
  });
}

/**
 * Point the tab icon at this module's drawing.
 *
 * `index.html` carries a hand-written copy so the tab is branded during the
 * milliseconds before this module parses. Assigning here makes the module the
 * single source of truth from then on, so the two can never drift apart
 * without someone noticing a flicker.
 */
function paintFavicon() {
  const link = document.querySelector('link[rel="icon"]');
  if (link) link.setAttribute('href', brand.faviconUri());
}

/** Swap the static splash mark for the real one, so the copy can't drift. */
function paintSplashMark() {
  const host = document.querySelector('.boot__mark');
  if (!host) return;
  host.textContent = '';
  host.appendChild(brand.mark(44, { label: null }));
}

/**
 * Paint a boot failure in place of the splash.
 * @param {*} error
 */
function showBootFailure(error) {
  const root = document.getElementById('app');
  if (!root) return;
  root.textContent = '';
  root.classList.remove('boot');
  root.removeAttribute('aria-busy');

  const wrap = ui.el('div', { class: 'boot' });
  const card = ui.el('div', { class: 'boot__card' });

  card.appendChild(brand.mark(44, { class: 'boot__mark', label: null }));
  card.appendChild(ui.el('span', { class: 'boot__brand', text: 'IMRI' }));
  card.appendChild(ui.el('p', { class: 'boot__tagline', text: t('errstate.offline') }));

  const actions = ui.el('div', { class: 'u-row', style: { marginTop: '16px' } });
  actions.appendChild(ui.button({
    label: t('common.retry'),
    variant: 'primary',
    icon: 'refresh',
    onClick: () => window.location.reload(),
  }));
  card.appendChild(actions);

  wrap.appendChild(card);
  root.appendChild(wrap);

  if (error) console.error('[boot] failed', error);
}

/**
 * Install the global API failure handlers.
 *
 * A dead session sends the user to the sign-in screen with one explanation; a
 * licence failure sends them to activation. Both are marked handled so the
 * caller does not also raise a toast.
 */
function installApiHandlers() {
  api.setHandlers({
    onUnauthorized: () => {
      if (!store.isAuthed()) return false;
      store.clearSession();
      ui.toast.warning(t('toast.sessionExpired'));
      router.navigate('/login', { replace: true });
      return true;
    },
    onLicenseRequired: () => {
      router.navigate('/activate', { replace: true });
      return true;
    },
  });
}

/**
 * Log catalogue problems once. Silence in production is the goal; a missing key
 * is a build-time mistake, not something to interrupt a librarian over.
 */
function reportCatalogueHealth() {
  const problems = i18n.checkCompleteness();
  if (!problems.length) return;
  console.warn('[i18n] catalogue gaps', problems);
}

/** Start the application. */
async function boot() {
  store.initPreferences();
  i18n.initLang();
  paintFavicon();
  paintSplashMark();
  installApiHandlers();
  // Before the first request, not after sign-in: see `startLiveness`.
  startLiveness();

  let payload;
  try {
    payload = await api.bootstrap();
  } catch (error) {
    showBootFailure(error);
    return;
  }

  store.applyBootstrap(payload);
  shell.mount(document.getElementById('app'));

  router.setGuards({ guard, leave });
  await router.start({ mount: document.getElementById('view') });

  // The shell was built before the first route painted, so refresh the pieces
  // that depend on the session (navigation capabilities, bell, avatar).
  shell.refresh();
  reportCatalogueHealth();

  // Handle for QA scripts and the browser console. Read-only in spirit.
  window.__IMRI__ = {
    i18n,
    store,
    ui,
    router,
    shell,
    api,
    checkCompleteness: i18n.checkCompleteness,
    setLeaveGuard,
  };
  window.__IMRI_BOOT__ = { ready: true, licensed: store.state.licensed };
}

boot();

export { boot, guard, leave };
