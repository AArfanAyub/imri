/**
 * IMRI — internationalisation.
 *
 * Three catalogues ship in the bundle and are imported statically, so switching
 * language is a synchronous in-memory swap: no fetch, no flash, no re-render of
 * data the user already has on screen (I18N-03). Everything the UI prints goes
 * through `t()`; nothing is hard-coded in a component (I18N-04).
 *
 * Lookup order for a key is: active catalogue → English → `⟨key⟩`. The last
 * step is deliberately ugly: a missing string must be visible in QA rather than
 * silently blank. `checkCompleteness()` reports every gap in one call.
 *
 * Numbers, money and dates go through `Intl`, which is built into Edge — no
 * locale data to ship, no format strings to translate.
 *
 * Public surface
 *   LANGS                     supported languages, in menu order
 *   getLang() / setLang()     read or switch the active language
 *   t(key, vars)              translate, interpolating `{name}` slots
 *   fmtDate / fmtDateTime     locale-correct dates from ISO strings or ms
 *   fmtMoney / fmtNum / fmtPct
 *   fmtRelative               "3 days ago", "today", "in 4 days"
 *   checkCompleteness()       key-set diff across all catalogues
 */

import en from './lang/en.js';
import ms from './lang/ms.js';
import zh from './lang/zh.js';

/** Supported languages, in the order they appear in the language chip. */
export const LANGS = Object.freeze([
  Object.freeze({ code: 'en', label: 'English', intl: 'en-MY', catalogue: en }),
  Object.freeze({ code: 'ms', label: 'Bahasa Melayu', intl: 'ms-MY', catalogue: ms }),
  Object.freeze({ code: 'zh', label: '中文', intl: 'zh-CN', catalogue: zh }),
]);

/** Language used when nothing is stored and the browser offers nothing usable. */
const DEFAULT_LANG = 'en';

/** localStorage key. `theme-boot.js` owns `imri.theme`; this one is ours. */
const STORAGE_KEY = 'imri.lang';

/** Fallback currency until `app.js` reads it from the server settings. */
let currencyCode = 'MYR';

/** Active language code. */
let activeLang = DEFAULT_LANG;

/** Subscribers notified after a language change. */
const listeners = new Set();

/** Memoised `Intl` objects. Constructing them is the expensive part. */
const dateFormatters = new Map();
const numberFormatters = new Map();

// --------------------------------------------------------------------------- //
// Language resolution
// --------------------------------------------------------------------------- //

/**
 * Pick the best supported language for a raw BCP-47 tag.
 *
 * @param {?string} tag e.g. `'ms-MY'`, `'zh-Hans-CN'`, `'en'`.
 * @returns {?string} A supported code, or `null` when nothing matches.
 */
function matchLang(tag) {
  if (!tag || typeof tag !== 'string') return null;
  const lower = tag.trim().toLowerCase();
  if (!lower) return null;
  for (const lang of LANGS) {
    if (lower === lang.code) return lang.code;
  }
  // 'zh-cn', 'zh-hans', 'ms-my' — match on the primary subtag.
  const primary = lower.split(/[-_]/)[0];
  for (const lang of LANGS) {
    if (primary === lang.code) return lang.code;
  }
  // Malay is also tagged `id` in some browsers; treat it as the same catalogue.
  if (primary === 'id' || primary === 'in') return 'ms';
  return null;
}

/** Read the persisted language, if any. Storage may be unavailable. */
function readStoredLang() {
  try {
    return matchLang(window.localStorage.getItem(STORAGE_KEY));
  } catch (err) {
    return null;
  }
}

/** Persist the language. Failures are silent — the session still works. */
function writeStoredLang(code) {
  try {
    window.localStorage.setItem(STORAGE_KEY, code);
  } catch (err) {
    /* private mode, quota, locked profile — not worth surfacing */
  }
}

/** @returns {Object} The catalogue for the active language. */
function activeCatalogue() {
  for (const lang of LANGS) {
    if (lang.code === activeLang) return lang.catalogue;
  }
  return en;
}

