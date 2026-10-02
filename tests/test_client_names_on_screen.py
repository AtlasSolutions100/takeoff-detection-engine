"""No client name, client job or client quote in anything a browser shows.

WHY. About eighteen accounts on the box are COMPETING contractors. A client's
name in a tooltip, a demo card, a bid-sheet note or an example value is
published to every one of them (ui-copy-rules, 2026-09-16; client-content scan
2026-09-28 and 2026-10-01). The 2026-09-28 sweep missed four findings because
it was case-sensitive and webui-only: "aqua-vista" passed a check written as
"aqua vista", and the server data the UI prints (the AI-grade tooltips, the
bid-sheet notes, the demo manifest) was not scanned at all.

SO THIS SCAN IS:
  * case- AND separator-insensitive: "Aqua Vista", "aqua-vista", "AquaVista",
    "AQUA_VISTA" all hit (tokens joined by [\\s_\\-.'’]*);
  * over the TEXT a browser receives, comments excluded: every string and
    code token of webui/**/*.js, the markup and attributes of webui/**/*.html,
    webui/**/*.css outside comments, the tooldemos manifest;
  * over the SERVER DATA the UI prints: demo-plans.json, server._AI_GRADES,
    server.BETA_*, takeoff_engine.bidsheet.DEFAULT_LINES, and every string
    literal that is not a docstring in server.py, takeoff_engine/*.py and
    cutfill/*.py (the dev-only corpus tools regress.py / sweep.py excepted,
    and pinned as never imported by the server);
  * over everything the server can send that is BUILT FROM DOCSTRINGS: the
    generated API description (/openapi.json, /docs, /redoc) of the host and
    every mounted app must be off, and if one is ever turned on its whole
    text is scanned (critic bid-content-1, RISK-4: /openapi.json carried four
    route docstrings naming clients to every signed-in account).

  * over the COMMENTS of every webui/ file a browser downloads (js, css, html,
    svg, vendored included), as a separate check with its own extra names
    (comment scrub 2026-10-01).

  * over the Python that SHIPS IN THE IMAGE (the Dockerfile COPYs server.py,
    provision_demo.py, takeoff_engine/ and cutfill/): every comment, every
    docstring and every other string literal. None reaches a browser, but
    anyone holding the image or the live repository reads them (Python scrub
    2026-10-02). The string literals that must keep a client's name - the
    gate's corpus file paths - are allow-listed below by exact file, exact
    text and reason; nothing else is.

The names live in THIS file only - tests/ is never served and never in the image.

    python tests/test_client_names_on_screen.py
"""

import io
import json
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEP = r"[\s_\-.'’]*"

