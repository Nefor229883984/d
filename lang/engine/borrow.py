import importlib.util
import os
import re
import sys

try:
    from brat.parser import _LINES
except ImportError:
    try:
        from parser import _LINES
    except ImportError:
        _LINES = {}

try:
    from diag import to_human, _entries_to_diags
except ImportError:  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from diag import to_human, _entries_to_diags


class Errs(list):
    def __init__(self):
        super().__init__()
        self.line = None

    def note(self, msg, rel=None):
        self.append((self.line, msg, rel))


BUILTINS = {
    "print", "log.info", "log.warn", "log.error",
    "trace.span",
    "join_all",
    "len", "__at", "Ok", "Err", "Some", "unwrap", "is_err", "is_none", "is_vec", "is_str", "is_map", "tagof",
    "str", "read_file", "write_file", "assert",
    "args", "exit", "eprint", "raise", "time_ms", "sleep_ms", "env_get",
    "__arena_enter", "__arena_leave", "own",
    "re_match",
    "tcp_listen", "tcp_connect", "tcp_accept",
    "tcp_send", "tcp_recv", "tcp_close",
    "sbuf", "sbuf_add", "sbuf_len", "sbuf_str",
}

BUILTIN_HEADS = set(b.split(".", 1)[0] for b in BUILTINS if "." in b)

MUTATORS = {"push", "pop", "sort", "sort_by", "ins", "del", "set"}

COPY_ANNS = {"i32", "i64", "u64", "f64", "bool"}

TYPE_WORDS = ("i32", "i64", "u64", "f64", "bool", "str")

_INTERP_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}")


class Sig(dict):
    """fn name -> param modes, plus `heads` (legal dotted callee heads) and `umeth`
    (method name -> takes `mut self`)."""

    def __init__(self):
        super().__init__()
        self.heads = set()
        self.umeth = {}


def fn_table(extra=None):
    t = Sig()
    if extra:
        t.update(extra)
    return t


def place_of(e):
    """Path naming the storage `e` denotes, or None if `e` computes a fresh value."""
    while isinstance(e, tuple) and len(e) > 1 and e[0] == "quest":
        e = e[1]
    if not isinstance(e, tuple) or not e:
        return None
    k = e[0]
    if k == "var":
        return e[1]
    if k == "move":
        return place_of(e[1])
    if k == "field":
        b = place_of(e[1])
        return b + "." + e[2] if b else None
    if k == "idx":
        b = place_of(e[1])
        return b + ".*" if b else None
    return None


def alias_owner(name, decl, depth=0):
    """Follow `let y = x` to the variable whose storage writing `name` writes."""
    if depth > 32:
        return name
    d = decl.get(name)
    if d is None or not d.get("alias"):
        return name
    src = d.get("src")
    if src and src != name:
        return alias_owner(src, decl, depth + 1)
    return name


def is_copy_expr(e, decl):
    if not isinstance(e, tuple) or not e:
        return False
    k = e[0]
    if k in ("num", "bool"):
        return True
    if k == "var":
        d = decl.get(e[1])
        return bool(d and d.get("copy"))
    if k == "un":
        return is_copy_expr(e[2], decl)
    if k == "move":
        return is_copy_expr(e[1], decl)
    if k == "bin":
        return is_copy_expr(e[2], decl) and is_copy_expr(e[3], decl)
    return False


def deref_place(e, decl, funcs):
    """Undo the parser's bare-name lowering for an uppercase local.

    The parser reads any bare uppercase identifier as a zero-arg call, so a
    local named `C` arrives as `('call', 'C', [])` rather than `('var', 'C')`.
    When such a call names a local in scope and takes no arguments it is a read
    of that local, so the ownership rules have to see the place it names, not an
    opaque call. Anything else stays as it came.
    """
    if (isinstance(e, tuple) and len(e) == 3 and e[0] == "call" and isinstance(e[1], str)
            and e[2] == [] and "." not in e[1] and e[1] in decl and e[1] not in funcs):
        return ("var", e[1])
    return e


def elem_copy(decl, base):
    """True when the element type behind the container `base` denotes is Copy.

    Only an annotation settles it (SPEC section 10): `vec[i64]` reads an element
    as a value copy, so `v[0]` is a fresh value and `let y = v[0]` borrows nothing
    — `fastvec.brat` and `generic.brat` depend on that. An unannotated binding is
    dynamically typed in v0.1, so its element type genuinely cannot be determined;
    it is conservatively non-Copy and `v[0]` hands back a borrow of the slot.
    """
    while isinstance(base, tuple) and len(base) > 1 and base[0] == "quest":
        base = base[1]
    if not isinstance(base, tuple) or not base:
        return False
    k = base[0]
    if k in ("move", "field", "idx"):
        return elem_copy(decl, base[1])
    if k != "var":
        return False
    d = decl.get(base[1])
    ann = d.get("ann") if d else None
    if not isinstance(ann, str):
        return False
    a = ann.strip()
    if a.startswith("vec[") and a.endswith("]"):
        a = a[4:-1].strip()
    elif a.startswith("map[") and a.endswith("]"):
        parts = a[4:-1].split(",")
        a = parts[-1].strip()
    else:
        return False
    return a in COPY_ANNS


def borrow_srcs(e, decl, funcs):
    """Locals whose storage `e` aliases, so `let y = e` borrows them.

    Only container wrappers propagate: `let v = [a]` keeps `a`'s storage reachable
    through `v`, so `return v[0]` hands back a borrow of `a`. A projection reaches
    into the container's own storage, so `let y = v[i]` / `let y = p.f` borrows `v`
    / `p` unless the element type is a provable Copy. Anything that computes a fresh
    value (arith, unary, a call, an unknown node) yields nothing, so `len(a)` and
    `a + b` stay copies.
    """
    while isinstance(e, tuple) and len(e) > 1 and e[0] == "quest":
        e = e[1]
    if not isinstance(e, tuple) or not e:
        return []
    e = deref_place(e, decl, funcs)
    k = e[0]
    if k == "var":
        d = decl.get(e[1])
        if d is None or d.get("moved") or d.get("copy"):
            return []
        src = d.get("src")
        if src and src != e[1]:
            return borrow_srcs(("var", src), decl, funcs)
        return [e[1]]
    if k == "move":
        return borrow_srcs(e[1], decl, funcs)
    if k == "idx":
        return [] if elem_copy(decl, e[1]) else borrow_srcs(e[1], decl, funcs)
    if k == "field":
        return [] if elem_copy(decl, e[1]) else borrow_srcs(e[1], decl, funcs)
    if k == "vec":
        return _flat(borrow_srcs(x, decl, funcs) for x in e[1])
    if k == "maplit":
        return _flat(borrow_srcs(a, decl, funcs) + borrow_srcs(b, decl, funcs) for a, b in e[1])
    if k == "struct":
        return _flat(borrow_srcs(v, decl, funcs) for _, v in e[2])
    if k == "or":
        return borrow_srcs(e[1], decl, funcs) + borrow_srcs(e[2], decl, funcs)
    if k == "call":
        if e[1] in ("ref", "borrow") and e[1] not in funcs and e[2]:
            return borrow_srcs(e[2][0], decl, funcs)
        return []
    return []