/**
 * The active language code.
 * @returns {string} One of `'en' | 'ms' | 'zh'`.
 */
export function getLang() {
  return activeLang;
}

/**
 * The `Intl` locale for a language code.
 * @param {?string} [code] Defaults to the active language.
 * @returns {string} e.g. `'ms-MY'`.
 */
export function intlLocale(code) {
  const wanted = code || activeLang;
  for (const lang of LANGS) {
    if (lang.code === wanted) return lang.intl;
  }
  return 'en-MY';
}

/**
 * The human name of a language, in that language ("English", "Bahasa Melayu").
 * @param {?string} [code]
 * @returns {string}
 */
export function langLabel(code) {
  const wanted = code || activeLang;
  for (const lang of LANGS) {
    if (lang.code === wanted) return lang.label;
  }
  return wanted;
}

/**
 * Switch language and repaint.
 *
 * Sets `<html lang>` (so hyphenation and screen-reader pronunciation follow),
 * persists the choice, notifies subscribers and dispatches `imri:lang` on the
 * document for modules that prefer events to callbacks.
 *
 * @param {string} code `'en' | 'ms' | 'zh'`.
 * @returns {boolean} True when the language actually changed.
 */
export function setLang(code) {
  const next = matchLang(code) || DEFAULT_LANG;
  if (next === activeLang) return false;
  activeLang = next;
  writeStoredLang(next);
  document.documentElement.setAttribute('lang', next);
  for (const fn of listeners) {
    try {
      fn(next);
    } catch (err) {
      // A broken subscriber must not stop the rest of the UI from repainting.
      console.error('[i18n] language listener failed', err);
    }
  }
  document.dispatchEvent(new CustomEvent('imri:lang', { detail: { lang: next } }));
  return true;
}

/**
 * Subscribe to language changes.
 * @param {(lang: string) => void} fn
 * @returns {() => void} Unsubscribe.
 */
export function onLangChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/**
 * Resolve the starting language: stored choice, then the browser's preference
 * list, then English.
 * @returns {string}
 */
export function resolveInitialLang() {
  const stored = readStoredLang();
  if (stored) return stored;
  const offered = Array.isArray(navigator.languages) && navigator.languages.length
    ? navigator.languages
    : [navigator.language];
  for (const tag of offered) {
    const hit = matchLang(tag);
    if (hit) return hit;
  }
  return DEFAULT_LANG;
}

/**
 * Apply the starting language without animating or announcing anything.
 * Called once by `app.js` before the first paint of the shell.
 * @param {?string} [preferred] Server-side default (`settings.default_locale`).
 */
export function initLang(preferred) {
  activeLang = readStoredLang() || matchLang(preferred) || resolveInitialLang();
  document.documentElement.setAttribute('lang', activeLang);
}

// --------------------------------------------------------------------------- //
// Translation
// --------------------------------------------------------------------------- //

/**
 * Translate a key.
 *
 * `{name}` slots are replaced from `vars`. A slot with no matching value is
 * left in place — visible, greppable, and never a silent empty string.
 *
 * @param {string} key Dotted key, e.g. `'cat.new'`.
 * @param {?Object<string, (string|number)>} [vars] Interpolation values.
 * @returns {string}
 */
export function t(key, vars) {
  if (typeof key !== 'string' || key === '') return '';
  const catalogue = activeCatalogue();
  let value = catalogue[key];
  if (typeof value !== 'string') value = en[key];
  if (typeof value !== 'string') return `⟨${key}⟩`;
  if (!vars) return value;
  return value.replace(/\{(\w+)\}/g, (whole, name) => {
    const replacement = vars[name];
    if (replacement === undefined || replacement === null) return whole;
    return String(replacement);
  });
}

/**
 * Whether a key exists in the active catalogue (or English).
 * @param {string} key
 * @returns {boolean}
 */
export function has(key) {
  if (typeof key !== 'string') return false;
  return typeof activeCatalogue()[key] === 'string' || typeof en[key] === 'string';
}

