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
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from diag import to_human, _entries_to_diags


class Errs(list):
    def __init__(self):
        super().__init__()
        self.line = None

    def note(self, msg):
        self.append((self.line, msg))


def report(f, errs, src):
    sys.stdout.write(to_human(f, src, _entries_to_diags(f, errs, "error")))


def ann_type(ann):
    if ann in ("i32", "i64", "u64"):
        return "int"
    if ann == "f64":
        return "float"
    if ann == "bool":
        return "bool"
    if ann == "str":
        return "str"
    if ann and ann.startswith("vec["):
        inner = ann[4:].rstrip("]")
        return ("vec", ann_type(inner) if inner else "dyn")
    if ann == "vec":
        return ("vec", "dyn")
    if ann and (ann == "map" or ann.startswith("map[")):
        return "map"
    return "dyn"


def tname(t):
    if isinstance(t, tuple):
        if t[0] == "vec":
            return "vec[" + tname(t[1]) + "]" if t[1] != "dyn" else "vec"
        return t[0]
    return {"int": "i64", "float": "f64", "dyn": "?"}.get(t, t)


def ok(want, got):
    if want == "dyn" or got in ("dyn", "tag", "none"):
        return True
    if want == got:
        return True
    if want == "float" and got == "int":
        return True
    if isinstance(want, tuple) and isinstance(got, tuple):
        if want[0] == "struct" or got[0] == "struct":
            return want[0] == "struct" and got[0] == "struct"
        if want[0] == "vec" and got[0] == "vec":
            return want[1] == "dyn" or got[1] == "dyn" or want[1] == got[1]
    return False


STR_METH = {
    "split": (["str"], ("vec", "str"), True),
    "lines": ([], ("vec", "str"), False),
    "trim": ([], "str", False),
    "lower": ([], "str", False),
    "upper": ([], "str", False),
    "starts_with": (["str"], "bool", False),
    "ends_with": (["str"], "bool", False),
    "contains": (["str"], "bool", False),
    "parse_int": ([], "tag", False),
    "replace": (["str", "str"], "str", False),
    "index_of": (["str"], "dyn", False),
    "repeat": (["int"], "str", False),
    "split_once": (["str"], "dyn", False),
    "copy": ([], "str", False),
    "chars": ([], ("vec", "str"), False),
    "parse_float": ([], "tag", False),
    "slice": (["int", "int"], "str", False),
}

VEC_METH = {
    "join": (["str"], "str", False),
    "push": (["$E"], "int", False),
    "pop": ([], "dyn", False),
    "first": ([], "dyn", False),
    "last": ([], "dyn", False),
    "sort": ([], "$S", False),
    "rev": ([], "$S", False),
    "ins": (["int", "$E"], "int", False),
    "del": (["int"], "dyn", False),
    "slice": (["int", "int"], "$S", False),
    "sum": ([], "$SUM", False),
    "min": ([], "dyn", False),
    "max": ([], "dyn", False),
    "enumerate": ([], ("vec", "dyn"), False),
    "copy": ([], "$S", False),
    "zip": (["dyn"], ("vec", "dyn"), False),
    "chunks": (["int"], "$S", False),
    "pos": (["dyn"], "dyn", False),
    "any": (["dyn"], "bool", False),
    "all": (["dyn"], "bool", False),
    "find": (["dyn"], "dyn", False),
    "fold": (["dyn", "dyn"], "dyn", False),
    "sort_by": (["dyn"], "$S", False),
}

MAP_METH = {
    "get": (["dyn"], "dyn", False),
    "has": (["dyn"], "bool", False),
    "keys": ([], ("vec", "str"), False),
    "del": (["dyn"], "bool", False),
    "set": (["dyn", "dyn"], "dyn", False),
    "items": ([], ("vec", "dyn"), False),
    "vals": ([], ("vec", "dyn"), False),
    "copy": ([], "map", False),
    "get_or": (["dyn", "dyn"], "dyn", False),
}