def _flat(groups):
    out = []
    for g in groups:
        for x in g:
            if x not in out:
                out.append(x)
    return out


def vars_in(e, acc):
    if not isinstance(e, tuple) or not e:
        return acc
    k = e[0]
    if k == "var":
        acc.append(e[1])
    elif k in ("num", "bool", "none"):
        return acc
    elif k == "str":
        for m in _INTERP_RE.finditer(e[1]):
            acc.append(name_root(m.group(1)))
        return acc
    elif k == "un":
        vars_in(e[2], acc)
    elif k == "move":
        vars_in(e[1], acc)
    elif k == "closure":
        return acc
    elif k == "bin":
        vars_in(e[2], acc)
        vars_in(e[3], acc)
    elif k == "or":
        vars_in(e[1], acc)
        vars_in(e[2], acc)
    elif k == "quest":
        vars_in(e[1], acc)
    elif k == "mcall":
        vars_in(e[1], acc)
        for a in e[3]:
            vars_in(a, acc)
    elif k == "vec":
        for x in e[1]:
            vars_in(x, acc)
    elif k == "maplit":
        for ke, ve in e[1]:
            vars_in(ke, acc)
            vars_in(ve, acc)
    elif k == "struct":
        for _, v in e[2]:
            vars_in(v, acc)
    elif k == "field":
        vars_in(e[1], acc)
    elif k == "idx":
        vars_in(e[1], acc)
        vars_in(e[2], acc)
    elif k == "call":
        acc.append(name_root(e[1]))
        for a in e[2]:
            vars_in(a, acc)
    return acc


def walk_closure_arg(a, decl, errs, funcs):
    """Check the body of an inline closure passed as an argument.

    `trace.span("x") { ... }` and `v.map(fn(x) { ... })` carry real code in the
    argument position, and it runs with the enclosing scope in view, so the
    ownership rules have to see it.
    """
    if isinstance(a, tuple) and a and a[0] == "closure":
        _, cname, cparams, cbody = a
        sub = dict(decl)
        if isinstance(cname, str):
            sub.setdefault(cname, bare_decl())
        for p in cparams:
            pn = p[0] if isinstance(p, tuple) else p
            sub[pn] = param_decl(p)
        walk_block(cbody, sub, {}, errs, funcs)


def undeclared_rel(name, node=None):
    """`rel` note for a name the scope does not have: how to introduce it.

    A newcomer reads `use of undeclared var `hits`` as "the checker lost my
    variable". The note has to say what IS in scope and, for the two shapes the
    parser rejects, what the correct spelling is -- `struct` is not a
    declaration form in this language, and a type name is never a value. A name
    that is really the tail of a cut string gets no scope lecture: it is not a
    scope problem at all.
    """
    if node is not None and id(node) in _CUT_PARTS:
        return None
    if name == "struct":
        return (None, "help: BRAT has no `struct` declaration: a value is written "
                      "`Name { field: value, ... }`, and `Name` is only a tag")
    if name in TYPE_WORDS:
        return (None, "help: `%s` is a type name for annotations "
                      "(`let n: %s = ...`), not a value" % (name, name))
    if name in ("enum", "trait", "impl"):
        return (None, "help: `%s` starts a declaration, so it cannot be used "
                      "inside an expression" % name)
    return (None, "help: in scope here are `let` bindings, fn parameters, `for` "
                  "bindings and `match` arm bindings; functions come from `fn`/`impl`; "
                  "a name from another file needs `import that.file`")


def check_expr(e, decl, errs, funcs, inpath=False):
    if not isinstance(e, tuple) or not e:
        errs.note("bad expr node %r" % (e,))
        return
    k = e[0]
    if k == "var":
        if e[1] not in decl and e[1] not in funcs:
            errs.note("use of undeclared var `%s`" % e[1], rel=undeclared_rel(e[1], e))
        elif e[1] in decl and decl[e[1]].get("moved"):
            errs.note("use of moved `%s` (hint: the value was moved; use a copy or re-bind)" % e[1],
                      rel=(decl[e[1]].get("moved_line"), "`%s` moved here" % e[1]))
        elif e[1] in decl and decl[e[1]].get("frozen_by"):
            errs.note("cannot use `%s` while mutably borrowed by `%s` (hint: end the borrow first)" % (e[1], decl[e[1]]["frozen_by"][0]),
                      rel=(decl[e[1]]["frozen_by"][1], "mut borrow here"))
    elif k in ("num", "str", "bool", "none", "rstr"):
        return
    elif k == "un":
        check_expr(e[2], decl, errs, funcs)
    elif k == "move":
        check_expr(e[1], decl, errs, funcs)
    elif k == "closure":
        walk_closure_arg(e, decl, errs, funcs)
    elif k == "bin":
        check_expr(e[2], decl, errs, funcs)
        check_expr(e[3], decl, errs, funcs)
    elif k == "or":
        check_expr(e[1], decl, errs, funcs)
        check_expr(e[2], decl, errs, funcs)
    elif k == "quest":
        check_expr(e[1], decl, errs, funcs)
    elif k == "mcall":
        bp = write_path(deref_place(e[1], decl, funcs))
        if bp is None or not callee_known(bp + "." + e[2], decl, funcs):
            check_expr(e[1], decl, errs, funcs)
        for a in e[3]:
            check_expr(a, decl, errs, funcs)
    elif k == "vec":
        for x in e[1]:
            check_expr(x, decl, errs, funcs)
    elif k == "maplit":
        for ke, ve in e[1]:
            check_expr(ke, decl, errs, funcs)
            check_expr(ve, decl, errs, funcs)
    elif k == "struct":
        for _, v in e[2]:
            check_expr(v, decl, errs, funcs)
    elif k == "field":
        check_expr(e[1], decl, errs, funcs, True)
        p = path_of(e)
        if p and not inpath:
            d = decl.get(name_root(p))
            if d is not None and not d.get("moved") and any(paths_conflict(p, mp) for mp in d.get("moved_paths", ())):
                errs.note("use of moved `%s`" % p)
    elif k == "idx":
        check_expr(e[1], decl, errs, funcs, True)
        check_expr(e[2], decl, errs, funcs)
        p = path_of(e)
        if p and not inpath:
            d = decl.get(name_root(p))
            if d is not None and not d.get("moved") and any(paths_conflict(p, mp) for mp in d.get("moved_paths", ())):
                errs.note("use of moved `%s`" % p)
    elif k == "call":
        for a in e[2]:
            check_expr(a, decl, errs, funcs)
        if not callee_known(e[1], decl, funcs):
            errs.note("use of undeclared var `%s`" % name_root(e[1]),
                      rel=undeclared_rel(name_root(e[1])))
    else:
        errs.note("bad expr node `%s`" % k)


