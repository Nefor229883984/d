import contextlib
import copy
import json
import os
import re
import socket
import sys
import threading
import time

try:
    from .lexer import Cur, lex
    from .parser import parse_prog, _LINES
except ImportError:
    from parser import parse_prog, _LINES

class Done(Exception):
    def __init__(self, v):
        self.v = v


class Ret(Exception):
    def __init__(self, v):
        self.v = v


class LoopBreak(Exception):
    pass


class LoopCont(Exception):
    pass
_MISS = object()
_MOVED = object()  # sentinel: reading a moved binding errors under BRAT_MOVE=1
_MOVE = os.environ.get('BRAT_MOVE', '1') not in ('0', 'false', 'False', '')

_INTERP_RE = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}')

_I64 = 1 << 63

# Language recursion budget: BRAT frames, not Python frames (DESIGN-fast-fail A1).
# 2000: measured refusal 0.4 s interpreter / instant C; a higher default is
# unreachable in the interpreter and overflows the C stack (~29k frames).
RECURSION_BUDGET = int(os.environ.get('BRAT_RECURSION_BUDGET', '2000'))

_DEPTH = threading.local()


@contextlib.contextmanager
def _frame():
    """Enter one BRAT frame; refuse past the budget so the error is a value."""
    d = getattr(_DEPTH, 'd', 0) + 1
    if d > RECURSION_BUDGET:
        raise RuntimeError('recursion too deep')
    _DEPTH.d = d
    try:
        yield
    finally:
        _DEPTH.d = d - 1


def wrap64(n):
    if -_I64 <= n < _I64:
        return n
    n &= (1 << 64) - 1
    return n - (1 << 64) if n >= _I64 else n


def val_type(v):
    if v is None:
        return 'none'
    if isinstance(v, bool):
        return 'bool'
    if isinstance(v, int):
        return 'i64'
    if isinstance(v, float):
        return 'f64'
    if isinstance(v, str):
        return 'str'
    if isinstance(v, list):
        return 'vec'
    if isinstance(v, dict):
        return 'struct ' + v['$type'] if v.get('$type') is not None else 'map'
    if isinstance(v, tuple):
        return str(v[0]) if v else 'tuple'
    return type(v).__name__


def _bstr(v):
    """Render a value like crt.h vprint_f does for `print`.

    Escapes only backslash, the chosen quote and \n \r \t, byte-wise: Python's
    repr escapes control characters and non-printables C cannot reproduce."""
    if isinstance(v, str):
        return v
    return _brepr(v)


def _brepr(v):
    if isinstance(v, bool):
        return 'True' if v else 'False'
    if v is None:
        return 'None'
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        quote = '"' if ("'" in v and '"' not in v) else "'"
        out = [quote]
        for ch in v:
            if ch == quote or ch == '\\':
                out.append('\\' + ch)
            elif ch == '\n':
                out.append('\\n')
            elif ch == '\r':
                out.append('\\r')
            elif ch == '\t':
                out.append('\\t')
            else:
                out.append(ch)
        out.append(quote)
        return ''.join(out)
    if isinstance(v, list):
        return '[' + ', '.join(_brepr(x) for x in v) + ']'
    if isinstance(v, dict):
        parts = []
        if v.get('$type') is not None:
            parts.append("'$type': '%s'" % v['$type'])
        for k, x in v.items():
            if k != '$type':
                parts.append('%s: %s' % (_brepr(k), _brepr(x)))
        return '{' + ', '.join(parts) + '}'
    if isinstance(v, tuple):
        parts = ["'%s'" % v[0]] + [_brepr(x) for x in v[1:]]
        return '(' + ', '.join(parts) + (',' if len(parts) == 1 else '') + ')'
    return str(v)


def find_path(env, path):
    parts = path.split('.')
    v = find(env, parts[0])
    for f in parts[1:]:
        if v is _MISS or not isinstance(v, dict) or f not in v:
            return _MISS
        v = v[f]
    return v


def grow(s, env):
    def sub(m):
        v = find_path(env, m.group(1))
        return m.group(0) if v is _MISS else _bstr(v)
    return _INTERP_RE.sub(sub, s)


def name_path(e):
    """Dotted name of a var/field chain, or None when `e` computes a value."""
    if isinstance(e, tuple) and e:
        if e[0] == 'var':
            return e[1]
        if e[0] == 'field':
            b = name_path(e[1])
            return b + '.' + e[2] if b else None
    return None


def find(env, n):
    for s in reversed(env):
        if n in s:
            v = s[n]
            if _MOVE and v is _MOVED:
                raise NameError('use of moved value ' + n)
            return v
    return _MISS

def _invalidate_move(env, node):
    if not _MOVE:
        return
    if isinstance(node, tuple) and node and node[0] == 'var':
        name = node[1]
        for s in reversed(env):
            if name in s:
                s[name] = _MOVED
                return

def put(env, n, v):
    for s in reversed(env):
        if n in s:
            s[n] = v
            return
    env[-1][n] = v

def vdrop(v):
    return None

def vmove(src):
    return src


_RC = {}
_MANAGED = bool(os.environ.get('BRAT_MANAGED', '1'))


def vrc_inc(v):
    if _MANAGED and isinstance(v, (str, list, dict)):
        e = _RC.get(id(v))
        if e is None:
            _RC[id(v)] = [1, v]
        else:
            e[0] += 1
    return v


def vrc_dec(v):
    if _MANAGED and isinstance(v, (str, list, dict)):
        e = _RC.get(id(v))
        if e is not None:
            e[0] -= 1
            if e[0] <= 0:
                _RC.pop(id(v), None)
    return None


def _drop_all(frame):
    if _MANAGED:
        for val in list(frame.values()):
            vrc_dec(val)


def _bind(frame, name, val):
    if _MANAGED:
        old = frame.get(name)
        if old is not None:
            vrc_dec(old)
        vrc_inc(val)
    frame[name] = val


MAX_STR = 1 << 30
_TYPE_KEY = '$type'


def check_map_key(k):
    if k == _TYPE_KEY:
        return 'map key "$type" is reserved'
    if isinstance(k, (list, dict)):
        return 'unhashable map key'
    return None


