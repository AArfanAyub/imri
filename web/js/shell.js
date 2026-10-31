/**
 * IMRI — application shell.
 *
 * Owns everything that is on screen no matter which page is mounted: the
 * sidebar, the topbar, the command palette, the shortcut sheet, the heartbeat
 * and the global error handlers.
 *
 * The shell is built once. Language changes repaint the chrome through a small
 * binding table rather than a rebuild, so switching language never reloads the
 * page, never re-fetches bootstrap, and never loses the current screen (I18N-03).
 *
 * Public surface
 *   mount(root)        build the shell, return `#view` for the router
 *   refresh()          re-read session/route and update the chrome
 *   repaint()          re-translate every bound label
 *   openPalette() / closePalette()
 *   openShortcuts()
 *   destroy()          stop timers and remove listeners
 */

import * as i18n from './i18n.js';
import * as store from './store.js';
import * as ui from './ui.js';
import * as router from './router.js';
import * as api from './api.js';
import * as brand from './brand.js';

const { t } = i18n;

// --------------------------------------------------------------------------- //
// Navigation registry
// --------------------------------------------------------------------------- //

/**
 * The five sidebar groups, in order.
 *
 * `cap` hides an entry the signed-in user cannot use, so the sidebar never
 * offers a link that would 403.
 */
export const NAV_GROUPS = Object.freeze([
  {
    key: 'nav.group.library',
    items: [
      { path: '/dashboard', name: 'dashboard', labelKey: 'nav.dashboard', icon: 'dashboard', cap: 'dashboard.read' },
      { path: '/catalogue', name: 'catalogue', labelKey: 'nav.catalogue', icon: 'book', cap: 'book.read', also: ['book'] },
      { path: '/members', name: 'members', labelKey: 'nav.members', icon: 'users', cap: 'member.read', also: ['member'] },
      { path: '/reading-lists', name: 'reading-lists', labelKey: 'nav.readingLists', icon: 'list', cap: 'member.read' },
      { path: '/labels', name: 'labels', labelKey: 'nav.labels', icon: 'tag', cap: 'label.read' },
    ],
  },
  {
    key: 'nav.group.circulation',
    items: [
      { path: '/counter', name: 'counter', labelKey: 'nav.counter', icon: 'swap', cap: 'circ.read' },
      { path: '/overdue', name: 'overdue', labelKey: 'nav.overdue', icon: 'clock', cap: 'circ.read' },
      { path: '/reservations', name: 'reservations', labelKey: 'nav.reservations', icon: 'bookmark', cap: 'reservation.read' },
    ],
  },
  {
    key: 'nav.group.insights',
    items: [
      { path: '/reports', name: 'reports', labelKey: 'nav.reports', icon: 'chart', cap: 'report.read' },
    ],
  },
  {
    key: 'nav.group.admin',
    items: [
      { path: '/admin', name: 'admin', labelKey: 'nav.users', icon: 'shield', cap: 'user.read' },
      { path: '/settings', name: 'settings', labelKey: 'nav.settings', icon: 'settings', cap: 'settings.read' },
      { path: '/backup', name: 'backup', labelKey: 'nav.backup', icon: 'database', cap: 'backup.create' },
      { path: '/audit', name: 'audit', labelKey: 'nav.audit', icon: 'archive', cap: 'audit.read' },
      { path: '/license', name: 'license', labelKey: 'nav.license', icon: 'key', cap: 'license.read' },
    ],
  },
  {
    key: 'nav.group.help',
    items: [
      { action: 'shortcuts', labelKey: 'nav.help', icon: 'help' },
      { action: 'about', labelKey: 'shell.about', icon: 'info' },
    ],
  },
]);

/** Flat list of navigable destinations, used by the palette's "Go to". */
function destinations() {
  const out = [];
  for (const group of NAV_GROUPS) {
    for (const item of group.items) {
      if (item.path && (!item.cap || store.can(item.cap))) out.push(item);
    }
  }
  return out;
}

// --------------------------------------------------------------------------- //
// i18n binding table
// --------------------------------------------------------------------------- //

/** Elements whose text or attribute is a catalogue key. */
const bindings = [];

/**
 * Bind an element's text to a key.
 * @param {HTMLElement} node
 * @param {string} key
 * @param {?Object} [vars]
 * @returns {HTMLElement} The same node, for chaining.
 */
function bindText(node, key, vars) {
  node.textContent = t(key, vars);
  bindings.push({ node, kind: 'text', key, vars });
  return node;
}

/**
 * Bind an attribute to a key.
 * @param {HTMLElement} node
 * @param {string} attr
 * @param {string} key
 * @returns {HTMLElement}
 */
function bindAttr(node, attr, key) {
  node.setAttribute(attr, t(key));
  bindings.push({ node, kind: 'attr', attr, key });
  return node;
}

/** Re-translate every live binding. Dead nodes are pruned as we go. */
export function repaint() {
  for (let i = bindings.length - 1; i >= 0; i -= 1) {
    const binding = bindings[i];
    if (!binding.node.isConnected) {
      bindings.splice(i, 1);
      continue;
    }
    if (binding.kind === 'text') binding.node.textContent = t(binding.key, binding.vars);
    else binding.node.setAttribute(binding.attr, t(binding.key));
  }
  updateTitle();
  updateThemeButton();
  updateAvatar();
}