# (label, tokens, word-end). From the 2026-10-01 scan's name list
# (out/verify/2026-10-01/readiness/CLIENT-CONTENT.md). Left out ON PURPOSE,
# as ordinary words or staff on screen: church, rose, bill, rick, jeff, jacob,
# jordan, jason, scott, christian, ruby, cistern, hurricane, rifle, flora,
# usda, billings, livingston, jensen, "barn" alone (a building word). The
# client behind each of those is caught by its distinctive name instead
# (seer stone, rose valley, ruby crossing, barn C3 / barn civil ...).
NAMES = [
    ("beaumont", ["bea", "u?", "mon", "t"], False),      # "Beau mont" too (critic bid-content-1)
    ("bill dunn", ["bill", "dunn"], False),
    ("thielen", ["thielen"], False),
    ("biggerstaff", ["biggerstaff"], False),
    ("swr", ["swr"], True),
    ("barn sheet", ["barn", "(c\\d|civil|residence|structural|s\\d)"], False),
    ("guide residence", ["guide", "residence"], False),
    ("true north survey", ["true", "north", "survey"], False),
    ("estimate 853", ["est(imate)?", "#?", "853"], False),
    ("ellis", ["ellis"], True),
    ("klayton", ["klayton"], True),
    ("stice", ["stice"], True),
    ("liberty park", ["liberty", "park"], False),
    ("oncor", ["oncor"], False),
    ("cloudcrest", ["cloud", "crest"], False),
    ("gchc", ["gchc"], False),
    ("seer stone", ["seer", "stone"], False),
    ("peony", ["peony"], True),
    ("wells built", ["wells", "built"], False),
    ("neibauer", ["neibauer"], False),
    ("gurr", ["gurr"], True),
    ("bryce", ["bryce"], True),
    ("approved construction set", ["approved", "construction", "set"], False),
    ("aqua vista", ["aqua", "vista"], False),
    ("sonora", ["sonora"], False),
    ("ryno", ["ryno"], False),
    ("co3", ["co3"], True),
    ("rose valley", ["rose", "valley"], False),
    ("clearwater", ["clear", "water"], False),
    ("ruby crossing", ["ruby", "crossing"], False),
    ("jaro", ["jaro"], True),
    ("home 2 suites", ["home", "2", "suites"], False),
    ("h2s", ["h2s"], True),
    ("cude", ["cude"], True),
    ("ernies", ["ernie'?s"], True),
    ("pierce", ["pierce"], False),
    ("kayden", ["kayden"], False),
    ("ut-809", ["ut", "809"], False),
    ("stully", ["stully"], False),
    ("shamrock", ["shamrock"], False),
    ("dibble", ["dibble"], True),
    ("jasper stone", ["jasper", "stone"], False),
    ("gateway commercial", ["gateway", "commercial"], False),
    ("french rose", ["french", "rose"], False),
    ("malta", ["malta"], True),
    ("7f services", ["7f", "services"], False),
    ("town pump", ["town", "pump"], False),
    ("grey bear", ["gr[ae]y", "bear"], False),
    ("gipsy", ["gipsy"], False),
    ("star meadow", ["star", "meadow"], False),
    ("gaoa", ["gaoa"], True),
    ("gallatin", ["gallat[a]?in"], False),
    ("cyprus", ["cyprus"], False),
    ("river crossing", ["river", "crossing"], False),
    ("kalispell", ["kalispell"], False),
    ("bernhardt", ["bernhardt"], False),
    ("bozeman", ["bozeman"], False),
    ("gardiner", ["gardiner"], False),
    ("sheridan", ["sheridan"], False),
    ("white sulfer", ["white", "sul(f|ph)[eu]r"], False),
    ("huckleberry", ["huckleberry"], False),
    ("rc ontario", ["rc", "ontario"], False),
    ("wccg", ["wccg"], False),
    ("million air", ["million", "air"], False),
    ("philips custom", ["philips", "custom"], False),
    ("school of the blind", ["school", "of", "(the)?", "blind"], False),
    ("cedar city", ["cedar", "city"], False),
    ("st george", ["st", "george"], True),
    # unnamed client material: a client's own words and a real job's numbers
    ("his notes", ["his", "notes?"], True),
    ("handwritten", ["hand", "written"], False),
    ("from the call", ["from", "the", "call"], True),
    ("transcript", ["transcript"], False),
    ("local prior", ["local", "prior"], False),
    ("barn datum 7732", ["7732"], True),
    ("barn figures", ["(224,058|5,730|8\\.29 ?in)"], False),
]


def _pat(tokens, strict):
    return re.compile(r"(?<![a-z0-9])" + SEP.join(tokens)
                      + (r"(?![a-z0-9])" if strict else ""), re.I)


PATS = [(lab, _pat(t, s)) for lab, t, s in NAMES]


def hits_in(text):
    return sorted({lab for lab, p in PATS if p.search(text)})


# ---- comment removal -------------------------------------------------------
def js_comment_spans(src):
    """(start, end) of every // and /* */ comment, stepping over strings, template
    literals and regex literals (same lexer as the 2026-10-01 scan)."""
    spans = []
    n = len(src)
    i = 0
    st = None
    prev = ""
    while i < n:
        c = src[i]
        if st is None:
            if src.startswith("//", i):
                j = src.find("\n", i)
                j = n if j < 0 else j
                spans.append((i, j))
                i = j
                continue
            if src.startswith("/*", i):
                j = src.find("*/", i + 2)
                j = n if j < 0 else j + 2
                spans.append((i, j))
                i = j
                continue
            if c in "'\"`":
                st = c
                i += 1
                continue
            if c == "/" and prev in "(,=:[!&|?{};+-*%<>~^":
                j = i + 1
                cls = False
                while j < n and src[j] != "\n":
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == "[":
                        cls = True
                    elif src[j] == "]":
                        cls = False
                    elif src[j] == "/" and not cls:
                        break
                    j += 1
                i = j + 1
                prev = "a"
                continue
            if not c.isspace():
                prev = c if not (c.isalnum() or c in "_$") else "a"
            i += 1
        else:
            if c == "\\":
                i += 2
                continue
            if c == st or (c == "\n" and st != "`"):
                st = None
                prev = "a"
            i += 1
    return spans


