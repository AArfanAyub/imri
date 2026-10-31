/**
 * IMRI — HTTP client.
 *
 * The server speaks one envelope shape for everything:
 *
 *   success  { "ok": true,  "data": <payload>, "meta": {...}? }
 *   failure  { "ok": false, "error": { "code": "...", "message": "...",
 *                                      "fields": {...}?, "details": {...}? } }
 *
 * This module hides that envelope. Callers get the payload directly, and any
 * `meta` block is attached to the returned value as a non-enumerable `__meta`
 * so pagination never leaks into rendering code:
 *
 *   const page = await api.get('/api/books', { page: 2, page_size: 10 });
 *   page.items.length          // rows
 *   page.__meta.total          // 137
 *
 * Failures reject with `ApiError`, which carries the machine-readable `code`
 * so views can branch on `err.is('copy_on_hold')` instead of parsing prose.
 *
 * Two failures are handled globally because every view would otherwise repeat
 * the same three lines:
 *
 *   401 auth_required / session_expired  ->  route to #/login
 *   403 license_required                 ->  route to #/activate
 *
 * Both are overridable via `setHandlers()`; the defaults just set
 * `location.hash`, which the shell's router already listens for.
 *
 * This file is the contract between the backend and the UI. Do not change the
 * exported names without telling the backend engineer — `api.js` is listed in
 * T3.5 of the task breakdown precisely so the surface is frozen.
 */

/** Machine-readable error code raised when the licence gate has not been passed. */
export const LICENSE_REQUIRED = 'license_required';

/** Error codes that mean "your session is gone, log in again". */
export const AUTH_CODES = Object.freeze(['auth_required', 'session_expired', 'account_disabled']);

/**
 * A failed API call.
 *
 * @property {string} code    Machine-readable code from ARCHITECTURE §8.2.
 * @property {number} status  HTTP status, or 0 for a network/abort failure.
 * @property {?Object} fields Per-field validation messages, when present.
 * @property {?Object} details Extra context (e.g. `{outstanding: "0.60"}`).
 */
export class ApiError extends Error {
  /**
   * @param {string} code
   * @param {string} message
   * @param {{status?: number, fields?: ?Object, details?: ?Object}} [extra]
   */
  constructor(code, message, extra) {
    const opts = extra || {};
    super(message || code || 'Request failed');
    this.name = 'ApiError';
    this.code = code || 'internal';
    this.status = typeof opts.status === 'number' ? opts.status : 0;
    this.fields = opts.fields || null;
    this.details = opts.details || null;
  }

  /** True when the server reported this exact code. @param {string} code */
  is(code) {
    return this.code === code;
  }

  /** True when any of the supplied codes matched. @param {...string} codes */
  isAny(...codes) {
    return codes.some((code) => this.code === code);
  }

  /** True when the failure was a validation rejection with field messages. */
  get isValidation() {
    return this.code === 'validation_failed' || this.status === 422;
  }

  /** True when the request never reached the server (offline, aborted). */
  get isNetwork() {
    return this.status === 0;
  }
}

/** @type {{onUnauthorized: ?Function, onLicenseRequired: ?Function, onError: ?Function}} */
const handlers = {
  onUnauthorized: null,
  onLicenseRequired: null,
  onError: null,
};

/** Timestamp (ms) of the most recent request, for the heartbeat watcher. */
let lastActivityAt = Date.now();

/** Monotonic counter so overlapping requests can be matched in logs. */
let requestSeq = 0;

/**
 * Install global failure handlers.
 *
 * Pass `null` for any handler to restore the default. Handlers receive the
 * `ApiError` and may return `true` to mark it as handled; returning anything
 * else lets the rejection continue to the caller.
 *
 * @param {{onUnauthorized?: ?Function, onLicenseRequired?: ?Function,
 *          onError?: ?Function}} next
 */
export function setHandlers(next) {
  if (!next) return;
  if ('onUnauthorized' in next) handlers.onUnauthorized = next.onUnauthorized;
  if ('onLicenseRequired' in next) handlers.onLicenseRequired = next.onLicenseRequired;
  if ('onError' in next) handlers.onError = next.onError;
}

/** Milliseconds since the last request completed. Used by the heartbeat. */
export function idleFor() {
  return Date.now() - lastActivityAt;
}

