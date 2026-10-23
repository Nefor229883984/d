import importlib.util
import json
import os
from dataclasses import asdict, dataclass, field


@dataclass
class Diagnostic:
    file: str
    line: int | None
    severity: str          # "error" | "warning" | "note"
    code: str
    message: str
    note: dict | None = None      # {"line": int, "message": str}
    helps: list = field(default_factory=list)
    fix: dict | None = None


_EXPLAIN = {
    "E0382": ("use of moved value",
              "Value was moved (`move`); the source location can no longer be used. "
              "Move is linear: one ownership.",
              "Use the variable the value was moved into, or copy it beforehand "
              "(Copy types only)."),
    "E0505": ("cannot move while borrowed",
              "Location is held by a live borrow; cannot move until the borrow ends.",
              "Wait for the last use of the borrower, then move."),
    "E0499": ("double mut borrow",
              "One location cannot have two mutable borrows at once "
              "(law of exclusivity).",
              "Keep one writer; write through the owner, not an alias copy."),
    "E0515": ("ref escapes via return",
              "A borrow of local storage escapes the function via `return`; "
              "the storage dies with the frame.",
              "Return a copy/value, not a borrow; borrowing a parameter is fine — "
              "its storage belongs to the caller."),
    "E0596": ("assign to immutable",
              "Assignment to an immutable binding or through an immutable receiver.",
              "Declare `let mut ...` (or a `mut ...` parameter) to write."),
    "B0521": ("ref escapes via spawn",
              "A borrow captured by `spawn`; the frame is copied, the borrow "
              "hangs on dead storage — data race.",
              "Pass only Copy values or explicitly `move` into `spawn`; "
              "do not capture a shared non-Copy binding."),
    "B0101": ("use of undeclared var",
              "Name not declared in this scope (no `let`, parameter, "
              "`for`/`match` binding).",
              "Add `let <name> = ...` or import the name from another file."),
    "B0000": ("other error",
              "Unclassified diagnostic.",
              "See the message text above."),
}


def classify(message):
    """Message -> stable code, deterministic by text."""
    m = message
    if "use of moved" in m:
        return "E0382"
    if "while borrowed" in m or "cannot pass" in m or "cannot use" in m \
            or "cannot assign" in m:
        return "E0505"
    if "double mut borrow" in m or "law of exclusivity" in m:
        return "E0499"
    if "escapes via return" in m:
        return "E0515"
    if "escapes via spawn" in m or "return escapes spawn" in m:
        return "B0521"
    if ("assign to immutable" in m or "cannot mutate immutable" in m
            or "pass immutable" in m or "assign to const" in m
            or "write through" in m):
        return "E0596"
    if "use of undeclared var" in m:
        return "B0101"
    return "B0000"


def explain(code):
    return _EXPLAIN.get(code, ("other error",
                               "See the diagnostic text above.",
                               "Fix per the message."))


def _source_line(lines, ln):
    if ln is not None and 1 <= ln <= len(lines):
        # rstrip, not strip: keep the line's own indentation so the snippet
        # lines up with the file a reader has open beside the error.
        return lines[ln - 1].rstrip()
    return None


def to_human(path, src, diags):
    """Byte-stable output shared by every emitter:

        file:line: severity: message
          | source line
        file:line: note: note message
          | note source line
    """
    lines = src.splitlines()
    out = []
    for d in diags:
        ln = d.line
        sl = _source_line(lines, ln)
        if sl is not None:
            out.append("%s:%d: %s: %s" % (d.file, ln, d.severity, d.message))
            out.append("  | " + sl)
        else:
            out.append("%s: %s: %s" % (d.file, d.severity, d.message))
        if d.note:
            nln = d.note.get("line")
            nmsg = d.note.get("message", "")
            nsl = _source_line(lines, nln)
            if nsl is not None:
                out.append("%s:%d: note: %s" % (d.file, nln, nmsg))
                out.append("  | " + nsl)
            else:
                out.append("%s: note: %s" % (d.file, nmsg))
    return ("\n".join(out) + "\n") if out else ""


def _entries_to_diags(file, entries, kind="error"):
    diags = []
    for entry in entries:
        ln = entry[0]
        msg = entry[1]
        rel = entry[2] if len(entry) > 2 else None
        note = None
        if rel is not None:
            note = {"line": rel[0], "message": rel[1]}
        diags.append(Diagnostic(file, ln, kind, classify(msg), msg, note=note))
    return diags


def _load_sibling(name):
    try:
        return __import__(name)
    except ImportError:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py")
        spec = importlib.util.spec_from_file_location("diag_" + name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


def _load_brat():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brat.py")
    spec = importlib.util.spec_from_file_location("diag_brat_oracle", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_error_line_from(exc):
    """Line number of a BRAT lexer/parser error, or None."""
    if getattr(exc, "filename", None) is not None:
        return None
    import re
    m = re.search(r"\bat line (\d+)\s*\Z", str(exc))
    return int(m.group(1)) if m else None


def check_program(path, src, extra=None):
    """Check one file: (diagnostics, AST program or None)."""
    diags = []
    try:
        brat = _load_brat()
        prog = brat.parse_prog(brat.Cur(brat.lex(src)))
    except SyntaxError as e:
        diags.append(Diagnostic(path, _parse_error_line_from(e), "error",
                                "B0000", str(e)))
        return diags, None
    except Exception as e:
        diags.append(Diagnostic(path, None, "error", "B0000",
                                "parse failed: %s" % (e,)))
        return diags, None

    borrow = _load_sibling("borrow")
    typer = _load_sibling("typer")
    borrow_errs, borrow_warns = borrow.check(prog, extra)
    typer_errs = typer.check(prog)

    diags.extend(_entries_to_diags(path, borrow_errs, "error"))
    for wln, wmsg in borrow_warns:
        diags.append(Diagnostic(path, wln, "warning", classify(wmsg), wmsg))
    diags.extend(_entries_to_diags(path, typer_errs, "error"))
    return diags, prog


def to_json(path, diags):
    """Protocol v1."""
    payload = {
        "version": 1,
        "file": path,
        "diagnostics": [asdict(d) for d in diags],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


_SEVERITY_LSP = {"error": 1, "warning": 2, "note": 3, "info": 3}


def to_lsp(path, diags):
    out = []
    for d in diags:
        line = max(0, (d.line or 1) - 1)
        out.append({
            "range": {
                "start": {"line": line, "character": 0},
                "end": {"line": line, "character": 0},
            },
            "severity": _SEVERITY_LSP.get(d.severity, 3),
            "code": d.code,
            "source": "brat",
            "message": d.message,
        })
    return out