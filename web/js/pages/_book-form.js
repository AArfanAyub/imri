/**
 * The book editor.
 *
 * One modal serves "Add book" from the catalogue and "Edit" from the record
 * drawer. It is deliberately thin on client-side rules: the server owns
 * validation and returns a `fields` map (`422`), which is rendered under the
 * exact input it belongs to. The only checks made here are the two that need no
 * round trip — an ISBN check digit and a Dewey shape — and even those only
 * produce a hint until the field loses focus.
 *
 * The accession number is never suggested by the client. Leaving it blank lets
 * the server allocate the next free `<prefix>-NNNNNN`, which is the same
 * guarantee without guessing at a number that might already be taken.
 */

import { t } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';

/** Material types the schema accepts, in the order a catalogue lists them. */
const MATERIAL_TYPES = ['book', 'textbook', 'reference', 'magazine', 'av', 'kit', 'thesis', 'other'];

/** Classification schemes. */
const SCHEMES = ['ddc', 'lcc', 'local'];

/** Languages offered by `GET /api/languages`, keyed to the catalogue. */
const LANGUAGE_CODES = ['en', 'ms', 'zh', 'ta', 'ar', 'fr', 'de', 'es', 'id', 'ja', 'ko', 'other'];

// --------------------------------------------------------------------------- //
// ISBN helpers
// --------------------------------------------------------------------------- //

/**
 * Strip an ISBN to its significant characters.
 * @param {*} raw
 * @returns {string}
 */
function isbnDigits(raw) {
  return String(raw === undefined || raw === null ? '' : raw).replace(/[^0-9Xx]/g, '').toUpperCase();
}

/**
 * @param {string} value Ten significant characters.
 * @returns {boolean}
 */
function validIsbn10(value) {
  if (value.length !== 10) return false;
  let sum = 0;
  for (let index = 0; index < 10; index += 1) {
    const char = value[index];
    const digit = char === 'X' ? 10 : Number(char);
    if (!Number.isFinite(digit)) return false;
    sum += digit * (10 - index);
  }
  return sum % 11 === 0;
}

/**
 * @param {string} value Thirteen significant characters.
 * @returns {boolean}
 */
function validIsbn13(value) {
  if (value.length !== 13) return false;
  let sum = 0;
  for (let index = 0; index < 13; index += 1) {
    const digit = Number(value[index]);
    if (!Number.isFinite(digit)) return false;
    sum += digit * (index % 2 === 0 ? 1 : 3);
  }
  return sum % 10 === 0;
}

/**
 * Convert a valid ISBN-10 to its ISBN-13 form.
 * @param {string} value
 * @returns {?string}
 */
function isbn10To13(value) {
  const core = `978${value.slice(0, 9)}`;
  let sum = 0;
  for (let index = 0; index < 12; index += 1) {
    sum += Number(core[index]) * (index % 2 === 0 ? 1 : 3);
  }
  const check = (10 - (sum % 10)) % 10;
  return `${core}${check}`;
}

/**
 * Normalise any ISBN the operator types to ISBN-13.
 * @param {*} raw
 * @returns {?string} `null` when the value is not a valid ISBN.
 */
export function toIsbn13(raw) {
  const digits = isbnDigits(raw);
  if (validIsbn13(digits)) return digits;
  if (validIsbn10(digits)) return isbn10To13(digits);
  return null;
}

// --------------------------------------------------------------------------- //
// The editor
// --------------------------------------------------------------------------- //

/**
 * Open the editor.
 *
 * @param {Object} options
 * @param {'create'|'edit'} [options.mode]
 * @param {Object} [options.book] Existing record for edit mode.
 * @param {Object} [options.subjects] Existing subject headings.
 * @param {() => void} [options.onSaved] Called after a successful save.
 */