// --------------------------------------------------------------------------- //
// Module state
// --------------------------------------------------------------------------- //

/** Root elements, filled in by `mount()`. */
const refs = {
  shell: null,
  sidebar: null,
  nav: null,
  topbar: null,
  title: null,
  view: null,
  bell: null,
  bellCount: null,
  themeButton: null,
  langButton: null,
  avatarButton: null,
  avatarInitials: null,
  collapseButton: null,
};

/** Overdue count for the bell, from `/api/dashboard`. */
let overdueCount = 0;

/** Pending `g` prefix for the "g then x" navigation shortcut. */
let pendingGo = null;

/** Timer that expires the `g` prefix. */
let pendingGoTimer = null;

/** Keydown listener reference, so `destroy()` can remove it. */
let keyHandler = null;

/** Palette instance while it is open. */
let palette = null;

/** Unsubscribe handles for store/i18n subscriptions. */
const unsubscribers = [];

// --------------------------------------------------------------------------- //
// Sidebar
// --------------------------------------------------------------------------- //

/**
 * Build one sidebar link.
 * @param {Object} item Entry from {@link NAV_GROUPS}.
 * @param {string} activeName Current route name.
 * @returns {HTMLElement}
 */
function navLink(item, activeName) {
  const isActive = item.name === activeName
    || (Array.isArray(item.also) && item.also.includes(activeName));

  const node = ui.el('a', {
    class: 'sb__item',
    href: router.href(item.path),
    attrs: {
      'aria-current': isActive ? 'page' : null,
      'data-nav': item.name,
      title: null,
    },
    on: {
      click: () => {
        closePalette();
      },
    },
  });
  node.appendChild(ui.el('span', { class: 'sb__icon' }, ui.icon(item.icon, 20)));
  node.appendChild(bindText(ui.el('span', { class: 'sb__text' }), item.labelKey));
  return node;
}

/**
 * Build one sidebar action (not a route): shortcuts, about.
 * @param {Object} item
 * @returns {HTMLElement}
 */
function navAction(item) {
  const node = ui.el('button', {
    class: 'sb__item',
    attrs: { type: 'button', 'data-nav': item.action },
    on: {
      click: () => {
        if (item.action === 'shortcuts') openShortcuts();
        else openAbout();
      },
    },
  });
  node.appendChild(ui.el('span', { class: 'sb__icon' }, ui.icon(item.icon, 20)));
  node.appendChild(bindText(ui.el('span', { class: 'sb__text' }), item.labelKey));
  return node;
}

/** Rebuild the navigation list — cheap, and keeps capability filtering exact. */
function renderNav() {
  const nav = refs.nav;
  if (!nav) return;
  const activeName = store.currentRoute().name;
  nav.textContent = '';

  for (const group of NAV_GROUPS) {
    const items = group.items.filter((item) => !item.cap || store.can(item.cap));
    if (!items.length) continue;
    const section = ui.el('div', { class: 'sb__group' });
    section.appendChild(bindText(ui.el('p', { class: 'sb__label' }), group.key));
    for (const item of items) {
      section.appendChild(item.path ? navLink(item, activeName) : navAction(item));
    }
    nav.appendChild(section);
  }
}

/**
 * Show the navigation, or step aside for an auth screen.
 *
 * The activation gate, sign-in and forced password change must not offer a way
 * around themselves, so `chrome: 'auth'` routes hide the sidebar and topbar.
 * The elements stay in the DOM (hidden by CSS) so returning to a normal screen
 * needs no rebuild.
 */
function applyChrome() {
  if (!refs.shell) return;
  const route = store.currentRoute();
  refs.shell.dataset.chrome = route && route.chrome === 'auth' ? 'auth' : 'app';
}

/** Reflect the collapsed flag on the shell and update the toggle's label. */
function applyCollapsed() {
  if (!refs.shell) return;
  const collapsed = store.isCollapsed();
  refs.shell.dataset.collapsed = collapsed ? 'true' : 'false';
  if (refs.collapseButton) {
    refs.collapseButton.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    const label = collapsed ? t('shell.expandNav') : t('shell.collapseNav');
    refs.collapseButton.setAttribute('aria-label', label);
    refs.collapseButton.setAttribute('title', label);
    const text = refs.collapseButton.querySelector('span');
    if (text) text.textContent = collapsed ? t('nav.expand') : t('nav.collapse');
    // The chevron points the way the click will move the edge of the sidebar.
    if (refs.collapseIcon) {
      const next = ui.icon(collapsed ? 'chevronRight' : 'chevronLeft', 16);
      refs.collapseIcon.replaceWith(next);
      refs.collapseIcon = next;
    }
  }
}

// --------------------------------------------------------------------------- //
// Topbar
// --------------------------------------------------------------------------- //

/** Update the page title from the active route. */
function updateTitle() {
  if (!refs.title) return;
  const route = store.currentRoute();
  const key = route && route.titleKey;
  refs.title.textContent = key ? t(key) : t('app.name');
}

