/**
 * IMRI — client state.
 *
 * One small observable store, no framework. Three things live here:
 *
 *   1. Session facts from `/api/bootstrap` — licence, user, capabilities,
 *      settings. Written once at boot and after sign-in.
 *   2. Preferences the user owns — theme, language, sidebar width. Persisted to
 *      `localStorage` so they survive a reload and, in the case of the theme,
 *      are already applied by `theme-boot.js` before the first paint.
 *   3. Per-route list state — filters, sort order and page number, keyed by
 *      route name. A librarian who filters the catalogue to "lost" copies,
 *      opens a book, then presses Back, must land on the same filtered page
 *      (UX-11). Keeping it here rather than in the page module means the state
 *      outlives the module instance.
 *
 * Nothing in this file talks to the network. `app.js` fills it in.
 */

import * as i18n from './i18n.js';

/** localStorage keys. `imri.theme` is shared with `theme-boot.js`. */
const STORAGE = Object.freeze({
  theme: 'imri.theme',
  sidebar: 'imri.sidebar',
});

/** Theme choices, in cycle order. */
export const THEMES = Object.freeze(['light', 'dark', 'system']);

/**
 * The client settings the server sends, with safe fallbacks.
 *
 * These must mirror ``DEFAULT_SETTINGS`` in ``imri/config.py`` exactly. They
 * are only visible for the instant before ``/api/bootstrap`` answers, but a
 * mismatch shows the librarian a currency or a fine rate the server does not
 * agree with — and a wrong fine rate at the counter is a real argument.
 */
const SETTING_DEFAULTS = Object.freeze({
  school_name: '',
  currency: 'RM',
  currency_space: '1',
  default_locale: 'en',
  theme: 'system',
  page_size_default: '50',
  fine_per_day: '0.20',
  loan_days: '14',
  max_loans: '3',
  max_renewals: '1',
  hold_days: '3',
});

/** A blank session, so callers never have to null-check. */
function blankSession() {
  return { user: null, role: '', caps: [], mustChangePassword: false };
}

/** A blank licence record. */
function blankLicense() {
  return { licensed: false, edition: '', activatedAt: '', schoolName: '' };
}

/**
 * The single mutable snapshot. Treat as read-only from outside this module;
 * mutate through the exported functions so subscribers are notified.
 */
export const state = {
  /** True once `applyBootstrap()` has run. */
  ready: false,
  /** Licence gate state. */
  licensed: false,
  license: blankLicense(),
  /** Signed-in user, role and capability list. */
  session: blankSession(),
  /** Client-side settings from the server. */
  settings: Object.assign({}, SETTING_DEFAULTS),
  /** Application version and database schema, shown in About. */
  version: '',
  schema: 0,
  /** Preferences. */
  theme: 'system',
  /** UI flags that are not persisted. */
  ui: {
    collapsed: false,
    paletteOpen: false,
  },
  /** Current route, maintained by `router.js`. */
  route: { name: '', path: '', params: {}, query: {} },
  /** `'online' | 'offline'` — driven by the heartbeat. */
  connection: 'online',
};

/** Capability lookup set, rebuilt whenever the session changes. */
let capSet = new Set();

/** Subscribers. Each receives `(state, reason)`. */
const listeners = new Set();

/** Per-route list state. Keyed by route name, never garbage collected on
 * navigation — that is the whole point — but dropped on sign-out. */
const listStates = new Map();

/** Cached `matchMedia` handle for the OS colour scheme. */
let darkQuery = null;

// --------------------------------------------------------------------------- //
// Persistence helpers
// --------------------------------------------------------------------------- //

/**
 * Read a string from `localStorage`, tolerating private mode and quota errors.
 * @param {string} key
 * @returns {?string}
 */
function readStored(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (err) {
    return null;
  }
}

/**
 * Write a string to `localStorage`, ignoring failures.
 * @param {string} key
 * @param {string} value
 */
function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (err) {
    /* not worth interrupting the user for */
  }
}

// --------------------------------------------------------------------------- //
// Subscription
// --------------------------------------------------------------------------- //

/**
 * Subscribe to state changes.
 * @param {(state: Object, reason: string) => void} fn
 * @returns {() => void} Unsubscribe.
 */
export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/**
 * Notify every subscriber. A throwing subscriber must not stop the others —
 * one broken panel should not freeze the shell.
 * @param {string} reason Short tag describing what changed.
 */
function notify(reason) {
  for (const fn of listeners) {
    try {
      fn(state, reason);
    } catch (err) {
      console.error('[store] subscriber failed', reason, err);
    }
  }
}