def name_root(name):
    """Head of a possibly-dotted name: `a.b.c` -> `a`, a bare name unchanged.

    A caller never needs the tail here; every place root it hands out is looked
    up in `decl`, which is keyed by the binding's bare name.
    """
    return name.split(".", 1)[0] if isinstance(name, str) and "." in name else name


def callee_known(name, decl, funcs):
    if not isinstance(name, str):
        return True
    if name in funcs or name in BUILTINS:
        return True
    if "." not in name:
        return name in decl
    head = name_root(name)
    if head in decl or head in funcs or head in BUILTIN_HEADS:
        return True
    return head in getattr(funcs, "heads", ())


def used_in_stmt(s, acc):
    k = s[0]
    if k == "let":
        vars_in(s[2], acc)
    elif k == "set":
        acc.append(name_root(s[1]))
        vars_in(s[2], acc)
    elif k == "setidx":
        vars_in(s[1], acc)
        vars_in(s[2], acc)
        vars_in(s[3], acc)
    elif k == "setfield":
        vars_in(s[1], acc)
        vars_in(s[3], acc)
    elif k == "return":
        vars_in(s[1], acc)
    elif k == "expr":
        vars_in(s[1], acc)
    elif k == "if":
        vars_in(s[1], acc)
        used_in_block(s[2], acc)
        if s[3]:
            eb = s[3]
            used_in_block(eb if isinstance(eb, list) else [eb], acc)
    elif k == "while":
        vars_in(s[1], acc)
        used_in_block(s[2], acc)
    elif k == "foriter":
        vars_in(s[2], acc)
        used_in_block(s[3], acc)
    elif k == "forrange":
        vars_in(s[2], acc)
        vars_in(s[3], acc)
        used_in_block(s[4], acc)
    elif k == "try":
        used_in_block(s[1], acc)
        used_in_block(s[2], acc)
    elif k == "spawn":
        used_in_block(s[1], acc)
    elif k == "defer":
        vars_in(s[1], acc)
    elif k == "impl":
        for f in s[3]:
            used_in_block(f[3], acc)
    elif k == "fn":
        used_in_block(s[3], acc)
    elif k == "enum":
        return acc
    elif k == "trait":
        return acc
    elif k == "import":
        return acc
    elif k == "extern":
        return acc
    elif k == "extern":
        return acc
    elif k == "pub":
        used_in_stmt(s[1], acc)
    return acc


def used_in_block(body, acc):
    for s in body:
        if isinstance(s, tuple) and s:
            used_in_stmt(s, acc)
    return acc


def path_of(e):
    if not isinstance(e, tuple) or not e:
        return None
    if e[0] == "var":
        return e[1]
    if e[0] == "field":
        b = path_of(e[1])
        return b + "." + e[2] if b else None
    if e[0] == "idx":
        b = path_of(e[1])
        return b + ".*" if b else None
    return None


def write_path(e):
    """Dotted path of a pure name chain, or None when `e` names computed storage.

    `a` and `a.b` are chains; a bare uppercase name arrives as `('call', 'a', [])`
    and is the same chain. `a[0].b` and `f().b` are not: an index or a call result
    is a location the binding does not name, so the path-based write rules do not
    apply to it.
    """
    if not isinstance(e, tuple) or not e:
        return None
    if e[0] == "var":
        return e[1]
    if e[0] == "field":
        b = write_path(e[1])
        return b + "." + e[2] if b else None
    if e[0] == "call" and len(e) == 3 and isinstance(e[1], str) and "." not in e[1] and not e[2]:
        # A bare uppercase name is a static chain (`Red.field`); a lowercase
        # zero-arg call is a computed value (`f().field`).
        return e[1] if e[1][:1].isupper() else None
    return None


def paths_conflict(p, q):
    if p == q:
        return True
    if p.startswith(q + ".") or q.startswith(p + "."):
        return True
    if q == p + ".*" or p == q + ".*":
        return True
    if q.endswith(".*") and p.startswith(q[:-1]):
        return True
    if p.endswith(".*") and q.startswith(p[:-1]):
        return True
    return False


def moved_targets(e, acc):
    if not isinstance(e, tuple) or not e:
        return acc
    if e[0] == "move" and isinstance(e[1], tuple) and e[1]:
        p = path_of(e[1])
        if p is not None:
            acc.append(p)
            return acc
    for sub in e[1:]:
        if isinstance(sub, tuple):
            moved_targets(sub, acc)
        elif isinstance(sub, list):
            for x in sub:
                if isinstance(x, tuple):
                    moved_targets(x, acc)
    return acc


def scan_moves(e, decl, borrows, errs):
    for v in moved_targets(e, []):
        root = name_root(v)
        d = decl.get(root)
        if d is None:
            continue
        if d.get("moved"):
            errs.note("use of moved `%s`" % root, rel=(d.get("moved_line"), "`%s` moved here" % root))
            continue
        if any(paths_conflict(v, mp) for mp in d.get("moved_paths", ())):
            errs.note("use of moved `%s`" % v)
            continue
        live = borrows.get(root, [])
        if live:
            errs.note("cannot move `%s` while borrowed by `%s`" % (v, live[-1][0]),
                      rel=(live[-1][2], "borrowed by `%s` here" % live[-1][0]))
            continue
        if v == root:
            d["moved"] = True
            d["moved_line"] = errs.line
        else:
            d.setdefault("moved_paths", set()).add(v)


def mutate_kind(meth, umeth):
    """Why calling `meth` writes, or None when it only reads."""
    if meth in umeth:
        return "mut self" if umeth[meth] else None
    return "builtin" if meth in MUTATORS else None