def strip_js_comments(src):
    """Blank every // and /* */ comment (newlines kept), leaving the code intact."""
    out = list(src)
    for i, j in js_comment_spans(src):
        for k in range(i, j):
            if out[k] != "\n":
                out[k] = " "
    return "".join(out)


def strip_css_comments(src):
    return re.sub(r"/\*.*?\*/", " ", src, flags=re.S)


def css_comment_spans(src):
    return [m.span() for m in re.finditer(r"/\*.*?\*/", src, flags=re.S)]


def html_comment_spans(src):
    """<!-- --> outside script/style, and the comments inside inline <script> / <style>."""
    spans = []
    blocks = [(m.start(2), m.group(1).lower(), m.group(2))
              for m in re.finditer(r"<(script|style)\b[^>]*>(.*?)</\1>", src, flags=re.S | re.I)]
    inside = [(b, b + len(body)) for b, _, body in blocks]
    for m in re.finditer(r"<!--.*?-->", src, flags=re.S):
        if not any(a <= m.start() < z for a, z in inside):
            spans.append(m.span())
    for b, kind, body in blocks:
        sub = js_comment_spans(body) if kind == "script" else css_comment_spans(body)
        spans += [(b + i, b + j) for i, j in sub]
    return sorted(spans)


def strip_html_comments(src):
    src = re.sub(r"<!--.*?-->", " ", src, flags=re.S)
    src = re.sub(r"(<script[^>]*>)(.*?)(</script>)",
                 lambda m: m.group(1) + strip_js_comments(m.group(2)) + m.group(3), src, flags=re.S | re.I)
    src = re.sub(r"(<style[^>]*>)(.*?)(</style>)",
                 lambda m: m.group(1) + strip_css_comments(m.group(2)) + m.group(3), src, flags=re.S | re.I)
    return src


def py_strings(src):
    """Every string literal in a Python source that is not a docstring."""
    out = []
    prev = None
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.STRING:
            is_doc = prev is None or prev.type in (tokenize.NEWLINE, tokenize.INDENT,
                                                   tokenize.DEDENT, tokenize.NL)
            if not is_doc:
                out.append((t.start[0], t.string))
        elif getattr(tokenize, "FSTRING_MIDDLE", None) == t.type:
            out.append((t.start[0], t.string))
        if t.type not in (tokenize.COMMENT, tokenize.NL):
            prev = t
    return out


def walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_strings(k)
            yield from walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from walk_strings(v)