/**
 * Count-aware translation helper.
 *
 * The catalogues carry explicit singular and plural keys rather than relying on
 * `Intl.PluralRules`, because Malay has no plural inflection and Chinese marks
 * it with a measure word — a single `{count}` template would be wrong in at
 * least one language.
 *
 * @param {string} singularKey Key used when `count === 1`.
 * @param {string} pluralKey Key used otherwise.
 * @param {number} count
 * @param {?Object} [vars] Extra slots; `count` is added automatically.
 * @returns {string}
 */
export function tn(singularKey, pluralKey, count, vars) {
  const n = Number(count) || 0;
  return t(n === 1 ? singularKey : pluralKey, Object.assign({ count: n }, vars || {}));
}

// --------------------------------------------------------------------------- //
// Formatting
// --------------------------------------------------------------------------- //

/**
 * Coerce a value into a `Date`, or `null`.
 *
 * Date-only strings (`'2025-03-12'`) are built in local time at midday. Using
 * `new Date('2025-03-12')` would parse as UTC midnight, which is the previous
 * day west of Greenwich — a librarian in Kuching would see the wrong due date.
 *
 * @param {?(string|number|Date)} value
 * @returns {?Date}
 */
export function toDate(value) {
  if (value === null || value === undefined || value === '') return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === 'number') {
    const fromNumber = new Date(value);
    return Number.isNaN(fromNumber.getTime()) ? null : fromNumber;
  }
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (!text) return null;

  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (dateOnly) {
    return new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]), 12, 0, 0);
  }
  const withTime = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(text);
  if (withTime) {
    // No zone designator: the server stores UTC, so treat it as UTC.
    return new Date(Date.UTC(
      Number(withTime[1]),
      Number(withTime[2]) - 1,
      Number(withTime[3]),
      Number(withTime[4]),
      Number(withTime[5]),
      Number(withTime[6] || 0),
    ));
  }
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/**
 * Format a `Date` as a local `YYYY-MM-DD` string.
 *
 * Never use `toISOString().slice(0, 10)` for this: that converts to UTC first,
 * so a librarian in Kuching (UTC+8) asking for "today" after 8pm gets
 * yesterday's date. Every date the user can see or type is a local date.
 *
 * @param {?(Date|string|number)} value Defaults to now.
 * @returns {string} `YYYY-MM-DD`, or `''` when the value is unparseable.
 */
export function toISODate(value = new Date()) {
  const date = value instanceof Date ? value : toDate(value);
  if (!date || Number.isNaN(date.getTime())) return '';
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

/** Named date formats. Keeping them here means one place to change them. */
const DATE_STYLES = Object.freeze({  short: Object.freeze({ day: '2-digit', month: '2-digit', year: 'numeric' }),
  medium: Object.freeze({ day: 'numeric', month: 'short', year: 'numeric' }),
  long: Object.freeze({ day: 'numeric', month: 'long', year: 'numeric' }),
  weekday: Object.freeze({ weekday: 'short', day: 'numeric', month: 'short' }),
  month: Object.freeze({ month: 'short', year: 'numeric' }),
  monthLong: Object.freeze({ month: 'long', year: 'numeric' }),
  time: Object.freeze({ hour: '2-digit', minute: '2-digit' }),
  datetime: Object.freeze({
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  }),
});

/**
 * Memoised `Intl.DateTimeFormat`.
 * @param {string} style Key of {@link DATE_STYLES}.
 * @returns {Intl.DateTimeFormat}
 */
function dateFormatter(style) {
  const options = DATE_STYLES[style] || DATE_STYLES.medium;
  const cacheKey = `${activeLang}|${style}`;
  let formatter = dateFormatters.get(cacheKey);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(intlLocale(), options);
    dateFormatters.set(cacheKey, formatter);
  }
  return formatter;
}

/**
 * Format a date for the active locale.
 *
 * @param {?(string|number|Date)} value ISO string, epoch milliseconds, or Date.
 * @param {'short'|'medium'|'long'|'weekday'|'month'|'monthLong'|'time'|'datetime'} [style]
 * @returns {string} `'—'` when the value is missing or unparseable.
 */