// --------------------------------------------------------------------------- //
// Reads
// --------------------------------------------------------------------------- //

/**
 * Read a dotted path out of the state snapshot.
 * @param {string} path e.g. `'session.role'`.
 * @param {*} [fallback]
 * @returns {*}
 */
export function get(path, fallback) {
  const parts = String(path || '').split('.');
  let node = state;
  for (const part of parts) {
    if (node === null || node === undefined) return fallback;
    node = node[part];
  }
  return node === undefined ? fallback : node;
}

/** @returns {?Object} The signed-in user, or null. */
export function user() {
  return state.session.user;
}

/** @returns {string} `'admin' | 'librarian' | 'assistant' | 'viewer' | ''`. */
export function role() {
  return state.session.role || '';
}

/** @returns {boolean} */
export function isAuthed() {
  return Boolean(state.session.user);
}

/** @returns {boolean} True when the user must choose a new password first. */
export function mustChangePassword() {
  return Boolean(state.session.mustChangePassword);
}

/**
 * Whether the signed-in user holds a capability.
 *
 * Capabilities arrive from the server (`bootstrap.session.caps`) and are the
 * same list the API enforces, so hiding a button here always matches a 403
 * there — no guessing.
 *
 * @param {string} cap e.g. `'book.create'`.
 * @returns {boolean}
 */
export function can(cap) {
  return capSet.has(cap);
}

/**
 * Whether the user holds at least one of the given capabilities.
 * @param {...string} caps
 * @returns {boolean}
 */
export function canAny(...caps) {
  return caps.some((cap) => capSet.has(cap));
}

/**
 * Whether the user holds every given capability.
 * @param {...string} caps
 * @returns {boolean}
 */
export function canAll(...caps) {
  return caps.every((cap) => capSet.has(cap));
}

/**
 * The school name, from settings, then the licence, then empty.
 * @returns {string}
 */
export function schoolName() {
  return String(state.settings.school_name || state.license.schoolName || '').trim();
}

/**
 * A numeric setting, with a fallback when the value is missing or junk.
 * @param {string} key
 * @param {number} fallback
 * @returns {number}
 */
function numericSetting(key, fallback) {
  const value = Number(state.settings[key]);
  return Number.isFinite(value) ? value : fallback;
}

/** @returns {number} Default page size for tables. */
export function pageSize() {
  const size = numericSetting('page_size_default', 50);
  return Math.min(200, Math.max(5, Math.round(size)));
}

/** @returns {number} Loan period in days. */
export function loanDays() {
  return numericSetting('loan_days', 14);
}

/** @returns {number} Maximum concurrent loans per member. */
export function maxLoans() {
  return numericSetting('max_loans', 3);
}

/** @returns {number} Maximum renewals per loan. */
export function maxRenewals() {
  return numericSetting('max_renewals', 1);
}

/** @returns {number} Days a reservation is held once ready. */
export function holdDays() {
  return numericSetting('hold_days', 3);
}

/** @returns {number} Fine per day, in the configured currency. */
export function finePerDay() {
  // Must match config.DEFAULTS['fine_per_day'] (0.20) or the counter
  // quotes half the fine the server actually charges.
  return numericSetting('fine_per_day', 0.2);
}

// --------------------------------------------------------------------------- //
// Writes
// --------------------------------------------------------------------------- //

/**
 * Set one dotted path, then notify.
 * @param {string} path
 * @param {*} value
 * @param {string} [reason]
 */
export function set(path, value, reason) {
  const parts = String(path || '').split('.');
  if (!parts.length || !parts[0]) return;
  let node = state;
  for (let i = 0; i < parts.length - 1; i += 1) {
    if (typeof node[parts[i]] !== 'object' || node[parts[i]] === null) {
      node[parts[i]] = {};
    }
    node = node[parts[i]];
  }
  node[parts[parts.length - 1]] = value;
  notify(reason || 'set');
}

/**
 * Shallow-merge several top-level slices at once and notify once.
 * @param {Object} patch
 * @param {string} [reason]
 */
export function patch(patchObject, reason) {
  if (!patchObject) return;
  Object.assign(state, patchObject);
  notify(reason || 'patch');
}

// --------------------------------------------------------------------------- //
// Session + licence
// --------------------------------------------------------------------------- //

/**
 * Apply a `/api/bootstrap` payload.
 *
 * This is the only place the shell learns whether it is licensed, who is
 * signed in, and what the school's settings are.
 *
 * @param {Object} payload The unwrapped bootstrap envelope.
 */