# ---- the sources ------------------------------------------------------------
def scan_browser_text(root):
    """{"path:line": [labels]} over what the browser receives, comments excluded."""
    found = {}
    web = root / "webui"
    for p in sorted(web.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in (".js", ".mjs", ".html", ".css", ".json", ".svg", ".txt", ".md"):
            continue
        if ext in (".js", ".mjs") and (".min." in p.name or p.name.startswith("three")):
            continue       # vendored libraries: hex tables, not our words
        src = p.read_text(encoding="utf-8", errors="replace")
        if ext in (".js", ".mjs"):
            src = strip_js_comments(src)
        elif ext == ".css":
            src = strip_css_comments(src)
        elif ext in (".html", ".svg"):
            src = strip_html_comments(src)
        for ln, line in enumerate(src.splitlines(), 1):
            h = hits_in(line)
            if h:
                found[f"{p.relative_to(root).as_posix()}:{ln}"] = h
    return found


# COMMENTS. Every browser downloads the comments with the file (comment scrub,
# 2026-10-01). On top of NAMES, names that are unambiguous INSIDE a comment:
# "Barn" there was always the client's job - except the drafter's xref layer
# `BR FND - BARN EAST`, which names a building and stays.
COMMENT_NAMES = [
    ("barn (the job)", [r"barn(?![\s_\-.'’]*east)"], True),
    ("kayden springs", ["kayden"], True),
    ("hurricane", ["hurricane"], True),
    ("mission bell", ["mission", "bell"], False),
    ("eci foundations", ["eci", "foundations"], False),
    ("estimate #853", ["#853"], True),
    ("client folder", ["client_meet/"], False),
]
COMMENT_PATS = PATS + [(lab, _pat(t, s)) for lab, t, s in COMMENT_NAMES]


def comment_hits_in(text):
    return sorted({lab for lab, p in COMMENT_PATS if p.search(text)})


def comment_spans(path, src):
    ext = path.suffix.lower()
    if ext in (".js", ".mjs"):
        return js_comment_spans(src)
    if ext == ".css":
        return css_comment_spans(src)
    if ext in (".html", ".svg"):
        return html_comment_spans(src)
    return []


_MARK = re.compile(r"^\s*(//+|/\*+|\*+(?!/)|<!--)?\s?|\s*(\*/|-->)\s*$")


def comment_text(src, spans):
    """[(line, text)] - consecutive comments with only whitespace between them are one
    passage, its lines joined without their // * <!-- markers, so a name broken
    across two comment lines ("Seer" / "Stone") is still one name."""
    groups = []
    for i, j in spans:
        if groups and not src[groups[-1][1]:i].strip():
            groups[-1][1] = j
            groups[-1][2].append((i, j))
        else:
            groups.append([i, j, [(i, j)]])
    out = []
    for start, _, parts in groups:
        lines = [ln for i, j in parts for ln in src[i:j].split("\n")]
        out.append((src.count("\n", 0, start) + 1, " ".join(_MARK.sub("", ln) for ln in lines)))
    return out


def scan_browser_comments(root):
    """{"path:line": [labels]} over the COMMENTS of every file under webui/ a browser
    can fetch (vendored libraries included: their comments ship too)."""
    found = {}
    for p in sorted((root / "webui").rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".js", ".mjs", ".html", ".css", ".svg"):
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        for ln, text in comment_text(src, comment_spans(p, src)):
            h = comment_hits_in(text)
            if h:
                found[f"{p.relative_to(root).as_posix()}:{ln}"] = h
    return found


def scan_server_data(root):
    found = {}
    man = root / "demo-plans.json"
    if man.exists():
        for ln, line in enumerate(man.read_text(encoding="utf-8").splitlines(), 1):
            h = hits_in(line)
            if h:
                found[f"demo-plans.json:{ln}"] = h
    src = (root / "server.py").read_text(encoding="utf-8")
    for ln, s in py_strings(src):
        h = hits_in(s)
        if h:
            found[f"server.py:{ln}"] = h
    return found


DEV_TOOLS = ("takeoff_engine/regress.py", "takeoff_engine/sweep.py")


def scan_engine_strings(root):
    """String literals (not docstrings) of the engine packages the server imports: any of them can reach a reply."""
    found = {}
    for pkg in ("takeoff_engine", "cutfill"):
        for f in sorted((root / pkg).rglob("*.py")):
            rel = f.relative_to(root).as_posix()
            if rel in DEV_TOOLS:
                continue
            for ln, lit in py_strings(f.read_text(encoding="utf-8")):
                h = hits_in(lit)
                if h:
                    found[f"{rel}:{ln}"] = h
    return found


def scan_docstring_surfaces():
    """Every FastAPI app the host serves (itself and its mounts): the generated docs must be off; any that is on
    is scanned whole, which is what a browser would get."""
    import server
    from fastapi import FastAPI
    from starlette.routing import Mount
    apps, on, found = [], [], {}

    def walk(a, prefix):
        apps.append((prefix or "/", a))
        for r in getattr(a, "routes", []):
            if isinstance(r, Mount) and isinstance(r.app, FastAPI):
                walk(r.app, prefix + r.path)
    walk(server.app, "")
    for prefix, a in apps:
        if a.openapi_url or a.docs_url or a.redoc_url:
            on.append(prefix)
            h = hits_in(json.dumps(a.openapi()))
            if h:
                found[prefix] = h
    return apps, on, found


def scan_live_objects():
    """The objects the UI renders, as loaded (catches text built at import)."""
    import os
    import tempfile
    os.environ.setdefault("TAKEOFF_JOBS_DIR", tempfile.mkdtemp(prefix="client_names_test_"))
    import server
    from takeoff_engine import bidsheet
    found = {}
    for name, obj in (("server._AI_GRADES", server._AI_GRADES),
                      ("server.BETA", [server.BETA_SHORT, server.BETA_LONG]),
                      ("server.DEMO_PLANS", server.DEMO_PLANS),
                      ("bidsheet.DEFAULT_LINES", bidsheet.DEFAULT_LINES)):
        for s in walk_strings(obj):
            h = hits_in(s)
            if h:
                found.setdefault(name, []).append((h, s[:120]))
    return found


# ---- the Python in the image: comments, docstrings, string literals ---------
# (Python scrub 2026-10-02.) On top of COMMENT_PATS, names that only ever
# appeared in Python comments: the engineer and architect of client sets, a
# client's job and street names, and the Barn datum's own levels.
PY_NAMES = [
    ("guide (the job)", ["guide", "(civil|structural|landscaping|residence|c\\d)"], False),
    ("imeg", ["imeg"], True),
    ("van sickle", ["van", "sickle"], False),
    ("kevin tsai", ["tsai"], True),
    ("workshop-p", ["workshop", "p"], True),
    ("tuff ranch", ["tuff", "ranch"], False),
    ("montana excavation", ["montana", "excavation"], False),
    ("incense rose", ["incense", "rose"], False),
    ("kings creek", ["kings", "creek"], False),
    ("leopard gate", ["leopard", "gate"], False),
    ("colliers", ["colliers"], True),
    ("aqua (the job)", ["aqua"], True),
    ("bill (the client)", ["bill(?:'s|/)"], False),
    ("client folder", ["client_meet"], False),
    ("barn datum levels", ["(7733\\.25|7719\\.125)"], False),
]
PY_PATS = COMMENT_PATS + [(lab, _pat(t, s)) for lab, t, s in PY_NAMES]

# The ONLY Python string literals allowed to name a client, by exact file and text. A string the program uses is code:
# changing it changes behaviour, so it stays and is listed here with its reason.
#  (a) corpus file paths the regression gate / the sweep open - dev tools, never imported by the server (pinned above);
#  (c) other: the default local plan root, and one dead string statement the scrub may not touch.
_GATE_PATHS = (
    "../TakeoffPro AI/Oncor Cloudcrest Substation Civils - 20251212.pdf_safe.pdf",
    "../client_meet/beaumont/SWR_Guide_Residence_-_Civil_5.8.26.pdf",
    "../client_meet/beaumont/SWR_Barn_Residence_-_Landscaping_4.7.26.pdf",
    "../client_meet/beaumont/SWR_Barn_Residence_-_Civil_4.7.26.pdf",
    "../client_meet/beaumont/SWR_Barn_Residence_-_Structural_4.7.26.pdf",
    "../client_meet/beaumont/260603 Thielen Biggerstaff Residence.pdf",
)
PY_ALLOWED = {("takeoff_engine/regress.py", p): "(a) gate corpus file path: CORPUS / CORPUS_BEAUMONT / CORPUS_FULL_EXTRA"
              for p in _GATE_PATHS}
PY_ALLOWED[("takeoff_engine/sweep.py", "Ryno Construction")] = "(a) sweep corpus folder name (dev tool)"
PY_ALLOWED[("server.py", "client_meet")] = ("(c) PLAN_ROOTS default for LOCAL runs only; TAKEOFF_HOSTED=1 (set by the "
                                            "Dockerfile) empties /api/samples")
PY_ALLOWED[("takeoff_engine/marks_naming.py", "Mission Bell")] = (
    "(c) read_scanned_schedule's would-be docstring sits after `fuzzy = True`, so it is a dead string statement, "
    "not a docstring: never executed, never shown, but a string literal all the same - owner decision")


def py_texts(src):
    """[(line, kind, text)]: comment passages (consecutive comment lines joined), docstrings (by ast), and every other
    string literal, f-string parts included."""
    import ast
    tree = ast.parse(src)
    docs = set()
    for node in [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]:
        b = node.body
        if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) and isinstance(b[0].value.value, str):
            docs.add((b[0].lineno, b[0].end_lineno))
    out, run = [], None
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.COMMENT:
            body = t.string.lstrip("#").strip()
            # a whole-line comment continues the passage above it; a comment after code starts its own
            if run and run[2] == t.start[0] - 1 and not t.line[:t.start[1]].strip():
                run[1] += " " + body
                run[2] = t.start[0]
            else:
                if run:
                    out.append((run[0], "comment", run[1]))
                run = [t.start[0], body, t.start[0]]
            continue
        if t.type == tokenize.STRING:
            kind = "docstring" if (t.start[0], t.end[0]) in docs else "string"
            out.append((t.start[0], kind, " ".join(t.string.split())))
        elif getattr(tokenize, "FSTRING_MIDDLE", None) == t.type:
            out.append((t.start[0], "string", t.string))
    if run:
        out.append((run[0], "comment", run[1]))
    return out