def numeric(t):
    return t in ("int", "float", "bool")


BRES = {
    "len": "int", "str": "str", "is_err": "bool", "is_none": "bool",
    "tagof": "str", "read_file": "tag", "write_file": "tag",
    "is_vec": "bool", "is_str": "bool", "is_map": "bool",
    "join_all": "int", "Ok": "tag", "Err": "tag", "Some": "tag",
    "args": ("vec", "str"), "exit": "dyn", "eprint": "dyn", "raise": "dyn",
    "time_ms": "int", "sleep_ms": "dyn", "env_get": "dyn",
    "__arena_enter": "int", "__arena_leave": "int",
    "re_match": "bool",
    "tcp_listen": "tag", "tcp_connect": "tag", "tcp_accept": "tag",
    "tcp_send": "tag", "tcp_recv": "tag", "tcp_close": "tag",
    "sbuf": "int", "sbuf_add": "int", "sbuf_len": "int", "sbuf_str": "str",
}


_ARENA_ENTER = ("expr", ("call", "__arena_enter", []))
_INTERP = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)')


def is_arena_block(s):
    """`if true { __arena_enter(); defer __arena_leave(); ... }` from parse_arena."""
    return (len(s) > 2 and s[1] == ("bool", True) and isinstance(s[2], list)
            and s[2] and s[2][0] == _ARENA_ENTER)


def first_var(e):
    """The first variable a place expression is rooted at, or None."""
    if isinstance(e, tuple) and e:
        if e[0] == "var":
            return e[1]
        for x in e[1:]:
            r = first_var(x)
            if r:
                return r
    elif isinstance(e, list):
        for x in e:
            r = first_var(x)
            if r:
                return r
    return None


def mentions(node, acc):
    """Every name a subtree reads, including names inside string interpolation.
    The argument of own() is not read: own() makes it a heap copy first."""
    if isinstance(node, tuple):
        if node and node[0] == "call" and node[1] == "own":
            return acc
        if node and node[0] == "var" and len(node) > 1 and isinstance(node[1], str):
            acc.add(node[1])
        elif node and node[0] == "str" and len(node) > 1 and isinstance(node[1], str):
            for m in _INTERP.finditer(node[1]):
                acc.add(m.group(1))
        for x in node:
            mentions(x, acc)
    elif isinstance(node, list):
        for x in node:
            mentions(x, acc)
    return acc


def has_node(node, tag):
    if isinstance(node, tuple):
        if node and node[0] == tag:
            return True
        return any(has_node(x, tag) for x in node)
    if isinstance(node, list):
        return any(has_node(x, tag) for x in node)
    return False


