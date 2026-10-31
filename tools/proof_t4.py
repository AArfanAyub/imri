"""T4 pass A proof — static checks for the design system and app shell.

Runs without a browser and without network. Every check prints one line, and
the script exits non-zero if anything fails, so it can gate a build.

    python tools/proof_t4.py

What it proves
  1. the three catalogues carry an identical key set, with no blank values
  2. every error code in `imri/errors.py` has an `err.<code>` sentence
  3. every literal `t('…')` call in the frontend names a real key
  4. every relative `import` resolves to a file that exists
  5. `app.css` keeps every colour literal inside the two token blocks
  6. `index.html` has no inline script and no remote asset
  7. the shell exposes the five navigation groups and the route table covers
     every screen named in the brief
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
JS = WEB / "js"
LANG = JS / "lang"

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    """Print one result line and keep the tally."""
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}")
    if detail:
        for line in str(detail).splitlines():
            print(f"        {line}")


def load_catalogue(path: Path) -> dict[str, str]:
    """Read a catalogue module as data.

    The files are a single `export default { 'key': 'value', … }` literal, so a
    tiny text transform is enough and avoids needing a JS runtime here.
    """
    text = path.read_text(encoding="utf-8")
    body = text.split("export default", 1)[1]
    body = body.rsplit("};", 1)[0]
    pairs = re.findall(r"'([^']+)':\s*'((?:[^'\\]|\\.)*)'", body)
    return {key: value for key, value in pairs}


def python_error_codes() -> list[str]:
    """Every error code declared in `imri/errors.py`."""
    text = (ROOT / "imri" / "errors.py").read_text(encoding="utf-8")
    return sorted(set(re.findall(r'^([A-Z][A-Z0-9_]+)\s*=\s*"([a-z0-9_]+)"', text, re.M) and
                      [value for _, value in re.findall(r'^([A-Z][A-Z0-9_]+)\s*=\s*"([a-z0-9_]+)"', text, re.M)]))


def main() -> int:
    print("T4 pass A — design system and shell\n")

    # ---- 1. catalogue parity ------------------------------------------- #
    print("[1] catalogues")
    en = load_catalogue(LANG / "en.js")
    ms = load_catalogue(LANG / "ms.js")
    zh = load_catalogue(LANG / "zh.js")
    check("en catalogue parsed", len(en) > 500, f"keys={len(en)}")
    check("ms catalogue parsed", len(ms) > 500, f"keys={len(ms)}")
    check("zh catalogue parsed", len(zh) > 500, f"keys={len(zh)}")

    base = set(en)
    for name, catalogue in (("ms", ms), ("zh", zh)):
        missing = sorted(base - set(catalogue))
        extra = sorted(set(catalogue) - base)
        blank = sorted(key for key, value in catalogue.items() if not value.strip())
        check(f"{name} has every en key", not missing, ", ".join(missing[:10]))
        check(f"{name} has no extra keys", not extra, ", ".join(extra[:10]))
        check(f"{name} has no blank values", not blank, ", ".join(blank[:10]))
    check("en has no blank values", not [k for k, v in en.items() if not v.strip()])

    # ---- 2. error coverage --------------------------------------------- #
    print("\n[2] error codes")
    codes = python_error_codes()
    check("error codes found in errors.py", len(codes) > 60, f"codes={len(codes)}")
    uncovered = [code for code in codes if f"err.{code}" not in en]
    check("every error code has a sentence", not uncovered, ", ".join(uncovered[:10]))

    # ---- 3. literal t() keys ------------------------------------------- #
    print("\n[3] translation call sites")
    call_sites: list[tuple[str, str]] = []
    dynamic: list[tuple[str, str]] = []
    for path in sorted(JS.rglob("*.js")):
        if LANG in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\bt\(\s*'([^']+)'", text):
            call_sites.append((str(path.relative_to(ROOT)), match.group(1)))
        for match in re.finditer(r"\bt\(\s*`([^`]+)`", text):
            dynamic.append((str(path.relative_to(ROOT)), match.group(1)))

    unknown = sorted({(src, key) for src, key in call_sites if key not in en})
    check(f"all {len(call_sites)} literal t() keys exist", not unknown,
          "\n".join(f"{src}: {key}" for src, key in unknown[:12]))

    # Dynamic keys must stay inside a known namespace. `type.`, `lang.` and
    # `condition.` are closed vocabularies (config.MATERIAL_TYPES,
    # config.LANGUAGES, config.COPY_CONDITIONS); the page proof checks that
    # every member of each renders a real label.
    allowed_prefixes = (
        "err.", "status.", "theme.", "role.", "lang.", "set.", "nav.", "type.",
        "condition.",
    )
    stray = [entry for entry in dynamic if not entry[1].startswith(allowed_prefixes)]
    check("dynamic keys use a known namespace", not stray,
          "\n".join(f"{src}: {key}" for src, key in stray[:8]))

    # ---- 4. imports resolve -------------------------------------------- #
    print("\n[4] module graph")
    missing_imports: list[str] = []
    for path in sorted(JS.rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"from\s+'(\.[^']+)'", text):
            target = (path.parent / match.group(1)).resolve()
            if not target.exists():
                missing_imports.append(f"{path.relative_to(ROOT)} -> {match.group(1)}")
    check("every relative import resolves", not missing_imports, "\n".join(missing_imports[:10]))

    # The router's lazy imports must all exist, or a route dies at click time.
    router_text = (JS / "router.js").read_text(encoding="utf-8")
    lazy = re.findall(r"import\('(\./pages/[^']+)'\)", router_text)
    absent = [spec for spec in lazy if not (JS / spec.replace("./", "")).exists()]
    check(f"all {len(lazy)} lazy page imports exist", not absent, ", ".join(absent[:10]))

    # ---- 5. css colour discipline -------------------------------------- #
    print("\n[5] stylesheet")
    css = (WEB / "css" / "app.css").read_text(encoding="utf-8")
    tokens_end = css.find("/* === 02 reset")
    check("token block marker found", tokens_end > 0)
    head, tail = css[:tokens_end], css[tokens_end:]
    hex_in_head = len(re.findall(r"#[0-9A-Fa-f]{3,8}\b", head))
    hex_in_tail = re.findall(r"#[0-9A-Fa-f]{3,8}\b", tail)
    rgba_in_tail = re.findall(r"rgba?\(", tail)
    check("tokens declare colours", hex_in_head > 20, f"literals={hex_in_head}")
    check("no hex below the token block", not hex_in_tail, ", ".join(hex_in_tail[:10]))
    check("no rgba() below the token block", not rgba_in_tail, ", ".join(rgba_in_tail[:10]))

    # `!important` is allowed only where it is the point: the reduced-motion
    # override, the print sheet, and the `.u-hidden` utility. Anywhere else it
    # is a specificity smell, so it fails the build.
    stripped = re.sub(r"/\*.*?\*/", "", tail, flags=re.S)
    for block in re.findall(r"@media \(prefers-reduced-motion[^)]*\)\s*\{.*?\n\}", stripped, re.S):
        stripped = stripped.replace(block, "")
    stripped = stripped.split("@media print")[0]
    stripped = re.sub(r"\.u-hidden \{[^}]*\}", "", stripped)
    check("no !important outside reduced-motion/print/utility", "!important" not in stripped)

    # ---- 6. html shell -------------------------------------------------- #
    print("\n[6] index.html")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    check("has #app mount point", 'id="app"' in html)
    check("no inline script", not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html))
    check("declares color-scheme", 'name="color-scheme"' in html)
    check("has a content security policy", "Content-Security-Policy" in html)
    check("no remote asset", not re.search(r'(src|href)="https?://', html))
    check("favicon is an inline svg", "data:image/svg+xml" in html)
    check("theme bootstrap runs before the stylesheet",
          html.index("theme-boot.js") < html.index("css/app.css"))

    # ---- 7. shell + routes --------------------------------------------- #
    print("\n[7] shell and routes")
    shell_text = (JS / "shell.js").read_text(encoding="utf-8")
    groups = re.findall(r"key: 'nav\.group\.[a-z]+'", shell_text)
    check("sidebar declares five groups", len(groups) == 5, f"found={len(groups)}")
    check("palette has four groups",
          all(f"palette.group.{name}" in en for name in ("actions", "books", "members", "goto")))

    expected_routes = {
        "activate", "login", "change-password", "dashboard", "catalogue", "book",
        "members", "member", "reading-lists", "counter", "overdue", "reservations",
        "reports", "labels", "admin", "settings", "backup", "audit", "license",
        "not-found",
    }
    declared = set(re.findall(r"name: '([a-z-]+)', pattern:", router_text))
    check("route table covers every screen", expected_routes <= declared,
          ", ".join(sorted(expected_routes - declared)))

    # ---- summary -------------------------------------------------------- #
    print(f"\n{'=' * 58}")
    print(f"  {PASS} passed, {FAIL} failed")
    print(f"{'=' * 58}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
