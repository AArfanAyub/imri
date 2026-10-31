/**
 * Issue & return — the counter.
 *
 * This is the screen a librarian uses fifty times a day, so it is built around
 * one rule: **the scan field always has focus**. A barcode scanner is a keyboard
 * that types the code and presses Enter; if focus has drifted, the scan goes
 * nowhere and the librarian has to reach for the mouse, which is the failure
 * this screen exists to prevent.
 *
 * Flow, issue:
 *   scan copy  -> the copy resolves and is shown
 *   scan member-> the member resolves, the issue fires immediately
 * A "not found" never clears the field — the usual cause is a mis-scan, and the
 * fix is to scan again, not to retype.
 *
 * Flow, return:
 *   scan copy  -> the loan closes; if it was late, the fine is shown with the
 *                 option to take payment on the spot.
 */

import { t, fmtNum, fmtDate, fmtMoney } from '../i18n.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import * as store from '../store.js';

/**
 * Render the counter.
 *
 * @param {{query: Object, navigate: Function}} ctx Router context.
 * @returns {{element: HTMLElement, destroy: () => void}}
 */
export function render(ctx) {
  const mode = (ctx.query && ctx.query.mode) === 'return' ? 'return' : 'issue';

  let destroyed = false;
  /** The copy resolved from the last scan. */
  let copy = null;
  /** The member resolved from the last scan (issue mode only). */
  let member = null;
  /** Recent transactions, newest first. */
  let recent = [];
  let busy = false;

  // -- header ------------------------------------------------------------ //
  const title = ui.el('h1', { class: 'page__title', text: t('circ.title') });
  const subtitle = ui.el('p', { class: 'page__sub', text: t('circ.subtitle') });

  const modeTabs = ui.tabs({
    value: mode,
    items: [
      { value: 'issue', label: t('circ.issue') },
      { value: 'return', label: t('circ.return') },
    ],
    onChange: (value) => ctx.navigate(`/counter?mode=${value}`),
  });

  const head = ui.el('div', { class: 'page__head' },
    ui.el('div', { class: 'page__heading' }, title, subtitle));

  // -- scan fields ------------------------------------------------------- //
  const copyInput = ui.input({
    placeholder: mode === 'issue' ? t('circ.scanCopy') : t('circ.scanCopy'),
    large: true,
    mono: true,
    onEnter: () => onCopyEnter(),
  });
  copyInput.id = 'counter-copy';
  copyInput.setAttribute('aria-label', t('circ.scanCopy'));

  const memberInput = ui.input({
    placeholder: t('circ.scanMember'),
    large: true,
    mono: true,
    onEnter: () => onMemberEnter(),
  });
  memberInput.id = 'counter-member';
  memberInput.setAttribute('aria-label', t('circ.scanMember'));

  // -- panels ------------------------------------------------------------ //
  const copyPanel = ui.el('div', { class: 'counter__slot' });
  const memberPanel = ui.el('div', { class: 'counter__slot' });

  const scanCard = ui.card({
    title: mode === 'issue' ? t('circ.issue') : t('circ.return'),
    body: ui.el('div', { class: 'u-col', style: { gap: '14px' } },
      ui.field({
        label: mode === 'issue' ? t('circ.scanCopy') : t('circ.scanCopy'),
        control: copyInput,
      }),
      copyPanel,
      mode === 'issue'
        ? ui.field({ label: t('circ.scanMember'), control: memberInput })
        : null,
      memberPanel),
  });

  const recentCard = ui.card({
    title: t('circ.active'),
    flush: true,
    body: ui.el('div', { class: 'u-col' }),
  });
  const recentBody = recentCard.querySelector('.u-col');

  const grid = ui.el('div', { class: 'counter__grid' },
    scanCard,
    recentCard);

  const root = ui.el('div', { class: 'u-col', style: { gap: '16px' } },
    head, modeTabs, grid);

  // -- helpers ----------------------------------------------------------- //
  /** Keep the caret in the scan field unless a dialog has taken over. */
  function refocus() {
    if (destroyed || ui.hasOpenLayer()) return;
    const target = mode === 'issue' && copy && !member ? memberInput : copyInput;
    window.requestAnimationFrame(() => target.focus());
  }

  function resetCopy() {
    copy = null;
    ui.clear(copyPanel);
    copyPanel.appendChild(ui.el('p', { class: 'u-sm u-muted', text: t('circ.scanCopy') }));
  }

  function resetMember() {
    member = null;
    ui.clear(memberPanel);
  }

  function showCopyError(message) {
    ui.clear(copyPanel);
    copyPanel.appendChild(ui.banner({ text: message }));
    copyInput.setAttribute('aria-invalid', 'true');
  }

  function showMemberError(message) {
    ui.clear(memberPanel);
    memberPanel.appendChild(ui.banner({ text: message }));
    memberInput.setAttribute('aria-invalid', 'true');
  }

  function copyCard(record) {
    const book = record.book || {};
    const copyRecord = record.copy || {};
    const status = copyRecord.status || book.status;
    return ui.el('div', { class: 'picked' },
      ui.el('div', { class: 'picked__main' },
        ui.el('span', { class: 'picked__title', text: book.title || t('common.na') }),
        ui.el('span', { class: 'picked__sub', text: [book.authors, copyRecord.barcode || copyRecord.accession_no].filter(Boolean).join(' · ') }),
        ui.el('span', { class: 'picked__meta', text: [book.call_no, book.dewey].filter(Boolean).join('  ·  ') })),
      ui.el('div', { class: 'picked__side' },
        ui.statusPill(status),
        copyRecord.copy_no ? ui.el('span', { class: 'u-xs u-muted', text: `${t('cat.col.copies')} #${copyRecord.copy_no}` }) : null));
  }

  function memberCard(record) {
    const atLimit = (record.active_loans || 0) >= store.maxLoans();
    return ui.el('div', { class: 'picked' },
      ui.el('div', { class: 'picked__main' },
        ui.el('span', { class: 'picked__title', text: record.name }),
        ui.el('span', { class: 'picked__sub', text: [record.member_code, record.class_name].filter(Boolean).join(' · ') }),
        ui.el('span', {
          class: `picked__meta${atLimit ? ' u-warn-text' : ''}`,
          text: `${fmtNum(record.active_loans || 0)} / ${fmtNum(store.maxLoans())}`,
        })),
      ui.el('div', { class: 'picked__side' },
        ui.statusPill(record.status),
        Number(record.outstanding) > 0
          ? ui.pill(fmtMoney(record.outstanding), 'warning')
          : null));
  }

  // -- scan handling ----------------------------------------------------- //
  async function onCopyEnter() {
    const code = copyInput.value.trim();
    if (!code || busy) return;
    busy = true;

    try {
      const record = await api.get('/api/books/lookup', { barcode: code });
      if (destroyed) return;
      copy = record;
      resetMember();
      copyInput.removeAttribute('aria-invalid');
      ui.clear(copyPanel);
      copyPanel.appendChild(copyCard(record));

      if (mode === 'return') {
        copyInput.value = '';
        await doReturn(record);
      } else {
        // The member scan is the next step; leave the code visible so a
        // mis-scan can be corrected without retyping.
        copyInput.value = '';
        memberInput.focus();
      }
    } catch (failure) {
      if (destroyed) return;
      copy = null;
      showCopyError(ui.formatError(failure).message);
      copyInput.select();
    } finally {
      busy = false;
      refocus();
    }
  }

  async function onMemberEnter() {
    const code = memberInput.value.trim();
    if (!code || busy) return;
    if (!copy) {
      showMemberError(t('circ.copyBlocked'));
      copyInput.focus();
      return;
    }
    busy = true;

    try {
      const page = await api.get('/api/members', { q: code, page: 1, page_size: 5 });
      const items = page.items || [];
      const match = items.find((row) => String(row.member_code).toLowerCase() === code.toLowerCase()) || items[0];
      if (!match) {
        showMemberError(t('common.noResults'));
        memberInput.select();
        return;
      }
      member = match;
      memberInput.removeAttribute('aria-invalid');
      ui.clear(memberPanel);
      memberPanel.appendChild(memberCard(match));
      memberInput.value = '';
      await doIssue();
    } catch (failure) {
      if (destroyed) return;
      showMemberError(ui.formatError(failure).message);
      memberInput.select();
    } finally {
      busy = false;
      refocus();
    }
  }

  // -- transactions ------------------------------------------------------ //
  async function doIssue() {
    const accession = (copy.copy && (copy.copy.accession_no || copy.copy.barcode))
      || (copy.book && copy.book.accession_no);
    const copyId = copy.copy && copy.copy.id;

    try {
      const result = await api.post('/api/circulation/issue', copyId
        ? { copy_id: copyId, member_id: member.id }
        : { accession_no: accession, member_code: member.member_code });

      const loan = (result && result.loan) || {};
      pushRecent({
        kind: 'issue',
        title: (copy.book && copy.book.title) || '',
        name: member.name,
        detail: t('circ.loan.due') + ' ' + fmtDate(loan.due_date),
      });
      ui.toast.success(t('circ.receipt.issue', { name: member.name }),
        `${(copy.book && copy.book.title) || ''} · ${t('circ.loan.due')} ${fmtDate(loan.due_date)}`);
      resetCopy();
      resetMember();
    } catch (failure) {
      showMemberError(ui.formatError(failure).message);
      ui.toast.error(t('toast.error'), ui.formatError(failure).message);
      resetCopy();
    }
  }

  async function doReturn(record) {
    const copyId = record.copy && record.copy.id;
    const loanId = record.copy && record.copy.current_loan_id;
    const barcode = (record.copy && record.copy.barcode) || record.copy_barcode;

    try {
      const result = await api.post('/api/circulation/return', loanId
        ? { loan_id: loanId }
        : (copyId ? { copy_id: copyId } : { copy_barcode: barcode }));

      const loan = (result && result.loan) || {};
      const fine = Number(loan.fine_amount) || 0;
      const promoted = result && result.promoted_reservation;

      pushRecent({
        kind: 'return',
        title: (record.book && record.book.title) || '',
        name: (result && result.member && result.member.name) || '',
        detail: fine ? t('circ.receipt.fine', { amount: fmtMoney(fine) }) : '',
      });

      if (fine > 0) {
        openFineDialog(loan, record);
      } else {
        ui.toast.success(t('circ.return'), (record.book && record.book.title) || '');
      }
      if (promoted) {
        ui.toast.info(t('res.ready'), t('res.forTitle', { title: (record.book && record.book.title) || '' }));
      }
      resetCopy();
    } catch (failure) {
      showCopyError(ui.formatError(failure).message);
    }
  }

  function pushRecent(entry) {
    recent.unshift(entry);
    if (recent.length > 12) recent.pop();
    paintRecent();
  }

  function paintRecent() {
    ui.clear(recentBody);
    if (!recent.length) {
      recentBody.appendChild(ui.emptyState({
        icon: 'swap',
        title: t('circ.empty.title'),
        body: t('circ.empty.body'),
      }));
      return;
    }
    for (const entry of recent) {
      recentBody.appendChild(ui.el('div', { class: 'recent' },
        ui.el('span', { class: `recent__dot recent__dot--${entry.kind}` }),
        ui.el('div', { class: 'recent__main' },
          ui.el('span', { class: 'recent__title', text: entry.title }),
          ui.el('span', { class: 'recent__sub', text: [entry.name, entry.detail].filter(Boolean).join(' · ') })),
        ui.el('span', { class: 'recent__kind', text: entry.kind === 'issue' ? t('circ.issue') : t('circ.return') })));
    }
  }

  // -- fine dialog ------------------------------------------------------- //
  function openFineDialog(loan, record) {
    const balance = Math.max(0, Number(loan.fine_amount || 0) - Number(loan.paid_amount || 0) - Number(loan.waived_amount || 0));
    const amountInput = ui.input({ value: balance.toFixed(2), type: 'number', min: '0', step: '0.01', mono: true });
    const methodSelect = ui.select({
      value: 'cash',
      options: [
        { value: 'cash', label: t('circ.fine.methodCash') },
        { value: 'other', label: t('circ.fine.methodOther') },
      ],
    });

    const payButton = ui.button({
      label: t('circ.fine.pay'),
      variant: 'primary',
      icon: 'check',
      onClick: async () => {
        const amount = Number(amountInput.value);
        if (!Number.isFinite(amount) || amount <= 0) {
          ui.toast.error(t('toast.error'), t('validate.number'));
          return;
        }
        payButton.disabled = true;
        try {
          await api.post(`/api/loans/${encodeURIComponent(String(loan.id))}/payments`, {
            amount,
            method: methodSelect.value,
          });
          ui.toast.success(t('circ.fine.paid'), fmtMoney(amount));
          dialog.close();
        } catch (failure) {
          ui.toast.error(t('toast.error'), ui.formatError(failure).message);
        } finally {
          payButton.disabled = false;
        }
      },
    });

    const dialog = ui.modal({
      title: t('circ.fine.title'),
      body: ui.el('div', { class: 'u-col', style: { gap: '14px' } },
        ui.el('div', { class: 'stat-row' },
          ui.el('div', { class: 'stat-mini' },
            ui.el('span', { class: 'stat-mini__label', text: t('circ.loan.title') }),
            ui.el('span', { class: 'stat-mini__value', text: (record.book && record.book.title) || '' })),
          ui.el('div', { class: 'stat-mini' },
            ui.el('span', { class: 'stat-mini__label', text: t('circ.fine.accrued') }),
            ui.el('span', { class: 'stat-mini__value u-warn-text', text: fmtMoney(loan.fine_amount) }))),
        ui.field({ label: t('circ.fine.amount'), control: amountInput }),
        ui.field({ label: t('circ.fine.method'), control: methodSelect })),
      footer: ui.el('div', { class: 'u-row u-gap-8' },
        ui.button({ label: t('common.close'), onClick: () => dialog.close() }),
        payButton),
    });

    window.requestAnimationFrame(() => amountInput.select());
  }

  // -- lifecycle --------------------------------------------------------- //
  resetCopy();
  resetMember();
  paintRecent();

  const onLang = () => {
    title.textContent = t('circ.title');
    subtitle.textContent = t('circ.subtitle');
    copyInput.setAttribute('placeholder', t('circ.scanCopy'));
    memberInput.setAttribute('placeholder', t('circ.scanMember'));
    paintRecent();
  };
  document.addEventListener('imri:lang', onLang);

  // A click anywhere that is not a control returns the caret to the scan field.
  const onClick = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest('input, select, textarea, button, a, [role="dialog"]')) return;
    refocus();
  };
  document.addEventListener('click', onClick);

  window.requestAnimationFrame(() => copyInput.focus());

  return {
    element: root,
    destroy() {
      destroyed = true;
      document.removeEventListener('imri:lang', onLang);
      document.removeEventListener('click', onClick);
    },
  };
}

export default { render };