def decl_hint(d, root):
    """`(line, text)` pointing at the binding that should have been `mut`.

    Without it the note, the newcomer blames the checker: the interpreter runs
    the write happily and only `brat check` refuses it, so the message has to
    name the line where the binding was made without `mut`.
    """
    if not d or d.get("dl") is None:
        return None
    if d.get("const"):
        return (d.get("dl"), "`%s` declared here as const" % root)
    return (d.get("dl"), "`%s` declared here without `mut` (help: `let mut %s`)" % (root, root))


def check_write(path, decl, borrows, errs, rebind=False, strict=True):
    """True when storing through `path` is legal. `rebind` marks `x = v`.

    A spawn frame is a copy, so a rebind there is a lost write; writing *through* a
    shared container inside spawn does reach the parent and is checked normally.
    `strict` is off only for a write whose last component is a *field* name:
    the corpus writes into nested containers behind an immutable binding, so
    there only const, moved and aliasing are enforced. An index write
    (`m["a"]["b"] = 2`, `p.items[0] = 9`) reaches into storage the binding owns
    and is checked exactly like a plain `v[0] = x`. Rebinding an alias is not
    aliasing: `let mut y = x; y = 1` leaves `x` alone, only `y[0] = 1` writes through.
    """
    root = name_root(path)
    d = decl.get(root)
    if d is None:
        return True
    if d.get("const"):
        errs.note("assign to const `%s`" % path)
        return False
    if d.get("moved"):
        errs.note("use of moved `%s`" % root, rel=(d.get("moved_line"), "`%s` moved here" % root))
        return False
    if d.get("spawn_frozen"):
        if rebind:
            errs.note("assign to captured `%s` in spawn (captured by copy, write is lost)" % root)
            return False
    if strict and not d.get("mut"):
        errs.note("assign to immutable `%s` (want `let mut` or mut param)" % path,
                  rel=decl_hint(d, root))
        return False
    if not rebind:
        if alias_place_conflict(root, path, decl, errs, root):
            return False
    live = borrows.get(root, [])
    if live:
        errs.note("cannot assign `%s` while borrowed by `%s`" % (root, live[-1][0]),
                  rel=(live[-1][2], "borrowed by `%s` here" % live[-1][0]))
        return False
    return True


def alias_place_conflict(root, path, decl, errs, who):
    """True when `root` is an alias over storage its owner already owns mutably.

    `let mut y = v` on a non-Copy value is an alias, not a copy (SPEC 3, Copy types
    are `i32/i64/u64/f64/bool`), so writing through `y` leaves `v` itself as a second
    mutable place over one heap buffer: one writer or N readers, never two writers.
    A Copy binding never reaches here — it records no borrow and has no owner.
    """
    own = alias_owner(root, decl)
    if own == root:
        return False
    od = decl.get(own)
    if od is None:
        return False
    if od.get("const"):
        errs.note("assign to const `%s` through alias `%s`" % (path, root))
        return True
    if od.get("mut") is False:
        errs.note("write through `%s` mutates immutable `%s`" % (root, own))
        return True
    if od.get("mut"):
        errs.note("double mut borrow of `%s`: `%s` through alias `%s` is a second mutable place"
                  % (own, who, root),
                  rel=(None, "help: `%s` is a second mutable place over `%s`; keep one "
                             "writer -- write through the owner (`%s[key][...] = ...`) "
                             "instead of holding a mutating copy of one element"
                             % (root, own, own)))
        return True
    return False


def check_mutate(path, decl, borrows, errs, kind, who):
    """A mutating method writes through its receiver.

    A `mut self` method says so in its signature, so an immutable receiver is an
    error outright. A builtin mutator (push/sort/set/...) writes through the
    receiver exactly the same way, so the immutability rule is the same; what
    differs is only the wording.
    """
    root = name_root(path)
    d = decl.get(root)
    if d is None:
        return False
    if d.get("const"):
        errs.note("assign to const `%s`" % path)
        return False
    if d.get("moved"):
        errs.note("use of moved `%s`" % root, rel=(d.get("moved_line"), "`%s` moved here" % root))
        return False
    if alias_place_conflict(root, path, decl, errs, who):
        return False
    if not d.get("mut"):
        hint = decl_hint(d, root)
        if kind == "mut self":
            errs.note("cannot mutate immutable `%s`: `%s` takes mut self" % (root, who), rel=hint)
        else:
            errs.note("cannot mutate immutable `%s`: `%s` writes through it" % (root, who), rel=hint)
        return False
    live = borrows.get(root, [])
    if live:
        errs.note("cannot mutate immutable `%s` while borrowed by `%s`" % (root, live[-1][0]),
                  rel=(live[-1][2], "borrowed by `%s` here" % live[-1][0]))
        return False
    return True


def claim_loe(path, who, claimed, errs):
    """Law of exclusivity over one whole statement, so nested calls count too."""
    for other in claimed:
        if paths_conflict(path, other[0]):
            errs.note("law of exclusivity: `%s` passed as mut twice in one expression "
                      "(clashes with `%s` used by `%s`)" % (path, other[0], other[1]))
            return False
    return True


