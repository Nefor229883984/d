import importlib.util as _ilu
import os as _o
import re as _re

_BRAT_PATH = _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), "brat.py")
_spec = _ilu.spec_from_file_location("brat_parser_full", _BRAT_PATH)
_brat = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_brat)

_C_KW = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if",
    "int", "long", "register", "return", "short", "signed", "sizeof",
    "static", "struct", "switch", "typedef", "union", "unsigned", "void",
    "volatile", "while",
}

_STRVEC_METH = ("split", "lines", "trim", "lower", "upper", "starts_with",
                "ends_with", "contains", "parse_int", "replace",
                "index_of", "repeat", "split_once", "chars", "parse_float",
                "join", "push", "pop", "first", "last", "sort", "rev",
                "ins", "del", "slice", "sum", "min", "max", "enumerate",
                "copy", "zip", "chunks")

_SV_SIMPLE = {
    "index_of": ("vindex_of", 1), "repeat": ("vrepeat", 1),
    "split_once": ("vsplit_once", 1), "ins": ("vins", 2),
    "sum": ("vsum", 0), "min": ("vmin", 0),
    "max": ("vmax", 0), "enumerate": ("venum", 0), "chars": ("vchars", 0),
    "parse_float": ("vparse_float", 0), "copy": ("vcopy", 0),
    "zip": ("vzip", 1), "chunks": ("vchunks", 1),
}

_VBUILTIN_DIRECT = ("del", "copy", "slice")

_MAP_METH = ("get", "has", "keys", "del", "set", "items", "vals", "copy", "get_or")


def _collect_vars(body, acc):
    for s in body:
        if not isinstance(s, tuple) or not s:
            continue
        k = s[0]
        if k == "let":
            acc.add(s[1].split(".", 1)[0])
        elif k == "set":
            acc.add(s[1].split(".", 1)[0])
        elif k == "foriter":
            acc.add(s[1])
            _collect_vars(s[3], acc)
        elif k == "forrange":
            acc.add(s[1])
            _collect_vars(s[4], acc)
        elif k == "if":
            _collect_vars(s[2], acc)
            els = s[3]
            if isinstance(els, tuple):
                _collect_vars([els], acc)
            elif els:
                _collect_vars(els, acc)
        elif k == "while":
            _collect_vars(s[2], acc)
        elif k == "try":
            _collect_vars(s[1], acc)
            _collect_vars(s[2], acc)
            if len(s) > 3 and s[3]:
                acc.add(s[3])
        elif k == "spawn":
            _collect_vars(s[1], acc)
        elif k in ("fn", "impl", "trait", "enum", "pub", "import"):
            pass
        elif k in ("expr", "return", "defer", "break", "continue", "setidx", "setfield"):
            pass
        else:
            raise RuntimeError("stmt " + k)


def _block_let_names(body):
    """Names bound by a top-level `let` in `body` (block-scoped bindings)."""
    out = []
    for s in body:
        if isinstance(s, tuple) and s and s[0] == "let":
            out.append(s[1].split(".", 1)[0])
    return out


def _expr_vars(e, acc):
    """Every name an expression reads, so a defer body can be captured whole.

    A defer runs after the statement that follows it in the block may already
    have declared locals, and its closure is emitted against the scope of the
    defer site, so the names it reads have to be in the capture set.
    """
    if not isinstance(e, tuple) or not e:
        return
    if e[0] == "var":
        acc.add(e[1])
    for x in e[1:]:
        if isinstance(x, tuple):
            _expr_vars(x, acc)
        elif isinstance(x, list):
            for y in x:
                _expr_vars(y, acc)


def _dec_able(e):
    """True when a value from `e` survives vrc_dec: a fresh heap value or an
    immediate.  A string literal counts because the emitter stores it heap-backed."""
    if not isinstance(e, tuple) or not e:
        return False
    if e[0] in ("str", "rstr", "num", "bool", "none", "bin", "vec", "maplit",
                "struct", "closure"):
        return True
    return e[0] == "move" and _dec_able(e[1])


def _reclaim_names(body, params):
    """Return (reclaim, relet) for a statement list.
    reclaim: locals rebound by `set`, whose every stored value survives
    vrc_dec -- a rebind releases the overwritten value.
    relet: locals declared under a loop, so the `let` itself re-runs each
    pass and must release the previous pass value before overwriting.
    Parameters never qualify: their storage belongs to the caller."""
    ok = {}
    has_set = set()
    loop_let = set()

    def walk(stmts, loop):
        for s in stmts:
            if not isinstance(s, tuple) or not s:
                continue
            k = s[0]
            if k == "let":
                n = s[1].split(".", 1)[0]
                ok[n] = ok.get(n, True) and _dec_able(s[2])
                if loop:
                    loop_let.add(n)
            elif k == "set":
                n = s[1].split(".", 1)[0]
                ok[n] = ok.get(n, True) and _dec_able(s[2])
                has_set.add(n)
            elif k == "foriter":
                ok[s[1]] = False  # elements come from the container
                walk(s[3], True)
            elif k == "forrange":
                ok[s[1]] = False
                walk(s[4], True)
            elif k == "if":
                walk(s[2], loop)
                els = s[3]
                if isinstance(els, tuple):
                    walk([els], loop)
                elif els:
                    walk(els, loop)
            elif k == "while":
                walk(s[2], True)
            elif k == "try":
                walk(s[1], loop)
                walk(s[2], loop)
                if len(s) > 3 and s[3]:
                    ok[s[3]] = False
            # a spawn body is its own frame, like a closure

    walk(body, False)
    reclaim = set(n for n, v in ok.items() if v and n in has_set and n not in params)
    relet = set(n for n in loop_let if ok.get(n) and n not in params)
    return reclaim, relet


def _block_defers(body):
    """True when this block level itself holds a `defer`.

    Deliberately does not recurse: a `defer` in a nested block is drained by
    that inner block, so the outer one needs no frame of its own. A `defer` in
    a nested `fn` belongs to that function's frame, not to this block's.
    """
    for s in body:
        if isinstance(s, tuple) and s and s[0] == "defer":
            return True
    return False


def _name_path(e):
    """Dotted name of a var/field chain, or None when `e` computes a value."""
    if isinstance(e, tuple) and e:
        if e[0] == "var":
            return e[1]
        if e[0] == "field":
            b = _name_path(e[1])
            return b + "." + e[2] if b else None
    return None


def _cname(n):
    n = n.replace(".", "_")
    n = "".join(c if (c.isalnum() or c == "_") else "_" for c in n)
    if not n or n[0].isdigit():
        n = "_" + n
    if n in _C_KW:
        n += "_"
    return "b_" + n


class Sym:
    """One BRAT binding as seen by the C emitter.

    `cn` is the C identifier holding the value. `own` classifies the binding:
    * "copy"   -- trivially copyable / immortal (scalars, string literals),
                  never dropped.
    * "borrow" -- aliases another binding's storage; never dropped, the
                  owner drops instead.
    * "owned"  -- the binding owns a heap value; dropped once at its death
                  point (or block end) under BRAT_MOVE_RT.
    `moved` is set when `move` reads the source, so a moved binding is never
    dropped (ownership has been transferred).
    """
    __slots__ = ("cn", "own", "moved")

    def __init__(self, cn, own):
        self.cn = cn
        self.own = own
        self.moved = False


_MOVE_RT = _o.environ.get("BRAT_MOVE_RT") == "1"


def _cstr_path(p):
    """C string literal for a filesystem path.

    Only the two characters that can end the literal get escaped. Bytes above
    0x7F are left alone: _clit turns each into a hex escape, and C hex escapes
    are greedy, so a non-ASCII path comes out as broken source.
    """
    return p.replace("\\\\", "\\\\\\\\").replace('"', '\\\\"')


def _clit(s):
    o = []
    for ch in s:
        if ch == "\\":
            o.append("\\\\")
        elif ch == "\"":
            o.append("\\\"")
        elif ch == "\n":
            o.append("\\n")
        elif ch == "\r":
            o.append("\\r")
        elif ch == "\t":
            o.append("\\t")
        elif 32 <= ord(ch) < 127:
            o.append(ch)
        else:
            for b in ch.encode("utf-8"):
                o.append("\\x%02x" % b)
            o.append("\"\"")
    return "".join(o)


_INTERP_RE = _re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}')

BINT_FMT = "%lld"
_I64 = 1 << 63


def _wrap64(n):
    """i64 wrap, matching brat/runtime.wrap64 (SPEC section 10, Rust release)."""
    if -_I64 <= n < _I64:
        return n
    n &= (1 << 64) - 1
    return n - (1 << 64) if n >= _I64 else n


def _split_interp(s):
    parts = []
    i = 0
    buf = ""
    while i < len(s):
        if s[i] == "{":
            m = _INTERP_RE.match(s[i:])
            if m:
                if buf:
                    parts.append(("lit", buf))
                    buf = ""
                parts.append(("var", m.group(1)))
                i += len(m.group(0))
                continue
        buf += s[i]
        i += 1
    if buf:
        parts.append(("lit", buf))
    return parts