export function applyBootstrap(payload) {
  const data = payload || {};
  const licence = data.license || {};
  const session = data.session || null;

  state.licensed = Boolean(data.licensed);
  state.license = {
    licensed: Boolean(data.licensed),
    edition: String(licence.edition || ''),
    activatedAt: String(licence.activated_at || ''),
    schoolName: String(licence.school_name || ''),
  };
  state.settings = Object.assign({}, SETTING_DEFAULTS, data.settings || {});
  state.version = String(data.version || '');
  state.schema = Number(data.schema) || 0;

  if (session && session.user) {
    applySession({
      user: session.user,
      role: session.role || session.user.role || '',
      caps: Array.isArray(session.caps) ? session.caps : [],
      mustChangePassword: Boolean(session.must_change_password),
    });
  } else {
    applySession(null);
  }

  // Locale and currency come from the server, but an explicit user choice wins.
  i18n.initLang(state.settings.default_locale);
  i18n.setCurrency(state.settings.currency);

  state.ready = true;
  notify('bootstrap');
}

/**
 * Replace the session slice.
 * @param {?{user: Object, role: string, caps: string[], mustChangePassword: boolean}} next
 */
export function applySession(next) {
  if (!next || !next.user) {
    state.session = blankSession();
    capSet = new Set();
    notify('session');
    return;
  }
  state.session = {
    user: next.user,
    role: String(next.role || next.user.role || ''),
    caps: Array.isArray(next.caps) ? next.caps.slice() : [],
    mustChangePassword: Boolean(next.mustChangePassword),
  };
  capSet = new Set(state.session.caps);
  notify('session');
}

/** Drop the session. Preferences and settings survive — only identity goes. */
export function clearSession() {
  state.session = blankSession();
  capSet = new Set();
  listStates.clear();
  notify('session');
}

/**
 * Record whether the backend is reachable. Drives the offline banner and
 * disables the heartbeat's error toast so a suspended laptop stays quiet.
 * @param {'online'|'offline'} status
 */
export function setConnection(status) {
  const next = status === 'offline' ? 'offline' : 'online';
  if (state.connection === next) return;
  state.connection = next;
  notify('connection');
}

// --------------------------------------------------------------------------- //
// Theme
// --------------------------------------------------------------------------- //

/** @returns {MediaQueryList} The OS colour-scheme query, created once. */
function systemDarkQuery() {
  if (!darkQuery) {
    darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
    darkQuery.addEventListener('change', () => {
      // Only follow the OS while the user has not pinned a theme.
      if (state.theme === 'system') applyTheme('system', { persist: false });
    });
  }
  return darkQuery;
}

/**
 * Resolve a theme choice against the OS preference.
 * @param {string} choice `'light' | 'dark' | 'system'`.
 * @returns {'light'|'dark'}
 */
export function resolveTheme(choice) {
  if (choice === 'light' || choice === 'dark') return choice;
  try {
    return systemDarkQuery().matches ? 'dark' : 'light';
  } catch (err) {
    return 'light';
  }
}

/**
 * Apply a theme choice to `<html>`.
 *
 * Writes both `data-theme` (the resolved value the CSS keys off) and
 * `data-theme-choice` (what the user picked, so the menu can tick the right
 * item) — the same contract `theme-boot.js` establishes before first paint.
 *
 * @param {string} choice
 * @param {{persist?: boolean, notify?: boolean}} [options]
 */
export function applyTheme(choice, options) {
  const opts = options || {};
  const picked = THEMES.includes(choice) ? choice : 'system';
  const resolved = resolveTheme(picked);
  const root = document.documentElement;

  root.setAttribute('data-theme', resolved);
  root.setAttribute('data-theme-choice', picked);
  root.style.colorScheme = resolved;
  state.theme = picked;

  if (opts.persist !== false) writeStored(STORAGE.theme, picked);
  if (opts.notify !== false) notify('theme');
}

/**
 * Choose a theme.
 * @param {string} choice
 */
export function setTheme(choice) {
  applyTheme(choice, { persist: true });
}

/** @returns {string} The user's choice, not the resolved value. */
export function getTheme() {
  return state.theme;
}

/**
 * Advance light → dark → system → light.
 * @returns {string} The new choice.
 */
export function cycleTheme() {
  const index = THEMES.indexOf(state.theme);
  const next = THEMES[(index + 1) % THEMES.length];
  setTheme(next);
  return next;
}

// --------------------------------------------------------------------------- //
// Sidebar
// --------------------------------------------------------------------------- //