def scan_calls(e, decl, borrows, errs, sigs, claimed):
    if not isinstance(e, tuple) or not e:
        return
    umeth = getattr(sigs, "umeth", {})
    k = e[0]
    if k == "call":
        sig = sigs.get(e[1]) if isinstance(sigs, dict) else None
        if isinstance(sig, list):
            for (pn, mode), arg in zip(sig, e[2]):
                while isinstance(arg, tuple) and arg and arg[0] == "quest":
                    arg = arg[1]
                if mode not in ("mut", "move") or not isinstance(arg, tuple) or not arg:
                    continue
                p = place_of(arg)
                if p is not None and claim_loe(p, pn, claimed, errs):
                    claimed.append((p, pn))
                whole = arg[0] == "var"
                if whole:
                    v = arg[1]
                elif arg[0] == "field" and p is not None:
                    v = name_root(p)
                else:
                    continue
                d = decl.get(v)
                live = borrows.get(v, [])
                if mode == "mut":
                    if d is not None and not d.get("mut"):
                        errs.note("pass immutable `%s` as mut param `%s`" % (v, pn))
                    elif d is not None and d.get("moved"):
                        errs.note("use of moved `%s`" % v, rel=(d.get("moved_line"), "`%s` moved here" % v))
                    elif live:
                        errs.note("cannot pass `%s` as mut while borrowed by `%s`" % (v, live[-1][0]),
                                  rel=(live[-1][2], "borrowed by `%s` here" % live[-1][0]))
                    continue
                if d is not None and d.get("moved"):
                    errs.note("use of moved `%s`" % v, rel=(d.get("moved_line"), "`%s` moved here" % v))
                    continue
                if live:
                    errs.note("cannot move `%s` while borrowed by `%s`" % (v, live[-1][0]),
                              rel=(live[-1][2], "borrowed by `%s` here" % live[-1][0]))
                    continue
                if whole and d is not None:
                    if any(paths_conflict(v, mp) for mp in d.get("moved_paths", ())):
                        errs.note("use of moved `%s`" % v)
                        continue
                    d["moved"] = True
                    d["moved_line"] = errs.line
    elif k == "mcall":
        kind = mutate_kind(e[2], umeth)
        b = deref_place(e[1], decl, sigs)
        bp = write_path(b)
        if kind and bp is not None:
            # A receiver that is a plain name chain reads the way a dotted
            # spelling did: the write lands on the root binding, and the full
            # path names the call.
            p = place_of(("var", name_root(bp)))
            who = bp + "." + e[2]
        else:
            p = path_of(b) if kind else None
            who = e[2]
        if p is not None and check_mutate(p, decl, borrows, errs, kind, who) and claim_loe(p, who, claimed, errs):
            claimed.append((p, who))
    for sub in e[1:]:
        if isinstance(sub, tuple):
            scan_calls(sub, decl, borrows, errs, sigs, claimed)
        elif isinstance(sub, list):
            for x in sub:
                if isinstance(x, tuple):
                    scan_calls(x, decl, borrows, errs, sigs, claimed)


def copy_decl(decl):
    out = {}
    for n, d in decl.items():
        c = dict(d)
        if c.get("moved_paths"):
            c["moved_paths"] = set(c["moved_paths"])
        out[n] = c
    return out


def copy_borrows(borrows):
    return {k: list(v) for k, v in borrows.items()}


def merge_moved(decl, post):
    """Fold a block's exit state back in: a path taken once may run, or branch, twice."""
    for n, d in post.items():
        t = decl.get(n)
        if t is None:
            continue
        if d.get("moved") and not t.get("moved"):
            t["moved"] = True
            t["moved_line"] = d.get("moved_line")
        if d.get("moved_paths"):
            t["moved_paths"] = (t.get("moved_paths") or set()) | set(d["moved_paths"])


def walk_block(body, decl, borrows, errs, funcs):
    """Walk a block, enforcing ownership/borrow rules.

    Returns `(decl, death_points)` where `death_points` maps every root path
    read in the block to the index of the last statement that reads it -- the
    point at which the binding's value dies and (under BRAT_MOVE_RT) may be
    dropped by the C emitter.
    """
    last = {}
    for i, s in enumerate(body):
        if isinstance(s, tuple) and s:
            for v in used_in_stmt(s, []):
                last[v] = i
    defers = []
    for i, s in enumerate(body):
        errs.idx = i
        for src in list(borrows):
            kept = [bm for bm in borrows[src]
                    if bm[0] not in decl or last.get(bm[0], bm[3]) >= i]
            if kept:
                borrows[src] = kept
            else:
                del borrows[src]
        for src in decl:
            decl[src].pop("frozen_by", None)
        for src, entries in borrows.items():
            for name, mut, ln, _born in entries:
                if mut and src in decl:
                    decl[src]["frozen_by"] = (name, ln)
        walk_stmt(s, decl, borrows, errs, funcs)
        if isinstance(s, tuple) and s and s[0] == "defer":
            defers.append(s[1])
    for e in reversed(defers):
        scan_moves(e, decl, borrows, errs)
    return decl, last


def param_decl(p):
    """A parameter is borrowed storage the CALLER owns: it never blocks a
    write, and a borrow of it may escape via return (the storage outlives the
    call), which is what the corpus does everywhere."""
    if not isinstance(p, tuple):
        return {"mut": False, "ref": False, "src": None, "srcs": [],
                "moved": False, "copy": False, "alias": False, "ann": None,
                "param": True}
    return {"mut": p[1] == "mut", "ref": False, "src": None, "srcs": [],
            "moved": False, "copy": p[2] in COPY_ANNS, "alias": False,
            "ann": p[2] if len(p) > 2 else None, "param": True}


def bare_decl():
    return {"mut": False, "ref": False, "src": None, "srcs": [],
            "moved": False, "copy": False, "alias": False, "ann": None}


def escaping_refs(e, decl, funcs, errs):
    """Report every ref `e` hands back, naming the local that holds it.

    Only transparent forms recurse: `return a + b` and `return len(a)` are fresh
    values, `return v[0]` and `return p.x` reach into the borrow and are one.

    A borrow of a *parameter's* storage is allowed to escape: the caller owns
    that storage and it outlives the call, which is exactly the shape the whole
    corpus uses (28 of 71 examples return a parameter, directly or through a
    container). A borrow of a *local's* storage still cannot -- the local dies
    with the frame. Rejecting the parameter case while the corpus, both
    self-hosted toolchains and `own_good.brat` all rely on it was the
    inconsistency test case a13 exposed.
    A call is NOT transparent: the parser bans `-> ref` returns, so no call
    can hand back a borrow -- `return len(v[0])` is a fresh i64, and treating
    the callee's arguments as escaped was a false positive (test case a11).
    Containers, projections and `or` still are: `return [v[0]]` and
    `return v[0]` both carry the borrow out.
    """
    while isinstance(e, tuple) and len(e) > 1 and e[0] == "quest":
        e = e[1]
    if isinstance(e, list):
        for x in e:
            escaping_refs(x, decl, funcs, errs)
        return
    if not isinstance(e, tuple) or not e:
        return
    k = e[0]
    if k == "var":
        d = decl.get(e[1])
        if d is None:
            return
        if d.get("ref"):
            root = e[1]
            for _ in range(32):
                rd = decl.get(root)
                if rd is None:
                    break
                if rd.get("param"):
                    return
                nxt = rd.get("src")
                if not nxt or nxt == root:
                    break
                root = nxt
            errs.note("ref `%s` escapes via return" % e[1])
            return
        src = d.get("src")
        if src and src != e[1]:
            escaping_refs(("var", src), decl, funcs, errs)
        return
    if k not in ("move", "field", "idx", "vec", "maplit", "struct", "or"):
        return
    for sub in e[1:]:
        escaping_refs(sub, decl, funcs, errs)