/**
 * Build a query string from a plain object.
 *
 * `undefined`, `null` and `''` are dropped so `?q=` never appears; arrays are
 * repeated (`?status=a&status=b`) to match the server's `parse_qs` handling,
 * which keeps the last value, so callers that need multi-value filters should
 * join with a comma themselves.
 *
 * @param {?Object} params
 * @returns {string} Either `''` or `'?a=1&b=2'`.
 */
export function toQuery(params) {
  if (!params) return '';
  const search = new URLSearchParams();
  for (const key of Object.keys(params)) {
    const value = params[key];
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) {
      value.forEach((item) => {
        if (item !== undefined && item !== null && item !== '') {
          search.append(key, String(item));
        }
      });
    } else if (typeof value === 'boolean') {
      search.append(key, value ? '1' : '0');
    } else {
      search.append(key, String(value));
    }
  }
  const text = search.toString();
  return text ? '?' + text : '';
}

/**
 * Attach the pagination block to a payload without polluting `JSON.stringify`
 * or `Object.keys`, so a table that spreads `{...row}` never sees it.
 *
 * @param {*} data
 * @param {?Object} meta
 * @returns {*} The same reference, for chaining.
 */
function attachMeta(data, meta) {
  if (!meta || data === null || data === undefined) return data;
  const carrier = typeof data === 'object' ? data : null;
  if (!carrier) return data;
  try {
    Object.defineProperty(carrier, '__meta', {
      value: meta,
      enumerable: false,
      configurable: true,
      writable: true,
    });
  } catch (err) {
    // Frozen payloads cannot carry meta; callers can still use `getWithMeta`.
  }
  return carrier;
}

/**
 * Extract a filename from a `Content-Disposition` header.
 * @param {?string} header
 * @returns {string} Empty string when the header is absent or unparseable.
 */