/** Swap the theme button's icon and label to match the current choice. */
function updateThemeButton() {
  const button = refs.themeButton;
  if (!button) return;
  const choice = store.getTheme();
  const iconName = choice === 'light' ? 'sun' : choice === 'dark' ? 'moon' : 'monitor';
  const label = `${t('shell.themeToggle')} · ${t(`theme.${choice}`)}`;
  button.textContent = '';
  button.appendChild(ui.icon(iconName, 18));
  button.setAttribute('aria-label', label);
  button.setAttribute('title', label);
}

/** Redraw the avatar initials and its menu label. */
function updateAvatar() {
  const user = store.user();
  const name = (user && (user.display_name || user.username)) || '';
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join('')
    .toUpperCase();

  if (refs.avatarInitials) refs.avatarInitials.textContent = initials || '·';
  if (refs.avatarButton) {
    const label = name ? t('shell.signedInAs', { name }) : t('shell.account');
    refs.avatarButton.setAttribute('aria-label', label);
    refs.avatarButton.setAttribute('title', label);
  }
  if (refs.bell) {
    const label = overdueCount
      ? `${t('shell.bell')} · ${t('shell.overdueCount', { count: overdueCount })}`
      : `${t('shell.bell')} · ${t('shell.noOverdue')}`;
    refs.bell.setAttribute('aria-label', label);
    refs.bell.setAttribute('title', label);
  }
}

/** Draw the overdue count on the bell. */
function renderBellCount() {
  if (!refs.bell) return;
  const dot = refs.bell.querySelector('.icon-btn__dot');
  if (dot) dot.remove();
  if (!overdueCount) return;
  const dotNode = ui.el('span', {
    class: 'icon-btn__dot u-num',
    text: overdueCount > 99 ? '99+' : String(overdueCount),
    attrs: { 'aria-hidden': 'true' },
  });
  refs.bell.appendChild(dotNode);
}

/**
 * Fetch the dashboard summary for the bell count.
 *
 * Best effort: a failure is silent because the bell is decoration, not data the
 * page depends on. Called after sign-in and on every navigation back to the
 * dashboard.
 */
async function refreshBell() {
  if (!store.isAuthed() || !store.can('dashboard.read')) return;
  try {
    const data = await api.get('/api/dashboard');
    const kpis = (data && data.kpis) || {};
    overdueCount = Number((kpis.overdue && kpis.overdue.value) || 0);
  } catch (err) {
    overdueCount = 0;
  }
  renderBellCount();
  updateAvatar();
}

/** Open the account menu. */
function openAccountMenu() {
  const user = store.user() || {};
  const items = [];
  if (user.display_name || user.username) {
    items.push({ group: user.display_name || user.username });
  }
  items.push({
    label: t('shell.mustChange'),
    icon: 'key',
    onClick: () => router.navigate('/change-password'),
  });
  if (store.can('settings.read')) {
    items.push({ label: t('nav.settings'), icon: 'settings', onClick: () => router.navigate('/settings') });
  }
  if (store.can('license.read')) {
    items.push({ label: t('nav.license'), icon: 'key', onClick: () => router.navigate('/license') });
  }
  items.push({ label: t('shell.about'), icon: 'info', onClick: openAbout });
  items.push('-');
  items.push({
    label: t('auth.signOut'),
    icon: 'logout',
    danger: true,
    onClick: () => signOut(),
  });

  ui.menu({ anchor: refs.avatarButton, items, align: 'right' });
}

/** Ask, then sign out. */
async function signOut() {
  const confirmed = await ui.confirmDialog({
    title: t('auth.signOutConfirm'),
    body: t('auth.signOutBody'),
    confirmLabel: t('auth.signOut'),
    tone: 'danger',
  });
  if (!confirmed) return;
  try {
    await api.logout();
  } catch (err) {
    /* the cookie is cleared server-side either way; proceed to the login screen */
  }
  store.clearSession();
  ui.toast.info(t('toast.signedOut'));
  router.navigate('/login', { replace: true });
}

/** Open the language menu. */
function openLangMenu() {
  const items = i18n.LANGS.map((lang) => ({
    label: lang.label,
    checked: lang.code === i18n.getLang(),
    onClick: () => {
      i18n.setLang(lang.code);
      updateLangButton();
      repaint();
    },
  }));
  ui.menu({ anchor: refs.langButton, items, align: 'right' });
}

/** Show the active language code on the chip. */
function updateLangButton() {
  if (!refs.langButton) return;
  const code = i18n.getLang();
  const short = code === 'zh' ? '中文' : code.toUpperCase();
  refs.langButton.textContent = '';
  refs.langButton.appendChild(ui.icon('globe', 16));
  refs.langButton.appendChild(ui.el('span', { text: short }));
  const label = `${t('shell.langSwitch')} · ${i18n.langLabel(code)}`;
  refs.langButton.setAttribute('aria-label', label);
  refs.langButton.setAttribute('title', label);
}