def image_python(root):
    files = [root / "server.py", root / "provision_demo.py"]
    for pkg in ("takeoff_engine", "cutfill"):
        files += sorted((root / pkg).rglob("*.py"))
    return [f for f in files if f.exists()]


def scan_image_python(root):
    """{"path:line kind": [labels]} over every comment, docstring and string literal of the Python in the image,
    minus PY_ALLOWED; and the allow-list entries that matched (so a stale entry is seen)."""
    found, used = {}, set()
    for f in image_python(root):
        rel = f.relative_to(root).as_posix()
        for ln, kind, text in py_texts(f.read_text(encoding="utf-8")):
            h = sorted({lab for lab, p in PY_PATS if p.search(text)})
            if not h:
                continue
            if kind == "string":
                key = next(((rel, a) for (r, a) in PY_ALLOWED if r == rel and a in text), None)
                if key:
                    used.add(key)
                    continue
            found[f"{rel}:{ln} {kind}"] = h
    return found, used


# ---- the old text must fail (the scan is not vacuous) -----------------------
CHECKS = []


def ok(cond, msg, got=None):
    CHECKS.append((bool(cond), msg, got))


OLD = [
    ("aqua-vista set. Where the engine already has a scale", "aqua vista"),
    ("4 wrong, all four on the scanned AquaVista set", "aqua vista"),
    ("Aqua Vista", "aqua vista"),
    ("AQUA_VISTA", "aqua vista"),
    ('struck through on the printed sheet - his notes: "we don\'t really use this much"', "his notes"),
    ("handwritten: surface SF + add 1'", "handwritten"),
    ("from the call: the size is read off the callout", "from the call"),
    ('inp.placeholder = "e.g. 7732";', "barn datum 7732"),
    ("Oncor Cloudcrest Substation Civils - 20251212.pdf_safe.pdf", "oncor"),
    ("Church - Peony G-L w Shop Lot 1 Block 10 - Seer Stone 3-18-2026.pdf", "seer stone"),
    ("Approved Construction Set_2-17-26 (2).pdf", "approved construction set"),
    ("GCHC North Stairs Plan 1 Grading.pdf", "gchc"),
    ("Measuring between geothermal wells on Barn C3.0", "barn sheet"),
    ("e.g. Ryno Construction LLC", "ryno"),
    ("Beau mont's printed sheet", "beaumont"),
    ("the Bill Dunn marks", "bill dunn"),
]
for text, want in OLD:
    ok(want in hits_in(text), f"the scan catches the old text {text[:48]!r} as {want!r}", hits_in(text))