export function fmtDate(value, style) {
  const date = toDate(value);
  if (!date) return t('common.na');
  return dateFormatter(style || 'medium').format(date);
}

/**
 * Format a date and time.
 * @param {?(string|number|Date)} value
 * @returns {string}
 */
export function fmtDateTime(value) {
  return fmtDate(value, 'datetime');
}

/**
 * Format a time of day.
 * @param {?(string|number|Date)} value
 * @returns {string}
 */
export function fmtTime(value) {
  return fmtDate(value, 'time');
}

/**
 * Format an amount of money in the configured currency.
 *
 * The currency comes from `settings.currency` (default `MYR`), so a school that
 * changes it in Settings sees every figure update without a code change.
 *
 * @param {?(number|string)} value
 * @param {{currency?: string, decimals?: number}} [options]
 * @returns {string} `'—'` when the value is not a finite number.
 */
export function fmtMoney(value, options) {
  const amount = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(amount)) return t('common.na');
  const opts = options || {};
  const code = String(opts.currency || currencyCode || 'MYR').toUpperCase();
  const decimals = Number.isInteger(opts.decimals) ? opts.decimals : 2;
  const cacheKey = `money|${activeLang}|${code}|${decimals}`;
  let formatter = numberFormatters.get(cacheKey);
  if (!formatter) {
    formatter = new Intl.NumberFormat(intlLocale(), {
      style: 'currency',
      currency: code,
      currencyDisplay: 'narrowSymbol',
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    numberFormatters.set(cacheKey, formatter);
  }
  return formatter.format(amount);
}

/**
 * Format a number with locale grouping.
 *
 * @param {?(number|string)} value
 * @param {{decimals?: number, min?: number, max?: number}} [options]
 * @returns {string}
 */
export function fmtNum(value, options) {
  const amount = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(amount)) return t('common.na');
  const opts = options || {};
  const decimals = Number.isInteger(opts.decimals) ? opts.decimals : 0;
  const min = Number.isInteger(opts.min) ? opts.min : decimals;
  const max = Number.isInteger(opts.max) ? opts.max : decimals;
  const cacheKey = `num|${activeLang}|${min}|${max}`;
  let formatter = numberFormatters.get(cacheKey);
  if (!formatter) {
    formatter = new Intl.NumberFormat(intlLocale(), {
      minimumFractionDigits: min,
      maximumFractionDigits: max,
    });
    numberFormatters.set(cacheKey, formatter);
  }
  return formatter.format(amount);
}

/**
 * Format a ratio as a percentage. `0.734` → `'73%'`.
 * @param {?(number|string)} value Ratio, not a percentage.
 * @param {number} [decimals]
 * @returns {string}
 */