/** Open the About dialog. */
function openAbout() {
  const rows = [
    { label: t('adm.license.product'), value: 'IMRI' },
    { label: t('adm.license.version'), value: store.state.version || t('common.na') },
    { label: t('adm.license.schema'), value: String(store.state.schema || '—') },
    { label: t('adm.license.school'), value: store.schoolName() || t('common.na') },
    {
      label: t('adm.license.status'),
      value: store.state.licensed ? t('adm.license.active') : t('adm.license.inactive'),
    },
  ];
  const dialog = ui.modal({
    title: t('shell.about'),
    body: [
      ui.kv(rows),
      ui.el('p', { class: 'u-xs u-muted', text: t('adm.license.note') }),
    ],
    footer: [ui.button({ label: t('common.close'), variant: 'secondary', onClick: () => dialog.close() })],
  });
}

// --------------------------------------------------------------------------- //
// Shortcut sheet
// --------------------------------------------------------------------------- //

/**
 * Open the `?` shortcut sheet.
 *
 * Built from the same registry the key handler uses, so the sheet cannot drift
 * out of date from the behaviour.
 */
export function openShortcuts() {
  const groups = [
    { key: 'keys.group.global', rows: SHORTCUT_HELP.filter((row) => row.group === 'global') },
    { key: 'keys.group.forms', rows: SHORTCUT_HELP.filter((row) => row.group === 'forms') },
  ];

  const grid = ui.el('div', { class: 'sheet__grid' });
  for (const group of groups) {
    const column = ui.el('div', { class: 'sheet__group' });
    column.appendChild(ui.el('p', { class: 'sb__label', text: t(group.key) }));
    for (const row of group.rows) {
      column.appendChild(ui.el('div', { class: 'sheet__row' },
        ui.el('span', { text: t(row.labelKey) }),
        ui.kbd(row.keys)));
    }
    grid.appendChild(column);
  }

  const dialog = ui.modal({
    title: t('keys.title'),
    body: grid,
    wide: true,
    footer: [ui.button({ label: t('common.close'), variant: 'secondary', onClick: () => dialog.close() })],
  });
}

/** Rows shown in the shortcut sheet. */
const SHORTCUT_HELP = Object.freeze([
  { keys: ['Ctrl', 'K'], labelKey: 'keys.palette', group: 'global' },
  { keys: ['/'], labelKey: 'keys.search', group: 'global' },
  { keys: ['G', 'then', 'D'], labelKey: 'keys.nav', group: 'global' },
  { keys: ['?'], labelKey: 'keys.help', group: 'global' },
  { keys: ['Esc'], labelKey: 'keys.close', group: 'global' },
  { keys: ['Ctrl', 'S'], labelKey: 'keys.save', group: 'forms' },
  { keys: ['Enter'], labelKey: 'keys.submit', group: 'forms' },
  { keys: ['Tab'], labelKey: 'keys.advance', group: 'forms' },
]);

// --------------------------------------------------------------------------- //
// Command palette
// --------------------------------------------------------------------------- //

/**
 * Score a fuzzy match.
 *
 * Every query character must appear in order. Consecutive matches and matches
 * at word boundaries score higher, which is what makes "abk" find
 * "Add book" before "Backup".
 *
 * @param {string} query
 * @param {string} text
 * @returns {?{score: number, marks: number[]}}
 */
export function fuzzyMatch(query, text) {
  const needle = String(query || '').toLowerCase();
  const haystack = String(text || '').toLowerCase();
  if (!needle) return { score: 0, marks: [] };
  if (needle.length > haystack.length) return null;

  const marks = [];
  let score = 0;
  let position = 0;
  let previousIndex = -2;

  for (const char of needle) {
    const found = haystack.indexOf(char, position);
    if (found === -1) return null;
    if (found === previousIndex + 1) score += 6;
    if (found === 0 || /[\s\-_/]/.test(haystack[found - 1])) score += 4;
    score += 1;
    marks.push(found);
    previousIndex = found;
    position = found + 1;
  }
  // Prefer shorter haystacks: "Book" beats "Book details and copies".
  score -= Math.min(8, Math.floor(haystack.length / 12));
  return { score, marks };
}

/**
 * Highlight the matched characters of a label.
 * @param {string} label
 * @param {number[]} marks
 * @returns {HTMLElement}
 */
function highlighted(label, marks) {
  const holder = ui.el('span');
  const set = new Set(marks || []);
  let buffer = '';
  let markMode = null;
  const flush = () => {
    if (!buffer) return;
    if (markMode) holder.appendChild(ui.el('mark', { class: 'palette__mark', text: buffer }));
    else holder.appendChild(ui.el('span', { text: buffer }));
    buffer = '';
  };
  for (let i = 0; i < label.length; i += 1) {
    const isMarked = set.has(i);
    if (markMode !== null && isMarked !== markMode) flush();
    markMode = isMarked;
    buffer += label[i];
  }
  flush();
  return holder;
}

