"""A MERGE CAN JOIN ONE SIDE'S HELPER WITH THE OTHER SIDE'S LOCAL OF THE SAME NAME, and git sees no conflict.

Found at the merge of 2026-10-04 (the takeoff starts empty, bids by e-mail, wall panels): the redesign's server.py had
a module-level helper `_n(n, word)` ("1 file" / "3 files") that its audit lines call; the fork's delete_job gained a
loop `for _n in _files:`. A name assigned anywhere in a function is a LOCAL of the whole function, so the audit line at
the end of delete_job - `_n(removed, "file")` - raised UnboundLocalError: the project was already deleted, its registry
row dropped, and the route answered 500. No test of the fork could see it (the fork has no such helper) and the merge
was textually clean. tests/test_team_roles.py caught it by deleting a project.

The helper is now `_audit_n`. This test holds the class of defect, in the files where the two sides meet: no function
may bind, as a local, a name that is a module-level function of the same file AND call that name.

Dependency-free:  python tests/test_merge_shadow.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["server.py", "takeoff_engine/users.py", "takeoff_engine/audit.py", "takeoff_engine/inbound.py",
         "takeoff_engine/wp_job.py", "takeoff_engine/wp_settings.py"]

fails = 0


def check(ok, msg):
    global fails
    if ok:
        print("ok:", msg)
    else:
        fails += 1
        print("FAIL:", msg)


def shadowed_calls(src: str) -> list:
    """[(function, its line, name, line bound, line called)] for every function that binds a module-level function's
    name as a local and also calls it. Nested functions, lambdas and comprehensions are scopes of their own."""
    tree = ast.parse(src)
    mod_funcs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    hits = []

    def scan(fn):
        bound, called, declared = {}, {}, set()
        args = {a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs}
        for extra in (fn.args.vararg, fn.args.kwarg):
            if extra:
                args.add(extra.arg)

        def walk(node):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    bound.setdefault(ch.name, ch.lineno)
                    continue                                   # its body is another scope (scanned on its own)
                if isinstance(ch, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                    for x in ast.walk(ch):                     # their targets are their own; their calls read ours
                        if isinstance(x, ast.Call) and isinstance(x.func, ast.Name):
                            called.setdefault(x.func.id, x.lineno)
                    continue
                if isinstance(ch, (ast.Global, ast.Nonlocal)):
                    declared.update(ch.names)
                if isinstance(ch, ast.Name) and isinstance(ch.ctx, ast.Store):
                    bound.setdefault(ch.id, ch.lineno)
                if isinstance(ch, (ast.Import, ast.ImportFrom)):
                    for a in ch.names:
                        bound.setdefault((a.asname or a.name).split(".")[0], ch.lineno)
                if isinstance(ch, ast.ExceptHandler) and ch.name:
                    bound.setdefault(ch.name, ch.lineno)
                if isinstance(ch, ast.Call) and isinstance(ch.func, ast.Name):
                    called.setdefault(ch.func.id, ch.lineno)
                walk(ch)

        walk(fn)
        for name, line in bound.items():
            if name in mod_funcs and name in called and name not in declared and name not in args:
                hits.append((fn.name, fn.lineno, name, line, called[name]))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan(node)
    return hits


# ---- the checker sees the defect it is for (the merged delete_job, cut down to its shape) ----------------------------
BROKEN = '''
def _n(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")

def delete_job(files):
    removed = 0
    for _n in files:
        removed += 1
    return _n(removed, "file") + " removed"
'''
FIXED = BROKEN.replace("def _n(", "def _audit_n(").replace("return _n(removed", "return _audit_n(removed")
check(shadowed_calls(BROKEN) == [("delete_job", 5, "_n", 7, 9)], "the checker finds a helper shadowed by a loop variable")
check(shadowed_calls(FIXED) == [], "...and nothing once the helper has a name of its own")
check(shadowed_calls("def f():\n    return 1\n\ndef g():\n    x = [f for f in (1, 2)]\n    return f()\n") == [],
      "a comprehension's own variable is not a local of the function around it")
check(shadowed_calls("def f():\n    return 1\n\ndef g():\n    def f():\n        return 2\n    return f()\n")
      == [("g", 4, "f", 5, 7)], "a nested def of the same name is reported too (it hides the module's)")

# ---- the files where the fork's code and the redesign's meet -----------------------------------------------------
for rel in FILES:
    p = ROOT / rel
    if not p.exists():
        check(False, f"{rel} is missing")
        continue
    hits = shadowed_calls(p.read_text(encoding="utf-8"))
    check(not hits, f"{rel}: no function calls a module-level function it also binds as a local"
          + ("".join(f"\n      def {fn} (line {fl}): `{nm}` bound at line {bl}, called at line {cl}"
                     for fn, fl, nm, bl, cl in hits)))

# ---- the one found: the audit helper has its own name, and delete_job's audit line uses it ---------------------------
srv = (ROOT / "server.py").read_text(encoding="utf-8")
check("\ndef _audit_n(n, word: str) -> str:" in srv and "\ndef _n(" not in srv,
      "server.py: the audit helper is _audit_n (no module-level _n for a loop variable to hide)")
dj = srv[srv.index("\ndef delete_job("):]
dj = dj[:dj.index("\n@app.", 10)]
check('_audit(request, "project_delete"' in dj and '_audit_n(removed, "file")' in dj and "for _n in _files" in dj,
      "delete_job: the fork's loop is as the fork wrote it, and the project_delete line counts with _audit_n")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all merge-shadow checks passed")