class Emitter:
    def __init__(self):
        self.tmp = 0
        self.fns = {}
        self.order = []
        self.mtab = {}
        self.vtab = {}
        self.traits = {}
        self.codes = {}
        self.user_meths = set()
        self.clos = []
        self.late = []
        self.seen_mods = set()
        self.spliced = set()
        self.top_exe = []
        self.out = []
        self.sym = {}
        self.cap = None
        self.dfb = []
        self.dfb_locals = set()
        self.try_n = 0
        self.fin = "__fin_top"
        self.err_goto = None  # catch label while inside try; else None → use fin
        self.err_base = None  # defer frame to drain before that catch label
        self.fast = {}
        self.fastc = {}
        self.fastkinds = {}
        self.fkind = "long"
        self.fsym = {}
        self.need_idiv = False
        self.need_imod = False
        self.need_fdiv = False
        self.need_fmod = False
        self.need_viat = False
        self.need_vset = False
        self.fastvec = {}
        self.fvecvars = frozenset()
        self.nesting = 0
        self.nested_seq = 0
        self.fn_alias = {}
        self.assigned = set()
        self.reclaim = set()    # bindings safe to release on rebind
        self.relet = set()      # loop-declared bindings a re-run `let` must release
        self.move_rt = _MOVE_RT
        # Ownership tracking (only active under BRAT_MOVE_RT).
        self.moved = set()      # bindings whose ownership was moved away
        self.captured = set()   # bindings captured by a closure/spawn/defer
        self.shared = set()     # bindings borrowed by another binding
        self.dropped = set()    # bindings already vdrop'ped (avoid double drop)

    def new_tmp(self, p="t"):
        self.tmp += 1
        return "_%s%d" % (p, self.tmp)

    def err_bail(self):
        """Leave the current frame after a raise (no longjmp)."""
        if self.err_goto is not None:
            pre = ("vdfreset(%s); " % self.err_base) if self.err_base else ""
            return "{ %sgoto %s; }" % (pre, self.err_goto)
        if self.fin == "__fin_top":
            return (
                '{ fprintf(stderr, "unhandled: "); vprint_err(verr_take()); '
                'fprintf(stderr, "\\n"); return 1; }'
            )
        return "{ __ret = verr_leave(); goto %s; }" % self.fin

    def err_check_line(self):
        """One C statement: if a raise is pending, leave this frame."""
        if self.err_goto is not None:
            if self.err_base:
                return "if (verr_pending()) { vdfreset(%s); goto %s; }" % (
                    self.err_base, self.err_goto)
            return "if (verr_pending()) goto %s;" % self.err_goto
        if self.fin == "__fin_top":
            return (
                'if (verr_pending()) { fprintf(stderr, "unhandled: "); '
                'vprint_err(verr_take()); fprintf(stderr, "\\n"); return 1; }'
            )
        return (
            "if (verr_pending()) { __ret = verr_leave(); goto %s; }" % self.fin
        )

    def emit_err_check(self, ind):
        """Propagate a pending runtime error without longjmp."""
        self.out.append(ind + self.err_check_line())

    def int_expr(self, e, cand, kind, intvars, vecvars=frozenset()):
        if not isinstance(e, tuple) or not e:
            return False
        k = e[0]
        if k == "num":
            if isinstance(e[1], bool):
                return True
            if isinstance(e[1], int):
                return True
            return kind == "f64"
        if k == "bool":
            return True
        if k == "var":
            return e[1] not in vecvars
        if k == "un":
            return e[1] in ("!", "-") and self.int_expr(e[2], cand, kind, intvars, vecvars)
        if k == "move":
            return self.int_expr(e[1], cand, kind, intvars, vecvars)
        if k == "idx":
            return (kind == "i64v" and isinstance(e[1], tuple) and e[1][0] == "var"
                    and e[1][1] in vecvars and self.intish(e[2], cand, kind, intvars))
        if k == "bin":
            if e[1] not in ("+", "-", "*", "/", "%", "==", "!=", "<", ">", "<=", ">=", "&&", "||"):
                return False
            if not self.int_expr(e[2], cand, kind, intvars, vecvars):
                return False
            if not self.int_expr(e[3], cand, kind, intvars, vecvars):
                return False
            if (e[1] in ("/", "%") and kind == "f64"
                    and self.intish(e[2], cand, kind, intvars)
                    and self.intish(e[3], cand, kind, intvars)):
                return False
            return True
        if k == "call":
            if e[1] == "len" and kind == "i64v" and len(e[2]) == 1:
                a = e[2][0]
                if isinstance(a, tuple) and a[0] == "var" and a[1] in vecvars:
                    return True
            return (e[1] in cand and self.fastkinds.get(e[1]) == kind
                    and all(self.int_expr(a, cand, kind, intvars, vecvars) for a in e[2]))
        return False

    def intish(self, e, cand, kind, intvars):
        if not isinstance(e, tuple) or not e:
            return False
        k = e[0]
        if k == "num":
            return isinstance(e[1], int) and not isinstance(e[1], bool)
        if k == "bool":
            return True
        if k == "var":
            return e[1] in intvars
        if k == "un":
            return self.intish(e[2], cand, kind, intvars)
        if k == "move":
            return self.intish(e[1], cand, kind, intvars)
        if k == "idx":
            return kind == "i64v"
        if k == "bin":
            if e[1] in ("==", "!=", "<", ">", "<=", ">=", "&&", "||"):
                return True
            return (self.intish(e[2], cand, kind, intvars)
                    and self.intish(e[3], cand, kind, intvars))
        if k == "call":
            return kind == "i64" and e[1] in cand and self.fastkinds.get(e[1]) == "i64"
        return False

    def _booly(self, e):
        return (isinstance(e, tuple) and e and
                (e[0] == "bool" or (e[0] == "un" and e[1] == "!")
                 or (e[0] == "bin" and e[1] in ("==", "!=", "<", ">", "<=", ">=", "&&", "||"))))

    def print_ok(self, e, cand, kind, intvars):
        if not (isinstance(e, tuple) and e and e[0] == "call" and e[1] == "print"):
            return False
        for a in e[2]:
            if not isinstance(a, tuple) or not a:
                return False
            if a[0] == "str":
                for kd, val in _split_interp(a[1]):
                    if kd == "var" and "." in val:
                        return False
                continue
            if self._booly(a):
                return False
            if not self.int_expr(a, cand, kind, intvars):
                return False
        return True

    def int_body(self, body, cand, kind, intvars, vecvars=frozenset()):
        for s in body:
            if not isinstance(s, tuple) or not s:
                return False
            k = s[0]
            if k == "let":
                if not self.int_expr(s[2], cand, kind, intvars, vecvars):
                    return False
                if self.intish(s[2], cand, kind, intvars):
                    intvars.add(s[1])
                else:
                    intvars.discard(s[1])
            elif k == "set":
                if "." in s[1] or not self.int_expr(s[2], cand, kind, intvars, vecvars):
                    return False
                if self.intish(s[2], cand, kind, intvars):
                    intvars.add(s[1])
                else:
                    intvars.discard(s[1])
            elif k == "setidx":
                if kind != "i64v":
                    return False
                if not (isinstance(s[1], tuple) and s[1][0] == "var" and s[1][1] in vecvars):
                    return False
                if not self.intish(s[2], cand, kind, intvars) or not self.intish(s[3], cand, kind, intvars):
                    return False
            elif k in ("return", "expr"):
                if k == "expr" and self.print_ok(s[1], cand, kind, intvars):
                    continue
                if not self.int_expr(s[1], cand, kind, intvars, vecvars):
                    return False
            elif k == "if":
                if not self.int_expr(s[1], cand, kind, intvars, vecvars):
                    return False
                if not self.int_body(s[2], cand, kind, set(intvars), vecvars):
                    return False
                els = s[3]
                if els and not self.int_body(els if isinstance(els, list) else [els], cand, kind, set(intvars), vecvars):
                    return False
            elif k == "while":
                if not self.int_expr(s[1], cand, kind, intvars, vecvars):
                    return False
                if not self.int_body(s[2], cand, kind, set(intvars), vecvars):
                    return False
            elif k == "forrange":
                if not self.intish(s[2], cand, kind, intvars) or not self.intish(s[3], cand, kind, intvars):
                    return False
                sub = set(intvars)
                sub.add(s[1])
                if not self.int_body(s[4], cand, kind, sub, vecvars):
                    return False
            elif k == "foriter":
                if kind != "i64v":
                    return False
                ve = s[2]
                if not (isinstance(ve, tuple) and ve[0] == "var" and ve[1] in vecvars):
                    return False
                sub = set(intvars)
                sub.add(s[1])
                if not self.int_body(s[3], cand, kind, sub, vecvars):
                    return False
            elif k in ("break", "continue"):
                pass
            else:
                return False
        return True

    def compute_fast(self):
        kinds = {}
        self.fastvec = {}
        for name, entry in self.fns.items():
            if not (isinstance(entry, tuple) and len(entry) == 2):
                continue
            params, body = entry
            anns = []
            ok = True
            for p in params:
                if isinstance(p, tuple) and len(p) > 2 and p[2] in ("i64", "f64", "vec[i64]"):
                    anns.append(p[2])
                else:
                    ok = False
            if ok and anns and "vec[i64]" in anns and all(a in ("i64", "vec[i64]") for a in anns):
                kinds[name] = "i64v"
                self.fastvec[name] = set(p[0] if isinstance(p, tuple) else p
                                         for p in params if isinstance(p, tuple) and len(p) > 2 and p[2] == "vec[i64]")
            elif ok and (not anns or len(set(anns)) == 1):
                kinds[name] = anns[0] if anns else "i64"
        self.fastkinds = dict(kinds)
        cand = dict(kinds)
        changed = True
        while changed:
            changed = False
            for name in list(cand):
                params, body = self.fns[name]
                kind = kinds[name]
                intvars = set()
                if kind == "i64":
                    intvars = set(p[0] if isinstance(p, tuple) else p for p in params)
                if not self.int_body(body, set(cand), kind, intvars, self.fastvec.get(name, frozenset())):
                    del cand[name]
                    changed = True
        cand = self._drop_recursive_fast(cand)
        self.fast = {n: self.fns[n] for n in cand}
        self.fastkinds = {n: kinds[n] for n in cand}
        for i, name in enumerate(sorted(self.fast)):
            self.fastc[name] = "bf_f%d" % i
        for name in self.fast:
            kind = self.fastkinds[name]
            for op in self.ops_in(self.fast[name][1]):
                if op == "/":
                    if kind == "i64":
                        self.need_idiv = True
                    else:
                        self.need_fdiv = True
                if op == "%":
                    if kind == "i64":
                        self.need_imod = True
                    else:
                        self.need_fmod = True
            if kind == "i64v":
                self.need_viat = True
                if self.has_setidx(self.fast[name][1]):
                    self.need_vset = True

    def has_setidx(self, body):
        for s in body:
            if not isinstance(s, tuple) or not s:
                continue
            if s[0] == "setidx":
                return True
            for sub in s[1:]:
                if isinstance(sub, list) and self.has_setidx(sub):
                    return True
        return False

    def ops_in(self, body):
        ops = set()

        def walk(e):
            if not isinstance(e, tuple) or not e:
                return
            if e[0] == "bin":
                ops.add(e[1])
            for sub in e[1:]:
                if isinstance(sub, tuple):
                    walk(sub)
                elif isinstance(sub, list):
                    for x in sub:
                        if isinstance(x, tuple):
                            walk(x)

        for s in body:
            walk(s)
        return ops

    def fx(self, e):
        k = e[0]
        dbl = self.fkind == "f64"
        if k == "num":
            return "(%.17g)" % float(e[1]) if dbl else "(%dLL)" % _wrap64(e[1])
        if k == "bool":
            return "(%.17g)" % (1.0 if e[1] else 0.0) if dbl else "(%dLL)" % (1 if e[1] else 0)
        if k == "var":
            return self.fsym[e[1]]
        if k == "un":
            v = self.fx(e[2])
            if e[1] == "!":
                return "(!(%s))" % v
            return "(-(%s))" % v if dbl else "((long long)(0ULL - (unsigned long long)(%s)))" % v
        if k == "move":
            return self.fx(e[1])
        if k == "idx":
            return "bf_viat(%s, %s)" % (self.fsym[e[1][1]], self.fx(e[2]))
        if k == "bin":
            op = e[1]
            a = self.fx(e[2])
            b = self.fx(e[3])
            if op == "/":
                return "%s(%s, %s)" % ("bf_fdiv" if dbl else "bf_idiv", a, b)
            if op == "%":
                return "%s(%s, %s)" % ("bf_fmod" if dbl else "bf_imod", a, b)
            if op == "&&":
                return "(%s ? %s : %s)" % (a, b, a)
            if op == "||":
                return "(%s ? %s : %s)" % (a, a, b)
            if not dbl and op in ("+", "-", "*"):
                return "((long long)((unsigned long long)(%s) %s (unsigned long long)(%s)))" % (a, op, b)
            return "(%s %s %s)" % (a, op, b)
        if k == "call":
            if e[1] == "len":
                return "((%s)->len)" % self.fsym[e[2][0][1]]
            args = ", ".join(self.fx(a) for a in e[2])
            return "%s(%s)" % (self.fastc[e[1]], args)
        raise RuntimeError("fast node " + k)

    def fprint(self, args, ind):
        dbl = self.fkind == "f64"
        fmt = []
        vals = []
        for ai, a in enumerate(args):
            if ai:
                fmt.append(" ")
            if a[0] == "str":
                for kind, val in _split_interp(a[1]):
                    if kind == "lit":
                        fmt.append(val.replace("%", "%%"))
                    else:
                        fmt.append("%s" if dbl else BINT_FMT)
                        vals.append("vfmt_double(%s)" % self.fsym[val] if dbl else self.fsym[val])
            elif a[0] == "num" and isinstance(a[1], float):
                fmt.append("%s")
                vals.append("vfmt_double(%r)" % a[1])
            elif dbl:
                fmt.append("%s")
                vals.append("vfmt_double(%s)" % self.fx(a))
            else:
                fmt.append(BINT_FMT)
                vals.append(self.fx(a))
        esc = _clit("".join(fmt))
        self.out.append(ind + 'printf("%s\\n"%s);' % (esc, "".join(", " + v for v in vals)))

    def fst(self, s, ind):
        k = s[0]
        if k in ("let", "set"):
            self.out.append(ind + "%s = %s;" % (self.fsym[s[1]], self.fx(s[2])))
        elif k == "return":
            self.out.append(ind + "return %s;" % self.fx(s[1]))
        elif k == "expr":
            if isinstance(s[1], tuple) and s[1] and s[1][0] == "call" and s[1][1] == "print":
                self.fprint(s[1][2], ind)
            else:
                self.out.append(ind + "(void)(%s);" % self.fx(s[1]))
        elif k == "if":
            self.out.append(ind + "if (%s) {" % self.fx(s[1]))
            for t in s[2]:
                self.fst(t, ind + "  ")
            els = s[3]
            if els:
                self.out.append(ind + "} else {")
                for t in (els if isinstance(els, list) else [els]):
                    self.fst(t, ind + "  ")
            self.out.append(ind + "}")
        elif k == "while":
            self.out.append(ind + "while (%s) {" % self.fx(s[1]))
            for t in s[2]:
                self.fst(t, ind + "  ")
            self.out.append(ind + "}")
        elif k == "forrange":
            _, vn, se, ee, body = s
            kk = self.new_tmp("k")
            lo = self.new_tmp("lo")
            hi = self.new_tmp("hi")
            self.out.append(ind + "long long %s = %s, %s = %s;" % (lo, self.fx(se), hi, self.fx(ee)))
            self.out.append(ind + "for (long long %s = %s; %s < %s; ++%s) {" % (kk, lo, kk, hi, kk))
            self.out.append(ind + "  %s = %s;" % (self.fsym[vn], kk))
            for t in body:
                self.fst(t, ind + "  ")
            self.out.append(ind + "}")
        elif k == "foriter":
            _, vn, ve, body = s
            vv = self.fsym[ve[1]]
            kk = self.new_tmp("k")
            self.out.append(ind + "for (long %s = 0; %s < %s->len; ++%s) {" % (kk, kk, vv, kk))
            self.out.append(ind + "  %s = bf_viat(%s, %s);" % (self.fsym[vn], vv, kk))
            for t in body:
                self.fst(t, ind + "  ")
            self.out.append(ind + "}")
        elif k == "setidx":
            self.out.append(ind + "bf_vset(%s, %s, %s);" % (self.fsym[s[1][1]], self.fx(s[2]), self.fx(s[3])))
        elif k == "break":
            self.out.append(ind + "break;")
        elif k == "continue":
            self.out.append(ind + "continue;")
        else:
            raise RuntimeError("fast stmt " + k)

    def _drop_recursive_fast(self, cand):
        """Route recursive fast fns through their counted Val frame.

        The raw C fast path calls itself directly, so it would skip the depth
        counter and tail-recurse into an endless loop. Anything that calls a
        dropped fn must drop too, or its raw call site loses its target.
        """
        fast = {n: self.fns[n][1] for n in cand}

        def callees(body):
            out = set()

            def walk(x):
                if isinstance(x, tuple):
                    if x and x[0] == "call" and isinstance(x[1], str):
                        out.add(x[1])
                    for y in x:
                        walk(y)
                elif isinstance(x, list):
                    for y in x:
                        walk(y)

            walk(body)
            return out & set(fast)

        edges = {n: callees(b) for n, b in fast.items()}

        def reaches(src, dst, seen):
            if src == dst:
                return True
            if src in seen:
                return False
            seen.add(src)
            return any(reaches(c, dst, seen) for c in edges.get(src, ()))

        drop = {n for n in fast if any(reaches(c, n, set()) for c in edges[n])}
        changed = True
        while changed:
            changed = False
            for n in fast:
                if n not in drop and edges[n] & drop:
                    drop.add(n)
                    changed = True
        return [n for n in cand if n not in drop]

    def emit_fast_fn(self, name):
        params, body = self.fast[name]
        self.fkind = self.fastkinds[name]
        ct = "double" if self.fkind == "f64" else "long long"
        self.fvecvars = self.fastvec.get(name, frozenset())
        names = [p[0] if isinstance(p, tuple) else p for p in params]
        self.fsym = {}
        args = []
        for n in names:
            cn = _cname(n)
            self.fsym[n] = cn
            if n in self.fvecvars:
                args.append("Vec *%s" % cn)
            else:
                args.append("%s %s" % (ct, cn))
        self.out.append("static %s %s(%s) {" % (ct, self.fastc[name], ", ".join(args) if args else "void"))
        vars_ = set(names)
        _collect_vars(body, vars_)
        for n in sorted(vars_ - set(names)):
            self.fsym[n] = _cname(n)
            self.out.append("  %s %s;" % (ct, _cname(n)))
        for t in body:
            self.fst(t, "  ")
        self.out.append("  return %s;" % ("0.0" if self.fkind == "f64" else "0"))
        self.out.append("}")
        self.out.append("")
        self.fsym = {}
        self.fkind = "long"
        self.fvecvars = frozenset()

    def fn_code(self, key):
        if key not in self.codes:
            self.codes[key] = len(self.codes)
        return self.codes[key]

    def collect_file(self, path):
        key = _o.path.abspath(path)
        if key in self.seen_mods:
            return
        self.seen_mods.add(key)
        src = open(path, encoding="utf-8").read()
        self.collect_src(src, _o.path.dirname(key))

    def collect_src(self, src, base):
        prog = _brat.parse_prog(_brat.Cur(_brat.lex(src)))
        for s in prog[1]:
            if s[0] in ("fn", "impl", "trait", "enum", "pub"):
                self.collect_top(s, base)
        for s in prog[1]:
            if s[0] == "import":
                self.collect_top(s, base)
            elif s[0] not in ("fn", "impl", "trait", "enum", "pub"):
                self.top_exe.append(("stmt", s))

    def collect_top(self, s, base):
        k = s[0]
        if k == "pub":
            return self.collect_top(s[1], base)
        if k == "fn":
            if s[1] not in self.fns:
                self.fns[s[1]] = (s[2], s[3])
                self.order.append(("fn", s[1]))
            self.collect_nested(s[3])
        elif k == "impl":
            _, trait, tname, fns = s
            if trait is not None:
                if trait not in self.traits:
                    raise RuntimeError("unknown trait " + trait)
                missing = [m for m in self.traits[trait] if m not in [f[1] for f in fns]]
                if missing:
                    raise RuntimeError("impl %s for %s missing %s" % (trait, tname, ",".join(missing)))
            for f in fns:
                key = tname + "." + f[1]
                if key not in self.fns:
                    self.fns[key] = (f[2], f[3])
                    self.order.append(("meth", tname, f[1]))
        elif k == "enum":
            for vname, fields in s[2]:
                tag = "none" if vname == "None" and not fields else vname.lower()
                self.vtab[tag] = s[1]
                if vname not in self.fns:
                    self.fns[vname] = ("ctor", tag, len(fields))
        elif k == "trait":
            if s[1] not in self.traits:
                self.traits[s[1]] = [g[1] for g in s[2]]
        elif k == "import":
            path = _o.path.join(base, *s[1].split(".")) + ".brat"
            if not _o.path.isfile(path):
                alt = _o.path.join(base, "vendor", *s[1].split(".")) + ".brat"
                if _o.path.isfile(alt):
                    path = alt
            self.collect_file(path)
            self.top_exe.append(("import", path))
        else:
            self.top_exe.append(("stmt", s))

    def collect_nested(self, node):
        """Register every `fn` declared inside a body, with a stable mangled name.

        The mangled name is assigned in discovery order, and collect_src runs
        once per program, so both emission passes agree on it. Without this the
        emitter only learns about a nested fn while writing the enclosing body,
        which is after the point where a call to it has to be numbered.
        """
        if isinstance(node, tuple) and node and node[0] == "fn":
            self.nested_seq += 1
            mangled = "%s@%d" % (node[1], self.nested_seq)
            self.fns[mangled] = (node[2], node[3])
            self.fn_alias[node[1]] = mangled
            if mangled not in [e[1] for e in self.late]:
                self.late.append(("fn", mangled, node[2], node[3]))
        if isinstance(node, (tuple, list)):
            for sub in node[1:] if isinstance(node, tuple) else node:
                if isinstance(sub, (tuple, list)):
                    self.collect_nested(sub)

    def cfn(self, key):
        return "bf_c%d" % self.fn_code(key)

    def fn_key(self, name):
        """Emission key for a callable name: the mangled key when `name` is a
        nested fn, so the call and the definition agree on one C name."""
        return self.fn_alias.get(name, name)

    def ex(self, e):
        k = e[0]
        if k == "num":
            v = e[1]
            if isinstance(v, bool):
                return ([], "vbool(%d)" % (1 if v else 0))
            if isinstance(v, int):
                return ([], "vint(%d)" % _wrap64(v))
            return ([], "vfloat(%r)" % float(v))
        if k == "bool":
            return ([], "vbool(%d)" % (1 if e[1] else 0))
        if k == "str":
            return self.ex_str(e[1])
        if k == "rstr":
            return ([], 'vlit("%s")' % _clit(e[1]))
        if k == "none":
            return ([], "vnil()")
        if k == "var":
            return ([], self.var_ref(e[1]))
        if k == "un":
            pre, v = self.ex(e[2])
            if e[1] == "!":
                return (pre, "vbool(!vis_true(%s))" % v)
            return (pre, "vneg(%s)" % v)
        if k == "bin":
            return self.ex_bin(e)
        if k == "or":
            pa, a = self.ex(e[1])
            pb, b = self.ex(e[2])
            t = self.new_tmp()
            pre = pa + ["volatile Val %s = (%s);" % (t, a)] + pb
            return (pre, "(vor_fb(%s) ? (%s) : %s)" % (t, b, t))
        if k == "quest":
            pa, a = self.ex(e[1])
            return (pa, "vquest_ok(vquest(%s))" % a)
        if k == "vec":
            return self.ex_vec(e[1])
        if k == "maplit":
            return self.ex_maplit(e[1])
        if k == "struct":
            return self.ex_struct(e[1], e[2])
        if k == "field":
            pa, a = self.ex(e[1])
            t = self.new_tmp("f")
            fname = _clit(e[2])
            bail = self.err_bail()
            pa.append("if ((%s).tag != V_STRUCT) { vraise(\"no field %s\"); %s }" % (a, fname, bail))
            pa.append("Val *%s = map_get((%s).as.map, \"%s\");" % (t, a, fname))
            pa.append("if (!%s) { vraise(\"no field %s\"); %s }" % (t, fname, bail))
            return (pa, "(*%s)" % t)
        if k == "idx":
            pa, a = self.ex(e[1])
            pb, b = self.ex(e[2])
            return (pa + pb, "vat(%s, %s)" % (a, b))
        if k == "mcall":
            return self.ex_mcall(e)
        if k == "call":
            return self.ex_call(e)
        if k == "closure":
            return self.ex_closure(e)
        if k == "move":
            pre, v = self.ex(e[1])
            if self.move_rt:
                src = self._place_root(e)
                if (
                    src is not None
                    and src in self.sym
                    and isinstance(e[1], tuple)
                    and e[1][0] == "var"
                ):
                    self.moved.add(src)
                    v = "vmove(&%s)" % self.sym[src].cn
            return (pre, v)
        raise RuntimeError("node " + k)

    def ex_str(self, s):
        parts = _split_interp(s)
        if not parts:
            return ([], 'vlit("")')
        if len(parts) == 1 and parts[0][0] == "lit":
            return ([], 'vlit("%s")' % _clit(parts[0][1]))
        t = self.new_tmp("s")
        pre = ['volatile Val %s = vlit("");' % t]
        for kind, val in parts:
            if kind == "lit":
                pre.append('%s = vadd(%s, vlit("%s"));' % (t, t, _clit(val)))
            else:
                pre.append('%s = vadd(%s, vstr_of(%s));' % (t, t, self.path_ref(val)))
        return (pre, t)

    def path_ref(self, path):
        """C expression for an interpolation placeholder, dotted paths included."""
        parts = path.split(".")
        e = self.var_ref(parts[0])
        for f in parts[1:]:
            e = 'vfield(%s, "%s")' % (e, _clit(f))
        return e

    def ex_bin(self, e):
        op = e[1]
        if op in ("&&", "||"):
            pa, a = self.ex(e[2])
            pb, b = self.ex(e[3])
            t = self.new_tmp()
            pre = pa + ["volatile Val %s = (%s);" % (t, a)]
            guard = "if (vis_true(%s)) {" % t if op == "&&" else "if (!vis_true(%s)) {" % t
            pre += [guard] + ["  " + p for p in pb] + ["  %s = (%s);" % (t, b), "}"]
            return (pre, t)
        pa, a = self.ex(e[2])
        pb, b = self.ex(e[3])
        pre = pa + pb
        if op == "+":
            return (pre, "vadd(%s, %s)" % (a, b))
        if op == "-":
            return (pre, "vsub(%s, %s)" % (a, b))
        if op == "*":
            return (pre, "vmul(%s, %s)" % (a, b))
        if op == "/":
            return (pre, "vdiv(%s, %s)" % (a, b))
        if op == "%":
            return (pre, "vmod(%s, %s)" % (a, b))
        if op == "==":
            return (pre, "vbool(veq(%s, %s))" % (a, b))
        if op == "!=":
            return (pre, "vbool(!veq(%s, %s))" % (a, b))
        if op == "<":
            return (pre, "vbool(vcmp(%s, %s) < 0)" % (a, b))
        if op == ">":
            return (pre, "vbool(vcmp(%s, %s) > 0)" % (a, b))
        if op == "<=":
            return (pre, "vbool(vcmp(%s, %s) <= 0)" % (a, b))
        if op == ">=":
            return (pre, "vbool(vcmp(%s, %s) >= 0)" % (a, b))
        if op == "in":
            return (pre, "vbool(vcontains(%s, %s))" % (b, a))
        raise RuntimeError("operator " + op)

    def ex_vec(self, items):
        t = self.new_tmp("v")
        pre = ["Vec *%s = vvec();" % t]
        for x in items:
            px, sx = self.ex(x)
            pre.extend(px)
            pre.append("vec_push(%s, %s);" % (t, sx))
            pre.extend(self._share_root(x))
        return (pre, "vvecv(%s)" % t)

    def ex_maplit(self, pairs):
        t = self.new_tmp("m")
        pre = ['Map *%s = vmap("");' % t]
        for ke, ve in pairs:
            pk, sk = self.ex(ke)
            pv, sv = self.ex(ve)
            pre.extend(pk)
            pre.extend(pv)
            pre.append("map_set_v(%s, %s, %s);" % (t, sk, sv))
            pre.extend(self._share_root(ke) + self._share_root(ve))
        r = self.new_tmp("ml")
        pre.append("volatile Val %s = vmap_has_reserved(%s) ? vmapkey_err() : vstruct(%s);"
                   % (r, t, t))
        return (pre, r)

    def ex_struct(self, tname, fields):
        t = self.new_tmp("m")
        pre = ['Map *%s = vmap("%s");' % (t, _clit(tname))]
        for fname, fval in fields:
            pf, sf = self.ex(fval)
            pre.extend(pf)
            pre.append('map_set(%s, "%s", %s);' % (t, _clit(fname), sf))
            pre.extend(self._share_root(fval))
        return (pre, "vstruct(%s)" % t)

    def args_vec(self, args):
        t = self.new_tmp("a")
        pre = ["Vec *%s = vvec();" % t]
        for a in args:
            pa, sa = self.ex(a)
            pre.extend(pa)
            pre.append("vec_push(%s, %s);" % (t, sa))
            pre.append(self.err_check_line())
        return (pre, t)

    def call_result(self, args, pre, vec, call):
        """A call can hand back one of its arguments, so those bindings are
        counted before the call and the argument vector is freed after it."""
        head = list(pre)
        for a in args:
            head.extend(self._share_root(a))
        t = self.new_tmp("c")
        return (head + ["volatile Val %s = (%s);" % (t, call),
                        "vrc_dec(vvecv(%s));" % vec], t)

    def need_arity(self, args, n, name):
        if len(args) != n:
            raise RuntimeError(name + " arity")

    def ex_call(self, e):
        name, args = e[1], e[2]
        if name == "print":
            if not args:
                return ([], '(printf("\\n"), vint(0))')
            pre_all = []
            parts = []
            for a in args:
                pa, sa = self.ex(a)
                pre_all.extend(pa)
                t = self.new_tmp("p")
                pre_all.append("volatile Val %s = (%s);" % (t, sa))
                pre_all.append(self.err_check_line())
                parts.append(t)
            out = []
            for i, t in enumerate(parts):
                if i:
                    out.append('printf(" ");')
                out.append("vprint(%s);" % t)
            out.append('printf("\\n");')
            return (pre_all + out, "vint(0)")
        if name in ("log.info", "log.warn", "log.error"):
            if not args:
                raise RuntimeError("log arity")
            pa, vec = self.args_vec(args)
            lvl = name.split(".")[1].upper()
            return (pa + ['vlogv("%s", %s);' % (lvl, vec),
                          "vrc_dec(vvecv(%s));" % vec], "vint(0)")
        if name == "trace.span":
            if not args:
                raise RuntimeError("span arity")
            rest = list(args)
            body = None
            if rest and isinstance(rest[-1], tuple) and rest[-1] and rest[-1][0] == "closure":
                body = rest.pop()
            pa, vec = self.args_vec(rest)
            out = list(pa) + ["vspan(vvecv(%s));" % vec,
                              "vrc_dec(vvecv(%s));" % vec]
            if body is not None:
                pb, cb = self.ex(body)
                out.extend(pb)
                t = self.new_tmp("sp")
                sv = self.new_tmp("sv")
                out.append("Vec *%s = vvec();" % sv)
                out.append("volatile Val %s = vcall_any(%s, %s);" % (t, cb, sv))
                out.append("vrc_dec(vvecv(%s));" % sv)
                return (out, t)
            return (out, "vint(0)")
        if name == "len":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "vlen(%s)" % a)
        if name == "__at":
            self.need_arity(args, 2, name)
            pa, a = self.ex(args[0])
            pb, b = self.ex(args[1])
            return (pa + pb, "vat(%s, %s)" % (a, b))
        if name in ("Ok", "Err", "Some"):
            self.need_arity(args, 1, name)
            return self.ex_tag(name.lower(), args)
        if name == "unwrap":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "vunwrap(%s)" % a)
        if name == "is_err":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "vbool(vis_err(%s))" % a)
        if name == "is_none":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "vbool(vis_none(%s))" % a)
        if name == "is_vec":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "vbool((%s).tag == V_VEC)" % a)
        if name == "is_str":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "vbool((%s).tag == V_STR)" % a)
        if name == "is_map":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "vbool((%s).tag == V_STRUCT)" % a)
        if name == "tagof":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "vtagof(%s)" % a)
        if name == "str":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "vstr_of(%s)" % a)
        if name == "assert":
            if not (1 <= len(args) <= 2):
                raise RuntimeError("assert arity")
            pa, a = self.ex(args[0])
            bail = self.err_bail()
            if len(args) == 2:
                pb, b = self.ex(args[1])
                return (pa + pb + [
                    'if (!vis_true(%s)) { Vec *_ae = vvec(); vec_push(_ae, %s); '
                    'vunwind = vtagv("err", _ae); vunwind_do(); %s }' % (a, b, bail)
                ], "vbool(1)")
            return (pa + ['if (!vis_true(%s)) { vraise("assert failed"); %s }' % (a, bail)], "vbool(1)")
        if name == "read_file":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "vreadfile(%s)" % a)
        if name == "write_file":
            self.need_arity(args, 2, name)
            pa, a = self.ex(args[0])
            pb, b = self.ex(args[1])
            return (pa + pb, "vwritefile(%s, %s)" % (a, b))
        if name in ("tcp_listen", "tcp_connect", "tcp_accept", "tcp_close"):
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "v%s(%s)" % (name, a))
        if name == "sbuf":
            self.need_arity(args, 0, name)
            return ([], "vsbuf()")
        if name == "sbuf_add":
            self.need_arity(args, 2, name)
            pa, a = self.ex(args[0])
            pb, b = self.ex(args[1])
            return (pa + pb, "vsbuf_add(%s, %s)" % (a, b))
        if name in ("sbuf_len", "sbuf_str"):
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "v%s(%s)" % (name, a))
        if name in ("tcp_recv", "tcp_send"):
            self.need_arity(args, 2, name)
            pa, a = self.ex(args[0])
            pb, b = self.ex(args[1])
            return (pa + pb, "v%s(%s, %s)" % (name, a, b))
        if name == "join_all":
            self.need_arity(args, 0, name)
            return ([], "(vjoin_all(), vint(0))")
        if name == "args":
            self.need_arity(args, 0, name)
            return ([], "vargs()")
        if name == "exit":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "(vexit(%s), vint(0))" % a)
        if name == "eprint":
            pa, vec = self.args_vec(args)
            return (pa + ["veprint(%s);" % vec,
                          "vrc_dec(vvecv(%s));" % vec], "vint(0)")
        if name == "raise":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa + ["vraisev(%s);" % a, self.err_bail()], "vnil()")
        if name == "own":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "vown(%s)" % a)
        if name in ("__arena_enter", "__arena_leave"):
            self.need_arity(args, 0, name)
            fn = "varena_enter" if name == "__arena_enter" else "varena_leave"
            return (["%s();" % fn], "vint(0)")
        if name == "time_ms":
            self.need_arity(args, 0, name)
            return ([], "vtime_ms()")
        if name == "sleep_ms":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "(vsleep_ms(%s), vint(0))" % a)
        if name == "env_get":
            self.need_arity(args, 1, name)
            pa, a = self.ex(args[0])
            return (pa, "venv_get(%s)" % a)
        if name == "re_match":
            self.need_arity(args, 2, name)
            pa, a = self.ex(args[0])
            pb, b = self.ex(args[1])
            return (pa + pb, "vbool(vre_match(%s, %s))" % (a, b))
        if self.fn_key(name) in self.fns:
            return self.ex_direct(self.fn_key(name), args)
        if "." in name:
            return self.ex_dotted(name, args)
        if (not args and "." not in name and name[:1].isupper()
                and (name in self.sym or (self.cap is not None and name in self.cap[1]))):
            # A bare uppercase name parses as a zero-arg call; when it names a
            # local binding it reads the binding, as the interpreter does.
            return self.ex(("var", name))
        if name in self.sym:
            return self.ex_vcall(name, args)
        if self.cap is not None and name in self.cap[1]:
            pa, vec = self.args_vec(args)
            return self.call_result(
                args, pa, vec,
                "vcall_any((*map_get(%s, \"%s\")), %s)" % (self.cap[0], name, vec))
        raise RuntimeError("unresolved call " + name)

    def ex_tag(self, tag, args):
        t = self.new_tmp("g")
        pre = ["Vec *%s = vvec();" % t]
        for a in args:
            pa, sa = self.ex(a)
            pre.extend(pa)
            pre.append("vec_push(%s, %s);" % (t, sa))
            pre.extend(self._share_root(a))
        return (pre, 'vtagv("%s", %s)' % (tag, t))

    def ex_dotted(self, name, args):
        parts = name.split(".")
        head, meth = parts[0], parts[-1]
        if not (head[:1].isupper() and len(parts) == 2 and head not in self.sym
                and not (self.cap is not None and head in self.cap[1])):
            node = ("var", head)
            for f in parts[1:-1]:
                node = ("field", node, f)
            po, so = self.ex(node)
            if ((meth in _VBUILTIN_DIRECT or (meth in _STRVEC_METH and meth in _MAP_METH))
                    and meth not in self.user_meths):
                pa, vec = self.args_vec(args)
                return self.call_result(
                    args, po + pa + self._share_root(node), vec,
                    'vbuiltin(%s, "%s", %s)' % (so, _clit(meth), vec))
            if meth in _STRVEC_METH and meth not in self.user_meths:
                return self.ex_strvec_meth(so, po, meth, args)
            if meth in _MAP_METH and meth not in self.user_meths:
                return self.ex_map_meth(so, po, meth, args)
            pa, vec = self.args_vec(args)
            return self.call_result(
                args, po + pa + self._share_root(node), vec,
                'vcall_method(%s, "%s", %s)' % (so, _clit(meth), vec))
        if head[:1].isupper() and len(parts) == 2:
            entry = self.fns.get(head)
            if isinstance(entry, tuple) and entry and entry[0] == "ctor" and entry[2] == 0:
                tag = entry[1]
                base = "vnil()" if tag == "none" else 'vtagv("%s", vvec())' % _clit(tag)
                pa, vec = self.args_vec(args)
                return self.call_result(
                    args, pa, vec,
                    'vcall_method(%s, "%s", %s)' % (base, _clit(meth), vec))
            key = head + "." + meth
            if key in self.fns and isinstance(self.fns[key], tuple) and len(self.fns[key]) == 2:
                params = self.fns[key][0]
                names = [p[0] if isinstance(p, tuple) else p for p in params]
                if names and names[0] == "self":
                    raise RuntimeError(key + " needs instance, use value.method()")
                args = self.pad_defaults(params, args, key)
                pa, vec = self.args_vec(args)
                return self.call_result(
                    args, pa, vec, "%s(NULL, %s)" % (self.cfn("meth:" + key), vec))
        node = ("var", head)
        for f in parts[1:-1]:
            node = ("field", node, f)
        po, so = self.ex(node)
        pa, vec = self.args_vec(args)
        return self.call_result(
            args, po + pa + self._share_root(node), vec,
            'vcall_method(%s, "%s", %s)' % (so, _clit(meth), vec))

    def pad_defaults(self, params, args, name):
        if len(args) == len(params):
            return args
        if len(args) < len(params):
            tail = params[len(args):]
            if all(isinstance(p, tuple) and len(p) > 3 and p[3] is not None for p in tail):
                return list(args) + [p[3] for p in tail]
        raise RuntimeError(name + " arity")

    def ex_direct(self, name, args):
        entry = self.fns[name]
        if isinstance(entry, tuple) and entry and entry[0] == "ctor":
            _, tag, arity = entry
            if len(args) != arity:
                raise RuntimeError(name + " arity")
            if tag == "none":
                return ([], "vnil()")
            return self.ex_tag(tag, args)
        params = entry[0]
        args = self.pad_defaults(params, args, name)
        pa, vec = self.args_vec(args)
        return self.call_result(
            args, pa, vec,
            "%s(NULL, %s)" % (self.cfn("fn:" + self.fn_key(name)), vec))

    def ex_vcall(self, name, args):
        pa, vec = self.args_vec(args)
        return self.call_result(
            args, pa, vec, "vcall_any(%s, %s)" % (self.var_ref(name), vec))

    def ex_mcall(self, e):
        obj_e, meth, args = e[1], e[2], e[3]
        if (isinstance(obj_e, tuple) and len(obj_e) == 3 and obj_e[0] == "call"
                and not obj_e[2] and isinstance(obj_e[1], str) and "." not in obj_e[1]
                and obj_e[1][:1].isupper()
                and (obj_e[1] in self.sym or (self.cap is not None and obj_e[1] in self.cap[1]))):
            # A bare uppercase local parses as a zero-arg call; `C.len()` reads
            # the local itself, the same way a dotted spelling did.
            obj_e = ("var", obj_e[1])
        if meth in ("map", "filter") and len(args) == 1:
            return self.ex_mapfilter(obj_e, meth, args[0])
        if meth in ("any", "all", "find") and len(args) == 1:
            return self.ex_hof(obj_e, meth, args[0])
        if meth == "fold" and len(args) == 2:
            return self.ex_hof(obj_e, meth, args[1], args[0])
        if meth == "sort_by" and len(args) == 1:
            return self.ex_hof(obj_e, meth, args[0])
        path = _name_path(obj_e)
        if path is not None and path.split(".")[0] not in self.sym and (
                self.cap is None or path.split(".")[0] not in self.cap[1]):
            # Static path: `log.info`, `Type.method`, `Type.Variant` -- the callee
            # is resolved by name, so no receiver storage is read.
            return self.ex_call(("call", path + "." + meth, args))
        po, so = self.ex(obj_e)
        if ((meth in _VBUILTIN_DIRECT or (meth in _STRVEC_METH and meth in _MAP_METH))
                and meth not in self.user_meths):
            pa, vec = self.args_vec(args)
            return self.call_result(
                args, po + pa + self._share_root(obj_e), vec,
                'vbuiltin(%s, "%s", %s)' % (so, _clit(meth), vec))
        if meth in _STRVEC_METH and meth not in self.user_meths:
            return self.ex_strvec_meth(so, po, meth, args)
        if meth in _MAP_METH and meth not in self.user_meths:
            return self.ex_map_meth(so, po, meth, args)
        pa, vec = self.args_vec(args)
        return self.call_result(
            args, po + pa + self._share_root(obj_e), vec,
            'vcall_method(%s, "%s", %s)' % (so, _clit(meth), vec))

    def ex_map_meth(self, so, po, meth, args):
        if meth == "keys":
            self.need_arity(args, 0, meth)
            return (po, "vmap_keys(%s)" % so)
        if meth == "items":
            self.need_arity(args, 0, meth)
            return (po, "vmap_items(%s)" % so)
        if meth == "vals":
            self.need_arity(args, 0, meth)
            return (po, "vmap_vals(%s)" % so)
        if meth == "copy":
            self.need_arity(args, 0, meth)
            return (po, "vcopy(%s)" % so)
        if meth == "get_or":
            self.need_arity(args, 2, meth)
            pa, a = self.ex(args[0])
            pb, b = self.ex(args[1])
            return (po + pa + pb + self._share_root(args[1]),
                    "vmap_get_or(%s, %s, %s)" % (so, a, b))
        if meth in ("get", "has", "del"):
            self.need_arity(args, 1, meth)
            pa, a = self.ex(args[0])
            fn = {"get": "vmap_getv", "has": "vmap_has", "del": "vmap_del"}[meth]
            return (po + pa, "%s(%s, %s)" % (fn, so, a))
        self.need_arity(args, 2, meth)
        pa, a = self.ex(args[0])
        pb, b = self.ex(args[1])
        return (po + pa + pb + self._share_root(args[0]) + self._share_root(args[1]),
                "vmap_setv(%s, %s, %s)" % (so, a, b))

    def ex_strvec_meth(self, so, po, meth, args):
        simple = _SV_SIMPLE.get(meth)
        if simple is not None:
            fn, arity = simple
            self.need_arity(args, arity, meth)
            pre = po
            cargs = []
            for a in args:
                pa, sa = self.ex(a)
                pre = pre + pa
                cargs.append(sa)
            if meth == "ins":
                pre = pre + self._share_root(args[1])
            return (pre, "%s(%s%s)" % (fn, so, "".join(", " + c for c in cargs)))
        if meth == "split":
            if len(args) > 1:
                raise RuntimeError("split arity")
            if not args:
                return (po + ["Vec *_sp = vvec();"], "vsplit(%s, vvecv(_sp))" % so)
            pa, a = self.ex(args[0])
            return (po + pa + [
                "if ((%s).tag == V_STR && !(%s).as.str[0]) { vraise(\"empty separator\"); %s }"
                % (a, a, self.err_bail())],
                "vsplit(%s, %s)" % (so, a))
        if meth == "lines":
            self.need_arity(args, 0, meth)
            return (po, "vlines(%s)" % so)
        if meth in ("trim", "lower", "upper"):
            self.need_arity(args, 0, meth)
            if meth == "trim":
                return (po, "vtrim(%s)" % so)
            return (po, "vcase(%s, %d)" % (so, 1 if meth == "upper" else 0))
        if meth in ("starts_with", "ends_with", "contains"):
            self.need_arity(args, 1, meth)
            pa, a = self.ex(args[0])
            if meth == "starts_with":
                return (po + pa, "vbool(vaffix(%s, %s, 1, 0))" % (so, a))
            if meth == "ends_with":
                return (po + pa, "vbool(vaffix(%s, %s, 0, 1))" % (so, a))
            return (po + pa, "vbool(vaffix(%s, %s, 0, 0))" % (so, a))
        if meth == "parse_int":
            self.need_arity(args, 0, meth)
            return (po, "vparse_int(%s)" % so)
        if meth == "replace":
            self.need_arity(args, 2, meth)
            pa, a = self.ex(args[0])
            pb, b = self.ex(args[1])
            return (po + pa + pb, "vreplace(%s, %s, %s)" % (so, a, b))
        if meth in ("first", "last", "sort", "rev"):
            self.need_arity(args, 0, meth)
            fn = {"first": "vfirst", "last": "vlast", "sort": "vsort", "rev": "vrev"}[meth]
            return (po, "%s(%s)" % (fn, so))
        if meth == "join":
            self.need_arity(args, 1, meth)
            pa, a = self.ex(args[0])
            return (po + pa, "vjoin(%s, %s)" % (so, a))
        if meth == "push":
            self.need_arity(args, 1, meth)
            pa, a = self.ex(args[0])
            return (po + pa + self._share_root(args[0]), "vpush(%s, %s)" % (so, a))
        if meth == "pop":
            self.need_arity(args, 0, meth)
            return (po, "vpop(%s)" % so)
        raise RuntimeError("method " + meth)

    def ex_hof(self, obj_e, meth, arg, first=None):
        po, so = self.ex(obj_e)
        entry = self.fns.get(arg[1]) if (isinstance(arg, tuple) and arg and arg[0] == "var") else None
        if isinstance(entry, tuple) and len(entry) == 2:
            code = self.fn_code("fn:" + arg[1])
            t = self.new_tmp("c")
            pre = po + [
                "Vec *_cb%d = vvec();" % self.tmp,
                "vec_push(_cb%d, vint(%d));" % (self.tmp, code),
                "vec_push(_cb%d, vstruct(vmap(\"\")));" % self.tmp,
                "volatile Val %s = vtagv(\"closure\", _cb%d);" % (t, self.tmp),
            ]
            fnv = t
        else:
            pa, sa = self.ex(arg)
            pre = po + pa
            fnv = sa
        if first is not None:
            pf, sf = self.ex(first)
            pre = pre + pf
            return (pre, 'vfold(%s, %s, %s)' % (so, sf, fnv))
        crt = {"any": 'vpred(%s, %s, "any")', "all": 'vpred(%s, %s, "all")',
               "find": 'vpred(%s, %s, "find")', "sort_by": "vsort_by(%s, %s)"}[meth]
        return (pre, crt % (so, fnv))

    def ex_mapfilter(self, obj_e, meth, arg):
        po, so = self.ex(obj_e)
        entry = self.fns.get(arg[1]) if (isinstance(arg, tuple) and arg and arg[0] == "var") else None
        if isinstance(entry, tuple) and len(entry) == 2:
            code = self.fn_code("fn:" + arg[1])
            t = self.new_tmp("c")
            pre = po + [
                "Vec *_cb%d = vvec();" % self.tmp,
                "vec_push(_cb%d, vint(%d));" % (self.tmp, code),
                "vec_push(_cb%d, vstruct(vmap(\"\")));" % self.tmp,
                "volatile Val %s = vtagv(\"closure\", _cb%d);" % (t, self.tmp),
            ]
            return (pre, 'vmap_with(%s, %s, "%s")' % (so, t, _clit(meth)))
        pa, sa = self.ex(arg)
        return (po + pa, 'vmap_with(%s, %s, "%s")' % (so, sa, _clit(meth)))

    def ex_closure(self, e):
        _, name, params, body = e
        code = self.fn_code("clo:%d" % len(self.clos))
        outer = set(self.cap[1]) if self.cap is not None else set()
        base = set(self.sym) & self.assigned
        capnames = sorted(base | outer | ({name} if name else set()))
        self.clos.append((params, body, capnames))
        for v in (base | outer):
            # a captured binding outlives this scope, so the closure aliases it
            self.captured.add(v)
        t = self.new_tmp("m")
        pre = ['Map *_cm%d = vmap("");' % self.tmp]
        for var in sorted((base | outer) - ({name} if name else set())):
            pre.extend(self._share_root(("var", var)))
            pre.append('map_set(_cm%d, "%s", %s);' % (self.tmp, var, self.var_ref(var)))
        pre.append("Vec *_cb%d = vvec();" % self.tmp)
        pre.append("vec_push(_cb%d, vint(%d));" % (self.tmp, code))
        pre.append("volatile Val %s = vtagv(\"closure\", _cb%d);" % (t, self.tmp))
        pre.append("{ Val _cv = vstruct(_cm%d); vec_push(_cb%d, _cv); }" % (self.tmp, self.tmp))
        if name:
            pre.append('map_set(_cm%d, "%s", %s);' % (self.tmp, name, t))
        return (pre, t)

    def st(self, s, ind):
        k = s[0]
        if k == "let":
            _, name, e = s[0], s[1], s[2]
            pre, v = self.ex(e)
            for p in pre:
                self.out.append(ind + p)
            if "." in name:
                head, rest = name.split(".", 1)
                if head in self.sym:
                    self.out.append(ind + 'vfieldset(%s, "%s", %s);' % (self.sym[head].cn, rest, v))
                    self.emit_err_check(ind)
                    for ln in self._share_root(e):
                        self.out.append(ind + ln)
                    return
            own = self._own_of(e)
            if (name in self.reclaim or name in self.relet) and v.startswith("vlit("):
                v = "vstr(" + v[5:]  # a release needs a real rc header
                own = "owned"
            if self.move_rt and isinstance(e, tuple) and e and e[0] == "move":
                src = self._place_root(e)
                if src is not None:
                    self.moved.add(src)
            d = self.sym.get(name)
            relet = (name in self.relet and d is not None
                     and name not in self.moved and name not in self.dropped
                     and self._place_root(e) != name)
            cn = self.var_def(name, own)[1]
            self.assigned.add(name)
            if relet:
                # after the RHS: it may still read the previous pass value
                tmp = self.new_tmp("v")
                self.out.append(ind + "volatile Val %s = (%s);" % (tmp, v))
                self.out.append(ind + "vrc_dec(%s);" % d.cn)
                self.out.append(ind + "%s = %s;" % (cn, tmp))
            else:
                self.out.append(ind + "%s = (%s);" % (cn, v))
            self.emit_err_check(ind)
            for ln in self._share_root(e):
                self.out.append(ind + ln)
        elif k == "set":
            pre, v = self.ex(s[2])
            for p in pre:
                self.out.append(ind + p)
            tgt = s[1]
            d = self.sym.get(tgt)
            own = self._own_of(s[2])
            if tgt in self.reclaim and v.startswith("vlit("):
                v = "vstr(" + v[5:]  # a release needs a real rc header
                own = "owned"
            release = (tgt in self.reclaim and d is not None
                       and tgt not in self.moved and tgt not in self.dropped
                       and self._place_root(s[2]) != tgt)
            cn = self.var_def(tgt, own)[1]
            self.assigned.add(tgt)
            if release:
                # after the RHS so a hot store loop pays one dec per store, not per element
                tmp = self.new_tmp("v")
                self.out.append(ind + "volatile Val %s = (%s);" % (tmp, v))
                self.out.append(ind + "vrc_dec(%s);" % d.cn)
                self.out.append(ind + "%s = %s;" % (cn, tmp))
            else:
                self.out.append(ind + "%s = (%s);" % (cn, v))
            self.emit_err_check(ind)
            for ln in self._share_root(s[2]):
                self.out.append(ind + ln)
        elif k == "setidx":
            pb, sb = self.ex(s[1])
            pi, si = self.ex(s[2])
            pv, sv = self.ex(s[3])
            for p in pb + pi + pv:
                self.out.append(ind + p)
            self.out.append(ind + "vset_at(%s, %s, %s);" % (sb, si, sv))
            self.emit_err_check(ind)
            for ln in self._share_root(s[2]) + self._share_root(s[3]):
                self.out.append(ind + ln)
            base = s[1]
            if isinstance(base, tuple) and base and base[0] == "var":
                self.assigned.add(base[1])
        elif k == "setfield":
            pb, sb = self.ex(s[1])
            pv, sv = self.ex(s[3])
            for p in pb + pv:
                self.out.append(ind + p)
            self.out.append(ind + 'vfieldset(%s, "%s", %s);' % (sb, s[2], sv))
            self.emit_err_check(ind)
            for ln in self._share_root(s[3]):
                self.out.append(ind + ln)
        elif k == "return":
            pre, v = self.ex(s[1])
            for p in pre:
                self.out.append(ind + p)
            self.emit_err_check(ind)
            if self.fin == "__fin_top":
                self.out.append(ind + '{ fprintf(stderr, "unhandled: "); vprint_err(%s); fprintf(stderr, "\\n"); return 1; }' % v)
            else:
                self.out.append(ind + "{ __ret = (%s); goto %s; }" % (v, self.fin))
        elif k == "expr":
            pre, v = self.ex(s[1])
            for p in pre:
                self.out.append(ind + p)
            self.out.append(ind + "(void)(%s);" % v)
            self.emit_err_check(ind)
        elif k == "if":
            _, cond, then, els = s
            pc, c = self.ex(cond)
            for p in pc:
                self.out.append(ind + p)
            self.emit_err_check(ind)
            self.out.append(ind + "if (vis_true(%s)) {" % c)
            self.emit_block(then, ind + "  ")
            if not els:
                self.out.append(ind + "}")
                return
            if isinstance(els, tuple):
                self.out.append(ind + "} else")
                self.st(els, ind)
                return
            self.out.append(ind + "} else {")
            self.emit_block(els, ind + "  ")
            self.out.append(ind + "}")
        elif k == "while":
            pc, c = self.ex(s[1])
            self.out.append(ind + "while (1) {")
            for p in pc:
                self.out.append(ind + "  " + p)
            self.emit_err_check(ind + "  ")
            self.out.append(ind + "  if (!vis_true(%s)) break;" % c)
            self.emit_block(s[2], ind + "  ")
            self.out.append(ind + "}")
        elif k == "forrange":
            _, vn, se, ee, body = s
            pa, a = self.ex(se)
            pb, b = self.ex(ee)
            for p in pa + pb:
                self.out.append(ind + p)
            self.emit_err_check(ind)
            lo = self.new_tmp("lo")
            hi = self.new_tmp("hi")
            kk = self.new_tmp("k")
            self.out.append(ind + "long long %s = vlong(%s);" % (lo, a))
            self.out.append(ind + "long long %s = vlong(%s);" % (hi, b))
            self.emit_err_check(ind)
            self.out.append(ind + "for (long long %s = %s; %s < %s; ++%s) {" % (kk, lo, kk, hi, kk))
            cn = self.var_def(vn)[1]
            self.out.append(ind + "  %s = vint(%s);" % (cn, kk))
            self.assigned.add(vn)
            self.emit_block(body, ind + "  ")
            self.out.append(ind + "}")
        elif k == "foriter":
            _, vn, ve, body = s
            pv, v = self.ex(ve)
            for p in pv:
                self.out.append(ind + p)
            self.emit_err_check(ind)
            vv = self.new_tmp("v")
            kk = self.new_tmp("k")
            self.out.append(ind + "Vec *%s = viter_vec(%s);" % (vv, v))
            self.emit_err_check(ind)
            self.out.append(ind + "for (long %s = 0; %s < %s->len; ++%s) {" % (kk, kk, vv, kk))
            cn = self.var_def(vn)[1]
            self.out.append(ind + "  %s = %s->items[%s];" % (cn, vv, kk))
            self.assigned.add(vn)
            self.emit_block(body, ind + "  ")
            self.out.append(ind + "}")
        elif k == "break":
            self.emit_loop_exit("break", ind)
        elif k == "continue":
            self.emit_loop_exit("continue", ind)
        elif k == "try":
            self.try_n += 1
            n = self.try_n
            ename = s[3] if len(s) > 3 else None
            catch_lbl = "__catch%d" % n
            after_lbl = "__after_try%d" % n
            saved_goto = self.err_goto
            saved_base = self.err_base
            base = self.new_tmp("dfb")
            self.out.append(ind + "{")
            self.out.append(ind + "  long %s = vdfsp;" % base)
            self.err_goto = catch_lbl
            self.err_base = base
            self.emit_block(s[1], ind + "  ")
            self.err_goto = saved_goto
            self.err_base = saved_base
            self.out.append(ind + "  goto %s;" % after_lbl)
            self.out.append(ind + "%s:;" % catch_lbl)
            if ename:
                cn = self.var_def(ename)[1]
                self.out.append(ind + "  { Val __eu = verr_take();")
                self.out.append(
                    ind + "    %s = (__eu.tag == V_TAG && __eu.as.tv.args->len)"
                    " ? __eu.as.tv.args->items[0] : vnil(); }" % cn
                )
                self.assigned.add(ename)
            else:
                self.out.append(ind + "  (void)verr_take();")
            self.emit_block(s[2], ind + "  ")
            if ename and ename in self.sym:
                del self.sym[ename]
                self.assigned.discard(ename)
            self.out.append(ind + "%s:;" % after_lbl)
            self.out.append(ind + "}")
        elif k == "spawn":
            code = self.fn_code("clo:%d" % len(self.clos))
            capnames = sorted((set(self.sym) & self.assigned)
                              | (set(self.cap[1]) if self.cap is not None else set()))
            self.clos.append(([], s[1], capnames))
            for v in capnames:
                self.captured.add(v)
            self.new_tmp("m")
            self.out.append(ind + 'Map *_cm%d = vmap("");' % self.tmp)
            for var in capnames:
                for ln in self._share_root(("var", var)):
                    self.out.append(ind + ln)
                self.out.append(ind + 'map_set(_cm%d, "%s", %s);' % (self.tmp, var, self.var_ref(var)))
            self.out.append(ind + "Vec *_cb%d = vvec();" % self.tmp)
            self.out.append(ind + "vec_push(_cb%d, vint(%d));" % (self.tmp, code))
            self.out.append(ind + "{ Val _cv = vstruct(_cm%d); vec_push(_cb%d, _cv); }" % (self.tmp, self.tmp))
            self.out.append(ind + 'vspawn(vtagv("closure", _cb%d));' % self.tmp)
        elif k == "defer":
            self.emit_defer(s[1], ind)
        elif k == "fn":
            name = s[1]
            if not self.nesting:
                self.fns[name] = (s[2], s[3])
                if ("fn", name) not in self.order:
                    self.order.append(("fn", name))
                self.late.append(s)
            else:
                mangled = self.fn_alias.get(name)
                if mangled is None:
                    self.nested_seq += 1
                    mangled = "%s@%d" % (name, self.nested_seq)
                    self.fns[mangled] = (s[2], s[3])
                    self.fn_alias[name] = mangled
                    self.late.append(("fn", mangled, s[2], s[3]))
                if mangled not in [e[1] for e in self.late]:
                    self.late.append(("fn", mangled, s[2], s[3]))
        elif k == "import":
            self.splice_file(s[1])
        elif k in ("trait", "enum"):
            pass
        elif k == "impl":
            pass
        elif k == "pub":
            self.st(s[1], ind)
        else:
            raise RuntimeError("stmt " + k)

    def var_def(self, name, own="copy"):
        cn = _cname(name)
        self.sym[name] = Sym(cn, own)
        return (None, cn)

    def emit_block(self, body, ind):
        """Emit a statement list as a defer block.

        A block that holds a defer of its own gets a defer frame per pass:
        pushed on entry, drained LIFO on the way out. Blocks without one emit
        nothing extra, so the common case is byte-identical to before.
        """
        if not _block_defers(body):
            for t in body:
                self.st(t, ind)
            if self.move_rt:
                for name in _block_let_names(body):
                    self.emit_drop(name, ind)
            return
        b = self.new_tmp("dfb")
        # volatile kept for ABI stability with older generated defer frames.
        self.out.append(ind + "volatile long %s = vdfsp;" % b)
        self.out.append(ind + "vdfpush();")
        self.dfb.append(b)
        for t in body:
            self.st(t, ind)
        self.dfb.pop()
        self.out.append(ind + "vdfreset(%s);" % b)
        if self.move_rt:
            for name in _block_let_names(body):
                self.emit_drop(name, ind)

    def emit_defer(self, e, ind):
        """Register a defer at RUNTIME, like the interpreter does.

        The body becomes a zero-arg closure over the locals it reads, pushed on
        the current defer frame; the frame belongs to the innermost lexical
        block being executed, or to the function. A defer in the top-level
        statement list has neither, and the interpreter never runs one there
        either, so it stays a no-op instead of raising.
        """
        if self.fin == "__fin_top" and not self.dfb:
            return
        code = self.fn_code("clo:%d" % len(self.clos))
        outer = set(self.cap[1]) if self.cap is not None else set()
        reads = set()
        _expr_vars(e, reads)
        capnames = sorted((set(self.sym) & self.assigned)
                          | (reads & (set(self.sym) | self.dfb_locals | outer))
                          | outer)
        self.clos.append(([], [("expr", e)], capnames))
        for v in capnames:
            self.captured.add(v)
        cm = self.new_tmp("m")
        cb = self.new_tmp("cb")
        pre = ['Map *%s = vmap("");' % cm]
        for var in capnames:
            pre.extend(self._share_root(("var", var)))
            pre.append('map_set(%s, "%s", %s);' % (cm, var, self.var_ref(var)))
        pre.append("Vec *%s = vvec();" % cb)
        pre.append("vec_push(%s, vint(%d));" % (cb, code))
        pre.append("{ Val _cv = vstruct(%s); vec_push(%s, _cv); }" % (cm, cb))
        pre.append('vdfadd(vtagv("closure", %s));' % cb)
        for p in pre:
            self.out.append(ind + p)

    def emit_loop_exit(self, k, ind):
        """break/continue leave the blocks they are lexically inside, so every
        defer frame down to the innermost block drains here. Left to the
        function exit instead, a loop that broke on every pass would grow the
        defer stack once per pass."""
        if self.dfb:
            self.out.append(ind + "vdfreset(%s);" % self.dfb[-1])
        self.out.append(ind + "%s;" % k)

    def var_ref(self, name):
        if name in self.sym:
            return self.sym[name].cn
        if self.cap is not None and name in self.cap[1]:
            return "(*map_get(%s, \"%s\"))" % (self.cap[0], name)
        return _cname(name)

    def _place_root(self, e):
        """Root var name of a place expression, or None when `e` computes a
        fresh value."""
        if not isinstance(e, tuple) or not e:
            return None
        k = e[0]
        if k == "var":
            return e[1]
        if k == "move":
            return self._place_root(e[1])
        if k == "field":
            return self._place_root(e[1])
        if k == "idx":
            return self._place_root(e[1])
        return None

    def _share_root(self, e):
        """A value stored or handed out aliases the binding it came from: count
        it so a later release cannot free storage the alias still reads."""
        src = self._place_root(e)
        if src is None or src not in self.sym:
            return []
        counted = src in self.reclaim or src in self.relet
        if counted or self.sym[src].own != "copy":
            self.shared.add(src)
        if counted:
            return ["vrc_inc(%s);" % self.sym[src].cn]
        return []

    def _own_of(self, e):
        """Ownership class of a value freshly computed by `e`.

        `copy` -- trivially copyable / immortal (scalars, string literals),
                  never dropped.
        `borrow` -- aliases another binding's storage; never dropped itself.
        `owned` -- the binding owns a fresh heap value; dropped once.
        """
        if not isinstance(e, tuple) or not e:
            return "copy"
        k = e[0]
        if k in ("num", "bool", "none", "str", "rstr"):
            return "copy"  # scalars and string literals are immortal
        if k == "var":
            d = self.sym.get(e[1])
            if d is None:
                return "copy"  # param / capture storage belongs to the caller
            return "copy" if d.own == "copy" else "borrow"
        if k == "move":
            return self._own_of(e[1])  # takes the source's ownership
        if k == "closure":
            return "owned"
        if k in ("vec", "maplit", "struct"):
            return "owned"  # fresh heap container
        if k == "bin":
            return "owned"  # fresh result (vdrop is a no-op for scalars/strings)
        if k in ("un", "or", "quest"):
            return "copy"
        if k in ("field", "idx"):
            return "borrow"  # read out of a container: aliases it
        if k in ("call", "mcall"):
            return "copy"  # callee-owned; never drop call results
        return "owned"

    def _drop_allowed(self, name):
        d = self.sym.get(name)
        return (
            self.move_rt
            and d is not None
            and d.own == "owned"
            and name not in self.moved
            and name not in self.captured
            and name not in self.shared
            and name not in self.dropped
        )

    def emit_drop(self, name, ind):
        if self._drop_allowed(name):
            self.dropped.add(name)
            self.out.append(ind + "vdrop(%s);" % self.sym[name].cn)

    def _emit_scope_drops(self, ind):
        """Drop every owned binding still alive at the end of this scope."""
        for name in sorted(self.sym):
            if self._drop_allowed(name):
                self.out.append(ind + "vdrop(%s);" % self.sym[name].cn)
                self.dropped.add(name)

    def splice_file(self, name):
        """Splice an imported module by name (`net` -> net.brat, also vendor/).

        A concrete path also works, because the top-level import loop passes one.
        """
        if name.endswith(".brat") and _o.path.isfile(name):
            path = name
        else:
            path = None
            for base in self.mod_bases:
                cand = _o.path.join(base, *name.split(".")) + ".brat"
                if _o.path.isfile(cand):
                    path = cand
                    break
                alt = _o.path.join(base, "vendor", *name.split(".")) + ".brat"
                if _o.path.isfile(alt):
                    path = alt
                    break
        if path is None:
            raise RuntimeError("no module " + name)
        key = _o.path.abspath(path)
        if key in self.spliced:
            return
        self.spliced.add(key)
        src = open(path, encoding="utf-8").read()
        prog = _brat.parse_prog(_brat.Cur(_brat.lex(src)))
        for st in prog[1]:
            if st[0] not in ("fn", "impl", "trait", "enum", "pub"):
                self.st(st, "  ")

    def define_fn(self, cname, params, body, cap=None, fast=None):
        names = [p[0] if isinstance(p, tuple) else p for p in params]
        saved_sym, saved_cap, saved_dfb, saved_fin = self.sym, self.cap, self.dfb, self.fin
        self.sym = {}
        saved_assigned = self.assigned
        self.assigned = set()
        self.cap = ("cap", set(cap)) if cap is not None else None
        dfb = "__vdf_%s" % cname
        self.dfb = []
        self.fin = "__fin_%s" % cname
        self.out.append("static Val %s(Map *cap, Vec *args) {" % cname)
        self.out.append("  (void)cap;")
        self.out.append("  if (args->len != %d) { vraise(\"arity\"); return vunwind; }" % len(names))
        if fast is not None:
            raw, kind = fast[0], fast[1]
            if kind == "f64":
                checks = " && ".join("(args->items[%d].tag == V_INT || args->items[%d].tag == V_FLOAT)" % (i, i) for i in range(len(names)))
                rawargs = ", ".join("(args->items[%d].tag == V_INT ? (double)args->items[%d].as.ival : args->items[%d].as.fval)" % (i, i, i) for i in range(len(names)))
                self.out.append("  if (%s) return vfloat(%s(%s));" % (checks if checks else "1", raw, rawargs))
            elif kind == "i64v":
                vv = self.fastvec.get(fast[2], frozenset())
                checks = []
                rawargs = []
                for i, n in enumerate(names):
                    if n in vv:
                        checks.append("args->items[%d].tag == V_VEC" % i)
                        rawargs.append("args->items[%d].as.vec" % i)
                    else:
                        checks.append("args->items[%d].tag == V_INT" % i)
                        rawargs.append("args->items[%d].as.ival" % i)
                self.out.append("  if (%s) return vint(%s(%s));" % (" && ".join(checks) if checks else "1", raw, ", ".join(rawargs)))
            else:
                checks = " && ".join("args->items[%d].tag == V_INT" % i for i in range(len(names)))
                rawargs = ", ".join("args->items[%d].as.ival" % i for i in range(len(names)))
                self.out.append("  if (%s) return vint(%s(%s));" % (checks if checks else "1", raw, rawargs))
        self.out.append("  if (bdepth_push()) { vraise(\"recursion too deep\"); return vunwind; }")
        self.out.append("  volatile Val __ret = vnil();")
        self.out.append("  volatile long %s = vdfsp;" % dfb)
        self.out.append("  vdfpush();")
        vars_ = set(names)
        _collect_vars(body, vars_)
        caps = set(cap) if cap is not None else set()
        saved_reclaim = self.reclaim
        saved_relet = self.relet
        # A captured binding has no owner here: releasing it would drop the map's reference.
        self.reclaim, self.relet = _reclaim_names(body, set(names))
        self.reclaim -= caps
        self.relet -= caps
        saved_shared = self.shared
        saved_captured = self.captured
        self.shared = set()
        self.captured = set()
        saved_locals = self.dfb_locals
        self.dfb_locals = vars_
        param_names = set(names)
        for n in sorted(vars_):
            own = "borrow" if n in param_names else "copy"
            self.sym[n] = Sym(_cname(n), own)
            self.out.append("  volatile Val %s;" % _cname(n))
        for n in sorted(self.relet & vars_):
            # First pass of the loop has nothing to release yet.
            self.out.append("  %s = vnil();" % _cname(n))
        for n in sorted((vars_ & caps) - param_names):
            # An assigned capture must start from the snapshot, not a fresh frame local.
            self.out.append('  %s = (*map_get(cap, "%s"));' % (_cname(n), n))
        for i, pn in enumerate(names):
            self.out.append("  %s = args->items[%d];" % (_cname(pn), i))
            self.assigned.add(pn)
        self.nesting += 1
        for t in body:
            self.st(t, "  ")
        self.nesting -= 1
        if self.move_rt:
            self._emit_scope_drops("  ")
        self.out.append("  __fin_%s:" % cname)
        self.out.append("  bdepth_pop();")
        self.out.append("  vdfreset(%s);" % dfb)
        self.out.append("  return __ret;")
        self.out.append("}")
        self.out.append("")
        self.sym, self.cap, self.dfb, self.fin, self.dfb_locals = saved_sym, saved_cap, saved_dfb, saved_fin, saved_locals
        self.assigned = saved_assigned
        self.reclaim = saved_reclaim
        self.relet = saved_relet
        self.shared = saved_shared
        self.captured = saved_captured

    def emit_fn_by_key(self, key):
        if key.startswith("fn:"):
            name = key[3:]
            params, body = self.fns[name]
            fast = None
            if name in self.fastc:
                fast = (self.fastc[name], self.fastkinds[name], name)
            self.define_fn(self.cfn(key), params, body, fast=fast)
        elif key.startswith("meth:"):
            tname, mname = key[5:].rsplit(".", 1)
            params, body = self.fns[tname + "." + mname]
            self.define_fn(self.cfn(key), params, body)
        else:
            raise RuntimeError("bad fn key " + key)

    def emit_prelude_tables(self):
        self.out.append("static const struct { const char *type, *meth; int code; } MTAB[] = {")
        for (tname, mname), code in sorted(self.mtab.items(), key=lambda kv: kv[1]):
            self.out.append('  {"%s", "%s", %d},' % (_clit(tname), _clit(mname), code))
        self.out.append("  {0, 0, 0}")
        self.out.append("};")
        self.out.append("static const struct { const char *tag, *type; } VTAB[] = {")
        for tag, tname in sorted(self.vtab.items()):
            self.out.append('  {"%s", "%s"},' % (_clit(tag), _clit(tname)))
        self.out.append("  {0, 0}")
        self.out.append("};")
        self.out.append("static Val vcall_any(Val fn, Vec *a);")
        self.out.append("static Val vcall_any(Val fn, Vec *a) {")
        self.out.append(
            "  if (fn.tag != V_TAG || strcmp(fn.as.tv.tag, \"closure\") || fn.as.tv.args->len != 2)"
            " { vraise(\"not callable\"); return vunwind; }"
        )
        self.out.append("  long code = vlong(fn.as.tv.args->items[0]);")
        self.out.append("  Val cp = fn.as.tv.args->items[1];")
        self.out.append("  Map *cap = cp.tag == V_STRUCT ? cp.as.map : NULL;")
        self.out.append("  switch (code) {")
        for key, code in sorted(self.codes.items(), key=lambda kv: kv[1]):
            self.out.append("    case %d: return %s(cap, a);" % (code, self.cfn(key)))
        self.out.append("  }")
        self.out.append('  vraise("bad code"); return vunwind;')
        self.out.append("}")
        self.out.append("static Val vcall_method(Val obj, const char *meth, Vec *a) {")
        self.out.append("  const char *tn = NULL;")
        self.out.append("  long i;")
        self.out.append("  if (obj.tag == V_STRUCT) tn = obj.as.map->type;")
        self.out.append("  else if (obj.tag == V_TAG) { for (i = 0; VTAB[i].tag; i++) if (!strcmp(VTAB[i].tag, obj.as.tv.tag)) tn = VTAB[i].type; }")
        self.out.append("  if (tn) for (i = 0; MTAB[i].type; i++) if (!strcmp(MTAB[i].type, tn) && !strcmp(MTAB[i].meth, meth)) {")
        self.out.append("    Vec *b = vvec(); vec_push(b, obj);")
        self.out.append("    for (long j = 0; j < a->len; j++) vec_push(b, a->items[j]);")
        self.out.append("    long code = MTAB[i].code;")
        self.out.append("    switch (code) {")
        for key, code in sorted(self.codes.items(), key=lambda kv: kv[1]):
            self.out.append("    case %d: return %s(NULL, b);" % (code, self.cfn(key)))
        self.out.append("    }")
        self.out.append("  }")
        self.out.append("  return vbuiltin(obj, meth, a);")
        self.out.append("}")
        self.out.append("static Val vmap_with(Val obj, Val fn, const char *meth) {")
        self.out.append("  Vec *w = vvec_of(obj);")
        self.out.append("  Vec *o = vvec();")
        self.out.append("  Vec *one = vvec();")
        self.out.append("  for (long i = 0; i < w->len; i++) {")
        self.out.append("    one->len = 0; vec_push(one, w->items[i]);")
        self.out.append('    Val r = vcall_any(fn, one);')
        self.out.append('    if (verr_pending()) return r;')
        self.out.append('    if (meth[0] == \'m\') vec_push(o, r); else if (vis_true(r)) vec_push(o, w->items[i]);')
        self.out.append("  }")
        self.out.append("  return vvecv(o);")
        self.out.append("}")

    def run_pipeline(self):
        for kind in self.order:
            if kind[0] == "fn":
                self.fn_code("fn:" + kind[1])
            else:
                self.fn_code("meth:" + kind[1] + "." + kind[2])
        for _, tname, mname in [k for k in self.order if k[0] == "meth"]:
            self.mtab[(tname, mname)] = self.fn_code("meth:" + tname + "." + mname)
        self.user_meths = set(m for _, m in self.mtab)
        self.compute_fast()
        if self.need_idiv:
            self.out.append("static long long bf_idiv(long long a, long long b) {")
            self.out.append("  if (!b) { vraise(\"div by zero\"); return 0; }")
            self.out.append("  if (a == INT64_MIN && b == -1) { return INT64_MIN; }")
            self.out.append("  long long q = a / b;")
            self.out.append("  if ((a % b) && ((a < 0) != (b < 0))) q--;")
            self.out.append("  return q;")
            self.out.append("}")
        if self.need_imod:
            self.out.append("static long long bf_imod(long long a, long long b) {")
            self.out.append("  if (!b) { vraise(\"div by zero\"); return 0; }")
            self.out.append("  if (a == INT64_MIN && b == -1) { return 0; }")
            self.out.append("  long long r = a % b;")
            self.out.append("  if (r && ((r < 0) != (b < 0))) r += b;")
            self.out.append("  return r;")
            self.out.append("}")
        if self.need_fdiv:
            self.out.append("static double bf_fdiv(double a, double b) {")
            self.out.append("  if (b == 0.0) { vraise(\"div by zero\"); return 0.0; }")
            self.out.append("  return a / b;")
            self.out.append("}")
        if self.need_fmod:
            self.out.append("static double bf_fmod(double a, double b) {")
            self.out.append("  if (b == 0.0) { vraise(\"div by zero\"); return 0.0; }")
            self.out.append("  double r = fmod(a, b);")
            self.out.append("  if (r != 0.0 && ((r < 0.0) != (b < 0.0))) r += b;")
            self.out.append("  return r;")
            self.out.append("}")
        if self.need_viat:
            self.out.append("static long long bf_viat(Vec *v, long i) {")
            self.out.append("  if (i < 0 || i >= v->len) { vraise(\"index out of range\"); return 0; }")
            self.out.append("  Val x = v->items[i];")
            self.out.append("  if (x.tag != V_INT) { vraise(\"vec elem not int\"); return 0; }")
            self.out.append("  return x.as.ival;")
            self.out.append("}")
        if self.need_vset:
            self.out.append("static void bf_vset(Vec *v, long i, long long x) {")
            self.out.append("  if (i < 0 || i >= v->len) { vraise(\"index out of range\"); return; }")
            self.out.append("  v->items[i] = vint(x);")
            self.out.append("}")
        self.emit_forward_decls()
        for kind in self.order:
            if kind[0] == "fn":
                self.emit_fn_by_key("fn:" + kind[1])
            else:
                self.emit_fn_by_key("meth:" + kind[1] + "." + kind[2])
        for name in sorted(self.fast):
            self.emit_fast_fn(name)
        self.emit_closures_queue()
        self.emit_late()

    def emit_late(self):
        """Emit the fns queued in `late` (nested fns), declaring each prototype
        as it becomes known: a nested fn's body can queue more of them."""
        i = getattr(self, "late_done", 0)
        while i < len(self.late):
            j = getattr(self, "late_declared", 0)
            while j < len(self.late):
                self.out.append("static Val %s(Map *cap, Vec *args);" % self.cfn("fn:" + self.late[j][1]))
                j += 1
            self.late_declared = j
            self.emit_fn_by_key("fn:" + self.late[i][1])
            i += 1
            self.late_done = i
        self.emit_closures_queue()

    def emit_main_open(self):
        self.sym = {}
        self.cap = None
        self.dfb = []
        self.fin = "__fin_top"
        if "fn:main" in self.codes:
            self.out.append("static const char *__brat_tagname(Val v) {")
            self.out.append("  switch (v.tag) {")
            self.out.append("    case V_NIL: return \"None\";")
            self.out.append("    case V_BOOL: return \"bool\";")
            self.out.append("    case V_INT: return \"i64\";")
            self.out.append("    case V_FLOAT: return \"f64\";")
            self.out.append("    case V_STR: return \"str\";")
            self.out.append("    case V_VEC: return \"vec\";")
            self.out.append("    case V_STRUCT: return \"struct\";")
            self.out.append("    default: return v.as.tv.tag ? v.as.tv.tag : \"tag\";")
            self.out.append("  }")
            self.out.append("}")
        self.out.append("int main(int argc, char **argv) {")
        self.out.append("  vmem_budget_apply();")
        self.out.append("  vargs_set(argc, argv);")
        self.out.append("  bdepth_init();")
        base = self.mod_bases[0] if self.mod_bases else "."
        self.out.append('  vbase_set("%s");' % _cstr_path(_o.path.abspath(base)))
        self.out.append("  Vec *_a = vvec();")
        names = set()
        seen = set()

        def scan_stmts(stmts, base):
            for s in stmts:
                if isinstance(s, str):
                    if not _o.path.isfile(s) or s in seen:
                        continue
                    seen.add(s)
                    src = open(s, encoding="utf-8").read()
                    prog = _brat.parse_prog(_brat.Cur(_brat.lex(src)))
                    scan_stmts(prog[1], _o.path.dirname(s))
                    continue
                if not isinstance(s, tuple) or not s:
                    continue
                if s[0] == "import":
                    path = None
                    for b in self.mod_bases + [base]:
                        cand = _o.path.join(b, *s[1].split(".")) + ".brat"
                        if _o.path.isfile(cand):
                            path = cand
                            break
                    if path is None or path in seen:
                        continue
                    seen.add(path)
                    src = open(path, encoding="utf-8").read()
                    prog = _brat.parse_prog(_brat.Cur(_brat.lex(src)))
                    scan_stmts(prog[1], _o.path.dirname(path))
                elif s[0] == "pub":
                    scan_stmts([s[1]], base)
                elif s[0] in ("fn", "impl", "trait", "enum"):
                    pass
                else:
                    _collect_vars([s], names)

        scan_stmts([s for _, s in self.top_exe], self.mod_bases[0] if self.mod_bases else ".")
        self.dfb_locals = set(names)
        for n in sorted(names):
            self.sym[n] = Sym(_cname(n), "copy")
            self.out.append("  volatile Val %s;" % _cname(n))
        for n in sorted(self.relet & names):
            self.out.append("  %s = vnil();" % _cname(n))

    def emit_main_close(self):
        if "fn:main" in self.codes:
            self.out.append("  Val __r = %s(NULL, _a);" % self.cfn("fn:main"))
            self.out.append("  vrc_dec(vvecv(_a));")
            self.out.append(
                "  if (verr_pending()) { fprintf(stderr, \"unhandled: \"); "
                "vprint_err(verr_take()); fprintf(stderr, \"\\n\"); return 1; }"
            )
        if self.move_rt:
            self._emit_scope_drops("  ")
        self.out.append("  vjoin_all();")
        self.out.append(
            "  if (verr_pending()) { fprintf(stderr, \"unhandled: \"); "
            "vprint_err(verr_take()); fprintf(stderr, \"\\n\"); return 1; }"
        )
        if "fn:main" in self.codes:
            self.out.append("  if (__r.tag == V_NIL) return 0;")
            self.out.append("  if (__r.tag == V_TAG) {")
            self.out.append("    if (__r.as.tv.tag && strcmp(__r.as.tv.tag, \"err\") == 0) {")
            self.out.append("      fprintf(stderr, \"unhandled: ('err', \");")
            self.out.append("      { Val __p = vnil();")
            self.out.append("        if (__r.as.tv.args && __r.as.tv.args->len > 0) __p = __r.as.tv.args->items[0];")
            self.out.append("        if (__p.tag == V_STR) fprintf(stderr, \"'%s'\", __p.as.str);")
            self.out.append("        else { Val __s = vstr_of(__p); fprintf(stderr, \"%s\", __s.as.str); } }")
            self.out.append("      fprintf(stderr, \")\\n\");")
            self.out.append("      return 1;")
            self.out.append("    }")
            self.out.append("    fprintf(stderr, \"TypeError: main must return i64, got %s\\n\", __r.as.tv.tag);")
            self.out.append("    return 1;")
            self.out.append("  }")
            self.out.append("  if (__r.tag == V_INT) return (int)__r.as.ival;")
            self.out.append("  fprintf(stderr, \"TypeError: main must return i64, got %s\\n\", __brat_tagname(__r));")
            self.out.append("  return 1;")
        self.out.append("  return 0;")
        self.out.append("}")

    def emit(self, entry):
        self.mod_bases = [_o.path.dirname(_o.path.abspath(entry))]
        self.collect_file(entry)
        return self.emit_program()

    def emit_src(self, src, base="."):
        self.mod_bases = [_o.path.abspath(base)]
        self.collect_src(src, self.mod_bases[0])
        return self.emit_program()

    def emit_program(self):
        """Two passes.

        A `fn` nested in a body, a closure inside a closure, and a spawn inside
        a nested fn are all discovered *while* emitting the surrounding body,
        which is after the point where C needs their prototypes. So pass one
        emits the whole program into a throwaway buffer purely to learn the
        complete set of functions, and pass two emits the real text with every
        prototype declared up front. `codes` survives between passes, so both
        passes number the functions identically.
        """
        self.out = []
        self.run_pipeline()
        self.out = []
        self.nested_seq = 0
        self.clos_done = 0
        self.clos_declared = 0
        self.late_done = 0
        self.late_declared = 0
        self.tmp = 0
        # Ownership is re-derived in pass two; a stale `dropped` would suppress its drops.
        self.moved = set()
        self.captured = set()
        self.shared = set()
        self.dropped = set()
        self.emit_prelude_headers()
        self.run_pipeline()
        self.reclaim, self.relet = _reclaim_names(
            [s for k, s in self.top_exe if k == "stmt"], set())
        self.emit_main_open()
        for kind, payload in self.top_exe:
            if kind == "import":
                self.splice_file(payload)
            else:
                self.st(payload, "  ")
        self.emit_main_close()
        self.emit_closures_queue()
        self.emit_prelude_tables()
        return "\n".join(self.out) + "\n"

    def emit_forward_decls(self):
        self.out.append("static Val vcall_any(Val fn, Vec *a);")
        self.out.append("static Val vcall_method(Val obj, const char *meth, Vec *a);")
        self.out.append("static Val vmap_with(Val obj, Val fn, const char *meth);")
        for key in sorted(self.codes, key=lambda k: self.codes[k]):
            self.out.append("static Val %s(Map *cap, Vec *args);" % self.cfn(key))
        for name in sorted(self.fast):
            params = self.fast[name][0]
            n = len(params)
            ct = "double" if self.fastkinds[name] == "f64" else "long long"
            if self.fastkinds[name] == "i64v":
                vv = self.fastvec.get(name, frozenset())
                ptypes = ["Vec *" if (p[0] if isinstance(p, tuple) else p) in vv else "long long" for p in params]
                self.out.append("static %s %s(%s);" % (ct, self.fastc[name], ", ".join(ptypes) if n else "void"))
            else:
                self.out.append("static %s %s(%s);" % (ct, self.fastc[name], ", ".join([ct] * n) if n else "void"))
        self.out.append("")

    def emit_closure_protos(self):
        """Declare every closure discovered so far.

        Bodies are emitted one at a time and each body can discover *more*
        closures (a `fn` nested inside a closure, a closure inside a closure),
        so the prototype list has to grow with the queue instead of being
        written once from a count that is not final yet.
        """
        i = getattr(self, "clos_declared", 0)
        while i < len(self.clos):
            self.out.append("static Val %s(Map *cap, Vec *args);" % self.cfn("clo:%d" % i))
            i += 1
        self.clos_declared = i

    def emit_closures_queue(self):
        i = getattr(self, "clos_done", 0)
        while i < len(self.clos):
            self.emit_closure_protos()
            params, body, capnames = self.clos[i]
            self.define_fn(self.cfn("clo:%d" % i), params, body, cap=capnames)
            i += 1
            self.clos_done = i
            self.emit_closure_protos()

    def emit_prelude_headers(self):
        self.out.append('#include "crt.h"')
        self.out.append("")


def emit(src, base="."):
    return Emitter().emit_src(src, base)


def emit_file(path, bundle=False):
    em = Emitter()
    em.mod_bases = [_o.path.dirname(_o.path.abspath(path))]
    src = em.emit(path)
    if bundle:
        crt_path = _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), "crt.h")
        with open(crt_path, encoding="utf-8") as f:
            crt_text = f.read()
        src = src.replace('#include "crt.h"', crt_text, 1)
    return src