/** The palette's static action list. */
function paletteActions() {
  const actions = [];
  if (store.can('book.write')) {
    actions.push({ id: 'new-book', label: t('cat.new'), icon: 'plus', run: () => router.navigate('/catalogue', { query: { new: '1' } }) });
  }
  if (store.can('member.write')) {
    actions.push({ id: 'new-member', label: t('mem.new'), icon: 'plus', run: () => router.navigate('/members', { query: { new: '1' } }) });
  }
  if (store.can('circ.write')) {
    actions.push({ id: 'issue', label: t('circ.issue'), icon: 'swap', run: () => router.navigate('/counter', { query: { mode: 'issue' } }) });
    actions.push({ id: 'return', label: t('circ.return'), icon: 'swap', run: () => router.navigate('/counter', { query: { mode: 'return' } }) });
  }
  if (store.can('reservation.write')) {
    actions.push({ id: 'reserve', label: t('nav.reservations'), icon: 'bookmark', run: () => router.navigate('/reservations', { query: { new: '1' } }) });
  }
  if (store.can('report.read')) {
    actions.push({ id: 'report', label: t('rep.title'), icon: 'chart', run: () => router.navigate('/reports') });
  }
  if (store.can('label.read')) {
    actions.push({ id: 'labels', label: t('label.title'), icon: 'tag', run: () => router.navigate('/labels') });
  }
  if (store.can('backup.create')) {
    actions.push({ id: 'backup', label: t('adm.backup.create'), icon: 'database', run: () => router.navigate('/backup') });
  }
  actions.push({
    id: 'theme',
    label: `${t('theme.label')} · ${t(`theme.${store.getTheme()}`)}`,
    icon: 'moon',
    run: () => {
      store.cycleTheme();
      updateThemeButton();
      ui.toast.info(`${t('theme.label')} · ${t(`theme.${store.getTheme()}`)}`);
    },
  });
  return actions;
}

/**
 * Open the command palette.
 *
 * Three sources, in order: static actions, navigation destinations, and live
 * results from `/api/search` (debounced 160 ms). Nothing is fetched until the
 * user types at least two characters.
 */