export function openBookForm(options) {
  const opts = options || {};
  const editing = opts.mode === 'edit';
  const book = opts.book || {};
  const controls = {};
  let dirty = false;
  let busy = false;
  let duplicateTimer = null;

  const form = ui.el('form', {
    class: 'form-grid',
    attrs: { novalidate: 'novalidate' },
    on: {
      submit: (event) => {
        event.preventDefault();
        save();
      },
      input: () => { dirty = true; },
      change: () => { dirty = true; },
    },
  });
  form.id = `book-form-${ui.nextId('f')}`;

  /**
   * Register a labelled control and remember it for error rendering.
   *
   * @param {Object} spec
   * @returns {HTMLElement} The field wrapper.
   */
  function addField(spec) {
    const control = spec.control;
    control.setAttribute('name', spec.name);
    const wrap = ui.field({
      label: spec.labelKey ? t(spec.labelKey) : '',
      hint: spec.hintKey ? t(spec.hintKey) : '',
      required: Boolean(spec.required),
      wide: Boolean(spec.wide),
      control,
    });
    controls[spec.name] = { control, wrap };
    if (spec.group) spec.group.appendChild(wrap);
    else form.appendChild(wrap);
    return wrap;
  }

  /** A titled band inside the two-column grid. */
  function addGroup(titleKey) {
    const group = ui.el('div', { class: 'form-grid__group' });
    group.appendChild(ui.el('p', { class: 'form-grid__title', text: t(titleKey) }));
    const fields = ui.el('div', { class: 'form-grid' });
    group.appendChild(fields);
    form.appendChild(group);
    return fields;
  }

  // --- identity ---------------------------------------------------------- //
  const identity = addGroup('cat.section.identity');
  const titleInput = ui.input({ value: book.title || '', autocomplete: 'off' });
  addField({ name: 'title', labelKey: 'cat.form.title', required: true, control: titleInput, group: identity });
  titleInput.addEventListener('blur', () => checkDuplicates());

  addField({
    name: 'subtitle',
    labelKey: 'cat.form.subtitle',
    control: ui.input({ value: book.subtitle || '' }),
    group: identity,
  });
  addField({
    name: 'authors',
    labelKey: 'cat.form.authors',
    control: ui.input({ value: book.authors || '' }),
    group: identity,
  });
  addField({
    name: 'subjects',
    labelKey: 'cat.form.subjects',
    hintKey: 'cat.form.subjectsHint',
    control: ui.input({ value: (opts.subjects || []).join(', ') }),
    group: identity,
  });

  const duplicateBox = ui.el('div', { class: 'dup', hidden: true });
  identity.appendChild(ui.el('div', { class: 'field--wide' }, duplicateBox));

  // --- classification ---------------------------------------------------- //
  const classification = addGroup('cat.section.classification');
  const isbnInput = ui.input({ value: book.isbn || '', autocomplete: 'off' });
  const isbnWrap = addField({
    name: 'isbn',
    labelKey: 'cat.form.isbn',
    hintKey: 'cat.form.isbnHint',
    control: isbnInput,
    group: classification,
  });
  const isbnNote = ui.el('p', { class: 'field__hint', hidden: true });
  isbnWrap.appendChild(isbnNote);
  isbnInput.addEventListener('blur', () => checkIsbn());

  addField({
    name: 'dewey',
    labelKey: 'cat.form.dewey',
    hintKey: 'cat.form.deweyHint',
    control: ui.input({ value: book.dewey || '', mono: true }),
    group: classification,
  }).querySelector('input').addEventListener('blur', (event) => checkDewey(event.target));

  addField({
    name: 'call_no',
    labelKey: 'cat.form.callNo',
    hintKey: 'cat.form.callNoHint',
    control: ui.input({ value: book.call_no || '', mono: true }),
    group: classification,
  });

  addField({
    name: 'classification',
    labelKey: 'cat.form.scheme',
    control: ui.select({
      value: book.classification || 'ddc',
      options: SCHEMES.map((code) => ({ value: code, label: code.toUpperCase() })),
    }),
    group: classification,
  });

  addField({
    name: 'material_type',
    labelKey: 'cat.form.material',
    control: ui.select({
      value: book.material_type || 'book',
      options: MATERIAL_TYPES.map((code) => ({ value: code, label: t(`type.${code}`) })),
    }),
    group: classification,
  });

  addField({
    name: 'language',
    labelKey: 'cat.form.language',
    control: ui.select({
      value: book.language || 'en',
      options: LANGUAGE_CODES.map((code) => ({ value: code, label: t(`lang.${code}`) })),
    }),
    group: classification,
  });

  // --- publication ------------------------------------------------------- //
  const publication = addGroup('cat.form.section.publication');
  addField({
    name: 'publisher',
    labelKey: 'cat.form.publisher',
    control: ui.input({ value: book.publisher || '' }),
    group: publication,
  });
  addField({
    name: 'pub_year',
    labelKey: 'cat.form.year',
    control: ui.input({ value: book.pub_year || '', inputMode: 'numeric', maxLength: 4 }),
    group: publication,
  });
  addField({
    name: 'edition',
    labelKey: 'cat.form.edition',
    control: ui.input({ value: book.edition || '' }),
    group: publication,
  });

  // --- holding ----------------------------------------------------------- //
  const holding = addGroup('cat.section.holding');
  const accessionInput = ui.input({ value: book.accession_no || '', mono: true, readOnly: editing });
  addField({
    name: 'accession_no',
    labelKey: 'cat.form.accession',
    hintKey: editing ? null : 'cat.form.accessionHint',
    control: accessionInput,
    group: holding,
  });

  let copiesInput = null;
  if (!editing) {
    copiesInput = ui.input({ value: '1', inputMode: 'numeric', min: 0, max: 500 });
    addField({
      name: 'copies',
      labelKey: 'cat.form.copies',
      hintKey: 'cat.form.copiesHint',
      control: copiesInput,
      group: holding,
    });
  }

  addField({
    name: 'location',
    labelKey: 'cat.form.location',
    control: ui.input({ value: book.location || '' }),
    group: holding,
  });
  addField({
    name: 'price',
    labelKey: 'cat.form.price',
    control: ui.input({ value: book.price === undefined || book.price === null ? '' : String(book.price), inputMode: 'decimal' }),
    group: holding,
  });

  // --- notes ------------------------------------------------------------- //
  const notes = addGroup('cat.section.notes');
  addField({
    name: 'synopsis',
    labelKey: 'cat.form.synopsis',
    wide: true,
    control: ui.textarea({ value: book.synopsis || '', rows: 3 }),
    group: notes,
  });

  // --- footer ------------------------------------------------------------ //
  const cancelButton = ui.button({
    label: t('common.cancel'),
    onClick: () => dialog.close(),
  });

  const saveButton = ui.button({
    label: editing ? t('common.saveChanges') : t('common.create'),
    variant: 'primary',
    type: 'submit',
  });
  saveButton.setAttribute('form', form.id);

  const dialog = ui.modal({
    title: editing ? t('cat.edit') : t('cat.new'),
    wide: true,
    body: form,
    footer: [cancelButton, saveButton],
    guard: () => dirty,
  });

  // ----------------------------------------------------------------------- //
  // Field-level feedback
  // ----------------------------------------------------------------------- //

  /**
   * Put a message under a named field.
   * @param {string} name
   * @param {string} message Empty string clears it.
   */
  function setFieldError(name, message) {
    const entry = controls[name];
    if (!entry) return;
    const { control, wrap } = entry;
    let node = wrap.querySelector('.field__error');
    if (!message) {
      wrap.classList.remove('field--error');
      control.removeAttribute('aria-invalid');
      if (node) node.remove();
      return;
    }
    wrap.classList.add('field--error');
    control.setAttribute('aria-invalid', 'true');
    if (!node) {
      node = ui.el('p', { class: 'field__error', attrs: { role: 'alert' } });
      wrap.appendChild(node);
    }
    node.textContent = '';
    node.appendChild(ui.icon('alertCircle', 14));
    node.appendChild(ui.el('span', { text: message }));
    if (node.id) control.setAttribute('aria-describedby', node.id);
  }

  /** Clear every message and every invalid flag. */
  function clearErrors() {
    for (const name of Object.keys(controls)) setFieldError(name, '');
  }

  /**
   * Render the server's `fields` map.
   * @param {?Object} fields
   */
  function applyServerErrors(fields) {
    if (!fields) return;
    for (const name of Object.keys(fields)) {
      setFieldError(name, t(`err.${fields[name]}`));
    }
    const first = Object.keys(fields).find((name) => controls[name]);
    if (first) controls[first].control.focus();
  }

  /** Validate the ISBN the way the server will, and hint at the stored form. */
  function checkIsbn() {
    const raw = isbnInput.value.trim();
    isbnNote.hidden = true;
    isbnNote.textContent = '';
    if (!raw) {
      setFieldError('isbn', '');
      return;
    }
    const normalised = toIsbn13(raw);
    if (!normalised) {
      setFieldError('isbn', t('validate.isbn'));
      return;
    }
    setFieldError('isbn', '');
    if (normalised !== isbnDigits(raw)) {
      isbnNote.textContent = t('cat.form.isbnNormalised', { isbn: normalised });
      isbnNote.hidden = false;
    }
  }

  /**
   * Dewey numbers are digits with at most one decimal point.
   * @param {HTMLInputElement} control
   */
  function checkDewey(control) {
    const value = control.value.trim();
    if (!value) {
      setFieldError('dewey', '');
      return;
    }
    if (!/^\d{1,3}(\.\d+)?$/.test(value)) {
      setFieldError('dewey', t('err.invalid_dewey'));
      return;
    }
    setFieldError('dewey', '');
  }

  /** Ask the server whether a title like this already exists. */
  function checkDuplicates() {
    const title = titleInput.value.trim();
    duplicateBox.hidden = true;
    duplicateBox.textContent = '';
    if (title.length < 4) return;
    if (duplicateTimer) window.clearTimeout(duplicateTimer);
    duplicateTimer = window.setTimeout(async () => {
      try {
        const query = { title };
        if (editing && book.id !== undefined) query.exclude_id = book.id;
        const found = await api.get('/api/books/duplicates', query);
        const candidates = (found && found.candidates) || [];
        if (!candidates.length) return;
        const first = candidates[0].book || {};
        duplicateBox.appendChild(ui.el('strong', { text: t('cat.form.duplicates') }));
        duplicateBox.appendChild(ui.el('span', { text: `${first.title || ''}${first.authors ? ` — ${first.authors}` : ''}` }));
        duplicateBox.hidden = false;
      } catch (err) {
        /* an advisory hint is never worth an error message */
      }
    }, 350);
  }

  // ----------------------------------------------------------------------- //
  // Save
  // ----------------------------------------------------------------------- //

  /** @returns {Object} The payload the API expects. */
  function collect() {
    const value = (name) => (controls[name] ? controls[name].control.value.trim() : '');
    const payload = {
      title: value('title'),
      subtitle: value('subtitle'),
      authors: value('authors'),
      subjects: value('subjects'),
      isbn: value('isbn'),
      dewey: value('dewey'),
      call_no: value('call_no'),
      classification: value('classification'),
      material_type: value('material_type'),
      language: value('language'),
      publisher: value('publisher'),
      pub_year: value('pub_year'),
      edition: value('edition'),
      location: value('location'),
      price: value('price'),
      synopsis: value('synopsis'),
    };
    if (!editing) {
      payload.accession_no = value('accession_no');
      payload.copies = value('copies') === '' ? 1 : Number(value('copies'));
    }
    // Empty strings mean "not recorded" for everything except the title.
    for (const key of Object.keys(payload)) {
      if (payload[key] === '' && key !== 'title') delete payload[key];
    }
    return payload;
  }

  async function save() {
    if (busy) return;
    clearErrors();

    const payload = collect();
    if (!payload.title) {
      setFieldError('title', t('err.title_required'));
      controls.title.control.focus();
      return;
    }
    if (payload.isbn && !toIsbn13(payload.isbn)) {
      setFieldError('isbn', t('validate.isbn'));
      controls.isbn.control.focus();
      return;
    }

    busy = true;
    saveButton.disabled = true;
    saveButton.setAttribute('aria-busy', 'true');
    dialog.setBusy(true);

    try {
      const saved = editing
        ? await api.patch(`/api/books/${encodeURIComponent(String(book.id))}`, payload)
        : await api.post('/api/books', payload);
      dirty = false;
      const record = (saved && saved.book) || saved || {};
      ui.toast.success(editing ? t('cat.updated') : t('cat.created'), record.title || payload.title);
      dialog.close();
      if (opts.onSaved) opts.onSaved(record);
    } catch (error) {
      if (error && error.isValidation) {
        applyServerErrors(error.fields);
        if (!error.fields) ui.toast.error(t('toast.error'), ui.formatError(error).message);
      } else if (error && error.is && error.is('duplicate_isbn')) {
        setFieldError('isbn', t('err.duplicate_isbn'));
      } else if (error && error.is && error.is('duplicate_accession')) {
        setFieldError('accession_no', t('err.duplicate_accession'));
      } else {
        ui.toast.error(t('toast.error'), ui.formatError(error).message);
      }
    } finally {
      busy = false;
      saveButton.disabled = false;
      saveButton.removeAttribute('aria-busy');
      dialog.setBusy(false);
    }
  }

  // The price placeholder shows the configured currency without typing it.
  const priceControl = controls.price && controls.price.control;
  if (priceControl) priceControl.setAttribute('placeholder', store.state.settings.currency || '');

  window.requestAnimationFrame(() => titleInput.focus());
}

export default { openBookForm, toIsbn13 };
