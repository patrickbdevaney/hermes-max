"""Advisory quality-bar checker (plan/execute split, Stage 4).

A senior reviewer flags things the deterministic test/lint/typecheck gate does
NOT: a public function with no docstring, missing type annotations, a leftover
TODO/placeholder, a bare `except:` that swallows everything. None of these fail a
build — but their absence is the texture that separates senior-grade output from
correct-but-shallow output. So this is ADVISORY ONLY: it surfaces warnings; it
never flips the hard gate (verify_core.verify) red.

Pure stdlib `ast` — no model call, no network, no extra dependency. Walks module-
level public functions AND public methods of public classes (the AST idiom mirrors
enhanced_verify._public_functions, extended to class bodies). Never raises: a
missing/unparseable path returns {ok: False, reason}.
"""

from __future__ import annotations

import ast
import os
import re
from typing import Any

# leftover-work markers a senior reviewer would not ship
_PLACEHOLDER = re.compile(r"\b(TODO|FIXME|XXX|HACK|placeholder|stub|not implemented|"
                          r"implement me|fill in)\b", re.I)


def _otel(name: str, attrs: dict) -> None:
    """Best-effort OTel span. Never raises (observability is optional)."""
    try:
        import otel_emit

        otel_emit.record(name, attrs, status="ok")
    except Exception:  # noqa: BLE001 - observability is optional
        pass


def _is_public(name: str) -> bool:
    """Public = not a dunder and not a single-underscore private. __init__ etc. are
    dunders (private API) and are exempt from the docstring requirement, but their
    parameter annotations are still checked (see _func_findings)."""
    return not name.startswith("_")


def _missing_annotations(fn: ast.FunctionDef | ast.AsyncFunctionDef,
                         is_method: bool) -> list[str]:
    """Return the names of params/return lacking a type annotation. `self`/`cls` on
    a method are exempt; *args/**kwargs are checked if present."""
    missing: list[str] = []
    a = fn.args
    posonly = list(getattr(a, "posonlyargs", []))
    params = posonly + list(a.args)
    skip_first = is_method and params and params[0].arg in ("self", "cls")
    for i, arg in enumerate(params):
        if skip_first and i == 0:
            continue
        if arg.annotation is None:
            missing.append(arg.arg)
    for arg in list(a.kwonlyargs):
        if arg.annotation is None:
            missing.append(arg.arg)
    if a.vararg is not None and a.vararg.annotation is None:
        missing.append("*" + a.vararg.arg)
    if a.kwarg is not None and a.kwarg.annotation is None:
        missing.append("**" + a.kwarg.arg)
    if fn.returns is None:
        missing.append("->return")
    return missing


def _func_findings(fn: ast.FunctionDef | ast.AsyncFunctionDef, qualname: str,
                   is_method: bool, annotations: list[str],
                   docstrings: list[str]) -> None:
    """Record annotation/docstring gaps for one public function/method (in place)."""
    miss = _missing_annotations(fn, is_method)
    if miss:
        annotations.append(f"{qualname}: missing annotation(s) for {', '.join(miss)}")
    # dunders (e.g. __init__, __contains__) are exempt from the docstring rule
    if not fn.name.startswith("__") and ast.get_docstring(fn) is None:
        docstrings.append(qualname)


def quality_check(path: str) -> dict[str, Any]:
    """Advisory senior-review pass over a Python file (NO model call, NO hard gate).

    Flags, for public functions and public methods of public classes:
      • annotations_missing — params or return without a type annotation;
      • docstrings_missing  — no docstring (dunders exempt);
      • placeholders        — TODO/FIXME/placeholder/stub markers anywhere in the file;
      • bare_excepts        — `except:` with no exception type (swallows everything).

    Returns {ok:True, status:"advisory", path, annotations_missing, docstrings_missing,
    placeholders, bare_excepts, clean, summary}. `clean` is True iff all four buckets
    are empty. This NEVER fails a build — it raises the texture toward senior-review
    standard; the hard pass/fail stays verify_core.verify. A missing/unparseable/
    non-Python path returns {ok:False, reason}. Never raises. Emits a quality_check
    span (the four counts).
    """
    abspath = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(abspath):
        return {"ok": False, "reason": f"not a file: {abspath}"}
    if not abspath.endswith(".py"):
        return {"ok": False, "reason": f"quality_check is Python-only (got {abspath})"}
    try:
        src = open(abspath, encoding="utf-8").read()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"unreadable: {type(e).__name__}: {e}"}
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"ok": False, "reason": f"syntax error (run verify first): {e}"}

    annotations_missing: list[str] = []
    docstrings_missing: list[str] = []
    bare_excepts: list[str] = []

    # module-level public functions
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(node.name):
            _func_findings(node, node.name, is_method=False,
                           annotations=annotations_missing, docstrings=docstrings_missing)
        # public classes -> their public methods
        elif isinstance(node, ast.ClassDef) and _is_public(node.name):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and (_is_public(item.name) or item.name.startswith("__")):
                    _func_findings(item, f"{node.name}.{item.name}", is_method=True,
                                   annotations=annotations_missing, docstrings=docstrings_missing)

    # bare except: anywhere
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            bare_excepts.append(f"line {node.lineno}")

    # placeholder markers (text scan, including comments)
    placeholders = [f"line {i}: {m.group(0)}"
                    for i, line in enumerate(src.splitlines(), 1)
                    for m in [_PLACEHOLDER.search(line)] if m]

    clean = not (annotations_missing or docstrings_missing or placeholders or bare_excepts)
    summary = ("clean — no senior-review texture gaps" if clean else
               f"{len(annotations_missing)} annotation, {len(docstrings_missing)} docstring, "
               f"{len(placeholders)} placeholder, {len(bare_excepts)} bare-except gap(s) (advisory)")
    _otel("quality_check", {"annotations_missing": len(annotations_missing),
                            "docstrings_missing": len(docstrings_missing),
                            "placeholders": len(placeholders),
                            "bare_excepts": len(bare_excepts)})
    return {"ok": True, "status": "advisory", "path": abspath,
            "annotations_missing": annotations_missing,
            "docstrings_missing": docstrings_missing,
            "placeholders": placeholders, "bare_excepts": bare_excepts,
            "clean": clean, "summary": summary}