export function filenameFrom(header) {
  if (!header) return '';
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (utf8 && utf8[1]) {
    try {
      return decodeURIComponent(utf8[1].trim().replace(/^"|"$/g, ''));
    } catch (err) {
      // fall through to the ASCII form
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain && plain[1] ? plain[1].trim() : '';
}

/**
 * Turn a non-2xx response into an `ApiError`.
 *
 * A 5xx that returned HTML (a proxy error page, say) has no envelope, so the
 * status text stands in for the message rather than showing the user markup.
 *
 * @param {Response} response
 * @returns {Promise<ApiError>}
 */
async function toApiError(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch (err) {
    payload = null;
  }
  const error = payload && payload.error ? payload.error : null;
  if (error && error.code) {
    return new ApiError(error.code, error.message, {
      status: response.status,
      fields: error.fields || null,
      details: error.details || null,
    });
  }
  return new ApiError(
    response.status === 413 ? 'payload_too_large' : 'internal',
    response.statusText || 'Request failed with status ' + response.status,
    { status: response.status },
  );
}

/**
 * Dispatch a global handler, returning whether it claimed the error.
 * @param {?Function} handler
 * @param {ApiError} error
 * @returns {boolean}
 */
function dispatch(handler, error) {
  if (typeof handler !== 'function') return false;
  try {
    return handler(error) === true;
  } catch (err) {
    return false;
  }
}

/** Default reaction to a lost session: send the shell to the login route. */
function defaultUnauthorized(error) {
  if (typeof location !== 'undefined' && location.hash.indexOf('#/login') !== 0) {
    location.hash = '#/login';
  }
  return true;
}

/** Default reaction to an unlicensed build: send the shell to activation. */
function defaultLicenseRequired(error) {
  if (typeof location !== 'undefined' && location.hash.indexOf('#/activate') !== 0) {
    location.hash = '#/activate';
  }
  return true;
}

/**
 * Apply the global handlers to a rejected request.
 * @param {ApiError} error
 * @returns {ApiError} The same error, for rethrowing.
 */
function report(error) {
  lastActivityAt = Date.now();
  if (error.code === LICENSE_REQUIRED) {
    if (dispatch(handlers.onLicenseRequired, error) || defaultLicenseRequired(error)) return error;
  }
  if (AUTH_CODES.indexOf(error.code) !== -1 || error.status === 401) {
    if (dispatch(handlers.onUnauthorized, error) || defaultUnauthorized(error)) return error;
  }
  dispatch(handlers.onError, error);
  return error;
}

/**
 * Core request. Everything else in this module funnels through here.
 *
 * @param {string} method
 * @param {string} path      Absolute path beginning with `/api/`.
 * @param {{query?: ?Object, body?: *, headers?: ?Object, signal?: ?AbortSignal,
 *          expect?: string}} [options]
 * @returns {Promise<*>} The unwrapped `data` payload.
 */
export async function request(method, path, options) {
  const opts = options || {};
  const seq = ++requestSeq;
  const headers = { Accept: 'application/json' };
  if (opts.headers) Object.assign(headers, opts.headers);

  let payload;
  if (opts.body !== undefined && opts.body !== null) {
    if (opts.body instanceof FormData || opts.body instanceof Blob) {
      payload = opts.body;
    } else {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(opts.body);
    }
  }

  // The CSRF stage rejects any state-changing request without this header.
  // It is not a secret — it proves the caller can set a custom header, which a
  // cross-site form post cannot.
  if (method !== 'GET' && method !== 'HEAD') {
    headers['X-IMRI-CSRF'] = '1';
  }

  let response;
  try {
    response = await fetch(path + toQuery(opts.query), {
      method: method,
      headers: headers,
      body: payload,
      credentials: 'same-origin',
      cache: 'no-store',
      signal: opts.signal,
    });
  } catch (err) {
    const aborted = err && (err.name === 'AbortError' || err.code === 20);
    throw report(
      new ApiError(
        aborted ? 'aborted' : 'network_error',
        aborted ? 'Request cancelled' : 'Cannot reach the IMRI service',
        { status: 0 },
      ),
    );
  }

  lastActivityAt = Date.now();

  if (response.status === 204) {
    return null;
  }

  const expect = opts.expect || 'json';
  if (expect !== 'json') {
    if (!response.ok) throw report(await toApiError(response));
    return response;
  }

  if (!response.ok) {
    throw report(await toApiError(response));
  }

  let envelope;
  try {
    envelope = await response.json();
  } catch (err) {
    throw report(new ApiError('internal', 'Malformed response from the IMRI service', {
      status: response.status,
    }));
  }

  if (!envelope || envelope.ok !== true) {
    const error = envelope && envelope.error ? envelope.error : {};
    throw report(
      new ApiError(error.code || 'internal', error.message, {
        status: response.status,
        fields: error.fields || null,
        details: error.details || null,
      }),
    );
  }

  if (typeof window !== 'undefined' && window.__IMRI_DEBUG__) {
    // eslint-disable-next-line no-console
    console.debug('[imri] #%d %s %s -> %d', seq, method, path, response.status);
  }

  return attachMeta(envelope.data, envelope.meta);
}

/**
 * Like `request`, but resolves `{data, meta}` instead of folding meta in.
 *
 * Use this when the payload is a primitive (a number, a string) and therefore
 * cannot carry `__meta`.
 *
 * @param {string} method
 * @param {string} path
 * @param {Object} [options]
 * @returns {Promise<{data: *, meta: ?Object}>}
 */
export async function requestWithMeta(method, path, options) {
  const opts = Object.assign({}, options || {});
  const response = await request(method, path, Object.assign({}, opts, { expect: 'json' }));
  const meta = response && typeof response === 'object' ? response.__meta || null : null;
  return { data: response, meta: meta };
}

/** GET a JSON payload. @param {string} path @param {?Object} [query] */
export function get(path, query) {
  return request('GET', path, { query: query });
}

/** GET a JSON payload together with its `meta` block. */
export function getWithMeta(path, query) {
  return requestWithMeta('GET', path, { query: query });
}

/** POST a JSON body. @param {string} path @param {*} [body] @param {?Object} [query] */
export function post(path, body, query) {
  return request('POST', path, { body: body === undefined ? {} : body, query: query });
}

/** PUT a JSON body. */
export function put(path, body, query) {
  return request('PUT', path, { body: body === undefined ? {} : body, query: query });
}

/** PATCH a JSON body. */
export function patch(path, body, query) {
  return request('PATCH', path, { body: body === undefined ? {} : body, query: query });
}

/** DELETE a resource. */
export function del(path, query) {
  return request('DELETE', path, { query: query });
}

/** Alias of {@link del} — `delete` is a reserved word, so this reads better. */
export const remove = del;

/**
 * Upload a file as a raw request body.
 *
 * IMRI deliberately does not parse `multipart/form-data` (stdlib only, and the
 * import pipeline wants the bytes untouched). The original filename travels in
 * the query string so the server can pick the CSV/XLSX reader.
 *
 * @param {string} path      e.g. `/api/books/import/preview`
 * @param {File|Blob} file
 * @param {?Object} [query]  Extra parameters, e.g. `{dry_run: true}`.
 * @returns {Promise<*>}
 */
export function upload(path, file, query) {
  const params = Object.assign({}, query || {});
  if (params.filename === undefined && file && file.name) {
    params.filename = file.name;
  }
  return request('POST', path, {
    body: file,
    query: params,
    headers: { 'Content-Type': file && file.type ? file.type : 'application/octet-stream' },
  });
}

/**
 * Request a binary response and hand it to the browser as a download.
 *
 * @param {string} path
 * @param {?Object} [query]
 * @param {string} [fallbackName] Used when the server sends no filename.
 * @returns {Promise<{blob: Blob, filename: string}>}
 */
export async function download(path, query, fallbackName) {
  const response = await request('GET', path, { query: query, expect: 'blob' });
  const blob = await response.blob();
  const name = filenameFrom(response.headers.get('Content-Disposition')) || fallbackName || 'imri-export';
  saveBlob(blob, name);
  return { blob: blob, filename: name };
}

/**
 * Hand a Blob to the browser's download machinery.
 * @param {Blob} blob
 * @param {string} filename
 */
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename || 'imri-export';
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  // Revoke on the next frame so Safari has time to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/**
 * Fetch an SVG endpoint and return its markup as a string.
 *
 * Labels and QR codes are rendered server-side into `<svg>` so the print path
 * never depends on a canvas or a font being present.
 *
 * @param {string} path
 * @param {?Object} [query]
 * @returns {Promise<string>}
 */
export async function svg(path, query) {
  const response = await request('GET', path, { query: query, expect: 'blob' });
  return response.text();
}

/**
 * Convenience wrapper for the two activation endpoints.
 *
 * @param {string} code The single activation code.
 * @returns {Promise<Object>} The licence status payload.
 */
export function activate(code) {
  return post('/api/license/activate', { code: String(code || '') });
}

/**
 * Licence + session + settings snapshot, readable before activation.
 *
 * This is the one call the shell can make while unlicensed, so the activation
 * screen is the only thing it needs to paint.
 *
 * @returns {Promise<Object>} `{licensed, license, session, settings, version, schema}`.
 */
export function bootstrap() {
  return get('/api/bootstrap');
}

/** @returns {Promise<Object>} Licence status — alias of {@link bootstrap}. */
export function licenseStatus() {
  return get('/api/bootstrap');
}

/**
 * Full licence record for Administration -> Licence & About.
 * Requires the `license.read` capability, so it needs a session.
 *
 * @returns {Promise<Object>}
 */
export function licenseDetail() {
  return get('/api/license');
}

/**
 * Log in and return the session payload (`{user, expires_at}`).
 * @param {string} username
 * @param {string} password
 */
export function login(username, password) {
  return post('/api/auth/login', { username: username, password: password });
}

/** Log out (the server clears the cookie). */
export function logout() {
  return post('/api/auth/logout', {});
}

/**
 * Tell the server the page is alive.
 *
 * The 120 s heartbeat backstop closes the app when the window dies without a
 * chance to say goodbye (browser crash, Task Manager kill). A failed ping is
 * not an error worth showing the user.
 *
 * @returns {Promise<boolean>} True when the ping reached the server.
 */
export async function heartbeat() {
  try {
    await post('/api/auth/heartbeat', {});
    return true;
  } catch (err) {
    return false;
  }
}

/**
 * Ask the server to shut down.
 *
 * The server arms a 3 s grace window rather than exiting immediately, so an
 * accidental Ctrl+R is harmless: the reloaded page's first request cancels it.
 *
 * @returns {Promise<Object>}
 */
export function bye() {
  return post('/api/system/bye', {});
}

/** Default export, for call sites that prefer `api.get(...)`. */
export default {
  ApiError,
  LICENSE_REQUIRED,
  AUTH_CODES,
  request,
  requestWithMeta,
  get,
  getWithMeta,
  post,
  put,
  patch,
  del,
  remove,
  upload,
  download,
  saveBlob,
  svg,
  activate,
  bootstrap,
  licenseStatus,
  licenseDetail,
  login,
  logout,
  heartbeat,
  bye,
  setHandlers,
  idleFor,
  toQuery,
  filenameFrom,
};
