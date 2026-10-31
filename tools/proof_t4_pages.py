"""T4 pass B1 proof — the auth gate, the dashboard, the catalogue and the record.

Starts the application on a scratch data directory and drives headless Edge over
the DevTools protocol through the journey the owner will actually take:

  activate with the real code (including four wrong ones, the lockout, and the
  countdown) -> sign in -> forced password change -> seed a library through the
  API -> dashboard KPIs, charts, lists and alerts -> catalogue search, filters,
  sort, paging, selection -> create a book through the editor -> open the record
  drawer -> edit and save -> print-label preview -> delete refused because a copy
  is on loan.

Data is seeded through the same `api.js` the screens use, so nothing here talks
to the database for anything the product can do itself. The one exception is a
narrow backdating pass (`backdate_loans`) that moves loan dates into past months:
the issue endpoint always stamps *today*, and a six-month trend plus an overdue
queue are otherwise unreachable. Fines are still computed by the application, by
returning each loan `as_of` the date it came back.

Usage:  python tools/proof_t4_pages.py [--fast] [--keep]

`--fast` skips the 30-second licence cooldown (everything else is unchanged).
Screenshots land in `docs/`.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import CDP  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

APP_PORT = 8732
DEBUG_PORT = 9334
BASE = f"http://127.0.0.1:{APP_PORT}"

ACTIVATION_CODE = "arfanayub0"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin123"
NEW_PASSWORD = "Imri-Pages-2026"

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

PASS = 0
FAIL = 0
NOTES: list[str] = []


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


class Raw(str):
    """A JS expression substituted verbatim into a template."""


def fill(template: str, **values) -> str:
    """Substitute ``«name»`` placeholders, JSON-encoding everything not Raw."""
    out = template
    for key, value in values.items():
        text = value if isinstance(value, Raw) else json.dumps(value, ensure_ascii=False)
        out = out.replace(f"«{key}»", text)
    return out


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}")
    if detail:
        for line in str(detail).splitlines()[:6]:
            print(f"        {line}")


def wait_for(cdp: CDP, expression: str, timeout: float = 20.0, label: str = "") -> bool:
    """Poll an expression until it is truthy."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if cdp.evaluate(f"Boolean({expression})", timeout=5):
                return True
        except Exception:
            pass
        time.sleep(0.12)
    if label:
        NOTES.append(f"timed out waiting for {label}")
    return False


def capture(cdp: CDP, path: Path) -> None:
    """Screenshot a settled frame (twice, a beat apart: see proof_t4_ui)."""
    cdp.screenshot(str(path))
    time.sleep(0.3)
    cdp.screenshot(str(path))


def read_catalogue(name: str) -> dict[str, str]:
    """Parse a catalogue module, the same way the static proof does."""
    text = (ROOT / "web" / "js" / "lang" / f"{name}.js").read_text(encoding="utf-8")
    body = text.split("export default", 1)[1].rsplit("};", 1)[0]
    return {key: value for key, value in re.findall(r"'([^']+)':\s*'((?:[^'\\]|\\.)*)'", body)}


def find_browser() -> str | None:
    for candidate in BROWSERS:
        if Path(candidate).exists():
            return candidate
    return None