def walk_loop(body, decl, borrows, errs, funcs, varname=None):
    """Walk a loop body twice: a `move` legal on pass one is a use-after-move on pass two."""
    def one():
        sub = copy_decl(decl)
        if varname is not None:
            sub[varname] = bare_decl()
        return walk_block(body, sub, copy_borrows(borrows), errs, funcs)[0]

    def moved_fingerprint():
        return tuple(sorted(
            (n, bool(d.get("moved")), tuple(sorted(d.get("moved_paths") or ())))
            for n, d in decl.items()))

    before = moved_fingerprint()
    merge_moved(decl, one())
    if moved_fingerprint() != before:
        merge_moved(decl, one())


def walk_stmt(s, decl, borrows, errs, funcs):
    ln = _LINES.get(id(s))
    if ln is not None:
        errs.line = ln
    k = s[0]
    if k == "let":
        _, name, e = s[0], s[1], s[2]
        mut = s[3] if len(s) > 3 else False
        const = s[4] if len(s) > 4 else False
        check_expr(e, decl, errs, funcs)
        scan_moves(e, decl, borrows, errs)
        scan_calls(e, decl, borrows, errs, funcs, [])
        srcs = borrow_srcs(e, decl, funcs)
        place = place_of(e)
        elem = place is not None and "." in place
        hm = mut and not elem
        for src in srcs:
            prev = borrows.setdefault(src, [])
            if prev and (hm or any(t[1] for t in prev)):
                errs.note("double mut borrow of `%s`: `%s` clashes with `%s`" % (src, name, prev[-1][0]),
                          rel=(prev[-1][2], "first borrow `%s` here" % prev[-1][0]))
            prev.append((name, hm, errs.line, getattr(errs, "idx", 0)))
        if mut and not elem:
            for src in srcs:
                sd = decl.get(src)
                if sd is None:
                    continue
                if sd.get("const"):
                    errs.note("assign to const `%s` through mut borrow `%s`" % (src, name))
                elif not sd.get("mut"):
                    errs.note("cannot borrow immutable `%s` as mut (`let mut %s`)" % (src, name))
        ann = s[5] if len(s) > 5 else None
        decl[name] = {"mut": mut, "ref": bool(srcs) and not elem, "src": srcs[0] if srcs else None,
                      "srcs": srcs, "moved": False, "const": const,
                      "alias": len(srcs) == 1 and place is not None,
                      "copy": ann in COPY_ANNS or is_copy_expr(e, decl),
                      "pinned": ann in COPY_ANNS, "ann": ann, "dl": errs.line}
    elif k == "set":
        check_expr(s[2], decl, errs, funcs)
        scan_moves(s[2], decl, borrows, errs)
        scan_calls(s[2], decl, borrows, errs, funcs, [])
        tgt = name_root(s[1])
        if tgt not in decl:
            errs.note("use of undeclared var `%s`" % s[1], rel=undeclared_rel(tgt))
        elif check_write(s[1], decl, borrows, errs, rebind=True):
            decl[tgt]["moved"] = False
            if decl[tgt].get("pinned") and is_copy_expr(s[2], decl):
                decl[tgt]["copy"] = True
            else:
                decl[tgt]["copy"] = is_copy_expr(s[2], decl)
                decl[tgt].pop("pinned", None)
            if "." not in s[1]:
                decl[tgt].pop("moved_paths", None)
            elif "moved_paths" in decl[tgt]:
                decl[tgt]["moved_paths"] = {mp for mp in decl[tgt]["moved_paths"] if not paths_conflict(s[1], mp)}
    elif k == "setidx":
        check_expr(s[1], decl, errs, funcs)
        check_expr(s[2], decl, errs, funcs)
        check_expr(s[3], decl, errs, funcs)
        scan_moves(s[3], decl, borrows, errs)
        scan_calls(s[3], decl, borrows, errs, funcs, [])
        base = path_of(deref_place(s[1], decl, funcs))
        if base is None:
            return
        root = name_root(base)
        if root not in decl:
            errs.note("use of undeclared var `%s`" % base, rel=undeclared_rel(root))
        else:
            check_write(base, decl, borrows, errs, strict=True)
    elif k == "setfield":
        b = deref_place(s[1], decl, funcs)
        base = write_path(b)
        if base is not None:
            # A write through a name chain is the statement a dotted spelling
            # used to build, so it takes the plain-`set` checks and bookkeeping.
            walk_stmt(("set", base + "." + s[2], s[3]), decl, borrows, errs, funcs)
            return
        check_expr(s[1], decl, errs, funcs, True)
        check_expr(s[3], decl, errs, funcs)
        scan_moves(s[3], decl, borrows, errs)
        scan_calls(s[3], decl, borrows, errs, funcs, [])
        base = path_of(b)
        if base is None:
            return
        p = base + "." + s[2]
        root = name_root(p)
        if root not in decl:
            errs.note("use of undeclared var `%s`" % base, rel=undeclared_rel(root))
        else:
            check_write(p, decl, borrows, errs, strict=False)
    elif k == "return":
        if getattr(errs, "in_spawn", 0):
            errs.note("return escapes spawn")
        check_expr(s[1], decl, errs, funcs)
        scan_moves(s[1], decl, borrows, errs)
        scan_calls(s[1], decl, borrows, errs, funcs, [])
        escaping_refs(s[1], decl, funcs, errs)
    elif k == "expr":
        check_expr(s[1], decl, errs, funcs)
        scan_moves(s[1], decl, borrows, errs)
        scan_calls(s[1], decl, borrows, errs, funcs, [])
    elif k == "fn":
        sub = dict(decl)
        for p in s[2]:
            pn = p[0] if isinstance(p, tuple) else p
            sub[pn] = param_decl(p)
        walk_block(s[3], sub, {}, errs, funcs)
    elif k == "if":
        check_expr(s[1], decl, errs, funcs)
        scan_moves(s[1], decl, borrows, errs)
        scan_calls(s[1], decl, borrows, errs, funcs, [])
        pre = walk_block(s[2], copy_decl(decl), copy_borrows(borrows), errs, funcs)[0]
        if s[3]:
            eb = s[3]
            post = walk_block(eb if isinstance(eb, list) else [eb], copy_decl(decl), copy_borrows(borrows), errs, funcs)[0]
            merge_moved(pre, post)
        merge_moved(decl, pre)
    elif k == "while":
        check_expr(s[1], decl, errs, funcs)
        scan_moves(s[1], decl, borrows, errs)
        scan_calls(s[1], decl, borrows, errs, funcs, [])
        walk_loop(s[2], decl, borrows, errs, funcs)
    elif k == "foriter":
        _, vn, ve, body = s
        check_expr(ve, decl, errs, funcs)
        scan_moves(ve, decl, borrows, errs)
        scan_calls(ve, decl, borrows, errs, funcs, [])
        walk_loop(body, decl, borrows, errs, funcs, vn)
    elif k == "forrange":
        _, vn, se, ee, body = s
        for b in (se, ee):
            check_expr(b, decl, errs, funcs)
            scan_moves(b, decl, borrows, errs)
            scan_calls(b, decl, borrows, errs, funcs, [])
        walk_loop(body, decl, borrows, errs, funcs, vn)
    elif k in ("break", "continue"):
        return
    elif k == "try":
        pre = walk_block(s[1], copy_decl(decl), copy_borrows(borrows), errs, funcs)[0]
        hdecl = copy_decl(decl)
        if len(s) > 3 and s[3]:
            hdecl[s[3]] = bare_decl()
        post = walk_block(s[2], hdecl, copy_borrows(borrows), errs, funcs)[0]
        merge_moved(pre, post)
        merge_moved(decl, pre)
    elif k == "spawn":
        seen = set()
        for v in used_in_block(s[1], []):
            d = decl.get(v)
            if d is not None and d.get("ref") and v not in seen:
                seen.add(v)
                errs.note("ref `%s` escapes via spawn" % v)
        sub = copy_decl(decl)
        for kk in sub:
            sub[kk]["spawn_frozen"] = True
        errs.in_spawn = getattr(errs, "in_spawn", 0) + 1
        post = walk_block(s[1], sub, copy_borrows(borrows), errs, funcs)[0]
        errs.in_spawn -= 1
        merge_moved(decl, post)
    elif k == "defer":
        check_expr(s[1], decl, errs, funcs)
        scan_calls(s[1], decl, borrows, errs, funcs, [])
    elif k == "import":
        return
    elif k == "extern":
        return
    elif k == "impl":
        seen = set()
        for f in s[3]:
            if f[1] in seen:
                errs.note("duplicate method `%s.%s`" % (s[2], f[1]))
            seen.add(f[1])
            sub = dict(decl)
            for p in f[2]:
                pn = p[0] if isinstance(p, tuple) else p
                sub[pn] = param_decl(p)
            walk_block(f[3], sub, {}, errs, funcs)
    elif k == "trait":
        return
    elif k == "enum":
        for vname, _fields in s[2]:
            funcs[vname] = []
    elif k == "pub":
        walk_stmt(s[1], decl, borrows, errs, funcs)
    else:
        errs.note("bad stmt `%s`" % k)