def ev(e, env, funcs):
    k = e[0]
    if k == 'num' or k == 'bool':
        return e[1]
    if k == 'none':
        return None
    if k == 'str':
        return grow(e[1], env)
    if k == 'rstr':
        return e[1]
    if k == 'vec':
        return [ev(x, env, funcs) for x in e[1]]
    if k == 'maplit':
        out = {}
        for ke, ve in e[1]:
            kv = ev(ke, env, funcs)
            bad = check_map_key(kv)
            if bad:
                return ('err', bad)
            out[kv] = ev(ve, env, funcs)
        return out
    if k == 'struct':
        return {'$type': e[1], **{f: ev(v, env, funcs) for f, v in e[2]}}
    if k == 'field':
        base = ev(e[1], env, funcs)
        if not isinstance(base, dict) or e[2] not in base:
            raise RuntimeError('no field ' + e[2])
        return base[e[2]]
    if k == 'idx':
        base = ev(e[1], env, funcs)
        ix = ev(e[2], env, funcs)
        if isinstance(base, dict):
            if '$type' in base:
                raise RuntimeError('no field ' + str(ix))
            if ix not in base:
                raise RuntimeError('no key ' + str(ix))
            return base[ix]
        if not isinstance(ix, (int, float)) or int(ix) != ix:
            raise RuntimeError('index must be int')
        ix = int(ix)
        if ix < 0 or ix >= len(base):
            raise RuntimeError('index out of range')
        return base[ix]
    if k == 'var':
        v = find(env, e[1])
        if v is _MISS:
            raise NameError('no var ' + e[1])
        return v
    if k == 'un':
        v = ev(e[2], env, funcs)
        if e[1] == '!':
            return not v
        if isinstance(v, int):
            return wrap64(-v)
        return -v
    if k == 'move':
        v = ev(e[1], env, funcs)
        _invalidate_move(env, e[1])
        return v
    if k == 'closure':
        _, name, params, body = e
        cap = dict(env[-1])
        node = ('closure', name, params, body, cap)
        if name:
            cap[name] = node
        return node
    if k == 'bin':
        op = e[1]
        a = ev(e[2], env, funcs)
        if op == '&&':
            return ev(e[3], env, funcs) if a else a
        if op == '||':
            return a if a else ev(e[3], env, funcs)
        b = ev(e[3], env, funcs)
        if op == '+':
            if isinstance(a, str) or isinstance(b, str):
                return str(a) + str(b)
            if isinstance(a, int) and isinstance(b, int):
                return wrap64(a + b)
            return a + b
        if op == '-':
            if isinstance(a, int) and isinstance(b, int):
                return wrap64(a - b)
            return a - b
        if op == '*':
            if isinstance(a, int) and isinstance(b, int):
                return wrap64(a * b)
            # str * int / int * str — same ceiling as .repeat / crt.h vrepeat_n
            if isinstance(a, str) and isinstance(b, int):
                if b <= 0 or not a:
                    return ''
                if len(a) * b > MAX_STR:
                    raise RuntimeError('out of memory')
                try:
                    return a * b
                except MemoryError:
                    raise RuntimeError('out of memory')
            if isinstance(b, str) and isinstance(a, int):
                if a <= 0 or not b:
                    return ''
                if len(b) * a > MAX_STR:
                    raise RuntimeError('out of memory')
                try:
                    return b * a
                except MemoryError:
                    raise RuntimeError('out of memory')
            return a * b
        if op == '/':
            if b == 0:
                raise RuntimeError('div by zero')
            if isinstance(a, float) or isinstance(b, float):
                return a / b
            if isinstance(a, int) and isinstance(b, int):
                if a == -_I64 and b == -1:
                    return -_I64
                return wrap64(a // b)
            return a // b
        if op == '%':
            if b == 0:
                raise RuntimeError('div by zero')
            if isinstance(a, int) and isinstance(b, int):
                if a == -_I64 and b == -1:
                    return 0
                return wrap64(a % b)
            return a % b
        if op == '==':
            return a == b
        if op == '!=':
            return a != b
        if op == '<':
            return a < b
        if op == '>':
            return a > b
        if op == '<=':
            return a <= b
        if op == '>=':
            return a >= b
        if op == 'in':
            if isinstance(b, dict):
                return a in b and a != '$type'
            if isinstance(b, (list, str)):
                return a in b
            raise TypeError('in of ' + type(b).__name__)
        raise RuntimeError('bad bin op ' + op)
    if k == 'or':
        a = ev(e[1], env, funcs)
        return a if a is not None else ev(e[2], env, funcs)
    if k == 'quest':
        v = ev(e[1], env, funcs)
        if isinstance(v, tuple) and len(v) == 2 and v[0] in ('ok', 'some'):
            return v[1]
        if isinstance(v, tuple) and len(v) == 2 and v[0] == 'err':
            raise Done(v)
        return v
    if k == 'mcall':
        path = name_path(e[1])
        if path is not None and find(env, path.split('.')[0]) is _MISS:
            # Static path: `log.info`, `Type.method`, `Type.Variant`. The callee
            # is looked up by name, so nothing is evaluated as a receiver.
            return call(path + '.' + e[2], e[3], env, funcs)
        obj = ev(e[1], env, funcs)
        if e[2] in ('map', 'filter', 'any', 'all', 'find', 'sort_by') and len(e[3]) == 1:
            return apply_hof(obj, e[2], e[3], env, funcs)
        if e[2] == 'fold' and len(e[3]) == 2:
            return apply_hof(obj, e[2], e[3], env, funcs)
        vals = [ev(a, env, funcs) for a in e[3]]
        fname = method_of(obj, e[2], funcs)
        if fname is not None:
            return call_method_fn(fname, obj, vals, env, funcs)
        return do_method(obj, e[2], vals)
    if k == 'call':
        if e[2] == [] and e[1][:1].isupper() and '.' not in e[1]:
            v = find(env, e[1])
            if v is not _MISS and e[1] not in funcs:
                return v
        return call(e[1], e[2], env, funcs)
    raise RuntimeError('bad node ' + k)

# One line per write under one lock: print() writes the text and the newline as
# two calls, so concurrent spawns could tear a line apart ("childchild").
_OUT_LOCK = threading.Lock()


def _out_line(line):
    with _OUT_LOCK:
        sys.stdout.write(line + '\n')


def emit_hit(level, msg):
    _out_line(json.dumps({'ts': time.time(), 'lvl': level, 'msg': msg}, ensure_ascii=False))


_RE_STEPS = threading.local()
_RE_MAX_STEPS = 2000000


def _re_parse(pat):
    pos = [0]
    n = len(pat)

    def peek():
        return pat[pos[0]] if pos[0] < n else None

    def parse_alt():
        alts = [parse_seq()]
        while peek() == '|':
            pos[0] += 1
            alts.append(parse_seq())
        if len(alts) == 1:
            return alts[0]
        return ('alt', alts)

    def parse_seq():
        items = []
        while True:
            c = peek()
            if c is None or c in ('|', ')'):
                return items
            atom = parse_atom()
            q = peek()
            if q in ('*', '+', '?'):
                pos[0] += 1
                atom = ({'*': 'star', '+': 'plus', '?': 'opt'}[q], atom)
            items.append(atom)

    def parse_atom():
        c = pat[pos[0]]
        if c == '(':
            pos[0] += 1
            inner = parse_alt()
            if peek() != ')':
                raise SyntaxError('regex: unbalanced ( in ' + pat)
            pos[0] += 1
            return ('group', inner)
        if c == '[':
            j = pos[0] + 1
            body = ''
            if j < n and pat[j] == '^':
                body += '^'
                j += 1
            while j < n and pat[j] != ']':
                body += pat[j]
                j += 1
            pos[0] = j + 1
            return ('set', body)
        if c == '.':
            pos[0] += 1
            return ('dot', None)
        if c == '^':
            pos[0] += 1
            return ('bol', None)
        if c == '$':
            pos[0] += 1
            return ('eol', None)
        if c == '\\':
            e = pat[pos[0] + 1] if pos[0] + 1 < n else '\\'
            pos[0] += 2
            if e == 'd':
                return ('set', '0-9')
            if e == 'D':
                return ('set', '^0-9')
            if e == 'w':
                return ('set', 'a-zA-Z0-9_')
            if e == 'W':
                return ('set', '^a-zA-Z0-9_')
            if e == 's':
                return ('set', ' \t\n\r')
            if e == 'S':
                return ('set', '^ \t\n\r')
            if e == 'n':
                return ('lit', '\n')
            if e == 't':
                return ('lit', '\t')
            if e == 'r':
                return ('lit', '\r')
            return ('lit', e)
        pos[0] += 1
        return ('lit', c)

    tree = parse_alt()
    if pos[0] != n:
        raise SyntaxError('regex: unbalanced ) in ' + pat)
    return tree


def _re_one(node, ch):
    k = node[0]
    if k == 'lit':
        return ch == node[1]
    if k == 'dot':
        return True
    if k == 'set':
        body = node[1]
        neg = body.startswith('^')
        if neg:
            body = body[1:]
        found = False
        i = 0
        while i < len(body):
            if i + 2 < len(body) and body[i + 1] == '-':
                if body[i] <= ch <= body[i + 2]:
                    found = True
                    break
                i += 3
            else:
                if body[i] == ch:
                    found = True
                    break
                i += 1
        return (not found) if neg else found
    return False


def _re_node_ends(node, s, si):
    k = node[0]
    if k == 'lit' or k == 'set' or k == 'dot':
        if si < len(s) and _re_one(node, s[si]):
            yield si + 1
        return
    if k == 'bol':
        if si == 0:
            yield si
        return
    if k == 'eol':
        if si == len(s):
            yield si
        return
    if k == 'group':
        inner = node[1]
        if isinstance(inner, tuple) and inner and inner[0] == 'alt':
            for alt in inner[1]:
                for e in _re_items_ends(alt, s, si):
                    yield e
        else:
            for e in _re_items_ends(inner, s, si):
                yield e
        return
    if k == 'alt':
        for alt in node[1]:
            for e in _re_items_ends(alt, s, si):
                yield e
        return
    if k in ('star', 'plus', 'opt'):
        ends = [si]
        i = si
        while i < len(s):
            nxt = None
            for e in _re_node_ends(node[1], s, i):
                nxt = e
                break
            if nxt is None or nxt == i:
                break
            i = nxt
            ends.append(i)
            if len(ends) > 100000:
                break
        if k == 'opt':
            ends = [si + 1, si] if len(ends) > 1 else [si, si]
        else:
            if k == 'plus':
                ends = ends[1:]
            ends = list(reversed(ends))
        for e in ends:
            yield e
        return


def _re_items_ends(items, s, si):
    _RE_STEPS.n = getattr(_RE_STEPS, 'n', 0) + 1
    if _RE_STEPS.n > _RE_MAX_STEPS:
        raise RuntimeError('regex too complex')
    if not items:
        yield si
        return
    for end in _re_node_ends(items[0], s, si):
        for e in _re_items_ends(items[1:], s, end):
            yield e


def re_match(s, pat):
    _RE_STEPS.n = 0
    # C matches raw UTF-8 bytes; latin-1 maps each byte to one code point so the
    # tree-walker indexes and matches by byte, keeping `.`/classes in parity.
    s = s.encode('utf-8').decode('latin-1')
    pat = pat.encode('utf-8').decode('latin-1')
    try:
        tree = _re_parse(pat)
    except SyntaxError:
        raise RuntimeError('bad regex pattern')
    for i in range(len(s) + 1):
        ends = _re_node_ends(tree, s, i) if isinstance(tree, tuple) else _re_items_ends(tree, s, i)
        for _ in ends:
            return True
    return False

def fill_defaults(params, vals, env, funcs, name):
    if len(vals) == len(params):
        return vals
    ndef = 0
    for p in params:
        if p[3] is not None:
            ndef += 1
    if len(vals) < len(params) - ndef or len(vals) > len(params):
        raise TypeError(name + ' wants %d args' % len(params))
    vals = list(vals)
    for i in range(len(vals), len(params)):
        vals.append(ev(params[i][3], env, funcs))
    return vals


def run_closure(node, vals, env, funcs):
    _, cname, params, body, cap = node
    names = [p[0] for p in params]
    vals = fill_defaults(params, vals, env, funcs, cname or 'closure')
    frame = dict(cap)
    if _MANAGED:
        for val in vals:
            vrc_inc(val)
    frame.update(zip(names, vals))
    if cname:
        frame[cname] = node
    inner = [frame]
    with _frame():
        try:
            run_block(body, inner, funcs, frame=True)
        except (Done, Ret) as r:
            if _MANAGED:
                for nm in names:
                    vrc_dec(frame.get(nm))
            return r.v
        if _MANAGED:
            for nm in names:
                vrc_dec(frame.get(nm))
        return 0


def resolve_fn(argnode, env, funcs, meth):
    if (isinstance(argnode, tuple) and argnode and argnode[0] == 'var'
            and argnode[1] not in env[-1] and argnode[1] in funcs
            and isinstance(funcs[argnode[1]], tuple) and len(funcs[argnode[1]]) == 2):
        params, body = funcs[argnode[1]]
        return ('closure', argnode[1], params, body, {})
    fnval = ev(argnode, env, funcs)
    if not (isinstance(fnval, tuple) and fnval and fnval[0] == 'closure'):
        raise TypeError(meth + ' wants fn')
    return fnval


def apply_iter(obj, meth, argnode, env, funcs):
    if not isinstance(obj, list):
        raise TypeError(meth + ' wants vec')
    fnval = resolve_fn(argnode, env, funcs, meth)
    out = []
    for x in obj:
        r = run_closure(fnval, [x], env, funcs)
        if meth == 'map':
            out.append(r)
        elif r:
            out.append(x)
    return out


def apply_hof(obj, meth, argnodes, env, funcs):
    if meth in ('map', 'filter'):
        return apply_iter(obj, meth, argnodes[0], env, funcs)
    if not isinstance(obj, list):
        raise TypeError(meth + ' wants vec')
    if meth in ('any', 'all', 'find'):
        fnval = resolve_fn(argnodes[0], env, funcs, meth)
        for x in obj:
            r = run_closure(fnval, [x], env, funcs)
            if meth == 'any' and r:
                return True
            if meth == 'all' and not r:
                return False
            if meth == 'find' and r:
                return x
        if meth == 'any':
            return False
        if meth == 'all':
            return True
        return None
    if meth == 'fold':
        fnval = resolve_fn(argnodes[1], env, funcs, 'fold')
        acc = ev(argnodes[0], env, funcs)
        for x in obj:
            acc = run_closure(fnval, [acc, x], env, funcs)
        return acc
    if meth == 'sort_by':
        fnval = resolve_fn(argnodes[0], env, funcs, 'sort_by')
        obj.sort(key=lambda x: run_closure(fnval, [x], env, funcs))
        return obj
    raise NameError('no method ' + meth)


_DFR = threading.local()


def _dfr_stack():
    st = getattr(_DFR, 'st', None)
    if st is None:
        st = _DFR.st = []
    return st


class _DfrScope(list):
    """One defer frame.

    `frame` marks the body of a function so its defers run after the BRAT depth
    is released, matching the C backend's `bdepth_pop(); vdfreset(...)` order.
    A nested block keeps its frame, so its defers still cost a depth level.
    """
    __slots__ = ('frame',)

    def __init__(self, frame=False):
        super().__init__()
        self.frame = frame


def run_defers(env, funcs):
    scope = _dfr_stack().pop()
    saved = getattr(_DEPTH, 'd', 0)
    if scope.frame:
        # Release this frame before the deferred call, as __fin does in C,
        # so a defer running exactly at the budget is not itself refused.
        _DEPTH.d = saved - 1
    try:
        for clo in reversed(scope):
            run_closure(clo, [], env, funcs)
    finally:
        _DEPTH.d = saved


_STR_METH = {'split', 'lines', 'trim', 'lower', 'upper', 'starts_with', 'ends_with', 'contains', 'parse_int', 'replace', 'index_of', 'repeat', 'split_once', 'copy', 'chars', 'parse_float', 'slice'}
_FLOAT_RE = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z')
_FLOAT_WORD = re.compile(r'[+-]?(?:inf(?:inity)?|nan)\Z', re.IGNORECASE)
_VEC_METH = {'join', 'push', 'pop', 'first', 'last', 'sort', 'rev', 'ins', 'del', 'slice', 'sum', 'min', 'max', 'enumerate', 'copy', 'zip', 'chunks', 'pos'}
_MAP_METH = {'get', 'has', 'keys', 'del', 'set', 'items', 'vals', 'copy', 'get_or'}


def do_method(obj, meth, vals):
    if isinstance(obj, str) and meth in _STR_METH:
        if meth == 'split':
            return obj.split(vals[0] if vals else None)
        if meth == 'lines':
            return obj.splitlines()
        if meth == 'trim':
            return obj.strip()
        if meth == 'lower':
            return obj.lower()
        if meth == 'upper':
            return obj.upper()
        if meth == 'starts_with':
            return obj.startswith(vals[0])
        if meth == 'ends_with':
            return obj.endswith(vals[0])
        if meth == 'contains':
            return vals[0] in obj
        if meth == 'parse_int':
            t = obj.strip()
            body = t[1:] if t[:1] in ('+', '-') else t
            if body and all('0' <= ch <= '9' for ch in body):
                return ('ok', wrap64(int(t)))
            return ('err', 'not a number')
        if meth == 'replace':
            return obj.replace(vals[0], vals[1])
        if meth == 'index_of':
            r = obj.find(vals[0])
            return None if r < 0 else r
        if meth == 'repeat':
            if len(obj) * int(vals[0]) > MAX_STR:
                raise RuntimeError('out of memory')
            try:
                return obj * vals[0]
            except MemoryError:
                raise RuntimeError('out of memory')
        if meth == 'split_once':
            h, sep, t = obj.partition(vals[0])
            return None if not sep and vals[0] else [h, t]
        if meth == 'copy':
            return obj
        if meth == 'chars':
            return list(obj)
        if meth == 'slice':
            return obj[int(vals[0]):int(vals[1])]
        if meth == 'parse_float':
            t = obj.strip()
            if _FLOAT_RE.match(t) or _FLOAT_WORD.match(t):
                try:
                    return ('ok', float(t))
                except ValueError:
                    pass
            return ('err', 'not a float')
    if isinstance(obj, dict) and meth in _MAP_METH:
        if meth == 'get':
            if len(vals) != 1:
                raise TypeError('get wants 1 arg')
            return obj.get(vals[0])
        if meth == 'get_or':
            return obj.get(vals[0], vals[1])
        if meth == 'has':
            return vals[0] in obj
        if meth == 'keys':
            return [k for k in obj.keys() if k != '$type']
        if meth == 'del':
            if vals[0] in obj:
                del obj[vals[0]]
                return True
            return False
        if meth == 'set':
            bad = check_map_key(vals[0])
            if bad:
                return ('err', bad)
            obj[vals[0]] = vals[1]
            return vals[1]
        if meth == 'items':
            return [[k, v] for k, v in obj.items() if k != '$type']
        if meth == 'vals':
            return [v for k, v in obj.items() if k != '$type']
        if meth == 'copy':
            return dict(obj)
    if isinstance(obj, list) and meth in _VEC_METH:
        if meth == 'join':
            return vals[0].join(str(x) for x in obj)
        if meth == 'push':
            obj.append(vals[0])
            return len(obj)
        if meth == 'pop':
            return obj.pop() if obj else None
        if meth == 'first':
            return obj[0] if obj else None
        if meth == 'last':
            return obj[-1] if obj else None
        if meth == 'sort':
            obj.sort()
            return obj
        if meth == 'rev':
            return list(reversed(obj))
        if meth == 'ins':
            obj.insert(int(vals[0]), vals[1])
            return len(obj)
        if meth == 'del':
            ix = int(vals[0])
            if ix < 0:
                ix += len(obj)
            if ix < 0 or ix >= len(obj):
                raise RuntimeError('index out of range')
            return obj.pop(ix)
        if meth == 'slice':
            return obj[int(vals[0]):int(vals[1])]
        if meth == 'sum':
            if not all(isinstance(x, (int, float, bool)) for x in obj):
                raise RuntimeError('sum of non-numeric')
            t = 0
            for x in obj:
                if isinstance(x, int):
                    t = wrap64(t + x)
                else:
                    t = t + x
            return t
        if meth == 'min':
            return min(obj) if obj else None
        if meth == 'max':
            return max(obj) if obj else None
        if meth == 'enumerate':
            return [[i, x] for i, x in enumerate(obj)]
        if meth == 'copy':
            return list(obj)
        if meth == 'zip':
            return [list(p) for p in zip(obj, vals[0])]
        if meth == 'chunks':
            n = int(vals[0])
            if n <= 0:
                raise RuntimeError('chunks wants n > 0')
            return [obj[i:i + n] for i in range(0, len(obj), n)]
        if meth == 'pos':
            try:
                return obj.index(vals[0])
            except ValueError:
                return None
    raise NameError('no method ' + meth + ' for ' + type(obj).__name__)


def call_value(name, vals, env, funcs):
    info = funcs.get(name)
    if isinstance(info, tuple) and info and info[0] == 'extern':
        # delegate to call with no eval by wrapping values as literals? use extern path directly
        # Reuse call's extern branch via a tiny AST is not possible; handle minimal:
        import ctypes
        libname = info[3]
        lib = ctypes.CDLL(libname) if libname else ctypes.CDLL(None)
        fn = getattr(lib, name)
        params, ret = info[1], info[2]
        varargs = info[4] if len(info) > 4 else None

        def to_c(ty, v):
            if isinstance(ty, str) and ty.startswith('fn'):
                return _callback_ptr(ty, v, funcs)
            if ty in _STRUCTS:
                cls = _struct_class(ty)
                inst = cls()
                for f, ft in _STRUCTS[ty]:
                    fv = v.get(f) if isinstance(v, dict) else v
                    setattr(inst, f, to_c(ft, fv))
                return inst
            if ty == 'i64':
                return ctypes.c_longlong(v)
            if ty == 'i32':
                return ctypes.c_int(v)
            if ty == 'f64':
                return ctypes.c_double(v)
            if ty == 'str':
                return ctypes.c_char_p(v.encode('utf-8') if isinstance(v, str) else v)
            if ty == 'ptr':
                return ctypes.c_void_p(v)
            return v

        cargs = []
        for p, v in zip(params, vals):
            ann = p[2] if isinstance(p, tuple) and len(p) > 2 else None
            cargs.append(to_c(ann, v))
        for v in vals[len(params):]:
            cargs.append(to_c(varargs, v))
        fn.argtypes = None
        if ret in ('i64', 'i32'):
            fn.restype = ctypes.c_longlong
        elif ret == 'str':
            fn.restype = ctypes.c_char_p
        elif ret == 'ptr':
            fn.restype = ctypes.c_void_p
        elif ret == 'f64':
            fn.restype = ctypes.c_double
        elif ret in _STRUCTS:
            fn.restype = _struct_class(ret)
        else:
            fn.restype = None
        r = fn(*cargs)
        if ret in _STRUCTS:
            out = {'$type': ret}
            for f, ft in _STRUCTS[ret]:
                val = getattr(r, f)
                if ft == 'str':
                    out[f] = val.decode('utf-8') if isinstance(val, bytes) else val
                elif ft == 'ptr':
                    out[f] = val.value if hasattr(val, 'value') else (val or 0)
                else:
                    out[f] = val
            return out
        if ret in ('i64', 'i32'):
            return r
        if ret == 'str':
            return r.decode('utf-8') if isinstance(r, bytes) else r
        if ret == 'ptr':
            if hasattr(r, 'value'):
                return r.value or 0
            return r if r is not None else 0
        if ret == 'f64':
            return r
        return None
    if name in funcs:
        params, body = funcs[name]
        vals = fill_defaults(params, vals, env, funcs, name)
        names = [p[0] for p in params]
        frame = {}
        for nm, val in zip(names, vals):
            if _MANAGED:
                vrc_inc(val)
            frame[nm] = val
        with _frame():
            env.append(frame)
            try:
                run_block(body, env, funcs, frame=True)
            except (Done, Ret) as r:
                _drop_all(frame)
                env.pop()
                return r.v
            except Exception:
                _drop_all(frame)
                env.pop()
                raise
            _drop_all(frame)
            env.pop()
            return None
    raise NameError(name)


def call(name, args, env, funcs):
    vals = [ev(a, env, funcs) for a in args]
    info = funcs.get(name)
    if isinstance(info, tuple) and info and info[0] == 'extern':
        import ctypes
        libname = info[3]
        lib = ctypes.CDLL(libname) if libname else ctypes.CDLL(None)
        fn = getattr(lib, name)
        params, ret = info[1], info[2]
        varargs = info[4] if len(info) > 4 else None
        # build ctypes argtypes
        def to_c(ty, v):
            if isinstance(ty, str) and ty.startswith('fn'):
                return _callback_ptr(ty, v, funcs)
            if ty in _STRUCTS:
                cls = _struct_class(ty)
                inst = cls()
                for f, ft in _STRUCTS[ty]:
                    fv = v.get(f) if isinstance(v, dict) else v
                    setattr(inst, f, to_c(ft, fv))
                return inst
            if ty == 'i64':
                return ctypes.c_longlong(v)
            if ty == 'i32':
                return ctypes.c_int(v)
            if ty == 'f64':
                return ctypes.c_double(v)
            if ty == 'str':
                return ctypes.c_char_p(v.encode('utf-8') if isinstance(v, str) else v)
            if ty == 'ptr':
                return ctypes.c_void_p(v)
            return v
        cargs = []
        for p, v in zip(params, vals):
            ann = p[2] if isinstance(p, tuple) and len(p) > 2 else None
            cargs.append(to_c(ann, v))
        for v in vals[len(params):]:
            cargs.append(to_c(varargs, v))
        fn.argtypes = None
        if varargs is None:
            atypes = []
            for p in params:
                ann = p[2] if isinstance(p, tuple) and len(p) > 2 else None
                atypes.append(_struct_ctype(ann) if ann in _STRUCTS or ann in ('i64','i32','f64','str','ptr') else None)
            # map basic
            fixed_types = []
            for p in params:
                ann = p[2] if isinstance(p, tuple) and len(p) > 2 else None
                if ann == 'i64':
                    fixed_types.append(ctypes.c_longlong)
                elif ann == 'i32':
                    fixed_types.append(ctypes.c_int)
                elif ann == 'f64':
                    fixed_types.append(ctypes.c_double)
                elif ann == 'str':
                    fixed_types.append(ctypes.c_char_p)
                elif ann == 'ptr':
                    fixed_types.append(ctypes.c_void_p)
                elif ann in _STRUCTS:
                    fixed_types.append(_struct_class(ann))
                else:
                    fixed_types.append(None)
            if all(t is not None for t in fixed_types):
                fn.argtypes = fixed_types
        if ret in ('i64', 'i32'):
            fn.restype = ctypes.c_longlong
        elif ret == 'str':
            fn.restype = ctypes.c_char_p
        elif ret == 'ptr':
            fn.restype = ctypes.c_void_p
        elif ret == 'f64':
            fn.restype = ctypes.c_double
        elif ret in _STRUCTS:
            fn.restype = _struct_class(ret)
        else:
            fn.restype = None
        r = fn(*cargs)
        if ret in _STRUCTS:
            out = {'$type': ret}
            for f, ft in _STRUCTS[ret]:
                val = getattr(r, f)
                if ft == 'str':
                    out[f] = val.decode('utf-8') if isinstance(val, bytes) else val
                elif ft == 'ptr':
                    out[f] = val.value if hasattr(val, 'value') else (val or 0)
                else:
                    out[f] = val
            return out
        if ret in ('i64', 'i32'):
            return r
        if ret == 'str':
            return r.decode('utf-8') if isinstance(r, bytes) else r
        if ret == 'ptr':
            if hasattr(r, 'value'):
                return r.value or 0
            return r if r is not None else 0
        if ret == 'f64':
            return r
        return None
    if name == 'print':
        _out_line(' '.join(_bstr(v) for v in vals))
        return 0
    if name in ('log.info', 'log.warn', 'log.error'):
        emit_hit(name.split('.')[1].upper(), ' '.join(str(v) for v in vals))
        return 0
    if name == 'trace.span':
        if vals and isinstance(vals[-1], tuple) and vals[-1] and vals[-1][0] == 'closure':
            clo = vals[-1]
            vals = vals[:-1]
            print(json.dumps({'ts': time.time(), 'span': vals}, ensure_ascii=False))
            return run_closure(clo, [], env, funcs)
        print(json.dumps({'ts': time.time(), 'span': vals}, ensure_ascii=False))
        return 0
    if name in ('Ok', 'Err', 'Some'):
        if len(vals) != 1:
            raise TypeError(name + ' wants 1 arg')
        return (name.lower(), vals[0])
    if name == 'unwrap':
        if len(vals) != 1:
            raise TypeError('unwrap wants 1 arg')
        v = vals[0]
        if isinstance(v, tuple) and len(v) == 2 and v[0] in ('ok', 'some'):
            return v[1]
        if isinstance(v, tuple) and len(v) == 2 and v[0] == 'err':
            raise Done(v)
        raise RuntimeError('unwrap of ' + val_type(v) + ' (want Ok/Err/Some)')
    if name == 'is_err':
        v = vals[0] if len(vals) == 1 else None
        return isinstance(v, tuple) and len(v) == 2 and v[0] == 'err'
    if name == 'is_none':
        v = vals[0] if len(vals) == 1 else None
        return v is None
    if name == 'is_vec':
        v = vals[0] if len(vals) == 1 else None
        return isinstance(v, list)
    if name == 'is_str':
        v = vals[0] if len(vals) == 1 else None
        return isinstance(v, str)
    if name == 'is_map':
        v = vals[0] if len(vals) == 1 else None
        return isinstance(v, dict)
    if name == 'len':
        if len(vals) != 1:
            raise TypeError('len wants 1 arg')
        if isinstance(vals[0], (list, str)):
            return len(vals[0])
        if isinstance(vals[0], dict):
            return len(vals[0]) - (1 if '$type' in vals[0] else 0)
        raise TypeError('len of ' + type(vals[0]).__name__)
    if name == '__at':
        if len(vals) != 2:
            raise TypeError('__at wants 2 args')
        if not isinstance(vals[1], (int, float)):
            raise TypeError('__at index must be num')
        return vals[0][int(vals[1])]
    if name == 'tagof':
        v = vals[0] if vals else None
        if v is None:
            return 'none'
        if isinstance(v, tuple) and v and isinstance(v[0], str):
            return v[0]
        return 'plain'
    if name == 'assert':
        if len(vals) not in (1, 2):
            raise TypeError('assert wants 1-2 args')
        if not vals[0]:
            raise RuntimeError(vals[1] if len(vals) == 2 else 'assert failed')
        return 0
    if name == 'str':
        if len(vals) != 1:
            raise TypeError('str wants 1 arg')
        v = vals[0]
        if isinstance(v, str):
            return v
        return _brepr(v)
    if name == 'read_file':
        if len(vals) != 1:
            raise TypeError('read_file wants 1 arg')
        if not isinstance(vals[0], str):
            return ('err', 'read_file wants a string path')
        try:
            path = confined_path(vals[0])
            if path is None:
                return ('err', 'path outside the program directory')
            with open(path, encoding='utf-8') as f:
                return ('ok', f.read())
        except UnicodeDecodeError:
            return ('err', 'invalid utf-8')
        except OSError as e:
            return ('err', str(e))
    if name == 'sbuf':
        if len(vals) != 0:
            raise TypeError('sbuf wants 0 args')
        return ['sb', '']
    if name == 'sbuf_add':
        if len(vals) != 2:
            raise TypeError('sbuf_add wants 2 args')
        h, part = vals
        if not (isinstance(h, list) and h and h[0] == 'sb'):
            raise TypeError('sbuf_add wants a builder')
        h[1] = h[1] + part
        return len(h[1])
    if name == 'sbuf_len':
        if len(vals) != 1:
            raise TypeError('sbuf_len wants 1 arg')
        h, = vals
        if not (isinstance(h, list) and h and h[0] == 'sb'):
            raise TypeError('sbuf_len wants a builder')
        return len(h[1])
    if name == 'sbuf_str':
        if len(vals) != 1:
            raise TypeError('sbuf_str wants 1 arg')
        h, = vals
        if not (isinstance(h, list) and h and h[0] == 'sb'):
            raise TypeError('sbuf_str wants a builder')
        return ('ok', h[1])
    if name == 'tcp_listen':
        if len(vals) != 1:
            raise TypeError('tcp_listen wants 1 arg')
        addr = _parse_addr(vals[0])
        if addr is None:
            return ('err', 'address wants host:port with port 1..65535')
        try:
            s_ = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            return ('err', 'socket failed')
        s_.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s_.bind(addr)
        except OSError:
            s_.close()
            return ('err', 'bind failed')
        try:
            s_.listen(16)
        except OSError:
            s_.close()
            return ('err', 'listen failed')
        return ('ok', _fd_for(s_))
    if name == 'tcp_accept':
        if len(vals) != 1:
            raise TypeError('tcp_accept wants 1 arg')
        s_ = _sock_get(vals[0])
        if s_ is None:
            return ('err', 'bad socket slot')
        try:
            conn, _ = s_.accept()
        except OSError:
            return ('err', 'accept failed')
        conn.settimeout(_SOCK_TIMEOUT)
        return ('ok', _fd_for(conn))
    if name == 'tcp_connect':
        if len(vals) != 1:
            raise TypeError('tcp_connect wants 1 arg')
        addr = _parse_addr(vals[0])
        if addr is None:
            return ('err', 'address wants host:port with port 1..65535')
        try:
            s_ = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            return ('err', 'socket failed')
        s_.settimeout(_SOCK_TIMEOUT)
        try:
            s_.connect(addr)
        except OSError:
            s_.close()
            return ('err', 'connect failed')
        return ('ok', _fd_for(s_))
    if name == 'tcp_recv':
        if len(vals) != 2:
            raise TypeError('tcp_recv wants 2 args')
        n = vals[1]
        if not isinstance(n, int) or not 0 <= n <= _RECV_MAX:
            return ('err', 'recv wants 0 <= n <= 1048576')
        s_ = _sock_get(vals[0])
        if s_ is None:
            return ('err', 'bad socket slot')
        try:
            b = s_.recv(n)
        except OSError:
            return ('err', 'recv failed')
        try:
            return ('ok', b.decode('utf-8'))
        except UnicodeDecodeError:
            return ('err', 'invalid utf-8')
    if name == 'tcp_send':
        if len(vals) != 2:
            raise TypeError('tcp_send wants 2 args')
        data = vals[1]
        if not isinstance(data, str):
            return ('err', 'tcp_send wants string data')
        s_ = _sock_get(vals[0])
        if s_ is None:
            return ('err', 'bad socket slot')
        payload = data.encode('utf-8')
        try:
            s_.sendall(payload)
        except OSError:
            return ('err', 'send failed')
        return ('ok', len(payload))
    if name == 'tcp_close':
        if len(vals) != 1:
            raise TypeError('tcp_close wants 1 arg')
        s_ = _sock_take(vals[0])
        if s_ is None:
            return ('err', 'bad socket slot')
        s_.close()
        return ('ok', 0)
    if name == 'write_file':
        if len(vals) != 2:
            raise TypeError('write_file wants 2 args')
        if not isinstance(vals[0], str) or not isinstance(vals[1], str):
            return ('err', 'write_file wants string path and text')
        try:
            path = confined_path(vals[0])
            if path is None:
                return ('err', 'path outside the program directory')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(vals[1])
            return ('ok', len(vals[1]))
        except OSError as e:
            return ('err', str(e))
    if name in funcs and isinstance(funcs[name], tuple) and funcs[name] and funcs[name][0] == 'ctor':
        _, tag, arity = funcs[name]
        if len(vals) != arity:
            raise TypeError(name + ' wants %d args' % arity)
        if tag == 'none':
            return None
        return (tag,) + tuple(vals)
    if '.' in name:
        parts = name.split('.')
        obj = find(env, parts[0])
        if obj is _MISS:
            if len(parts) == 2 and parts[0][:1].isupper():
                f0 = funcs.get(parts[0])
                if (isinstance(f0, tuple) and f0 and f0[0] == 'ctor' and f0[2] == 0):
                    tag = f0[1]
                    obj = None if tag == 'none' else (tag,)
                else:
                    fname = parts[0] + '.' + parts[1]
                    fn = funcs.get(fname)
                    if isinstance(fn, tuple) and len(fn) == 2:
                        params, body = fn
                        names = [p[0] for p in params]
                        if len(vals) != len(names):
                            raise TypeError(fname + ' wants %d args' % len(names))
                        if names and names[0] == 'self':
                            raise TypeError(fname + ' needs instance, use value.method()')
                        frame = {}
                        for nm, val in zip(names, vals):
                            if _MANAGED:
                                vrc_inc(val)
                            frame[nm] = val
                        with _frame():
                            env.append(frame)
                            try:
                                run_block(body, env, funcs, frame=True)
                            except (Done, Ret) as r:
                                _drop_all(frame)
                                env.pop()
                                return r.v
                            _drop_all(frame)
                            env.pop()
                            return 0
                    raise NameError('no var ' + parts[0])
            else:
                raise NameError('no var ' + parts[0])
        for f in parts[1:-1]:
            if not isinstance(obj, dict) or f not in obj:
                raise RuntimeError('no field ' + f)
            obj = obj[f]
        meth = parts[-1]
        if meth in ('map', 'filter') and len(args) == 1:
            return apply_iter(obj, meth, args[0], env, funcs)
        fname = method_of(obj, meth, funcs)
        if fname is not None:
            return call_method_fn(fname, obj, vals, env, funcs)
        return do_method(obj, meth, vals)
    v = find(env, name)
    if isinstance(v, tuple) and v and v[0] == 'closure':
        return run_closure(v, vals, env, funcs)
    if name in funcs:
        params, body = funcs[name]
        vals = fill_defaults(params, vals, env, funcs, name)
        names = [p[0] for p in params]
        frame = {}
        for nm, val in zip(names, vals):
            if _MANAGED:
                vrc_inc(val)
            frame[nm] = val
        with _frame():
            env.append(frame)
            try:
                run_block(body, env, funcs, frame=True)
            except (Done, Ret) as r:
                _drop_all(frame)
                env.pop()
                return r.v
            _drop_all(frame)
            env.pop()
            return 0
    if name == 'join_all':
        if vals:
            raise TypeError('join_all wants 0 args')
        return join_all()
    if name == 'args':
        return list(_ARGS[0])
    if name == 'exit':
        code = vals[0] if vals else 0
        if not isinstance(code, (int, float)):
            raise TypeError('exit wants int')
        sys.exit(int(code))
    if name == 'eprint':
        print(' '.join(_bstr(v) for v in vals), file=sys.stderr)
        return 0
    if name == 'raise':
        if len(vals) != 1:
            raise TypeError('raise wants 1 arg')
        raise Done(('err', vals[0]))
    if name == 'own':
        return copy.deepcopy(vals[0])
    if name in ('__arena_enter', '__arena_leave'):
        # The interpreter has no arena memory; the blocks only need to run.
        return 0
    if name == 'time_ms':
        return int(time.time() * 1000)
    if name == 'sleep_ms':
        if len(vals) != 1 or not isinstance(vals[0], (int, float)):
            raise TypeError('sleep_ms wants 1 int arg')
        time.sleep(max(0, vals[0]) / 1000.0)
        return 0
    if name == 'ptr_null':
        return 0
    if name == 'ptr_is_null':
        if len(vals) != 1:
            raise TypeError('ptr_is_null wants 1 ptr arg')
        return vals[0] == 0
    if name == 'ptr_offset':
        if len(vals) != 2:
            raise TypeError('ptr_offset wants 2 args')
        return vals[0] + vals[1]
    if name == 'ptr_read_i64':
        if len(vals) != 1:
            raise TypeError('ptr_read_i64 wants 1 ptr arg')
        import ctypes
        if vals[0] == 0:
            raise RuntimeError('null pointer dereference')
        return ctypes.cast(vals[0], ctypes.POINTER(ctypes.c_longlong)).contents.value
    if name == 'ptr_write_i64':
        if len(vals) != 2:
            raise TypeError('ptr_write_i64 wants 2 args')
        import ctypes
        if vals[0] == 0:
            raise RuntimeError('null pointer dereference')
        ctypes.cast(vals[0], ctypes.POINTER(ctypes.c_longlong)).contents.value = vals[1]
        return 0
    if name == 'ptr_read_f64':
        if len(vals) != 1:
            raise TypeError('ptr_read_f64 wants 1 ptr arg')
        import ctypes
        if vals[0] == 0:
            raise RuntimeError('null pointer dereference')
        return ctypes.cast(vals[0], ctypes.POINTER(ctypes.c_double)).contents.value
    if name == 'ptr_write_f64':
        if len(vals) != 2:
            raise TypeError('ptr_write_f64 wants 2 args')
        import ctypes
        if vals[0] == 0:
            raise RuntimeError('null pointer dereference')
        ctypes.cast(vals[0], ctypes.POINTER(ctypes.c_double)).contents.value = vals[1]
        return 0
    if name == 'ptr_read_ptr':
        if len(vals) != 1:
            raise TypeError('ptr_read_ptr wants 1 ptr arg')
        import ctypes
        if vals[0] == 0:
            raise RuntimeError('null pointer dereference')
        return ctypes.cast(vals[0], ctypes.POINTER(ctypes.c_void_p)).contents.value or 0
    if name == 'ptr_read_str':
        if len(vals) != 1:
            raise TypeError('ptr_read_str wants 1 ptr arg')
        import ctypes
        if vals[0] == 0:
            raise RuntimeError('null pointer dereference')
        return ctypes.cast(vals[0], ctypes.c_char_p).value.decode('utf-8')
    if name == 'ptr_write_ptr':
        if len(vals) != 2:
            raise TypeError('ptr_write_ptr wants 2 args')
        import ctypes
        if vals[0] == 0:
            raise RuntimeError('null pointer dereference')
        ctypes.cast(vals[0], ctypes.POINTER(ctypes.c_void_p)).contents.value = vals[1]
        return 0
    if name == 'env_get':
        if len(vals) != 1:
            raise TypeError('env_get wants 1 arg')
        key = str(vals[0])
        upper = key.upper()
        for bad in ("SECRET", "KEY", "TOKEN", "PASS", "PASSWORD", "CREDENTIAL", "PRIVATE"):
            if bad in upper:
                return ('err', 'env_get refused: %r' % key)
        return os.environ.get(key)
    if name == 're_match':
        if len(vals) != 2:
            raise TypeError('re_match wants 2 args')
        if not isinstance(vals[0], str) or not isinstance(vals[1], str):
            raise RuntimeError('re_match wants str')
        return re_match(vals[0], vals[1])
    raise NameError('no fn ' + name)

def run_block(body, env, funcs, frame=False):
    st = _dfr_stack()
    st.append(_DfrScope(frame))
    try:
        for s in body:
            run_stmt(s, env, funcs)
    finally:
        run_defers(env, funcs)

_MOD_BASE = ['.']

_FILES_FREE = bool(os.environ.get('BRAT_FILES'))


def confined_path(p):
    # Non-str resolves to None: a raw int would otherwise reach open() as an fd.
    if not isinstance(p, str):
        return None
    if _FILES_FREE:
        return p
    base = os.path.realpath(_MOD_BASE[0])
    if not base or base == os.sep:
        return None  # refuse to run with an empty or filesystem-root base
    full = os.path.realpath(os.path.join(base, p))
    if full == base or full.startswith(base + os.sep):
        return full
    return None

_MOD_LOADED = [set()]
_ARGS = [[]]

_SOCKETS = {}
_NEXT_FD = [1]
_SOCK_LOCK = threading.Lock()
_SOCK_TIMEOUT = 30.0      # connected sockets: a silent peer frees its thread
_RECV_MAX = 1 << 20       # one tcp_recv returns at most 1 MiB; mirrors crt.h


def _parse_addr(text):
    """'host:port' -> (host, port), or None. Mirrors crt.h _vhost_port."""
    if not isinstance(text, str):
        return None
    host, sep, port = text.rpartition(':')
    if not sep or not host or len(host.encode('utf-8')) > 255:
        return None
    if not (port.isascii() and port.isdigit()) or not 1 <= int(port) <= 65535:
        return None
    try:
        packed = socket.inet_aton(host)
    except OSError:
        return None
    if packed == b'\xff\xff\xff\xff':
        return None
    return socket.inet_ntoa(packed), int(port)


def _fd_for(sock):
    with _SOCK_LOCK:
        fd = _NEXT_FD[0]
        _NEXT_FD[0] += 1
        _SOCKETS[fd] = sock
    return fd


def _sock_get(fd):
    if not isinstance(fd, int):
        return None
    with _SOCK_LOCK:
        return _SOCKETS.get(fd)


def _sock_take(fd):
    if not isinstance(fd, int):
        return None
    with _SOCK_LOCK:
        return _SOCKETS.pop(fd, None)

_POOL = {'cond': threading.Condition(), 'q': [], 'active': 0, 'parked': 0, 'done': []}
_SPAWN_WORKERS = None
_CURRENT = threading.local()


def _spawn_worker():
    while True:
        with _POOL['cond']:
            while not _POOL['q']:
                _POOL['cond'].wait()
            task = _POOL['q'].pop(0)
        _run_task(task)


class _Spawn:
    __slots__ = ('err',)

    def __init__(self):
        self.err = None


def _run_task(task):
    block, snap, funcs, handle = task
    outer = getattr(_CURRENT, 'h', None)
    _CURRENT.h = handle
    try:
        run_block(block, snap, funcs)
    except Exception as ex:
        handle.err = ex
    finally:
        _CURRENT.h = outer
        with _POOL['cond']:
            _POOL['active'] -= 1
            _POOL['done'].append(handle)
            _POOL['cond'].notify_all()


def _ensure_workers():
    global _SPAWN_WORKERS
    if _SPAWN_WORKERS is None:
        n = min(2 * (os.cpu_count() or 1), 32)
        _SPAWN_WORKERS = []
        for _ in range(n):
            t = threading.Thread(target=_spawn_worker, daemon=True)
            t.start()
            _SPAWN_WORKERS.append(t)


def _spawn(block, snap, funcs):
    handle = _Spawn()
    _ensure_workers()
    with _POOL['cond']:
        _POOL['q'].append((block, snap, funcs, handle))
        _POOL['active'] += 1
        _POOL['cond'].notify()
    return handle


def join_all():
    # Wait until every task but the parked join_all callers is done, running
    # queued work here: a bounded pool that only blocked would starve a nested
    # join_all's own children. Mirrors crt.h vjoin_all.
    in_task = getattr(_CURRENT, 'h', None) is not None
    with _POOL['cond']:
        if in_task:
            _POOL['parked'] += 1
    try:
        while True:
            with _POOL['cond']:
                target = _POOL['parked'] if in_task else 0
                if not _POOL['q'] and _POOL['active'] <= target:
                    break
                task = _POOL['q'].pop(0) if _POOL['q'] else None
                if task is None:
                    _POOL['cond'].wait()
            if task is not None:
                _run_task(task)
        with _POOL['cond']:
            done = _POOL['done']
            _POOL['done'] = []
    finally:
        with _POOL['cond']:
            if in_task:
                _POOL['parked'] -= 1
    err = None
    for h in done:
        if err is None and h.err is not None:
            err = h.err
    if err is not None:
        raise err
    return 0


_TRAITS = {}
_VARIANTS = {}
_STRUCTS = {}
_STRUCT_CACHE = {}
_CALLBACK_REFS = []


def _struct_ctype(ty):
    import ctypes
    if ty == 'i64':
        return ctypes.c_longlong
    if ty == 'i32':
        return ctypes.c_int
    if ty == 'f64':
        return ctypes.c_double
    if ty == 'str':
        return ctypes.c_char_p
    if ty == 'ptr':
        return ctypes.c_void_p
    if ty in _STRUCTS:
        return _struct_class(ty)
    return ctypes.c_longlong


def _struct_class(name):
    if name in _STRUCT_CACHE:
        return _STRUCT_CACHE[name]
    import ctypes
    fields = _STRUCTS[name]
    cls = type(name, (ctypes.Structure,), {
        '_fields_': [(f, _struct_ctype(t)) for f, t in fields],
    })
    _STRUCT_CACHE[name] = cls
    return cls


def _parse_cb_sig(sig):
    s = sig.strip()
    i = s.index('(')
    j = s.index(')')
    params_str = s[i + 1:j].strip()
    rest = s[j + 1:].strip()
    if rest.startswith('->'):
        ret = rest[2:].strip()
    else:
        ret = 'none'
    if params_str == '':
        params = []
    else:
        params = [p.strip() for p in params_str.split(',')]
    return params, ret


def _cb_ctype(ty):
    import ctypes
    if ty == 'i64':
        return ctypes.c_longlong
    if ty == 'i32':
        return ctypes.c_int
    if ty == 'f64':
        return ctypes.c_double
    if ty == 'str':
        return ctypes.c_char_p
    if ty == 'ptr':
        return ctypes.c_void_p
    if ty in _STRUCTS:
        return _struct_class(ty)
    return ctypes.c_longlong


def _callback_ptr(sig, fname, funcs):
    import ctypes
    ptypes, ret = _parse_cb_sig(sig)
    argtypes = [_cb_ctype(t) for t in ptypes]
    if ret in ('i64', 'i32'):
        restype = ctypes.c_longlong
    elif ret == 'f64':
        restype = ctypes.c_double
    elif ret == 'str':
        restype = ctypes.c_char_p
    elif ret == 'ptr':
        restype = ctypes.c_void_p
    else:
        restype = None

    def _c_to_brat(ty, cv):
        if ty == 'str':
            return cv.decode('utf-8') if isinstance(cv, bytes) else cv
        if ty == 'ptr':
            return cv.value if hasattr(cv, 'value') else (cv or 0)
        return cv

    def wrapper(*cvals):
        brat_args = []
        for t, cv in zip(ptypes, cvals):
            v = _c_to_brat(t, cv)
            if t == 'str':
                brat_args.append(('str', v))
            elif t == 'f64':
                brat_args.append(('num', v))
            else:
                brat_args.append(('num', v))
        r = call(fname, brat_args, [{}], funcs)
        if ret in ('i64', 'i32', 'ptr'):
            return int(r) if not isinstance(r, bool) else int(r)
        if ret == 'f64':
            return float(r)
        if ret == 'str':
            return r.encode('utf-8') if isinstance(r, str) else r
        return None

    if argtypes:
        CFUNCTYPE = ctypes.CFUNCTYPE(restype, *argtypes)
    else:
        CFUNCTYPE = ctypes.CFUNCTYPE(restype)
    ptr = CFUNCTYPE(wrapper)
    _CALLBACK_REFS.append(ptr)
    return ptr


def hoist(s, funcs):
    if s[0] == 'pub':
        s = s[1]
    if s[0] in ('fn', 'exportfn'):
        if s[1] not in funcs:
            funcs[s[1]] = (s[2], s[3])
    elif s[0] == 'extern':
        if s[2] not in funcs:
            funcs[s[2]] = ('extern', s[3], s[4], s[1], s[5] if len(s) > 5 else None)
    elif s[0] == 'externstruct':
        _STRUCTS[s[1]] = s[2]
    elif s[0] == 'enum':
        for vname, fields in s[2]:
            tag = 'none' if vname == 'None' and not fields else vname.lower()
            if vname not in funcs:
                funcs[vname] = ('ctor', tag, len(fields))
            _VARIANTS.setdefault(tag, s[1])
    elif s[0] == 'trait':
        if s[1] not in _TRAITS:
            _TRAITS[s[1]] = [g[1] for g in s[2]]
    elif s[0] == 'impl':
        _, trait, tname, fns = s
        if trait is not None:
            if trait not in _TRAITS:
                raise RuntimeError('unknown trait ' + trait)
            missing = [m for m in _TRAITS[trait] if m not in [f[1] for f in fns]]
            if missing:
                raise RuntimeError('impl %s for %s missing %s' % (trait, tname, ','.join(missing)))
        seen = set()
        for f in fns:
            if f[1] in seen:
                raise RuntimeError('duplicate method %s.%s' % (tname, f[1]))
            seen.add(f[1])
            if tname + '.' + f[1] not in funcs:
                funcs[tname + '.' + f[1]] = (f[2], f[3])


def method_of(obj, meth, funcs):
    if isinstance(obj, dict) and '$type' in obj:
        fname = obj['$type'] + '.' + meth
        if fname in funcs:
            return fname
    if isinstance(obj, tuple) and obj and isinstance(obj[0], str):
        tname = _VARIANTS.get(obj[0], obj[0].capitalize())
        fname = tname + '.' + meth
        if fname in funcs:
            return fname
    return None


def call_method_fn(fname, this, vals, env, funcs):
    params, body = funcs[fname]
    names = [p[0] for p in params]
    vals = fill_defaults(params, [this] + list(vals), env, funcs, fname)[1:]
    if len(vals) + 1 != len(names):
        raise TypeError(fname + ' wants %d args' % (len(names) - 1))
    frame = {}
    for nm, val in zip(names, [this] + vals):
        if _MANAGED:
            vrc_inc(val)
        frame[nm] = val
    with _frame():
        env.append(frame)
        try:
            run_block(body, env, funcs, frame=True)
        except (Done, Ret) as r:
            _drop_all(frame)
            env.pop()
            return r.v
        _drop_all(frame)
        env.pop()
        return 0


def load_mod(name, env, funcs):
    base = _MOD_BASE[0]
    if name in _MOD_LOADED[0]:
        return
    path = os.path.join(base, *name.split('.')) + '.brat'
    if not os.path.isfile(path):
        alt = os.path.join(base, 'vendor', *name.split('.')) + '.brat'
        if os.path.isfile(alt):
            path = alt
    path = confined_path(path)
    if path is None:
        raise RuntimeError('no module ' + name)
    try:
        src = open(path, encoding='utf-8').read()
    except OSError:
        raise RuntimeError('no module ' + name)
    _MOD_LOADED[0].add(name)
    try:
        prog = parse_prog(Cur(lex(src)))
    except SyntaxError as e:
        raise SyntaxError('%s (module %s)' % (e, name))
    for s in prog[1]:
        hoist(s, funcs)
    for s in prog[1]:
        if s[0] not in ('fn', 'impl', 'trait', 'enum', 'pub', 'extern', 'externstruct'):
            run_stmt(s, env, funcs)


def run_stmt(s, env, funcs):
    try:
        _run_stmt(s, env, funcs)
    except (Done, LoopBreak, LoopCont):
        raise
    except Exception as ex:
        ln = _LINES.get(id(s))
        if ln is not None and getattr(ex, '_brat_ln', None) is None:
            ex._brat_ln = ln
        raise


def _run_stmt(s, env, funcs):
    k = s[0]
    if k == 'unsafe':
        run_block(s[1], env, funcs)
    elif k == 'let':
        _bind(env[-1], s[1], ev(s[2], env, funcs))
    elif k == 'set':
        name = s[1]
        v = ev(s[2], env, funcs)
        if _MANAGED:
            tgt = None
            for f in reversed(env):
                if name in f:
                    tgt = f
                    break
            if tgt is not None:
                vrc_dec(tgt[name])
            vrc_inc(v)
        put(env, name, v)
    elif k == 'setidx':
        base = ev(s[1], env, funcs)
        ix = ev(s[2], env, funcs)
        v = ev(s[3], env, funcs)
        if isinstance(base, dict):
            if '$type' in base:
                raise RuntimeError('no field ' + str(ix))
            bad = check_map_key(ix)
            if bad:
                return ('err', bad)
            base[ix] = v
        elif isinstance(base, list):
            if not isinstance(ix, (int, float)) or int(ix) != ix:
                raise RuntimeError('index must be int')
            ix = int(ix)
            if ix < 0 or ix >= len(base):
                raise RuntimeError('index out of range')
            base[ix] = v
        else:
            raise RuntimeError('setidx of non-indexable')
    elif k == 'setfield':
        base = ev(s[1], env, funcs)
        v = ev(s[3], env, funcs)
        if not isinstance(base, dict):
            raise RuntimeError('no field ' + s[2])
        base[s[2]] = v
    elif k in ('fn', 'exportfn'):
        funcs[s[1]] = (s[2], s[3])
    elif k == 'if':
        _, cond, then, els = s
        if ev(cond, env, funcs):
            run_block(then, env, funcs)
        elif els:
            run_block(els if isinstance(els, list) else [els], env, funcs)
    elif k == 'while':
        while ev(s[1], env, funcs):
            try:
                run_block(s[2], env, funcs)
            except LoopCont:
                pass
            except LoopBreak:
                break
    elif k == 'foriter':
        _, vn, ve, body = s
        seq = ev(ve, env, funcs)
        if isinstance(seq, dict):
            seq = [k for k in seq if k != '$type']
        frame = env[-1]
        had = vn in frame
        old = frame.get(vn)
        try:
            for x in seq:
                _bind(frame, vn, x)
                try:
                    run_block(body, env, funcs)
                except LoopCont:
                    continue
                except LoopBreak:
                    break
        finally:
            if had:
                frame[vn] = old
            else:
                frame.pop(vn, None)
    elif k == 'forrange':
        _, vn, se, ee, body = s
        a, b = ev(se, env, funcs), ev(ee, env, funcs)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            raise RuntimeError('range bounds must be num')
        frame = env[-1]
        had = vn in frame
        old = frame.get(vn)
        i = int(a)
        try:
            while i < int(b):
                _bind(frame, vn, i)
                try:
                    run_block(body, env, funcs)
                except LoopCont:
                    pass
                except LoopBreak:
                    break
                i += 1
        finally:
            if had:
                frame[vn] = old
            else:
                frame.pop(vn, None)
    elif k == 'return':
        raise Ret(ev(s[1], env, funcs))
    elif k == 'try':
        try:
            run_block(s[1], env, funcs)
        except Done as r:
            v = r.v
            payload = v[1] if isinstance(v, tuple) and len(v) == 2 else v
            if s[3]:
                _bind(env[-1], s[3], payload)
            run_block(s[2], env, funcs)
        except (Ret, LoopBreak, LoopCont):
            raise
        except Exception as ex:
            if s[3]:
                _bind(env[-1], s[3], str(ex))
            run_block(s[2], env, funcs)
    elif k == 'spawn':
        snap = [dict(f) for f in env]
        _spawn(s[1], snap, funcs)
    elif k == 'defer':
        st = _dfr_stack()
        # defer is a snapshot closure (SPEC 9); outside any block the C backend skips it too.
        if st:
            st[-1].append(('closure', None, [], [('expr', s[1])], dict(env[-1])))
    elif k == 'break':
        raise LoopBreak()
    elif k == 'continue':
        raise LoopCont()
    elif k == 'import':
        load_mod(s[1], env, funcs)
    elif k == 'externstruct':
        pass
    elif k == 'impl':
        hoist(s, funcs)
    elif k == 'enum':
        hoist(s, funcs)
    elif k == 'pub':
        run_stmt(s[1], env, funcs)
    elif k == 'expr':
        ev(s[1], env, funcs)
    else:
        raise RuntimeError('bad stmt ' + k)

def run(src, base='.', argv=None):
    _MOD_BASE[0] = base
    _MOD_LOADED[0] = set()
    _ARGS[0] = list(argv) if argv else []
    _TRAITS.clear()
    _VARIANTS.clear()
    prog = parse_prog(Cur(lex(src)))
    env = [{}]
    funcs = {}
    for s in prog[1]:
        hoist(s, funcs)
    for s in prog[1]:
        if s[0] not in ('fn', 'impl', 'trait', 'enum', 'pub', 'extern', 'externstruct'):
            run_stmt(s, env, funcs)
    out = call('main', [], env, funcs) if 'main' in funcs else 0
    join_all()
    return out