# and does not cry wolf on the words it must leave alone
for text in ("name (House, Barn, Garage...)", "Automated detection can be wrong.",
             "Belle Farms Structural Plans.pdf", "C4.0-SITE-GRADING-PLAN-Rev.1.pdf",
             "e.g. 5000", "a note like [100' = 5000']"):
    ok(not hits_in(text), f"no false alarm on {text!r}", hits_in(text))
ok(hits_in(strip_js_comments('x = 1; // Oncor C-04\n/* Beaumont */ y = "ok";')) == [],
   "comments are excluded")
ok(hits_in(strip_js_comments('t.title = "the scanned aqua-vista set";')) == ["aqua vista"],
   "...strings are not")
# the comment scan (separately) sees the comments, and only them
_cj = 'x = "Oncor"; // measured on Oncor C-04\n// Wells Built / Seer\n// Stone A2.1\ny = 1;'
_ct = comment_text(_cj, js_comment_spans(_cj))
ok(len(_ct) == 1 and comment_hits_in(_ct[0][1]) == ["oncor", "seer stone", "wells built"],
   "the comment scan joins a passage, so a name broken across lines is caught", _ct)
ok(comment_text('s = "// not a comment"; r = /\\/\\//g;', js_comment_spans('s = "// not a comment"; r = /\\/\\//g;')) == [],
   "...and does not read strings or regex literals as comments")
_ch = '<p>Pierce</p><!-- Pierce\'s bands --><style>/* on Barn C3.0 */</style><script>// Beaumont\n</script>'
ok([comment_hits_in(t) for _, t in comment_text(_ch, html_comment_spans(_ch))]
   == [["pierce"], ["barn (the job)", "barn sheet"], ["beaumont"]], "HTML: <!-- -->, inline style and script comments")