def fn_modes(params):
    out = []
    for p in params:
        if isinstance(p, tuple):
            out.append((p[0], p[1]))
        else:
            out.append((p, "ref"))
    return out


def _plus_operands(e, out):
    if isinstance(e, tuple) and len(e) > 2 and e[0] == "bin" and e[1] == "+":
        _plus_operands(e[2], out)
        _plus_operands(e[3], out)
    elif isinstance(e, (tuple, list)):
        out.append(e)


_CUT_PARTS = set()


def _cut_string(ops):
    """True when one string literal was chopped into these pieces.

    The lexer ends a literal at the first `"` that is not inside parentheses, so
    `"{e["hits"]}"` becomes `"{e["`, the name `hits`, and `"]}"` -- as call
    arguments or `+` operands, depending on where it sits. The fingerprint is a
    literal piece ending in `{name` or `{name.`: a JSON blob keeps its braces
    paired, and a plain `"{"` has no name after the brace.
    """
    if len(ops) < 2:
        return False
    if not any(isinstance(o, tuple) and o and o[0] == "rstr" for o in ops):
        return False
    if not any(not (isinstance(o, tuple) and o and o[0] in ("rstr", "str")) for o in ops):
        return False
    for o in ops:
        if isinstance(o, tuple) and o and o[0] == "rstr" and _unclosed_brace(o[1]):
            return True
    return False