class FnTyper:
    # Types whose values cannot reference arena memory.
    SCALAR = ("int", "float", "bool", "none")

    def __init__(self, sigs, ret, errs):
        self.sigs = sigs
        self.ret = ret
        self.errs = errs
        self.env = {}
        self.arena = []   # one set per open arena block: names declared inside it
        self.tainted = []  # outer containers that now hold arena memory

    def inner(self, name):
        return bool(self.arena) and name in self.arena[-1]

    def declare(self, name):
        if self.arena and isinstance(name, str):
            self.arena[-1].add(name)

    def taint(self, name):
        if self.arena and isinstance(name, str) and not self.inner(name):
            if not self.tainted:
                self.tainted.append(set())
            self.tainted[-1].add(name)

    def is_tainted(self, name):
        if not isinstance(name, str):
            return False
        for s in self.tainted:
            if name in s:
                return True
        return False

    def non_scalar_inner(self, names):
        return [n for n in names if self.inner(n) and self.env.get(n, "dyn") not in self.SCALAR]

    NUM_OPS = ("-", "*", "/", "%", "==", "!=", "<", ">", "<=", ">=", "&&", "||")

    def escapes(self, e):
        """True when value e may carry arena memory out of the innermost block."""
        if not self.arena or not isinstance(e, tuple) or not e:
            return False
        if not self.non_scalar_inner(mentions(e, set())):
            return False
        # Arithmetic and comparisons yield numbers or bools whatever the operand
        # types; `+` does too when one operand is a number.
        if e[0] == "bin":
            if e[1] in self.NUM_OPS:
                return False
            if e[1] == "+" and any(self.expr(x) in ("int", "float") for x in e[2:4]):
                return False
        if e[0] == "un":
            return False
        return self.expr(e) not in self.SCALAR

    def arena_note(self, msg, ownable=True):
        if ownable:
            msg += "  (hint: hand out a heap copy with `own(...)`, e.g. `return Err(own(msg))`)"
        else:
            msg += "  (hint: keep the value inside the block, or copy it with `own(...)` first)"
        self.errs.note("arena: " + msg)

    def _closure_body(self, e):
        """Type-check the body of an inline closure passed as an argument.

        `trace.span("x") { ... }` puts real statements in argument position;
        skipping them would leave that code outside every static island.
        """
        _, _cname, cparams, cbody = e
        saved = dict(self.env)
        for p in cparams:
            pn = p[0] if isinstance(p, tuple) else p
            ann = p[2] if isinstance(p, tuple) and len(p) > 2 else None
            self.env[pn] = ann_type(ann)
        self.block(cbody)
        self.env = saved

    def field_names(self, t):
        """`ip, code, size` for a struct type, or an empty string."""
        return ", ".join(t[1]) if isinstance(t, tuple) and t[0] == "struct" else ""

    def struct_field_err(self, e2, bt):
        fields = self.field_names(bt)
        if fields:
            return "no field `%s` on struct (has %s)" % (e2, fields)
        return "no field `%s` on struct" % e2

    def expr(self, e):
        if not isinstance(e, tuple) or not e:
            return "dyn"
        k = e[0]
        if k == "num":
            return "float" if isinstance(e[1], float) else "int"
        if k == "bool":
            return "bool"
        if k == "str":
            return "str"
        if k == "rstr":
            return "str"
        if k == "none":
            return "none"
        if k == "vec":
            ts = [self.expr(x) for x in e[1]]
            t = ts[0] if ts and all(x == ts[0] for x in ts) else "dyn"
            return ("vec", t)
        if k == "maplit":
            for ke, ve in e[1]:
                self.expr(ke)
                self.expr(ve)
            return "map"
        if k == "struct":
            return ("struct", {f: self.expr(v) for f, v in e[2]})
        if k == "var":
            return self.env.get(e[1], "dyn")
        if k == "un":
            t = self.expr(e[2])
            if e[1] == "!":
                return "bool"
            return t if numeric(t) else "dyn"
        if k == "move":
            return self.expr(e[1])
        if k == "closure":
            self._closure_body(e)
            return "dyn"
        if k == "bin":
            op = e[1]
            a = self.expr(e[2])
            b = self.expr(e[3])
            if op in ("==", "!=", "<", ">", "<=", ">=", "in"):
                return "bool"
            if op in ("&&", "||"):
                return a if a == b else "dyn"
            if op == "+":
                if a == "str" or b == "str":
                    return "str"
                if isinstance(a, tuple) and isinstance(b, tuple):
                    return ("vec", "dyn")
                if numeric(a) and numeric(b):
                    return "float" if "float" in (a, b) else "int"
                return "dyn"
            if numeric(a) and numeric(b):
                if op == "*" and (a == "str" or b == "str"):
                    return "str"
                return "float" if "float" in (a, b) else "int"
            if op == "*" and (a == "str" or b == "str") and (a == "int" or b == "int"):
                return "str"
            return "dyn"
        if k == "or":
            a = self.expr(e[1])
            b = self.expr(e[2])
            return a if a == b else "dyn"
        if k == "quest":
            return self.expr(e[1])
        if k == "mcall":
            ot = self.expr(e[1])
            for a in e[3]:
                self.expr(a)
            return self._meth(ot, e[2], e[3])
        if k == "field":
            bt = self.expr(e[1])
            if isinstance(bt, tuple) and bt[0] == "struct":
                if e[2] not in bt[1]:
                    self.errs.note(self.struct_field_err(e[2], bt))
                    return "dyn"
                return bt[1][e[2]]
            return "dyn"
        if k == "idx":
            b = self.expr(e[1])
            self.expr(e[2])
            if isinstance(b, tuple) and b[0] == "vec":
                return b[1]
            if b == "str":
                return "str"
            return "dyn"
        if k == "call":
            for a in e[2]:
                self.expr(a)
            name = e[1]
            if name == "own":
                if len(e[2]) != 1:
                    self.errs.note("`own` wants 1 arg, got %d" % len(e[2]))
                    return "dyn"
                return self.expr(e[2][0])
            if name in BRES:
                return BRES[name]
            if name in self.sigs:
                ptypes, rtype, ndef = self.sigs[name]
                if len(e[2]) < len(ptypes) - ndef or len(e[2]) > len(ptypes):
                    self.errs.note("`%s` wants %d args, got %d" % (name, len(ptypes), len(e[2])))
                for i, pt in enumerate(ptypes):
                    if i < len(e[2]):
                        gt = self.expr(e[2][i])
                        if not ok(pt, gt):
                            self.errs.note("arg %d of `%s`: want %s, got %s" % (i + 1, name, tname(pt), tname(gt)))
                return rtype
            return "dyn"
        return "dyn"

    def _meth(self, ot, meth, args):
        tab = None
        if ot == "str":
            tab = STR_METH
        elif isinstance(ot, tuple) and ot[0] == "vec":
            tab = VEC_METH
        elif ot == "map":
            tab = MAP_METH
        if tab is None:
            return "dyn"
        if meth in ("map", "filter"):
            return ("vec", "dyn")
        sig = tab.get(meth)
        if sig is None:
            self.errs.note("no method `%s` for %s" % (meth, tname(ot)))
            return "dyn"
        ptypes, rtype, flex = sig
        if len(args) != len(ptypes) and not (flex and len(args) == len(ptypes) - 1):
            self.errs.note("method `%s` wants %d args" % (meth, len(ptypes)))
            return "dyn"
        for i, pt in enumerate(ptypes):
            if i >= len(args):
                break
            want = ot[1] if pt == "$E" else pt
            gt = self.expr(args[i])
            if not ok(want, gt):
                self.errs.note("method `%s` arg %d: want %s, got %s" % (meth, i + 1, tname(want), tname(gt)))
        if rtype == "$SUM":
            return ot[1] if isinstance(ot, tuple) and numeric(ot[1]) else "dyn"
        return ot if rtype == "$S" else rtype

    def stmt(self, s):
        ln = _LINES.get(id(s))
        if ln is not None:
            self.errs.line = ln
        k = s[0]
        if k == "let":
            ann = s[5] if len(s) > 5 else None
            want = ann_type(ann)
            got = self.expr(s[2])
            if want != "dyn" and not ok(want, got):
                self.errs.note("let `%s`: annotated %s, got %s" % (s[1], tname(want), tname(got)))
            self.env[s[1]] = want if want != "dyn" else got
            # A binding made from an outer container is an alias of it, not a new value.
            outer_alias = any(not self.inner(n) and self.env.get(n, "dyn") not in self.SCALAR
                              for n in mentions(s[2], set()) if n != s[1])
            if not outer_alias:
                self.declare(s[1])
            # Propagate taint: an alias of a tainted outer container is tainted too.
            if self.arena and isinstance(s[1], str) and any(self.is_tainted(n) for n in mentions(s[2], set())):
                self.taint(s[1])
            if self.arena and has_node(s[2], "closure") and self.non_scalar_inner(mentions(s[2], set())):
                self.arena_note("a closure inside the block captures a value made in it",
                                ownable=False)
        elif k == "set":
            got = self.expr(s[2])
            target = s[1].split(".", 1)[0]
            if self.arena and not self.inner(target) and self.escapes(s[2]):
                self.arena_note("a value made inside the block is assigned to `%s`, "
                                "which lives outside it" % target)
                self.taint(target)
            if "." in s[1]:
                head, field = s[1].split(".", 1)
                bt = self.env.get(head)
                if isinstance(bt, tuple) and bt[0] == "struct" and "." not in field:
                    if field not in bt[1]:
                        self.errs.note(self.struct_field_err(field, bt))
                    elif not ok(bt[1][field], got):
                        self.errs.note("assign to `%s`: want %s, got %s" % (s[1], tname(bt[1][field]), tname(got)))
            else:
                cur = self.env.get(s[1])
                if cur is not None and cur != "dyn" and not ok(cur, got):
                    self.errs.note("assign to `%s`: want %s, got %s" % (s[1], tname(cur), tname(got)))
        elif k == "setidx":
            self.expr(s[1])
            self.expr(s[2])
            self.expr(s[3])
            root = first_var(s[1])
            if self.arena and root and not self.inner(root) and self.escapes(s[3]):
                self.arena_note("a value made inside the block is stored into `%s`, "
                                "which lives outside it" % root)
                self.taint(root)
        elif k == "setfield":
            bt = self.expr(s[1])
            got = self.expr(s[3])
            root = first_var(s[1])
            if self.arena and root and not self.inner(root) and self.escapes(s[3]):
                self.arena_note("a value made inside the block is stored into `%s`, "
                                "which lives outside it" % root)
                self.taint(root)
            if isinstance(bt, tuple) and bt[0] == "struct":
                if s[2] not in bt[1]:
                    self.errs.note(self.struct_field_err(s[2], bt))
                elif not ok(bt[1][s[2]], got):
                    self.errs.note("assign to `%s`: want %s, got %s"
                                   % (s[2], tname(bt[1][s[2]]), tname(got)))
        elif k == "return":
            got = self.expr(s[1])
            if self.escapes(s[1]):
                self.arena_note("a value made inside the block is returned out of it")
            if not ok(self.ret, got):
                self.errs.note("return: want %s, got %s" % (tname(self.ret), tname(got)))
        elif k == "expr":
            self.expr(s[1])
            e = s[1]
            if self.arena and isinstance(e, tuple) and e:
                if e[0] == "mcall":
                    root = first_var(e[1])
                    args = [a for a in e[3] if not (isinstance(a, tuple) and a and a[0] == "closure")]
                    if root and root in self.env and not self.inner(root) and any(self.escapes(a) for a in args):
                        self.arena_note("a value made inside the block is stored into `%s`, "
                                        "which lives outside it" % root)
                        self.taint(root)
                elif e[0] == "call" and e[1] in self.sigs:
                    args = e[2]
                    if any(self.escapes(a) for a in args) and any(
                            first_var(a) and not self.inner(first_var(a)) for a in args):
                        self.arena_note("a value made inside the block is passed to `%s` "
                                        "next to an outer binding" % e[1])
                elif e[0] == "closure" and self.non_scalar_inner(mentions(e, set())):
                    self.arena_note("a closure inside the block captures a value made in it",
                                ownable=False)
                elif e[0] == "closure" and any(self.is_tainted(n) for n in mentions(e, set())):
                    self.arena_note("a closure inside the block captures a value made in it",
                                ownable=False)
        elif k == "if":
            self.expr(s[1])
            if is_arena_block(s):
                self.arena.append(set())
                self.tainted.append(set())
                self.block(s[2])
                self.arena.pop()
                self.tainted.pop()
            else:
                self.block(s[2])
            if s[3]:
                self.block(s[3] if isinstance(s[3], list) else [s[3]])
        elif k == "while":
            self.expr(s[1])
            self.block(s[2])
        elif k == "forrange":
            self.declare(s[1])
            for b in (s[2], s[3]):
                t = self.expr(b)
                if t not in ("int", "bool", "dyn"):
                    self.errs.note("forrange bound: want i64, got %s" % tname(t))
            old = self.env.get(s[1])
            self.env[s[1]] = "int"
            self.block(s[4])
            if old is None:
                self.env.pop(s[1], None)
            else:
                self.env[s[1]] = old
        elif k == "foriter":
            st = self.expr(s[2])
            old = self.env.get(s[1])
            self.declare(s[1])
            self.env[s[1]] = st[1] if isinstance(st, tuple) else ("str" if st == "str" else "dyn")
            self.block(s[3])
            if old is None:
                self.env.pop(s[1], None)
            else:
                self.env[s[1]] = old
        elif k in ("break", "continue", "import", "trait", "enum"):
            pass
        elif k == "try":
            self.block(s[1])
            old = self.env.get(s[3]) if len(s) > 3 and s[3] else None
            if len(s) > 3 and s[3]:
                self.declare(s[3])
                self.env[s[3]] = "dyn"
            self.block(s[2])
            if len(s) > 3 and s[3]:
                if old is None:
                    self.env.pop(s[3], None)
                else:
                    self.env[s[3]] = old
        elif k == "spawn":
            if self.arena and self.non_scalar_inner(mentions(s[1], set())):
                self.arena_note("a spawn inside the block captures a value made in it")
            if self.arena and any(self.is_tainted(n) for n in mentions(s[1], set())):
                self.arena_note("a spawn inside the block captures a value made in it",
                                ownable=False)
            self.block(s[1])
        elif k == "defer":
            self.expr(s[1])
        elif k == "fn":
            pass
        elif k == "impl":
            pass
        elif k == "pub":
            self.stmt(s[1])

    def block(self, body):
        for s in body:
            if isinstance(s, tuple) and s:
                self.stmt(s)