export function openPalette() {
  if (palette) return;

  const opener = document.activeElement;
  const scrim = ui.el('div', { class: 'scrim', attrs: { 'data-layer': 'palette' } });
  const panel = ui.el('div', {
    class: 'palette',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': t('shell.palette') },
  });

  const input = ui.el('input', {
    class: 'palette__input',
    attrs: {
      type: 'text',
      placeholder: t('palette.placeholder'),
      'aria-label': t('palette.placeholder'),
      'aria-controls': 'palette-list',
      autocomplete: 'off',
      spellcheck: 'false',
    },
  });

  const searchRow = ui.el('div', { class: 'palette__search' },
    ui.icon('search', 18),
    input,
    ui.kbd(['Esc']));

  const list = ui.el('div', {
    class: 'palette__list',
    attrs: { id: 'palette-list', role: 'listbox', 'aria-label': t('shell.palette') },
  });

  const foot = ui.el('div', { class: 'palette__foot' },
    ui.el('span', { class: 'palette__hint' }, ui.kbd(['↑', '↓']), ui.el('span', { text: t('palette.hint.navigate') })),
    ui.el('span', { class: 'palette__hint' }, ui.kbd(['↵']), ui.el('span', { text: t('palette.hint.run') })),
    ui.el('span', { class: 'palette__hint' }, ui.kbd(['Esc']), ui.el('span', { text: t('palette.hint.close') })));

  panel.append(searchRow, list, foot);
  document.body.appendChild(scrim);
  document.body.appendChild(panel);

  const releaseTrap = ui.trapFocus(panel);

  /** Flat, ordered list of selectable rows for keyboard movement. */
  let flat = [];
  let selected = 0;
  let query = '';
  let remote = { books: [], members: [] };
  let remoteTimer = null;
  let remoteSeq = 0;
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    if (remoteTimer) window.clearTimeout(remoteTimer);
    releaseTrap();
    scrim.remove();
    panel.remove();
    document.removeEventListener('keydown', onDocumentKey, true);
    palette = null;
    if (opener && typeof opener.focus === 'function') opener.focus();
  };

  const runRow = (row) => {
    if (!row) return;
    close();
    if (row.run) row.run();
  };

  /** Draw the list for the current query and selection. */
  const draw = () => {
    list.textContent = '';
    flat = [];

    const groups = [];
    const q = query.trim();

    // Actions
    const actions = paletteActions()
      .map((action) => ({ ...action, kind: 'action', text: action.label }))
      .map((action) => {
        const hit = q ? fuzzyMatch(q, action.text) : { score: 0, marks: [] };
        return hit ? { ...action, marks: hit.marks, score: hit.score + 2 } : null;
      })
      .filter(Boolean)
      .sort((a, b) => b.score - a.score);
    if (actions.length) groups.push({ key: 'palette.group.actions', rows: actions });

    // Destinations
    const gos = destinations()
      .map((item) => {
        const label = t(item.labelKey);
        const hit = q ? fuzzyMatch(q, label) : { score: 0, marks: [] };
        return hit
          ? { id: `go:${item.name}`, kind: 'goto', label, marks: hit.marks, icon: item.icon, score: hit.score, run: () => router.navigate(item.path) }
          : null;
      })
      .filter(Boolean)
      .sort((a, b) => b.score - a.score);
    if (gos.length) groups.push({ key: 'palette.group.goto', rows: gos });

    // Remote results, only once the user has typed something meaningful.
    if (q.length >= 2) {
      const books = remote.books.map((book) => ({
        id: `book:${book.id}`,
        kind: 'book',
        label: book.title || book.accession_no || '',
        meta: book.accession_no || '',
        icon: 'book',
        run: () => router.navigate(`/catalogue/${encodeURIComponent(book.id)}`),
      }));
      if (books.length) groups.push({ key: 'palette.group.books', rows: books });

      const members = remote.members.map((member) => ({
        id: `member:${member.id}`,
        kind: 'member',
        label: member.name || member.member_code || '',
        meta: member.member_code || '',
        icon: 'user',
        run: () => router.navigate(`/members/${encodeURIComponent(member.id)}`),
      }));
      if (members.length) groups.push({ key: 'palette.group.members', rows: members });
    }

    if (!groups.length) {
      list.appendChild(ui.el('p', {
        class: 'palette__empty',
        text: q ? t('palette.empty', { query: q }) : t('empty.generic.title'),
      }));
      selected = 0;
      return;
    }

    for (const group of groups) {
      const section = ui.el('div', { class: 'palette__group' });
      section.appendChild(ui.el('p', { class: 'palette__label', text: t(group.key) }));
      const groupList = ui.el('div', { attrs: { role: 'group', 'aria-label': t(group.key) } });
      for (const row of group.rows) {
        const index = flat.length;
        const item = ui.el('div', {
          class: 'palette__item',
          attrs: {
            role: 'option',
            id: `palette-opt-${index}`,
            'aria-selected': 'false',
          },
          on: {
            mouseenter: () => select(index, false),
            click: () => runRow(row),
          },
        });
        item.appendChild(ui.el('span', { class: 'palette__icon' }, ui.icon(row.icon || 'chevronRight', 18)));
        item.appendChild(highlighted(row.label, row.marks));
        if (row.meta) item.appendChild(ui.el('span', { class: 'palette__meta', text: row.meta }));
        groupList.appendChild(item);
        flat.push({ row, element: item });
      }
      section.appendChild(groupList);
      list.appendChild(section);
    }

    selected = Math.min(selected, flat.length - 1);
    if (selected < 0) selected = 0;
    applySelection();
  };

  /** Paint the selection and keep it in view. */
  const applySelection = () => {
    flat.forEach((entry, index) => {
      entry.element.setAttribute('aria-selected', index === selected ? 'true' : 'false');
    });
    const active = flat[selected];
    if (active) {
      active.element.scrollIntoView({ block: 'nearest' });
      input.setAttribute('aria-activedescendant', active.element.id);
    }
  };

  /** Move the selection. */
  const select = (index, scroll) => {
    if (!flat.length) return;
    selected = ((index % flat.length) + flat.length) % flat.length;
    applySelection();
    if (scroll === false) return;
  };

  /** Fetch remote results for the current query. */
  const fetchRemote = (term) => {
    if (!store.can('search.read') || term.length < 2) {
      remote = { books: [], members: [] };
      return;
    }
    remoteSeq += 1;
    const seq = remoteSeq;
    api.get('/api/search', { q: term, limit: 5 })
      .then((data) => {
        if (seq !== remoteSeq || closed) return;
        remote = {
          books: (data && data.books && data.books.items) || [],
          members: (data && data.members && data.members.items) || [],
        };
        draw();
      })
      .catch(() => {
        if (seq !== remoteSeq || closed) return;
        remote = { books: [], members: [] };
      });
  };

  input.addEventListener('input', () => {
    query = input.value;
    selected = 0;
    draw();
    if (remoteTimer) window.clearTimeout(remoteTimer);
    remoteTimer = window.setTimeout(() => fetchRemote(query.trim()), 160);
  });

  const onDocumentKey = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      select(selected + 1);
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      select(selected - 1);
      return;
    }
    if (event.key === 'Home') {
      event.preventDefault();
      select(0);
      return;
    }
    if (event.key === 'End') {
      event.preventDefault();
      select(flat.length - 1);
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      const active = flat[selected];
      if (active) runRow(active.row);
    }
  };

  document.addEventListener('keydown', onDocumentKey, true);
  scrim.addEventListener('mousedown', close);

  palette = { close };
  draw();
  window.requestAnimationFrame(() => input.focus());
}

/** Close the palette if it is open. */
export function closePalette() {
  if (palette) palette.close();
}

// --------------------------------------------------------------------------- //
// Global keyboard handling
// --------------------------------------------------------------------------- //

/** Map for the "g then x" navigation prefix. */
const GO_MAP = Object.freeze({
  d: '/dashboard',
  c: '/catalogue',
  m: '/members',
  r: '/reports',
  i: '/counter',
  o: '/overdue',
  v: '/reservations',
  l: '/labels',
  s: '/settings',
  b: '/backup',
  a: '/audit',
  u: '/admin',
  k: '/license',
});