_UNCLOSED = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\.?[\[\"']*[^}]*$")


def _unclosed_brace(s):
    t = s.replace("{{", "").replace("}}", "")
    if t.count("{") == t.count("}"):
        return False
    return bool(_UNCLOSED.search(t))


def cut_string_warns(node, warns):
    """Warn about a string literal cut short by a quote inside `{...}`.

    The checker otherwise reports the pieces of the literal as undeclared vars,
    which reads as a broken checker instead of a quoting mistake.
    """
    if isinstance(node, list):
        ops = [x for x in node if isinstance(x, (tuple, list))]
        if _cut_string(ops):
            _CUT_PARTS.update(id(o) for o in ops if isinstance(o, tuple))
            _warn_cut(ops, warns)
        for x in node:
            cut_string_warns(x, warns)
        return
    if not isinstance(node, tuple) or not node:
        return
    if len(node) > 2 and node[0] == "bin" and node[1] == "+":
        ops = []
        _plus_operands(node, ops)
        if _cut_string(ops):
            _CUT_PARTS.update(id(o) for o in ops if isinstance(o, tuple))
            _warn_cut(node, warns)
            return
    for sub in node[1:]:
        cut_string_warns(sub, warns)


def _warn_cut(node, warns):
    """Line of the literal piece, so the warning lands on the offending line."""
    ln = None
    for o in (node if isinstance(node, list) else [node]):
        if isinstance(o, tuple) and o:
            ln = ln or _LINES.get(id(o))
    warns.append((ln,
                  "string literal looks cut short: a `{...}` placeholder stops "
                  "at a bare `\"`, so `\"{e[\\\"hits\\\"]}\"` is not the placeholder "
                  "it looks like (help: `\"{e.hits}\"`, or `let hits = e[\"hits\"]` "
                  "then `\"{hits}\"`; a quote inside a call is fine: "
                  "`\"{m.get(\\\"hits\\\")}\"`)"))


def unused_in(body, warns):
    declared = []
    for s in body:
        if isinstance(s, tuple) and s:
            if s[0] == "let":
                declared.append((s[1], _LINES.get(id(s))))
            elif s[0] == "pub":
                if isinstance(s[1], tuple) and s[1] and s[1][0] == "let":
                    declared.append((s[1][1], _LINES.get(id(s[1]))))
    used = set(used_in_block(body, []))
    for name, ln in declared:
        if name.startswith("_"):
            continue
        if name not in used:
            warns.append((ln, "unused var `%s`" % name))


def collect_heads(prog, heads, umeth):
    """Names a dotted callee may legally start with, and which methods take `mut self`."""
    for s in prog[1]:
        if isinstance(s, tuple) and s:
            if s[0] == "import":
                parts = str(s[1]).split(".")
                for i in range(1, len(parts) + 1):
                    heads.add(".".join(parts[:i]))
            node = s[1] if s[0] == "pub" else s
            if isinstance(node, tuple) and node:
                if node[0] == "impl":
                    heads.add(node[2])
                    for f in node[3]:
                        ps = f[2]
                        if ps and isinstance(ps[0], tuple) and ps[0][0] == "self":
                            if f[1] in MUTATORS and ps[0][1] != "mut":
                                continue
                            umeth[f[1]] = umeth.get(f[1], False) or ps[0][1] == "mut"
                elif node[0] == "enum":
                    heads.add(node[1])
    scan_type_names(prog[1], heads)


def scan_type_names(node, heads):
    if isinstance(node, list):
        for x in node:
            scan_type_names(x, heads)
        return
    if not isinstance(node, tuple) or not node:
        return
    if node[0] == "struct":
        heads.add(node[1])
    for sub in node[1:]:
        if isinstance(sub, (tuple, list)):
            scan_type_names(sub, heads)


def check(prog, extra=None):
    errs = Errs()
    warns = []
    if not isinstance(prog, tuple) or not prog or prog[0] != "prog":
        return [(None, "bad prog node")], warns
    _CUT_PARTS.clear()
    funcs = fn_table(extra)
    traits = {}
    for s in prog[1]:
        node = s[1] if (isinstance(s, tuple) and s and s[0] == "pub") else s
        if isinstance(node, tuple) and node and node[0] in ("fn", "extern"):
            if node[0] == "extern":
                funcs[node[2]] = fn_modes(node[3])
            else:
                funcs[node[1]] = fn_modes(node[2])
        elif isinstance(node, tuple) and node and node[0] == "enum":
            for vname, _fields in node[2]:
                funcs[vname] = []
        elif isinstance(node, tuple) and node and node[0] == "trait":
            traits[node[1]] = [g[1] for g in node[2]]
    collect_heads(prog, funcs.heads, funcs.umeth)
    for s in prog[1]:
        n_ = s[1] if (isinstance(s, tuple) and s and s[0] == "pub") else s
        if isinstance(n_, tuple) and n_ and n_[0] == "impl":
            for f in n_[3]:
                ps = f[2]
                is_mut = bool(ps) and isinstance(ps[0], tuple) and ps[0][0] == "self" \
                    and ps[0][1] == "mut"
                if f[1] in MUTATORS and not is_mut:
                    errs.line = _LINES.get(id(n_))
                    errs.note("method name %s is reserved (built-in mutator)" % f[1])
    for s in prog[1]:
        node = s[1] if (isinstance(s, tuple) and s and s[0] == "pub") else s
        if isinstance(node, tuple) and node and node[0] == "impl":
            if node[1] is not None and node[1] not in traits:
                errs.line = _LINES.get(id(node))
                errs.note("unknown trait %s (declare it with `trait %s { ... }`)"
                          % (node[1], node[1]))
                continue
            missing = [m for m in traits.get(node[1], []) if m not in [f[1] for f in node[3]]]
            if missing:
                errs.line = _LINES.get(id(node))
                for m in missing:
                    errs.note("impl %s for %s missing %s" % (node[1], node[2], m))
    cut_string_warns(prog[1], warns)
    walk_block(prog[1], {}, {}, errs, funcs)
    unused_in(prog[1], warns)
    for ln, body in stray_from_brat(load_brat()):
        warns.append((ln, "placeholder `{%s}` is not an expression and stays "
                          "text: check for a typo" % body))
    for s in prog[1]:
        node = s[1] if (isinstance(s, tuple) and s and s[0] == "pub") else s
        if isinstance(node, tuple) and node:
            if node[0] == "fn":
                unused_in(node[3], warns)
            elif node[0] == "impl":
                for f in node[3]:
                    unused_in(f[3], warns)
    out = []
    for m in errs:
        if m not in out:
            out.append(m)
    wout = []
    for m in warns:
        if m not in wout:
            wout.append(m)
    return out, wout


def report(f, errs, src, kind="error"):
    sys.stdout.write(to_human(f, src, _entries_to_diags(f, errs, kind)))


def stray_from_brat(brat):
    """Stray-placeholder findings recorded while the parser read this file."""
    take = getattr(brat, "take_stray_braces", None)
    if take is None:
        return []
    try:
        return take()
    except Exception as e:
        sys.stderr.write("brat: stray-placeholder scan failed: %s: %s\n"
                         % (type(e).__name__, e))
        return []


def load_brat(path=None):
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brat.py")
    spec = importlib.util.spec_from_file_location("brat_oracle", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    brat = load_brat()
    progs = []
    seen = set()
    srcs = {}

    def add_file(f):
        f = os.path.abspath(f)
        if f in seen:
            return
        seen.add(f)
        src = open(f, encoding="utf-8").read()
        srcs[f] = src
        prog = brat.parse_prog(brat.Cur(brat.lex(src)))
        progs.append((f, prog))
        base = os.path.dirname(f)
        for s in prog[1]:
            if isinstance(s, tuple) and s and s[0] == "import":
                add_file(os.path.join(base, *s[1].split(".")) + ".brat")

    for f in argv[1:]:
        try:
            add_file(f)
        except OSError as e:
            print("%s: load error %s" % (f, e))
            return 2
    def pub_sigs(prog):
        out = {}
        for s in prog[1]:
            if isinstance(s, tuple) and s and s[0] == "pub":
                inner = s[1]
                if isinstance(inner, tuple) and inner:
                    if inner[0] == "fn":
                        out[inner[1]] = fn_modes(inner[2])
                    elif inner[0] == "enum":
                        for vname, _fields in inner[2]:
                            out[vname] = []
        return out

    per_file = [pub_sigs(prog) for _, prog in progs]
    code = 0
    for idx, (f, prog) in enumerate(progs):
        merged = {}
        for j, sigs in enumerate(per_file):
            if j != idx:
                merged.update(sigs)
        errs, warns = check(prog, merged)
        print("%s: %d error(s), %d warning(s)" % (f, len(errs), len(warns)))
        report(f, errs, srcs.get(f, ""))
        report(f, warns, srcs.get(f, ""), kind="warning")
        if errs:
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))