def fn_ret(node):
    return node[5] if len(node) > 5 else None


def collect_sigs(prog, sigs):
    for s in prog[1]:
        node = s[1] if (isinstance(s, tuple) and s and s[0] == "pub") else s
        if not (isinstance(node, tuple) and node and node[0] == "fn"):
            continue
        ptypes = []
        ndef = 0
        for p in node[2]:
            ann = p[2] if isinstance(p, tuple) and len(p) > 2 else None
            ptypes.append(ann_type(ann))
            if isinstance(p, tuple) and len(p) > 3 and p[3] is not None:
                ndef += 1
        sigs[node[1]] = (ptypes, ann_type(fn_ret(node)), ndef)
    return sigs


def check(prog, extra=None):
    errs = Errs()
    sigs = dict(extra) if extra else {}
    collect_sigs(prog, sigs)
    for s in prog[1]:
        node = s[1] if (isinstance(s, tuple) and s and s[0] == "pub") else s
        if not (isinstance(node, tuple) and node and node[0] == "fn"):
            continue
        ret = ann_type(fn_ret(node))
        if ret == "dyn":
            continue
        tp = FnTyper(sigs, ret, errs)
        for p in node[2]:
            pn = p[0] if isinstance(p, tuple) else p
            ann = p[2] if isinstance(p, tuple) and len(p) > 2 else None
            tp.env[pn] = ann_type(ann)
        tp.block(node[3])
    top = FnTyper(sigs, "dyn", errs)
    for s in prog[1]:
        node = s[1] if (isinstance(s, tuple) and s and s[0] == "pub") else s
        if isinstance(node, tuple) and node and node[0] in ("fn", "impl", "trait", "enum"):
            continue
        top.stmt(s)
    out = []
    for m in errs:
        if m not in out:
            out.append(m)
    return out


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
    merged = {}
    for idx, (_, prog) in enumerate(progs):
        if idx == 0:
            continue
        collect_sigs(prog, merged)
    code = 0
    for f, prog in progs:
        errs = check(prog, merged)
        print("%s: %d type error(s)" % (f, len(errs)))
        report(f, errs, srcs.get(f, ""))
        if errs:
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))