/** @returns {boolean} Whether focus is in a text field. */
function inTextField(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

/** Install the global key handler. */
function installKeys() {
  keyHandler = (event) => {
    const meta = event.ctrlKey || event.metaKey;

    // Ctrl/Cmd+K — palette. Works even from a text field.
    if (meta && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (palette) closePalette();
      else openPalette();
      return;
    }

    // Ctrl/Cmd+S — let the page save; the page decides whether it can.
    if (meta && event.key.toLowerCase() === 's') {
      event.preventDefault();
      document.dispatchEvent(new CustomEvent('imri:save'));
      return;
    }

    if (event.key === 'Escape') {
      if (palette) {
        event.preventDefault();
        closePalette();
      }
      return;
    }

    if (inTextField(event.target) || meta || event.altKey) return;
    if (ui.hasOpenLayer()) return;

    // `/` focuses the topbar search.
    if (event.key === '/') {
      event.preventDefault();
      openPalette();
      return;
    }

    // `?` opens the shortcut sheet.
    if (event.key === '?') {
      event.preventDefault();
      openShortcuts();
      return;
    }

    // `g` then a letter jumps to a section.
    if (pendingGo) {
      const path = GO_MAP[event.key.toLowerCase()];
      if (pendingGoTimer) window.clearTimeout(pendingGoTimer);
      pendingGo = null;
      if (path) {
        event.preventDefault();
        router.navigate(path);
      }
      return;
    }
    if (event.key.toLowerCase() === 'g') {
      pendingGo = 'g';
      pendingGoTimer = window.setTimeout(() => {
        pendingGo = null;
      }, 900);
    }
  };
  document.addEventListener('keydown', keyHandler);
}

// --------------------------------------------------------------------------- //
// Heartbeat
// --------------------------------------------------------------------------- //
// The liveness ping lives in `app.js` (`startLiveness`) and starts at boot,
// before sign-in. It used to live here and start only once the shell mounted,
// which left the activation and login screens silent and let the server's
// idle watchdog shut it down while a librarian was still reading the form.

// --------------------------------------------------------------------------- //
// Global error handling
// --------------------------------------------------------------------------- //

/** Turn an unexpected rejection into one toast, never a stack of them. */
function installErrorHandlers() {
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    // ApiError already has a sentence; anything else is a bug and is logged.
    if (reason && reason.code) {
      const info = ui.formatError(reason);
      ui.toast.error(info.title, info.message);
      event.preventDefault();
      return;
    }
    console.error('[shell] unhandled rejection', reason);
    ui.toast.error(t('toast.error'), String((reason && reason.message) || reason || ''));
    event.preventDefault();
  });

  window.addEventListener('error', (event) => {
    // Resource load failures bubble here with no `error` object; ignore them.
    if (!event.error) return;
    console.error('[shell] uncaught error', event.error);
  });
}

// --------------------------------------------------------------------------- //
// Build
// --------------------------------------------------------------------------- //

/**
 * Build the shell and return the container the router should render into.
 *
 * @param {HTMLElement} root Usually `#app`.
 * @returns {HTMLElement} The `#view` element.
 */