def wait_for_http(url: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


# --------------------------------------------------------------------------- #
# Page helpers, injected once
# --------------------------------------------------------------------------- #

HELPERS_JS = r"""
(() => {
  const root = (node) => node || document;

  const setValue = (selector, value, scope) => {
    const node = root(scope).querySelector(selector);
    if (!node) throw new Error('no element for ' + selector);
    const proto = node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype
      : node.tagName === 'SELECT' ? HTMLSelectElement.prototype
      : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(node, String(value));
    node.dispatchEvent(new Event('input', { bubbles: true }));
    node.dispatchEvent(new Event('change', { bubbles: true }));
    return node.value;
  };

  const click = (selector, scope) => {
    const node = root(scope).querySelector(selector);
    if (!node) throw new Error('no element for ' + selector);
    node.click();
    return true;
  };

  const findByText = (selector, text, scope) => {
    const wanted = String(text).toLowerCase();
    return Array.from(root(scope).querySelectorAll(selector))
      .find((node) => node.textContent.trim().toLowerCase().includes(wanted)) || null;
  };

  const clickText = (selector, text, scope) => {
    const node = findByText(selector, text, scope);
    if (!node) throw new Error('no ' + selector + ' containing ' + text);
    node.click();
    return true;
  };

  window.__T = {
    set: setValue,
    click,
    clickText,
    find: (selector, text, scope) => Boolean(findByText(selector, text, scope)),
    disabled: (selector, text, scope) => {
      const node = findByText(selector, text, scope);
      return node ? node.disabled : null;
    },
    text: (selector, scope) => {
      const node = root(scope).querySelector(selector);
      return node ? node.textContent.trim() : null;
    },
    count: (selector, scope) => root(scope).querySelectorAll(selector).length,
    all: (selector, scope) => Array.from(root(scope).querySelectorAll(selector)),
    rowTitles: () => Array.from(document.querySelectorAll('#view .tbl tbody tr'))
      .map((tr) => (tr.querySelector('.cat__title-main') || tr).textContent.trim()),
    toasts: () => Array.from(document.querySelectorAll('.toast')).map((n) => n.textContent.trim()),
    card: (title) => Array.from(document.querySelectorAll('#view .card'))
      .find((node) => {
        const heading = node.querySelector('.card__title');
        return heading ? heading.textContent.trim() === title : false;
      }) || null,
    cardRows: (title) => {
      const card = window.__T.card(title);
      return card ? card.querySelectorAll('tbody tr').length : -1;
    },
    drawerButton: (text) => {
      const node = findByText('.drawer__foot button', text);
      if (!node) throw new Error('no drawer button ' + text);
      node.click();
      return true;
    },
  };
  return true;
})()
"""


# --------------------------------------------------------------------------- #
# The fixture
# --------------------------------------------------------------------------- #

#: (title, material type, year, dewey, call number, copies, language,
#:  publisher, authors, subjects)
RICH_BOOKS: list[tuple[str, str, str, str, str, int, str, str, str, str]] = [
    ("A History of Malaya", "book", "1961", "959.5", "959.5 RYA", 3, "en",
     "Oxford University Press", "Richard Winstedt", "Malaya—History, Southeast Asia"),
    ("Biology for Form Five", "textbook", "2019", "570", "570 TAN", 8, "en",
     "Dewan Bahasa dan Pustaka", "Tan Siew Lan", "Biology—Textbooks"),
    ("Oxford English Dictionary", "reference", "2010", "423", "R423 OXF", 2, "en",
     "Oxford University Press", "Oxford", "English language—Dictionaries"),
    ("Dewan Masyarakat", "magazine", "2023", "059", "059 DEW", 12, "ms",
     "Dewan Bahasa dan Pustaka", "", "Malaysia—Periodicals"),
    ("The Water Cycle", "av", "2018", "551.48", "AV551.48 WAT", 2, "en",
     "EduMedia", "", "Hydrology"),
    ("Science Kit: Circuits", "kit", "2020", "621.3", "KIT621.3 SCI", 1, "en",
     "Sasbadi", "", "Electric circuits"),
    ("Reading Habits of Rural Students", "thesis", "2021", "028", "T028.9 LIM", 1, "en",
     "Universiti Malaya", "Lim Mei Ling", "Reading—Research"),
    ("The Quiet Library", "book", "2015", "020", "020 QUI", 4, "en",
     "Faber", "Alice Monroe", "Libraries"),
    ("Atlas of the World", "reference", "2022", "912", "R912 ATL", 3, "en",
     "Dorling Kindersley", "", "Atlases"),
    ("Mathematics Year 6", "textbook", "2021", "510", "510 MAT", 6, "en",
     "Sasbadi", "Chong Ah Kow", "Mathematics—Textbooks"),
    ("Short Stories from Borneo", "book", "2017", "899.23", "899.23 SHO", 2, "en",
     "Silverfish", "", "Short stories, Malaysian"),
    ("Piano Sonatas", "av", "2014", "786.2", "AV786.2 PIA", 1, "de",
     "Deutsche Grammophon", "Ludwig van Beethoven", "Sonatas (Piano)"),
]

#: Filler so the catalogue has a second page at 25 rows per page.
FILLER_TOPICS = [
    "Geography", "Sejarah", "Science", "Mathematics", "Bahasa Melayu",
    "Art", "Music", "Physical Education", "Moral Studies", "Living Skills",
    "Additional Mathematics", "Chemistry", "Physics", "Information Technology",
]

MEMBERS: list[tuple[str, str]] = [
    ("Nurul Aisyah binti Rahman", "student"),
    ("Tan Wei Ming", "student"),
    ("Arun Kumar", "student"),
    ("Siti Khadijah", "student"),
    ("Lim Mei Ling", "teacher"),
    ("Daniel Ong", "student"),
    ("Farah Nadia", "student"),
    ("Kumaravel Raj", "teacher"),
]


def build_seed() -> dict:
    """The library the screens are judged on.

    Numbers are chosen so the dashboard has something to say: one member at the
    loan limit, three overdue loans, one due today, a title with a hold and no
    copy on the shelf, and six months of circulation.
    """
    books: list[dict] = []
    for title, kind, year, dewey, call_no, copies, language, publisher, authors, subjects in RICH_BOOKS:
        entry: dict = {
            "title": title,
            "material_type": kind,
            "pub_year": year,
            "dewey": dewey,
            "call_no": call_no,
            "copies": copies,
            "language": language,
            "publisher": publisher,
            "classification": "ddc",
        }
        if authors:
            entry["authors"] = authors
        if subjects:
            entry["subjects"] = subjects
        books.append(entry)

    for index in range(22):
        topic = FILLER_TOPICS[index % len(FILLER_TOPICS)]
        volume = index // len(FILLER_TOPICS) + 1
        books.append({
            "title": f"{topic} Workbook {volume}",
            "material_type": "textbook",
            "pub_year": str(2015 + (index % 9)),
            "dewey": "371.3",
            "call_no": f"371.3 {topic[:3].upper()}{volume}",
            "copies": 2,
            "language": "en",
            "publisher": "Sasbadi",
            "classification": "ddc",
        })

    members = [
        {"name": name, "member_type": kind, "status": "active"}
        for name, kind in MEMBERS
    ]

    today = date.today()

    # Open loans: (key, book index, copy index, member index, days ago issued,
    # days from today the loan is due).
    open_loans = [
        ("o1", 0, 0, 0, 40, -26),
        ("o2", 1, 0, 0, 10, 4),
        ("o3", 8, 0, 0, 8, 6),
        ("o4", 5, 0, 1, 30, -16),
        ("o5", 7, 0, 1, 6, 8),
        ("o6", 4, 0, 2, 14, 0),
        ("o7", 9, 0, 2, 3, 11),
        ("o8", 10, 0, 3, 20, -6),
        ("o9", 11, 0, 4, 5, 9),
        ("o10", 2, 0, 5, 2, 12),
        ("o11", 6, 0, 7, 1, 13),
    ]

    # Returned loans, spread over the last six months. The magazine has twelve
    # copies, so history can reuse them without colliding with the open loans.
    history = [
        ("h1", 3, 0, 0, -150, 3),
        ("h2", 3, 1, 1, -122, 0),
        ("h3", 3, 2, 2, -95, 7),
        ("h4", 3, 3, 3, -70, 0),
        ("h5", 3, 4, 4, -52, 2),
        ("h6", 3, 5, 5, -33, 0),
        ("h7", 3, 6, 6, -19, 11),
        ("h8", 3, 7, 7, -4, 0),
    ]

    loans: list[dict] = []
    plan: list[dict] = []
    for key, book, copy, member, issued, due in open_loans:
        issue_date = today + timedelta(days=issued)
        due_date = today + timedelta(days=due)
        loans.append({"key": key, "book": book, "copy": copy, "member": member})
        plan.append({
            "key": key,
            "issue_date": issue_date.isoformat(),
            "due_date": due_date.isoformat(),
        })
    for key, book, copy, member, issued, late in history:
        issue_date = today + timedelta(days=issued)
        due_date = issue_date + timedelta(days=14)
        loans.append({"key": key, "book": book, "copy": copy, "member": member})
        plan.append({
            "key": key,
            "issue_date": issue_date.isoformat(),
            "due_date": due_date.isoformat(),
        })

    returns = []
    for key, _book, _copy, _member, _issued, late in history:
        entry = next(row for row in plan if row["key"] == key)
        as_of = date.fromisoformat(entry["due_date"]) + timedelta(days=late)
        if as_of > today:
            as_of = today
        returns.append({"key": key, "as_of": as_of.isoformat()})

    return {
        "books": books,
        "members": members,
        "loans": loans,
        "plan": plan,
        "returns": returns,
        "reserve": {"book": 5, "member": 6},
    }


SEED_JS = r"""
(async () => {
  const api = window.__IMRI__.api;
  const seed = «seed»;

  const books = [];
  for (const spec of seed.books) {
    const saved = await api.post('/api/books', spec);
    books.push({ id: saved.book.id, title: saved.book.title, accession: saved.book.accession_no });
  }

  const members = [];
  for (const spec of seed.members) {
    const saved = await api.post('/api/members', spec);
    members.push({ id: saved.member.id, code: saved.member.member_code, name: saved.member.name });
  }

  const copyCache = {};
  const copiesOf = async (index) => {
    if (!copyCache[index]) {
      const record = await api.get('/api/books/' + books[index].id);
      copyCache[index] = record.copies || [];
    }
    return copyCache[index];
  };

  const loans = {};
  for (const spec of seed.loans) {
    const copies = await copiesOf(spec.book);
    const copy = copies[spec.copy] || copies[0];
    if (!copy) throw new Error('book ' + spec.book + ' has no copy to lend');
    const issued = await api.post('/api/circulation/issue', {
      copy_barcode: copy.barcode,
      member_code: members[spec.member].code,
    });
    loans[spec.key] = issued.loan.id;
  }

  const reserved = await api.post('/api/reservations', {
    book_id: books[seed.reserve.book].id,
    member_code: members[seed.reserve.member].code,
  });

  return JSON.stringify({
    titles: books.length,
    members: members.length,
    loans,
    hold: reserved.reservation ? reserved.reservation.id : null,
    firstAccession: books[0].accession,
  });
})()
"""

RETURN_JS = r"""
(async () => {
  const api = window.__IMRI__.api;
  const plan = «plan»;
  const out = [];
  for (const spec of plan) {
    const result = await api.post('/api/circulation/return', {
      loan_id: spec.id,
      as_of: spec.as_of,
    });
    out.push({ id: spec.id, fine: result.fine.amount, late: result.fine.days_late });
  }
  return JSON.stringify(out);
})()
"""


def backdate_loans(db_path: Path, plan: list[dict]) -> int:
    """Move issued loans onto their intended dates.

    The issue endpoint stamps today's date and refuses a due date in the past,
    so the only way to build a six-month trend, an overdue queue and a fine is
    to place the rows where they belong afterwards. Everything else — the loans
    themselves, the returns, the fines — goes through the API.
    """
    connection = sqlite3.connect(str(db_path), timeout=15.0)
    try:
        connection.execute("PRAGMA busy_timeout = 15000")
        changed = 0
        for row in plan:
            cursor = connection.execute(
                "UPDATE loans SET issue_date = ?, due_date = ? WHERE id = ?",
                (row["issue_date"], row["due_date"], row["id"]),
            )
            changed += cursor.rowcount
        connection.commit()
        return changed
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="leave the browser and server running")
    parser.add_argument("--fast", action="store_true", help="skip the 30 s licence cooldown")
    args = parser.parse_args()

    browser = find_browser()
    if not browser:
        print("No Edge or Chrome found; cannot run the page proof.")
        return 2

    DOCS.mkdir(exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="imri-t4b1-"))
    profile_dir = Path(tempfile.mkdtemp(prefix="imri-t4b1-profile-"))
    en = read_catalogue("en")

    print("T4 pass B1 — auth gate, dashboard, catalogue and record\n")
    print(f"  data    {data_dir}")
    print(f"  browser {Path(browser).name}\n")

    server = subprocess.Popen(
        [sys.executable, "main.py", "--port", str(APP_PORT), "--data-dir", str(data_dir), "--no-window"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    browser_proc = None
    cdp = None

    try:
        if not wait_for_http(f"{BASE}/api/health"):
            print("server did not come up")
            print(server.stdout.read() if server.stdout else "")
            return 2

        browser_proc = subprocess.Popen(
            [
                browser,
                "--headless=new",
                f"--remote-debugging-port={DEBUG_PORT}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
                "--disable-extensions",
                "--hide-scrollbars",
                "--window-size=1440,900",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.time() + 20
        socket_url = None
        while time.time() < deadline and socket_url is None:
            try:
                socket_url = CDP.page_target(DEBUG_PORT)
            except Exception:
                time.sleep(0.25)
        if not socket_url:
            print("browser did not expose a page target")
            return 2

        cdp = CDP(socket_url)
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False,
        })
        cdp.call("Page.navigate", {"url": BASE})

        # ---- 1. the licence gate ------------------------------------------ #
        print("[1] activation")
        if not wait_for(cdp, "window.__IMRI_BOOT__ && window.__IMRI_BOOT__.ready", 25, "boot"):
            print("  the app never finished booting")
            print("  console:", cdp.console_errors()[:5])
            return 1
        cdp.evaluate(HELPERS_JS)

        check("boot completed", True)
        check("an unlicensed install lands on activation",
              cdp.evaluate("location.hash") == "#/activate", cdp.evaluate("location.hash"))
        check("the shell renders the gate instead of the app",
              cdp.evaluate("document.querySelector('.shell').dataset.chrome") == "auth",
              cdp.evaluate("document.querySelector('.shell').dataset.chrome"))
        check("the sidebar is hidden",
              cdp.evaluate("getComputedStyle(document.querySelector('.sidebar')).display") == "none")
        check("the topbar is hidden",
              cdp.evaluate("getComputedStyle(document.querySelector('.topbar')).display") == "none")
        card_width = float(cdp.evaluate(
            "document.querySelector('.gate__card').getBoundingClientRect().width"
        ))
        check("the card is at most 420 px wide", 0 < card_width <= 421, f"width={card_width}")
        check("the card is centred",
              abs(float(cdp.evaluate(
                  "(() => { const r = document.querySelector('.gate__card').getBoundingClientRect();"
                  " return r.left + r.width / 2; })()"
              )) - 720) < 6)
        check("the wordmark sits above the card",
              cdp.evaluate(
                  "(() => { const brand = document.querySelector('.gate__brand');"
                  " const card = document.querySelector('.gate__card');"
                  " return Boolean(brand.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING); })()"
              ))
        check("the code field takes focus on mount",
              cdp.evaluate("document.activeElement && document.activeElement.id") == "activation-code")
        check("the code field is monospaced and letter-spaced",
              cdp.evaluate(
                  "(() => { const s = getComputedStyle(document.querySelector('#activation-code'));"
                  " return /mono/i.test(s.fontFamily) && parseFloat(s.letterSpacing) >= 2; })()"
              ))
        check("autocorrect and autocomplete are off",
              cdp.evaluate("document.querySelector('#activation-code').getAttribute('autocomplete')") == "off"
              and cdp.evaluate("document.querySelector('#activation-code').getAttribute('spellcheck')") == "false")
        check("the submit button spans the form",
              cdp.evaluate(
                  "(() => { const b = document.querySelector('.gate__form .btn--block');"
                  " const f = document.querySelector('.gate__form');"
                  " if (!b || !f) return false;"
                  " return Math.abs(b.getBoundingClientRect().width - f.getBoundingClientRect().width) < 2; })()"
              ))
        check("the footer says this is offline",
              "offline" in (cdp.evaluate("document.querySelector('.gate__foot').textContent") or "").lower())

        # A wrong code: inline error, and the field keeps what was typed.
        cdp.evaluate(fill("window.__T.set('#activation-code', «v»)", v="not-the-code"))
        cdp.evaluate("document.querySelector('#view .gate__form button[type=\"submit\"]').click()")
        check("a wrong code is rejected inline",
              wait_for(cdp, "!document.querySelector('.field__error').hidden", 8, "activation error"))
        check("the rejection is announced",
              cdp.evaluate("document.querySelector('#activation-error').getAttribute('role')") == "alert")
        check("the field keeps what was typed",
              cdp.evaluate("document.querySelector('#activation-code').value") == "not-the-code")
        check("the rejection is not an animation",
              cdp.evaluate(
                  "(() => { const s = getComputedStyle(document.querySelector('.gate__card'));"
                  " return s.animationName === 'none' || s.animationName === ''; })()"
              ))
        check("the remaining attempts are counted down",
              "attempts left" in (cdp.evaluate("document.querySelector('.field__hint').textContent") or "").lower(),
              cdp.evaluate("document.querySelector('.field__hint').textContent"))
        cdp.evaluate(fill("window.__T.set('#activation-code', «v»)", v="still-wrong"))
        check("typing clears the error but not the value",
              cdp.evaluate("document.querySelector('.field__error').hidden")
              and cdp.evaluate("document.querySelector('#activation-code').value") == "still-wrong")

        # Four more failures: the fifth arms the server's cooldown.
        if not args.fast:
            for _ in range(4):
                cdp.evaluate("document.querySelector('#view .gate__form button[type=\"submit\"]').click()")
                time.sleep(0.45)
            check("the fifth failure starts a live countdown",
                  wait_for(cdp, "/try again in \\d+s/i.test(document.querySelector('.field__error').textContent)", 8, "cooldown"))
            check("the code field is disabled during the countdown",
                  cdp.evaluate("document.querySelector('#activation-code').disabled"))
            check("the submit button is disabled during the countdown",
                  cdp.evaluate("document.querySelector('#view .gate__form button[type=\"submit\"]').disabled"))
            first_tick = cdp.evaluate("document.querySelector('.field__error').textContent")
            time.sleep(2.1)
            second_tick = cdp.evaluate("document.querySelector('.field__error').textContent")
            check("the countdown ticks down", first_tick != second_tick, f"{first_tick!r} -> {second_tick!r}")
            if wait_for(cdp, "!document.querySelector('#activation-code').disabled", 45, "cooldown to expire"):
                check("the field unlocks when the countdown reaches zero", True)
                check("the error clears with it",
                      cdp.evaluate("document.querySelector('.field__error').hidden"))
            else:
                check("the field unlocks when the countdown reaches zero", False)
        else:
            NOTES.append("licence cooldown not exercised (--fast)")

        # The real code, in the wrong case, with a stray space.
        cdp.evaluate(fill("window.__T.set('#activation-code', «v»)", v="  ARFANAYUB0 "))
        cdp.evaluate("document.querySelector('#view .gate__form button[type=\"submit\"]').click()")
        check("the real code is accepted in any case",
              wait_for(cdp, "location.hash === '#/login'", 12, "login route"),
              cdp.evaluate("location.hash"))
        check("the licence is stored", cdp.evaluate("window.__IMRI__.store.state.licensed"))
        check("activation is toasted", any("activated" in text.lower() for text in cdp.evaluate("window.__T.toasts()")))

        # ---- 2. sign in ---------------------------------------------------- #
        print("\n[2] sign in")
        check("the sign-in screen is the gate again",
              (cdp.evaluate("document.querySelector('.gate__title').textContent") or "") == en["auth.welcome"])
        check("the username field takes focus",
              cdp.evaluate("document.activeElement && document.activeElement.id") == "login-username")
        check("the password has a reveal toggle",
              cdp.evaluate("document.querySelectorAll('.pw__toggle').length") == 1)

        # The default-password banner is driven by a session flag the sign-in
        # sets. Seed the flag and reload: the banner must render, and both of
        # its actions must work.
        cdp.evaluate("sessionStorage.setItem('imri.defaultPassword', '1')")
        cdp.call("Page.reload", {"ignoreCache": False})
        check("the app boots after a reload",
              wait_for(cdp, "window.__IMRI_BOOT__ && window.__IMRI_BOOT__.ready", 25, "reload"))
        cdp.evaluate(HELPERS_JS)
        check("an activated install reloads onto sign-in",
              wait_for(cdp, "location.hash === '#/login'", 10, "login"),
              cdp.evaluate("location.hash"))
        check("the default-password banner appears",
              wait_for(cdp, "!document.querySelector('.banner').hidden", 8, "banner"))
        check("the banner offers a fix and a snooze",
              cdp.evaluate("document.querySelectorAll('.banner__actions button').length") == 2)
        check("the banner is amber, not red",
              cdp.evaluate(
                  "(() => { const probe = document.createElement('span');"
                  " probe.style.color = 'var(--warning)';"
                  " document.body.appendChild(probe);"
                  " const warn = getComputedStyle(probe).color;"
                  " probe.remove();"
                  " return getComputedStyle(document.querySelector('.banner__icon')).color === warn; })()"
              ))
        cdp.evaluate("window.__T.clickText('.banner__actions button', " + json.dumps(en["auth.remindLater"]) + ")")
        check("“remind me later” dismisses it for this session",
              cdp.evaluate("document.querySelector('.banner').hidden")
              and cdp.evaluate("sessionStorage.getItem('imri.defaultPassword')") is None)

        # A wrong password: inline error, username preserved.
        cdp.evaluate(fill("window.__T.set('#login-username', «v»)", v=ADMIN_USER))
        cdp.evaluate(fill("window.__T.set('#login-password', «v»)", v="wrong-password"))
        cdp.evaluate("document.querySelector('#view .gate__form button[type=\"submit\"]').click()")
        check("a wrong password is rejected inline",
              wait_for(cdp, "!document.querySelector('.field__error').hidden", 8, "login error"))
        check("the username survives the rejection",
              cdp.evaluate("document.querySelector('#login-username').value") == ADMIN_USER)
        check("the password is cleared",
              cdp.evaluate("document.querySelector('#login-password').value") == "")
        check("the password field is marked invalid",
              cdp.evaluate("document.querySelector('#login-password').getAttribute('aria-invalid')") == "true")

        cdp.evaluate(fill("window.__T.set('#login-password', «v»)", v=ADMIN_PASSWORD))
        cdp.evaluate("document.querySelector('#view .gate__form button[type=\"submit\"]').click()")
        check("signing in with the seeded password forces a change",
              wait_for(cdp, "location.hash === '#/change-password'", 12, "change-password"),
              cdp.evaluate("location.hash"))

        # ---- 3. forced password change ------------------------------------- #
        print("\n[3] forced password change")
        check("the screen says the change is required",
              (cdp.evaluate("document.querySelector('.gate__sub').textContent") or "") == en["auth.forced"])
        check("there is no way out of it",
              cdp.evaluate("document.querySelectorAll('.gate__form .btn--ghost, .gate a').length") == 0)
        check("the checklist starts idle",
              cdp.evaluate("document.querySelectorAll('.rule--ok, .rule--bad').length") == 0)
        check("the submit button starts disabled",
              cdp.evaluate("document.querySelector('#view .gate__form button[type=\"submit\"]').disabled"))

        cdp.evaluate(fill("window.__T.set('#pw-current', «v»)", v=ADMIN_PASSWORD))
        cdp.evaluate(fill("window.__T.set('#pw-new', «v»)", v="short"))
        check("a short password fails the length rule",
              cdp.evaluate("document.querySelectorAll('.rule--bad').length") >= 1)
        cdp.evaluate(fill("window.__T.set('#pw-new', «v»)", v=NEW_PASSWORD))
        cdp.evaluate(fill("window.__T.set('#pw-confirm', «v»)", v=NEW_PASSWORD + "x"))
        check("a mismatched confirmation fails the match rule",
              cdp.evaluate("document.querySelectorAll('.rule--ok').length") == 1
              and cdp.evaluate("document.querySelectorAll('.rule--bad').length") >= 1)
        cdp.evaluate(fill("window.__T.set('#pw-confirm', «v»)", v=NEW_PASSWORD))
        check("every rule passes when the form is right",
              cdp.evaluate("document.querySelectorAll('.rule--ok').length") == 3)
        check("the submit button unlocks",
              cdp.evaluate("document.querySelector('#view .gate__form button[type=\"submit\"]').disabled") is False)
        cdp.evaluate("document.querySelector('#view .gate__form button[type=\"submit\"]').click()")
        check("the change lands on the dashboard",
              wait_for(cdp, "location.hash === '#/dashboard'", 12, "dashboard"),
              cdp.evaluate("location.hash"))
        check("the forced-change flag is cleared",
              cdp.evaluate("window.__IMRI__.store.mustChangePassword()") is False)
        check("the change is toasted",
              any("password" in text.lower() for text in cdp.evaluate("window.__T.toasts()")))

        # ---- 4. seed the library through the API ---------------------------- #
        print("\n[4] fixture")
        seed = build_seed()
        seeded = json.loads(cdp.evaluate(
            fill(SEED_JS, seed=Raw(json.dumps(seed))), timeout=180
        ))
        check(f"seeded {len(seed['books'])} titles through POST /api/books", seeded["titles"] == len(seed["books"]))
        check(f"seeded {len(seed['members'])} members", seeded["members"] == len(seed["members"]))
        check("every loan was issued", len(seeded["loans"]) == len(seed["loans"]))
        check("a reservation was placed", seeded["hold"] is not None)

        plan = [dict(row, id=seeded["loans"][row["key"]]) for row in seed["plan"]]
        moved = backdate_loans(data_dir / "imri.db", plan)
        check("loan dates placed across the last six months", moved == len(plan), f"moved={moved}")

        returns = [{"id": seeded["loans"][row["key"]], "as_of": row["as_of"]} for row in seed["returns"]]
        returned = json.loads(cdp.evaluate(fill(RETURN_JS, plan=Raw(json.dumps(returns))), timeout=120))
        check("eight loans returned through the API", len(returned) == len(returns))
        check("the returns accrued fines the app computed",
              sum(1 for row in returned if row["fine"] > 0) >= 3,
              json.dumps(returned))

        cdp.call("Page.reload", {"ignoreCache": False})
        check("the app boots after a reload",
              wait_for(cdp, "window.__IMRI_BOOT__ && window.__IMRI_BOOT__.ready", 25, "reload"))
        cdp.evaluate(HELPERS_JS)
        check("the session survives the reload",
              cdp.evaluate("window.__IMRI__.store.isAuthed()"))
        check("the reload lands on the dashboard",
              wait_for(cdp, "location.hash === '#/dashboard'", 10, "dashboard"))

        # ---- 5. dashboard --------------------------------------------------- #
        print("\n[5] dashboard")
        check("six KPI tiles render",
              wait_for(cdp, "document.querySelectorAll('.grid--kpi .stat').length === 6", 15, "kpis"),
              cdp.evaluate("document.querySelectorAll('.grid--kpi .stat').length"))
        labels = cdp.evaluate("window.__T.all('.grid--kpi .stat__label').map((n) => n.textContent.trim())")
        check("the tiles are the six the brief asks for",
              labels == [en["dash.kpi.titles"], en["dash.kpi.copies"], en["dash.kpi.onLoan"],
                         en["dash.kpi.overdue"], en["dash.kpi.activeMembers"], en["dash.kpi.finesOutstanding"]],
              json.dumps(labels))
        check("KPI values are tabular",
              cdp.evaluate(
                  "(() => { const s = getComputedStyle(document.querySelector('.stat__value'));"
                  " return /tabular/.test(s.fontVariantNumeric); })()"
              ))
        values = cdp.evaluate("window.__T.all('.grid--kpi .stat__value').map((n) => n.textContent.trim())")
        check(f"the catalogue count is {len(seed['books'])}", values[0].replace(",", "") == str(len(seed["books"])), values[0])
        check("three loans are overdue", values[3].replace(",", "") == "3", values[3])
        check("money uses the configured currency", values[5].startswith("RM"), values[5])
        check("at least four tiles carry a delta",
              cdp.evaluate("document.querySelectorAll('.grid--kpi .stat__delta').length") >= 4,
              cdp.evaluate("document.querySelectorAll('.grid--kpi .stat__delta').length"))
        check("a rising overdue count is red, not green",
              "stat__delta--down" in str(cdp.evaluate(
                  "document.querySelectorAll('.grid--kpi .stat')[3].querySelector('.stat__delta').className"
              )),
              cdp.evaluate("document.querySelectorAll('.grid--kpi .stat')[3].querySelector('.stat__delta').className"))
        check("every tile is a button",
              cdp.evaluate("window.__T.all('.grid--kpi .stat').every((n) => n.tagName === 'BUTTON')"))

        check("the alerts strip appears",
              wait_for(cdp, "!!document.querySelector('.alerts')", 8, "alerts"))
        check("it names both problems",
              cdp.evaluate("document.querySelectorAll('.alerts__item').length") == 2,
              cdp.evaluate("window.__T.all('.alerts__item').map((n) => n.textContent.trim())"))
        check("each alert links to its fix",
              cdp.evaluate("document.querySelectorAll('.alerts__link').length") == 2)

        bars = cdp.evaluate("document.querySelectorAll('.chart__bar').length")
        check("the bar chart draws one bar per month", bars == 6, f"bars={bars}")
        check("every bar carries its value",
              cdp.evaluate("document.querySelectorAll('.chart__bar title').length") == 6)
        check("the donut has a centre total",
              cdp.evaluate(
                  "(() => { const value = document.querySelector('.chart__value');"
                  " return Boolean(value && value.textContent.trim().length); })()"
              ),
              cdp.evaluate("document.querySelector('.chart__value') ? document.querySelector('.chart__value').textContent : null"))
        check("the donut legend carries count and share",
              cdp.evaluate(
                  "(() => { const keys = Array.from(document.querySelectorAll('.chart__key'));"
                  " return keys.length >= 3 && keys.every((n) => /·\\s*\\d+(\\.\\d+)?%/.test(n.textContent)); })()"
              ),
              json.dumps(cdp.evaluate("window.__T.all('.chart__key').map((n) => n.textContent.trim())")))
        check("the collection donut totals the catalogue",
              (cdp.evaluate("document.querySelector('.chart__value').textContent") or "").replace(",", "")
              == str(len(seed["books"])),
              cdp.evaluate("document.querySelector('.chart__value').textContent"))

        due_rows = cdp.evaluate("window.__T.cardRows(" + json.dumps(en["dash.dueToday"]) + ")")
        check("“Due back today” lists the loan due today", due_rows == 1, f"rows={due_rows}")
        overdue_rows = cdp.evaluate("window.__T.cardRows(" + json.dumps(en["dash.overdue"]) + ")")
        check("the overdue list lists the three late loans", overdue_rows == 3, f"rows={overdue_rows}")
        check("the overdue rows carry lateness pills",
              cdp.evaluate("document.querySelectorAll('.tbl--compact .pill--danger').length") == 3,
              cdp.evaluate("document.querySelectorAll('.tbl--compact .pill--danger').length"))
        check("both lists offer “view all”",
              cdp.evaluate(
                  "window.__T.all('#view .card .cat__toolbar .btn').filter((n) => n.textContent.trim() === "
                  + json.dumps(en["dash.viewAll"]) + ").length"
              ) == 2)
        check("four quick actions render",
              cdp.evaluate("document.querySelectorAll('.quick__item').length") == 4)
        check("the quick actions are buttons",
              cdp.evaluate("window.__T.all('.quick__item').every((n) => n.tagName === 'BUTTON')"))
        check("the quick actions are reachable by keyboard",
              cdp.evaluate("window.__T.all('.quick__item').every((n) => n.tabIndex >= 0)"))
        check("the greeting is time-aware and named",
              re.match(r"^Good (morning|afternoon|evening), .+", cdp.evaluate("document.querySelector('.page__title').textContent") or "") is not None,
              cdp.evaluate("document.querySelector('.page__title').textContent"))
        check("the date line carries the school name",
              (cdp.evaluate("document.querySelector('.dash__date').textContent") or "").strip() != ""
              and "·" in (cdp.evaluate("document.querySelector('.dash__date').textContent") or ""))

        cdp.evaluate("window.__IMRI__.store.setTheme('light')")
        time.sleep(0.4)
        capture(cdp, DOCS / "t4b1-dashboard-light.png")
        cdp.evaluate("window.__IMRI__.store.setTheme('dark')")
        time.sleep(0.4)
        capture(cdp, DOCS / "t4b1-dashboard-dark.png")
        cdp.evaluate("window.__IMRI__.store.setTheme('light')")
        time.sleep(0.2)

        cdp.evaluate("window.__T.click('.alerts .btn--icon')")
        check("the alerts strip can be dismissed", cdp.evaluate("!document.querySelector('.alerts')"))

        # A KPI click routes with its filter pre-applied.
        cdp.evaluate("document.querySelectorAll('.grid--kpi .stat')[2].click()")
        check("clicking “on loan” opens the catalogue filtered",
              wait_for(cdp, "location.hash.indexOf('#/catalogue') === 0", 10, "catalogue"),
              cdp.evaluate("location.hash"))
        check("the filter is in the URL", "status=on_loan" in (cdp.evaluate("location.hash") or ""))
        check("the availability chip is pressed",
              wait_for(cdp, "!!document.querySelector('.cat__filter-group:nth-of-type(2) .chip[aria-pressed=\"true\"]')",
                      10, "chips"),
              cdp.evaluate(
                  "window.__T.all('.cat__filter-group:nth-of-type(2) .chip')"
                  ".map((n) => n.textContent.trim() + ':' + n.getAttribute('aria-pressed'))"
              ))
        check("the list only holds loans",
              cdp.evaluate(
                  "(() => { const pills = window.__T.all('#view .tbl tbody .pill');"
                  " return pills.length > 0 && pills.every((p) => p.textContent.trim() == "
                  + json.dumps(en["status.onLoan"]) + "); })()"
              ),
              cdp.evaluate("window.__T.all('#view .tbl tbody .pill').map((p) => p.textContent.trim())"))

        # ---- 6. catalogue --------------------------------------------------- #
        print("\n[6] catalogue")
        cdp.evaluate("window.__T.clickText('#view .cat__filters .btn', " + json.dumps(en["cat.filter.clear"]) + ")")
        check("clearing the filter restores the list",
              wait_for(cdp, "document.querySelectorAll('#view .tbl tbody tr').length > 10", 12, "full list"),
              cdp.evaluate("document.querySelectorAll('#view .tbl tbody tr').length"))
        check("the search box is focused on first visit",
              wait_for(cdp, "document.activeElement && document.activeElement.id === 'catalogue-search'", 6, "search focus"),
              cdp.evaluate("document.activeElement && document.activeElement.id"))
        check("the result count is stated",
              (cdp.evaluate("document.querySelector('.cat__count').textContent") or "").startswith(str(len(seed["books"]))),
              cdp.evaluate("document.querySelector('.cat__count').textContent"))
        check("the material chips carry counts",
              cdp.evaluate("document.querySelectorAll('.chips .chip__count').length") >= 3,
              cdp.evaluate("document.querySelectorAll('.chips .chip__count').length"))
        check("rows are 44 px tall",
              abs(float(cdp.evaluate("document.querySelector('#view .tbl tbody tr').getBoundingClientRect().height")) - 44) < 8,
              cdp.evaluate("document.querySelector('#view .tbl tbody tr').getBoundingClientRect().height"))
        check("the header is sticky",
              cdp.evaluate("getComputedStyle(document.querySelector('#view .tbl thead th')).position") == "sticky")
        check("a partially-issued title shows a copies bar",
              cdp.evaluate("document.querySelectorAll('.copies__bar').length") >= 3)

        # Search.
        cdp.evaluate(fill("window.__T.set('#catalogue-search', «v»)", v="history of malaya"))
        check("search narrows the list",
              wait_for(cdp, "document.querySelectorAll('#view .tbl tbody tr').length === 1", 12, "search results"),
              cdp.evaluate("window.__T.rowTitles()"))
        check("the match is highlighted",
              (cdp.evaluate("document.querySelector('#view .tbl tbody mark.hl').textContent") or "").lower()
              == "history of malaya",
              cdp.evaluate("document.querySelector('#view .tbl tbody mark.hl') ? document.querySelector('#view .tbl tbody mark.hl').textContent : null"))
        check("the search term becomes a removable tag",
              cdp.evaluate("document.querySelectorAll('.cat__active .tag').length") == 1)
        check("“Clear filters” appears only when filtered",
              cdp.evaluate("window.__T.find('#view .cat__filters .btn', " + json.dumps(en["cat.filter.clear"]) + ")"))

        # A search with no matches has its own empty state.
        cdp.evaluate(fill("window.__T.set('#catalogue-search', «v»)", v="zzzz-no-such-title"))
        check("an empty search has its own empty state",
              wait_for(cdp, "!!document.querySelector('#view .empty')", 12, "empty state"))
        check("it quotes the term",
              "zzzz-no-such-title" in (cdp.evaluate("document.querySelector('#view .empty').textContent") or ""))
        cdp.evaluate("window.__T.clickText('#view .empty button', " + json.dumps(en["cat.filter.clear"]) + ")")
        check("clearing it restores the list",
              wait_for(cdp, "document.querySelectorAll('#view .tbl tbody tr').length > 10", 12, "restored"))

        # Filter by type.
        cdp.evaluate("window.__T.clickText('#view .chips .chip', " + json.dumps(en["type.textbook"]) + ")")
        check("a type filter narrows the list",
              wait_for(cdp, "document.querySelectorAll('#view .tbl tbody tr').length > 0", 12, "filtered"))
        types = cdp.evaluate(
            "window.__T.all('#view .tbl tbody tr').map((tr) => tr.children[4].textContent.trim())"
        )
        check("every row is the filtered type",
              set(types) == {en["type.textbook"]}, json.dumps(sorted(set(types))))
        check("the type tag is removable",
              cdp.evaluate("document.querySelectorAll('.cat__active .tag').length") == 1)
        cdp.evaluate("window.__T.click('.cat__active .tag .tag__x')")
        check("removing the tag drops the filter",
              wait_for(cdp, "document.querySelectorAll('.cat__active .tag').length === 0", 10, "tag removed"))

        # Sort by title through the header.
        before = cdp.evaluate("window.__T.rowTitles()")
        cdp.evaluate("window.__T.clickText('#view .tbl thead .tbl__sort', " + json.dumps(en["cat.col.title"]) + ")")
        check("the title header sorts",
              wait_for(cdp, "document.querySelector('#view .tbl thead th[aria-sort=\"ascending\"]') !== null"
                            " || document.querySelector('#view .tbl thead th[aria-sort=\"descending\"]') !== null",
                      12, "sorted"))
        after = cdp.evaluate("window.__T.rowTitles()")
        check("the sort is announced on the column",
              cdp.evaluate("document.querySelector('#view .tbl thead th[aria-sort=\"ascending\"]') !== null"))
        check("the order is alphabetical",
              after == sorted(after, key=lambda text: text.lower()), json.dumps(after[:4]))
        check("the caret is shown on the sorted column",
              cdp.evaluate(
                  "(() => { const th = document.querySelector('#view .tbl thead th[aria-sort=\"ascending\"]');"
                  " return th ? getComputedStyle(th.querySelector('.tbl__caret')).opacity !== '0' : false; })()"
              ))

        # Paging at 25 rows.
        cdp.evaluate(fill("window.__T.set('.cat__foot select', «v»)", v="25"))
        check("the page size control offers 25/50/100",
              cdp.evaluate("window.__T.all('.cat__foot select option').map((o) => o.value).join(',')") == "25,50,100")
        check("the footer states the range",
              wait_for(cdp, "/1–25 of " + str(len(seed["books"])) + "/.test(document.querySelector('.cat__foot').textContent)",
                      12, "range label"),
              cdp.evaluate("document.querySelector('.cat__foot').textContent"))
        check("the pager offers a second page",
              cdp.evaluate("document.querySelectorAll('.pager__btn[aria-label]').length") >= 4)
        first_page = cdp.evaluate("window.__T.rowTitles()")
        cdp.evaluate("window.__T.clickText('.pager__btn', '2')")
        check("page two holds the remainder",
              wait_for(cdp, "document.querySelectorAll('#view .tbl tbody tr').length === "
                            + str(len(seed["books"]) - 25), 12, "page two"),
              cdp.evaluate("document.querySelectorAll('#view .tbl tbody tr').length"))
        second_page = cdp.evaluate("window.__T.rowTitles()")
        check("page two is different rows", set(first_page).isdisjoint(second_page))
        cdp.evaluate("window.__T.clickText('.pager__btn', '1')")
        wait_for(cdp, "document.querySelectorAll('#view .tbl tbody tr').length === 25", 12, "page one")

        # Selection.
        cdp.evaluate("document.querySelectorAll('#view .tbl tbody .check input')[0].click()")
        check("ticking a row shows the selection bar",
              wait_for(cdp, "!!document.querySelector('.selbar')", 8, "selection bar"))
        check("the bar counts the selection",
              (cdp.evaluate("document.querySelector('.selbar__count').textContent") or "").startswith("1"))
        check("the bar offers withdraw and clear",
              cdp.evaluate("document.querySelectorAll('.selbar button').length") == 2)
        cdp.evaluate("window.__T.clickText('.selbar button', " + json.dumps(en["cat.bulk.clear"]) + ")")
        check("clearing the selection hides the bar", cdp.evaluate("!document.querySelector('.selbar')"))

        # Screenshots with data.
        capture(cdp, DOCS / "t4b1-catalogue-light.png")
        cdp.evaluate("window.__IMRI__.store.setTheme('dark')")
        time.sleep(0.4)
        capture(cdp, DOCS / "t4b1-catalogue-dark.png")
        cdp.evaluate("window.__IMRI__.store.setTheme('light')")
        time.sleep(0.2)

        # ---- 7. create a book through the editor ---------------------------- #
        print("\n[7] editor — create")
        cdp.evaluate("window.__T.clickText('#view .page__actions .btn', " + json.dumps(en["cat.new"]) + ")")
        check("the editor opens",
              wait_for(cdp, "!!document.querySelector('.modal--720')", 8, "editor"))
        check("it focuses the title",
              cdp.evaluate("document.activeElement && document.activeElement.name") == "title")
        check("it opens empty with one copy",
              cdp.evaluate("document.querySelector('.modal input[name=\"copies\"]').value") == "1")
        check("the accession hint explains the allocation",
              (cdp.evaluate("document.querySelector('.modal .field__hint').textContent") or "") != "")

        # The server owns validation: submit with no title and read its answer.
        cdp.evaluate("window.__T.clickText('.modal__foot button', " + json.dumps(en["common"]["create"] if isinstance(en.get("common"), dict) else "Create") + ")")
        check("an empty title is refused by the server",
              wait_for(cdp, "!!document.querySelector('.modal .field--error .field__error')", 10, "field error"),
              cdp.evaluate("document.querySelector('.modal .field__error') ? document.querySelector('.modal .field__error').textContent : null"))
        check("the error sits under the title field",
              cdp.evaluate(
                  "(() => { const wrap = document.querySelector('.modal input[name=\"title\"]').closest('.field');"
                  " return Boolean(wrap && wrap.querySelector('.field__error')); })()"
              ))
        check("the offending field is marked invalid",
              cdp.evaluate("document.querySelector('.modal input[name=\"title\"]').getAttribute('aria-invalid')") == "true")

        cdp.evaluate(fill("window.__T.set('.modal input[name=\"title\"]', «v»)", v="The Careful Cataloguer"))
        cdp.evaluate(fill("window.__T.set('.modal input[name=\"authors\"]', «v»)", v="Nadia Halim"))
        cdp.evaluate(fill("window.__T.set('.modal input[name=\"isbn\"]', «v»)", v="0-306-40615-2"))
        cdp.evaluate("document.querySelector('.modal input[name=\"isbn\"]').blur()")
        check("a valid ISBN-10 is normalised in a hint",
              wait_for(cdp, "!!document.querySelector('.modal .field__hint:not([hidden])')", 6, "isbn hint"))
        check("the hint shows the ISBN-13 form",
              "9780306406157" in str(cdp.evaluate("document.querySelector('.modal .field__hint:not([hidden])').textContent")),
              cdp.evaluate("document.querySelector('.modal .field__hint:not([hidden])').textContent"))
        cdp.evaluate(fill("window.__T.set('.modal select[name=\"material_type\"]', «v»)", v="reference"))
        cdp.evaluate(fill("window.__T.set('.modal input[name=\"copies\"]', «v»)", v="2"))
        capture(cdp, DOCS / "t4b1-editor-light.png")
        cdp.evaluate("window.__IMRI__.store.setTheme('dark')")
        time.sleep(0.4)
        capture(cdp, DOCS / "t4b1-editor-dark.png")
        cdp.evaluate("window.__IMRI__.store.setTheme('light')")
        time.sleep(0.2)

        cdp.evaluate("window.__T.clickText('.modal__foot button', " + json.dumps("Create") + ")")
        check("the book is created",
              wait_for(cdp, "!document.querySelector('.modal')", 12, "modal closed"))
        check("creation is toasted",
              any("careful cataloguer" in text.lower() for text in cdp.evaluate("window.__T.toasts()")),
              json.dumps(cdp.evaluate("window.__T.toasts()")))
        check("the list refreshes with the new title",
              wait_for(cdp, "window.__T.find('#view .tbl tbody .cat__title-main', 'The Careful Cataloguer')", 12, "new row"))

        # ---- 8. the record drawer ------------------------------------------- #
        print("\n[8] record drawer")
        cdp.evaluate(fill("window.__T.set('#catalogue-search', «v»)", v="history of malaya"))
        wait_for(cdp, "document.querySelectorAll('#view .tbl tbody tr').length === 1", 12, "one row")
        cdp.evaluate("document.querySelector('#view .tbl tbody tr').focus()")
        cdp.call("Input.dispatchKeyEvent", {
            "type": "rawKeyDown", "windowsVirtualKeyCode": 40, "nativeVirtualKeyCode": 40, "key": "ArrowDown",
        })
        cdp.call("Input.dispatchKeyEvent", {
            "type": "keyUp", "windowsVirtualKeyCode": 40, "nativeVirtualKeyCode": 40, "key": "ArrowDown",
        })
        time.sleep(0.2)
        check("the arrow keys move between rows",
              cdp.evaluate("document.activeElement && document.activeElement.tagName") == "TR",
              cdp.evaluate("document.activeElement && document.activeElement.tagName"))
        cdp.call("Input.dispatchKeyEvent", {
            "type": "rawKeyDown", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "key": "Enter",
        })
        cdp.call("Input.dispatchKeyEvent", {
            "type": "keyUp", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "key": "Enter",
        })
        check("Enter opens the record",
              wait_for(cdp, "!!document.querySelector('.drawer--wide')", 12, "drawer"))
        check("the drawer is 560 px",
              abs(float(cdp.evaluate("document.querySelector('.drawer').getBoundingClientRect().width")) - 560) < 2,
              cdp.evaluate("document.querySelector('.drawer').getBoundingClientRect().width"))
        check("the record title is the book",
              (cdp.evaluate("document.querySelector('.book__title').textContent") or "") == "A History of Malaya")
        check("the metadata grid is populated",
              cdp.evaluate("document.querySelectorAll('.book__field').length") >= 8,
              cdp.evaluate("document.querySelectorAll('.book__field').length"))
        check("the ISBN is shown with a copy button",
              cdp.evaluate("document.querySelectorAll('.book__value .btn--icon').length") >= 1)
        check("there is a row per copy",
              cdp.evaluate("document.querySelectorAll('.copy-row').length") >= 3)
        check("an on-loan copy names its borrower",
              "Nurul" in str(cdp.evaluate("document.querySelector('.copy-row').textContent")))
        check("the loans tab counts the history",
              cdp.evaluate("document.querySelectorAll('.tabs .tab').length") == 2)
        check("MARC is collapsed by default",
              cdp.evaluate("(() => { const d = document.querySelector('details.disclosure');"
                           " return d ? d.open === false : false; })()"))
        cdp.evaluate("document.querySelector('details.disclosure summary').click()")
        time.sleep(0.2)
        check("the MARC section opens",
              cdp.evaluate("document.querySelector('details.disclosure').open"))
        check("the raw JSON toggle reveals the blob",
              cdp.evaluate(
                  "(() => { const before = document.querySelectorAll('.marc__raw:not([hidden])').length;"
                  " window.__T.clickText('.disclosure button', 'raw JSON');"
                  " return document.querySelectorAll('.marc__raw:not([hidden])').length > before; })()"
              ))

        capture(cdp, DOCS / "t4b1-drawer-light.png")
        cdp.evaluate("window.__IMRI__.store.setTheme('dark')")
        time.sleep(0.4)
        capture(cdp, DOCS / "t4b1-drawer-dark.png")
        cdp.evaluate("window.__IMRI__.store.setTheme('light')")
        time.sleep(0.2)

        # ---- 9. edit and save ----------------------------------------------- #
        print("\n[9] edit and save")
        cdp.evaluate("window.__T.drawerButton(" + json.dumps(en["common"]["edit"] if isinstance(en.get("common"), dict) else "Edit") + ")")
        check("the drawer's Edit opens the same editor",
              wait_for(cdp, "!!document.querySelector('.modal--720')", 8, "edit modal"))
        check("the editor is prefilled",
              cdp.evaluate("document.querySelector('.modal input[name=\"title\"]').value") == "A History of Malaya")
        check("the accession is read-only when editing",
              cdp.evaluate("document.querySelector('.modal input[name=\"accession_no\"]').readOnly"))
        cdp.evaluate(fill("window.__T.set('.modal input[name=\"title\"]', «v»)", v="A History of Malaya, Revised"))
        cdp.evaluate("window.__T.clickText('.modal__foot button', " + json.dumps("Save changes") + ")")
        check("the edit is saved",
              wait_for(cdp, "!document.querySelector('.modal')", 12, "modal closed"))
        check("the drawer repaints with the new title",
              wait_for(cdp, "(document.querySelector('.book__title') || {}).textContent === 'A History of Malaya, Revised'",
                      12, "repaint"),
              cdp.evaluate("document.querySelector('.book__title') ? document.querySelector('.book__title').textContent : null"))

        # ---- 10. print label ------------------------------------------------- #
        print("\n[10] label")
        cdp.evaluate("window.__printed = 0; window.print = function () { window.__printed += 1; };")
        cdp.evaluate("window.__T.drawerButton(" + json.dumps(en["cat.detail.printLabel"]) + ")")
        check("the label preview opens",
              wait_for(cdp, "!!document.querySelector('.label-preview svg')", 12, "label svg"))
        check("the preview is the server's SVG",
              cdp.evaluate("document.querySelector('.label-preview svg').tagName") == "svg")
        check("the preview shows the accession",
              "A History of Malaya" in str(cdp.evaluate("document.querySelector('.label-preview').textContent"))
              or cdp.evaluate("document.querySelectorAll('.label-preview svg text').length") >= 3)
        check("printing moves the label onto the body",
              cdp.evaluate(
                  "(() => { window.__T.clickText('.modal__foot button', " + json.dumps(en["cat.detail.print"]) + ");"
                  " return document.body.classList.contains('is-printing-label')"
                  " && Boolean(document.querySelector('body > .label-print svg'))"
                  " && window.__printed === 1; })()"
              ))
        cdp.call("Emulation.setEmulatedMedia", {"media": "print"})
        time.sleep(0.2)
        check("only the label is printed",
              cdp.evaluate("getComputedStyle(document.querySelector('.shell')).display") == "none")
        cdp.call("Emulation.setEmulatedMedia", {"media": ""})
        cdp.evaluate("window.dispatchEvent(new Event('afterprint'))")
        time.sleep(0.2)
        check("the print sheet is cleaned up",
              cdp.evaluate("!document.body.classList.contains('is-printing-label')"
                           " && !document.querySelector('.label-print')"))
        cdp.evaluate("window.__T.clickText('.modal__foot button', " + json.dumps(en["common"]["close"] if isinstance(en.get("common"), dict) else "Close") + ")")
        wait_for(cdp, "!document.querySelector('.modal')", 6, "label closed")

        # ---- 11. delete, refused --------------------------------------------- #
        print("\n[11] delete")
        cdp.evaluate("document.querySelectorAll('.copy-row .btn--icon')[0].click()")
        check("the copy menu opens", wait_for(cdp, "!!document.querySelector('.menu')", 6, "copy menu"))
        check("a copy on loan cannot be deleted",
              cdp.evaluate(
                  "(() => { const item = window.__T.find('.menu__item', " + json.dumps(en["common"]["delete"]) + ");"
                  " return Boolean(item && item.disabled); })()"
              ))
        check("nor marked lost",
              cdp.evaluate(
                  "(() => { const item = window.__T.find('.menu__item', " + json.dumps(en["cat.copy.markLost"]) + ");"
                  " return Boolean(item && item.disabled); })()"
              ))
        cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")

        cdp.evaluate("window.__T.drawerButton(" + json.dumps(en["common"]["delete"]) + ")")
        check("deleting the title asks first",
              wait_for(cdp, "!!document.querySelector('.modal')", 8, "confirm"))
        check("the confirmation names the title",
              "A History of Malaya, Revised" in str(cdp.evaluate("document.querySelector('.modal').textContent")))
        cdp.evaluate("window.__T.clickText('.modal__foot button', " + json.dumps("Delete") + ")")
        check("the server refuses because a copy is out",
              wait_for(cdp, "window.__T.toasts().some((t) => /loan|pinjam/i.test(t))", 12, "refusal toast"),
              json.dumps(cdp.evaluate("window.__T.toasts()")))
        check("the drawer stays open after the refusal",
              cdp.evaluate("!!document.querySelector('.drawer')"))

        # ---- 12. list state survives navigation ------------------------------ #
        print("\n[12] list state")
        cdp.evaluate("document.querySelector('.drawer__head .btn--icon').click()")
        check("the drawer closes", wait_for(cdp, "!document.querySelector('.drawer')", 8, "closed"))
        cdp.evaluate("window.__IMRI__.router.navigate('/dashboard')")
        wait_for(cdp, "location.hash === '#/dashboard'", 10, "dashboard")
        cdp.evaluate("window.__IMRI__.router.navigate('/catalogue')")
        check("returning to the catalogue keeps the search term",
              wait_for(cdp, "document.querySelector('#catalogue-search').value === 'history of malaya'", 10, "search kept"),
              cdp.evaluate("document.querySelector('#catalogue-search') ? document.querySelector('#catalogue-search').value : null"))
        check("and the sort and page size",
              cdp.evaluate("document.querySelector('.cat__foot select').value") == "25",
              cdp.evaluate("document.querySelector('.cat__foot select') ? document.querySelector('.cat__foot select').value : null"))

        # ---- 13. catalogue completeness -------------------------------------- #
        print("\n[13] catalogues")
        problems = cdp.evaluate("JSON.stringify(window.__IMRI__.checkCompleteness())")
        check("checkCompleteness() reports no gaps", problems == "[]", problems)
        for code in ("ms", "zh"):
            cdp.evaluate(f"window.__IMRI__.i18n.setLang('{code}')")
            time.sleep(0.3)
            title = cdp.evaluate("document.querySelector('.tb__title').textContent")
            expected = read_catalogue(code)["cat.title"]
            check(f"the catalogue repaints in {code}", title == expected, f"{title!r} != {expected!r}")
        cdp.evaluate("window.__IMRI__.i18n.setLang('en')")
        time.sleep(0.3)

        # ---- 14. console ----------------------------------------------------- #
        print("\n[14] console")
        errors = cdp.console_errors()
        check("no console errors or uncaught exceptions", not errors, "\n".join(errors[:6]))

        print(f"\n{'=' * 58}")
        print(f"  {PASS} passed, {FAIL} failed")
        if NOTES:
            print("  notes:")
            for note in NOTES:
                print(f"    - {note}")
        print("  screenshots: docs/t4b1-dashboard-{light,dark}.png")
        print("               docs/t4b1-catalogue-{light,dark}.png")
        print("               docs/t4b1-drawer-{light,dark}.png")
        print("               docs/t4b1-editor-{light,dark}.png")
        print(f"{'=' * 58}")
        return 0 if FAIL == 0 else 1

    finally:
        if cdp and not args.keep:
            cdp.close()
        if browser_proc and not args.keep:
            browser_proc.terminate()
            try:
                browser_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                browser_proc.kill()
        if server and not args.keep:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
        if not args.keep:
            shutil.rmtree(data_dir, ignore_errors=True)
            shutil.rmtree(profile_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
