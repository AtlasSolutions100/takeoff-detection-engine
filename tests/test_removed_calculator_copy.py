"""No screen text offers the removed Earthwork calculator as a tool the user can use.

WHY. The old Earthwork calculator (and its panel) was replaced by Cut / fill on
2026-09-25. A live round on 2026-10-02 found the Work area tool's tip still
saying "The earthwork and trench calculators keep their own limit-of-work
boundary", sending the user to a tool that no longer exists. The words now name
what exists: Cut / fill and Trench.

Scanned: the text a browser receives from every webui/**/*.js and *.html file,
comments excluded (the fork's files and, once merged, the redesign frame's).
Allowed: sentences that say the OLD calculator is gone ("old Earthwork
calculator", "Earthwork from the old calculator was removed"), the "Earthwork"
module tab and the "Earthwork & Grading" trade.

    python tests/test_removed_calculator_copy.py
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "webui"

# a phrase that offers the removed tool; "old ..." before it is allowed
BAD = [
    re.compile(r"(?<!old )\bearthwork\s+(?:and\s+trench\s+)?calculators?\b", re.I),
    re.compile(r"(?<!old )\bearthwork\s+panel\b", re.I),
    re.compile(r"\bearthwork\s+and\s+trench\s+keep\b", re.I),
    re.compile(r"\bthe\s+calculators\s+keep\s+their\s+own\b", re.I),
]


def strip_js_comments(src):
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1]); i = j + 1
        elif src.startswith("//", i):
            # a regex literal or URL inside code can hold //; only treat it as a comment
            # when it starts a line's code or follows whitespace / punctuation
            prev = src[i - 1] if i else "\n"
            if prev in " \t\n;,{}()":
                j = src.find("\n", i); i = n if j < 0 else j
            else:
                out.append(c); i += 1
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2); i = n if j < 0 else j + 2
        else:
            out.append(c); i += 1
    return "".join(out)


def visible_text(path):
    src = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".html":
        src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
        # inline scripts are code: drop their comments too
        src = re.sub(r"(<script\b[^>]*>)(.*?)(</script>)",
                     lambda m: m.group(1) + strip_js_comments(m.group(2)) + m.group(3), src, flags=re.S)
        return src
    return strip_js_comments(src)


def main():
    files = [p for p in WEB.rglob("*") if p.suffix in (".js", ".html") and "vendor" not in p.parts]
    assert files, "no webui files found"
    hits = []
    for p in files:
        text = visible_text(p)
        flat = re.sub(r"[\"'`]\s*\+\s*[\"'`]", "", text)   # join "a " + "b" across concatenations
        flat = re.sub(r"\s+", " ", flat)
        for rx in BAD:
            for m in rx.finditer(flat):
                hits.append(f"{p.relative_to(ROOT)}: ...{flat[max(0, m.start() - 60):m.end() + 40]}...")
    assert not hits, "screen text still offers the removed Earthwork calculator:\n  " + "\n  ".join(hits)

    # the Work area tool names what exists now, in its tip, its status line and its tally note
    html = (WEB / "index.html").read_text(encoding="utf-8")
    tip = re.search(r'id="wzBtn"[^>]*title="([^"]*)"', html)
    assert tip and "Cut / fill and Trench keep their own limit of work" in tip.group(1), tip and tip.group(1)
    app = re.sub(r"\s+", " ", re.sub(r"[\"'`]\s*\+\s*[\"'`]", "", (WEB / "app.js").read_text(encoding="utf-8")))
    assert "(Cut / fill and Trench keep their own limit of work.)" in app
    assert "Cut / fill and Trench keep their own boundary (the limit of work)" in app

    # what must stay: the module's name, the trade, and the old-earthwork note
    assert "Earthwork & Grading" in app
    print(f"OK: {len(files)} webui files, no screen text offers the removed Earthwork calculator")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("FAIL:", e)
        sys.exit(1)