/**
 * Expand or collapse the sidebar and remember the choice.
 * @param {boolean} collapsed
 */
export function setCollapsed(collapsed) {
  state.ui.collapsed = Boolean(collapsed);
  writeStored(STORAGE.sidebar, state.ui.collapsed ? '1' : '0');
  notify('ui');
}

/** @returns {boolean} */
export function isCollapsed() {
  return Boolean(state.ui.collapsed);
}

/** Flip the sidebar. @returns {boolean} The new collapsed value. */
export function toggleSidebar() {
  setCollapsed(!state.ui.collapsed);
  return state.ui.collapsed;
}

// --------------------------------------------------------------------------- //
// Route + per-route list state
// --------------------------------------------------------------------------- //

/**
 * Record the active route. Called by `router.js` before it paints.
 *
 * `titleKey` is carried through so the topbar can translate the page title
 * without importing the route table. `chrome` tells the shell whether to show
 * its navigation (`'app'`, the default) or step aside (`'auth'`).
 *
 * @param {{name: string, path: string, params: Object, query: Object,
 *          route?: Object}} route
 */
export function setRoute(route) {
  const descriptor = (route && route.route) || {};
  state.route = {
    name: String((route && route.name) || ''),
    path: String((route && route.path) || ''),
    params: (route && route.params) || {},
    query: (route && route.query) || {},
    titleKey: String(descriptor.titleKey || ''),
    chrome: descriptor.chrome === 'auth' ? 'auth' : 'app',
  };
  notify('route');
}

/** @returns {Object} The active route descriptor. */
export function currentRoute() {
  return state.route;
}

/**
 * The remembered list state for a route, created on first use.
 *
 * @param {string} key Usually the route name, e.g. `'catalogue'`.
 * @returns {{q: string, filters: Object, sort: {key: string, dir: string},
 *            page: number, pageSize: number}}
 */
export function listState(key) {
  const name = String(key || 'default');
  let entry = listStates.get(name);
  if (!entry) {
    entry = {
      q: '',
      filters: {},
      sort: { key: '', dir: 'asc' },
      page: 1,
      pageSize: pageSize(),
    };
    listStates.set(name, entry);
  }
  return entry;
}

/**
 * Merge a patch into a route's list state and notify.
 *
 * Changing a filter or the search term resets the page to 1: staying on page 7
 * of a result set that now has two rows is the classic table bug.
 *
 * @param {string} key
 * @param {Object} patchObject
 * @returns {Object} The updated entry.
 */
export function updateListState(key, patchObject) {
  const entry = listState(key);
  const changes = patchObject || {};
  const resetsPage = 'q' in changes || 'filters' in changes;
  Object.assign(entry, changes);
  if (resetsPage && !('page' in changes)) entry.page = 1;
  notify('list');
  return entry;
}

/**
 * Forget a route's list state — used by "Clear filters" and after a delete.
 * @param {string} key
 */
export function resetListState(key) {
  const name = String(key || 'default');
  listStates.delete(name);
  notify('list');
}

/** Forget every route's list state. Called on sign-out. */
export function resetAllListState() {
  listStates.clear();
  notify('list');
}

// --------------------------------------------------------------------------- //
// Startup
// --------------------------------------------------------------------------- //

/**
 * Read persisted preferences and apply them. Runs once, before the shell is
 * built. `theme-boot.js` has already set the attributes; this brings the store
 * into agreement with them so the first toggle behaves correctly.
 */
export function initPreferences() {
  const storedTheme = readStored(STORAGE.theme);
  const choice = THEMES.includes(storedTheme) ? storedTheme : 'system';
  applyTheme(choice, { persist: false, notify: false });

  const collapsed = readStored(STORAGE.sidebar) === '1';
  state.ui.collapsed = collapsed;
  systemDarkQuery();
}

/** Default export, for modules that prefer `store.state`. */
export default {
  state,
  THEMES,
  subscribe,
  get,
  set,
  patch,
  user,
  role,
  isAuthed,
  mustChangePassword,
  can,
  canAny,
  canAll,
  schoolName,
  pageSize,
  loanDays,
  maxLoans,
  maxRenewals,
  holdDays,
  finePerDay,
  applyBootstrap,
  applySession,
  clearSession,
  setConnection,
  resolveTheme,
  applyTheme,
  setTheme,
  getTheme,
  cycleTheme,
  setCollapsed,
  isCollapsed,
  toggleSidebar,
  setRoute,
  currentRoute,
  listState,
  updateListState,
  resetListState,
  resetAllListState,
  initPreferences,
};