for text, want in (("// Estimate #853 bills", "estimate #853"), ("// how #853 bills", "estimate #853"),
                   ("// on Kayden sheet 39", "kayden springs"), ("// Barn's own xref", "barn (the job)"),
                   ("// Bill's Mission Bell answer", "mission bell"), ("// (client_meet/beaumont", "client folder")):
    ok(want in comment_hits_in(text), f"comment scan catches {text!r} as {want!r}", comment_hits_in(text))
for text in ("// `BR FND - BARN EAST|A-WALL`", "// typing `BARN EAST` has to find it", "// a civil sheet (C3.0)",
             "// rather than implying a clean bill", "// Belle Farms Structural"):
    ok(not comment_hits_in(text), f"comment scan: no false alarm on {text!r}", comment_hits_in(text))
# the Python scan: comments joined across lines, docstrings, strings - and the dead string statement counts as a string
_py = ('"""Measured on Beaumont Guide civil page index 2."""\n'
       'def f():\n    fuzzy = True\n    """A schedule - Mission Bell\'s A-6.00."""\n'
       '# on Seer\n# Stone A2.1 (IMEG drew it)\nx = "SWR_Barn_Residence"  # Kevin Tsai\'s A830\n'
       'y = 1  # the estimate bills the same area twice; BILL THE GAPS; `Scott` and `scott`\n')
_pt = {(ln, kind): sorted({lab for lab, p in PY_PATS if p.search(t)}) for ln, kind, t in py_texts(_py)}
ok(_pt.get((1, "docstring")) == ["beaumont", "guide (the job)"], "Python scan: a module docstring", _pt)
ok(_pt.get((4, "string")) == ["mission bell"], "...a string statement that is not a docstring is a STRING", _pt)
ok(_pt.get((5, "comment")) == ["imeg", "seer stone"], "...a comment passage joined across lines", _pt)
ok(_pt.get((7, "string")) == ["barn (the job)", "barn sheet", "swr"] and _pt.get((7, "comment")) == ["kevin tsai"],
   "...a string literal and a trailing comment, separately", _pt)
ok(_pt.get((8, "comment")) == [], "...no false alarm on 'bills', 'BILL THE GAPS', `Scott`", _pt)

# ---- the shipped tree --------------------------------------------------------
b = scan_browser_text(ROOT)
ok(not b, "webui: no client name in any string, markup, attribute or style a browser receives",
   dict(list(b.items())[:20]))
bc = scan_browser_comments(ROOT)
ok(not bc, "webui: no client name in any COMMENT of a file a browser downloads (js, css, html, svg)",
   dict(list(bc.items())[:20]))
s = scan_server_data(ROOT)
ok(not s, "server data: no client name in demo-plans.json or server.py strings", dict(list(s.items())[:20]))
e = scan_engine_strings(ROOT)
ok(not e, "engine strings: no client name in a takeoff_engine / cutfill string literal", dict(list(e.items())[:20]))
srv_src = (ROOT / "server.py").read_text(encoding="utf-8")
ok(not any(f"takeoff_engine.{Path(t).stem}" in srv_src or f"import {Path(t).stem}" in srv_src for t in DEV_TOOLS),
   "the dev-only corpus tools (regress.py, sweep.py) are not imported by the server")
apps, docs_on, d = scan_docstring_surfaces()
ok(len(apps) >= 2 and not docs_on, "no app serves generated API docs (built from docstrings)",
   (len(apps), docs_on))
ok(not d, "...and none that did would carry a client name", d)
py_found, py_used = scan_image_python(ROOT)
ok(not py_found, "image Python: no client name in any comment, docstring or string literal of server.py, "
   "provision_demo.py, takeoff_engine/ or cutfill/ (allow-list aside)", dict(list(py_found.items())[:20]))
ok(py_used == set(PY_ALLOWED), "...and every allow-list entry is still needed (a stale one is removed, not kept)",
   sorted(set(PY_ALLOWED) - py_used))
o = scan_live_objects()
ok(not o, "loaded objects: _AI_GRADES, BETA, DEMO_PLANS, bid-sheet DEFAULT_LINES are clean", o)

fails = [c for c in CHECKS if not c[0]]
for good, msg, got in CHECKS:
    print(("ok:   " if good else "FAIL: ") + msg
          + ("" if good or got is None else f"   got={got!r}"))
print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")
sys.exit(1 if fails else 0)