export function mount(root) {
  root.classList.remove('boot');
  root.removeAttribute('aria-busy');
  root.textContent = '';

  const shell = ui.el('div', { class: 'shell', attrs: { 'data-collapsed': 'false' } });
  refs.shell = shell;

  // Skip link: first tab stop, invisible until focused.
  const skip = ui.el('a', {
    class: 'skip-link u-sr',
    href: '#view',
    on: { click: () => { window.setTimeout(() => refs.view && refs.view.focus(), 0); } },
  });
  bindText(skip, 'shell.skip');
  shell.appendChild(skip);

  // --- sidebar -----------------------------------------------------------
  const sidebar = ui.el('aside', { class: 'sidebar', attrs: { 'aria-label': t('shell.nav') } });
  refs.sidebar = sidebar;

  // Named `brandLink`, not `brand`: a local `const brand` here would shadow the
  // imported `brand` module for the whole function body, and `brand.mark()`
  // below would then throw "Cannot access 'brand' before initialization" —
  // which kills boot, not just the sidebar.
  const brandLink = ui.el('a', { class: 'sb__brand', href: router.href('/dashboard') },
    // Decorative: the wordmark sits right beside it, so an aria-label here
    // would make a screen reader announce "IMRI IMRI".
    ui.el('span', { class: 'sb__mark' }, brand.mark(28, { label: null })),
    ui.el('span', { class: 'sb__text' },
      ui.el('span', { class: 'sb__wordmark', text: 'IMRI' }),
      ui.el('span', { class: 'sb__sub', text: store.schoolName() || t('app.tagline') })));
  sidebar.appendChild(brandLink);

  const nav = ui.el('nav', { class: 'sb__nav' });
  refs.nav = nav;
  sidebar.appendChild(nav);

  const foot = ui.el('div', { class: 'sb__foot' });
  const collapseIcon = ui.icon('chevronLeft', 16);
  const collapseButton = ui.el('button', {
    class: 'sb__collapse',
    attrs: { type: 'button', 'aria-expanded': 'true' },
    on: {
      click: () => {
        store.toggleSidebar();
        applyCollapsed();
      },
    },
  }, collapseIcon, ui.el('span', { text: t('nav.collapse') }));
  refs.collapseButton = collapseButton;
  refs.collapseIcon = collapseIcon;
  foot.appendChild(collapseButton);
  sidebar.appendChild(foot);

  // --- main column -------------------------------------------------------
  const main = ui.el('div', { class: 'main' });

  const topbar = ui.el('header', { class: 'topbar' });
  refs.topbar = topbar;

  const menuButton = ui.iconButton({
    icon: 'menu',
    label: t('nav.openMenu'),
    onClick: () => {
      store.toggleSidebar();
      applyCollapsed();
    },
  });
  menuButton.classList.add('tb__menu');

  const title = ui.el('h1', { class: 'tb__title' });
  refs.title = title;

  const spacer = ui.el('div', { class: 'tb__spacer' });

  const searchButton = ui.el('button', {
    class: 'tb__search',
    attrs: { type: 'button', 'aria-label': t('shell.searchAria'), title: t('shell.searchHint') },
    on: { click: () => openPalette() },
  },
  ui.icon('search', 16),
  bindText(ui.el('span', { class: 'u-muted u-truncate' }), 'shell.search'),
  ui.kbd(['Ctrl', 'K']));

  const bell = ui.iconButton({
    icon: 'bell',
    label: t('shell.bell'),
    onClick: () => router.navigate('/overdue'),
  });
  refs.bell = bell;

  const themeButton = ui.iconButton({
    icon: 'sun',
    label: t('shell.themeToggle'),
    onClick: () => {
      store.cycleTheme();
      updateThemeButton();
    },
  });
  refs.themeButton = themeButton;

  const langButton = ui.el('button', {
    class: 'icon-btn tb__lang',
    attrs: { type: 'button', 'aria-label': t('shell.langSwitch'), title: t('shell.langSwitch') },
    on: { click: () => openLangMenu() },
  });
  refs.langButton = langButton;

  const divider = ui.el('span', { class: 'tb__divider', attrs: { 'aria-hidden': 'true' } });

  const avatarButton = ui.el('button', {
    class: 'avatar',
    attrs: { type: 'button', 'aria-haspopup': 'menu' },
    on: { click: () => openAccountMenu() },
  },
  ui.el('span', { class: 'avatar__img', attrs: { 'aria-hidden': 'true' } }),
  ui.el('span', { class: 'avatar__meta' },
    ui.el('span', { class: 'avatar__name' }),
    ui.el('span', { class: 'avatar__role' })));
  refs.avatarButton = avatarButton;
  refs.avatarInitials = avatarButton.querySelector('.avatar__img');

  const actions = ui.el('div', { class: 'tb__actions' },
    searchButton, bell, themeButton, langButton, divider, avatarButton);

  topbar.append(menuButton, title, spacer, actions);

  const content = ui.el('div', { class: 'content' });
  const view = ui.el('div', { class: 'view', attrs: { id: 'view', tabindex: '-1' } });
  content.appendChild(view);
  refs.view = view;

  main.append(topbar, content);
  shell.append(sidebar, main);
  root.appendChild(shell);

  // --- first paint -------------------------------------------------------
  applyCollapsed();
  applyChrome();
  renderNav();
  updateTitle();
  updateThemeButton();
  updateLangButton();
  updateAvatar();
  updateAccountChip();

  installKeys();
  installErrorHandlers();

  // --- subscriptions -----------------------------------------------------
  unsubscribers.push(store.subscribe((state, reason) => {
    if (reason === 'route') {
      applyChrome();
      renderNav();
      updateTitle();
      return;
    }
    if (reason === 'session') {
      renderNav();
      updateAvatar();
      updateAccountChip();
      refreshBell();
      return;
    }
    if (reason === 'ui') {
      applyCollapsed();
      return;
    }
    if (reason === 'theme') {
      updateThemeButton();
    }
  }));

  unsubscribers.push(i18n.onLangChange(() => {
    repaint();
    updateLangButton();
    renderNav();
  }));

  document.addEventListener('imri:lang', repaint);

  refreshBell();

  return view;
}

/** Show the user's name and role in the topbar avatar. */
function updateAccountChip() {
  if (!refs.avatarButton) return;
  const user = store.user() || {};
  const name = refs.avatarButton.querySelector('.avatar__name');
  const roleNode = refs.avatarButton.querySelector('.avatar__role');
  if (name) name.textContent = user.display_name || user.username || '';
  if (roleNode) roleNode.textContent = store.role() ? t(`role.${store.role()}`) : '';
}

/**
 * Re-read session and route and update the chrome. Called after bootstrap.
 */
export function refresh() {
  applyChrome();
  renderNav();
  updateTitle();
  updateAvatar();
  updateAccountChip();
  updateLangButton();
  updateThemeButton();
  refreshBell();
}

/** Remove every listener and timer the shell installed. */
export function destroy() {
  for (const off of unsubscribers) {
    try {
      off();
    } catch (err) {
      /* ignore */
    }
  }
  unsubscribers.length = 0;
  if (keyHandler) document.removeEventListener('keydown', keyHandler);
  keyHandler = null;
  document.removeEventListener('imri:lang', repaint);
  closePalette();
}

/** Default export. */
export default {
  NAV_GROUPS,
  mount,
  refresh,
  repaint,
  openPalette,
  closePalette,
  openShortcuts,
  fuzzyMatch,
  destroy,
};