export function fmtPct(value, decimals) {
  const ratio = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(ratio)) return t('common.na');
  const digits = Number.isInteger(decimals) ? decimals : 0;
  const cacheKey = `pct|${activeLang}|${digits}`;
  let formatter = numberFormatters.get(cacheKey);
  if (!formatter) {
    formatter = new Intl.NumberFormat(intlLocale(), {
      style: 'percent',
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    numberFormatters.set(cacheKey, formatter);
  }
  return formatter.format(ratio);
}

/**
 * Format a byte count for backup sizes. Units are not translated: `MB` reads
 * the same in all three languages.
 * @param {?(number|string)} value
 * @returns {string}
 */
export function fmtBytes(value) {
  const bytes = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return t('common.na');
  if (bytes < 1024) return `${fmtNum(bytes)} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = bytes / 1024;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${fmtNum(size, { decimals: size >= 100 ? 0 : 1 })} ${units[index]}`;
}

/**
 * Human distance from now, in the active language.
 *
 * Deliberately coarse: a due date is read as "today" / "in 4 days", not
 * "in 4 days 3 hours". Sub-hour differences read as "just now".
 *
 * @param {?(string|number|Date)} value
 * @returns {string}
 */
export function fmtRelative(value) {
  const date = toDate(value);
  if (!date) return t('common.na');

  const now = Date.now();
  const deltaMs = date.getTime() - now;
  const absMs = Math.abs(deltaMs);

  if (absMs < 60_000) return t('time.justNow');
  if (absMs < 3_600_000) {
    const minutes = Math.round(absMs / 60_000);
    return deltaMs > 0
      ? t('time.inMinutes', { count: minutes })
      : t('time.minutesAgo', { count: minutes });
  }
  if (absMs < 86_400_000) {
    const hours = Math.round(absMs / 3_600_000);
    return deltaMs > 0
      ? t('time.inHours', { count: hours })
      : t('time.hoursAgo', { count: hours });
  }

  // Day arithmetic on local midnights, so "tomorrow" is right even across DST.
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const startOfOther = new Date(date.getTime());
  startOfOther.setHours(0, 0, 0, 0);
  const days = Math.round((startOfOther - startOfToday) / 86_400_000);

  if (days === 0) return t('time.today');
  if (days === 1) return t('time.tomorrow');
  if (days === -1) return t('time.yesterday');
  if (days > 1) return t('time.inDays', { count: days });
  return t('time.daysAgo', { count: -days });
}

/**
 * Whole days from today to `value`, on local midnights. Negative means past.
 * @param {?(string|number|Date)} value
 * @returns {?number} `null` when the value is unparseable.
 */
export function daysFromToday(value) {
  const date = toDate(value);
  if (!date) return null;
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const startOfOther = new Date(date.getTime());
  startOfOther.setHours(0, 0, 0, 0);
  return Math.round((startOfOther - startOfToday) / 86_400_000);
}

/**
 * Set the currency used by {@link fmtMoney}. Called once at boot from
 * `settings.currency`, and again whenever Settings saves a new one.
 * @param {?string} code ISO 4217 code, e.g. `'MYR'`.
 */
export function setCurrency(code) {
  const next = String(code || '').trim().toUpperCase();
  if (next.length === 3) currencyCode = next;
}

/** @returns {string} The active ISO 4217 code. */
export function getCurrency() {
  return currencyCode;
}

// --------------------------------------------------------------------------- //
// Completeness check
// --------------------------------------------------------------------------- //

/**
 * Diff the catalogues.
 *
 * Returns one entry per non-English language listing keys that are missing,
 * extra, or mapped to an empty string, plus an entry for English itself so an
 * accidentally blanked string is caught too. `app.js` calls this in development
 * and logs a single summary line; the QA script calls it directly.
 *
 * @returns {Array<{lang: string, missing: string[], extra: string[], empty: string[]}>}
 *   An empty array means every catalogue agrees with English.
 */
export function checkCompleteness() {
  const base = Object.keys(en);
  const problems = [];

  const blank = Object.keys(en).filter((key) => String(en[key]).trim() === '');
  if (blank.length) {
    problems.push({ lang: 'en', missing: [], extra: [], empty: blank });
  }

  for (const lang of LANGS) {
    if (lang.code === 'en') continue;
    const catalogue = lang.catalogue;
    const keys = Object.keys(catalogue);
    const missing = base.filter((key) => typeof catalogue[key] !== 'string');
    const extra = keys.filter((key) => !(key in en));
    const empty = keys.filter(
      (key) => typeof catalogue[key] === 'string' && catalogue[key].trim() === '',
    );
    if (missing.length || extra.length || empty.length) {
      problems.push({ lang: lang.code, missing, extra, empty });
    }
  }
  return problems;
}

/** Default export, for modules that prefer `i18n.t(...)`. */
export default {
  LANGS,
  getLang,
  setLang,
  onLangChange,
  initLang,
  resolveInitialLang,
  intlLocale,
  langLabel,
  t,
  tn,
  has,
  toDate,
  fmtDate,
  fmtDateTime,
  fmtTime,
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtBytes,
  fmtRelative,
  daysFromToday,
  setCurrency,
  getCurrency,
  checkCompleteness,
};
