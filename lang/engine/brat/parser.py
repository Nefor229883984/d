_seq = 0
_LINES = {}
_KEEP = []
_ENUMS = {}
_STRAY_BRACES = []
_CURLINE = [1]
_ENUMS_REV = {}

_ESC = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', '"': '"', "'": "'", '0': '\0'}


def unesc(s):
    out = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s) and s[i + 1] in _ESC:
            out.append(_ESC[s[i + 1]])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return ''.join(out)

def fresh(tag):
    global _seq
    _seq += 1
    return '@%s%d' % (tag, _seq)


def _mark(n, ln):
    if isinstance(n, tuple) and n:
        if id(n) not in _LINES:
            _LINES[id(n)] = ln
            _KEEP.append(n)
        for sub in n[1:]:
            if isinstance(sub, (tuple, list)):
                _mark(sub, ln)
    elif isinstance(n, list):
        for x in n:
            _mark(x, ln)

try:
    from .lexer import Cur, eline, lex
except ImportError:
    from lexer import Cur, eline, lex


def parse_prog(c):
    _ENUMS.clear()
    _ENUMS_REV.clear()
    body = []
    while c.peek().k != 'EOF':
        r = parse_stmt(c)
        if isinstance(r, list):
            body.extend(r)
        else:
            body.append(r)
    return ('prog', body)

def parse_stmt(c):
    ln = c.peek().line
    _CURLINE[0] = ln
    r = _parse_stmt(c)
    _mark(r, ln)
    return r


def _parse_stmt(c):
    t = c.peek()
    if t.k == 'ID' and t.v == 'arena' and c.i + 1 < len(c.t) and c.t[c.i + 1].v == '{':
        return parse_arena(c)
    if t.k == 'KW' and t.v == 'let':
        return parse_let(c)
    if t.k == 'KW' and t.v == 'const':
        c.step()
        return parse_let(c, True)
    if t.k == 'KW' and t.v == 'extern':
        c.step()
        if c.peek().k == 'STR':
            c.step()
        c.want('KW', 'fn') if False else None
        if not (c.peek().k == 'KW' and c.peek().v == 'fn'):
            raise SyntaxError('extern wants fn at line %d' % eline(c))
        return parse_extern(c)
    if t.k == 'KW' and t.v == 'fn':
        return parse_fn(c)
    if t.k == 'KW' and t.v == 'if':
        return parse_if(c)
    if t.k == 'KW' and t.v == 'while':
        return parse_while(c)
    if t.k == 'KW' and t.v == 'for':
        return parse_for(c)
    if t.k == 'KW' and t.v == 'impl':
        return parse_impl(c)
    if t.k == 'KW' and t.v == 'trait':
        return parse_trait(c)
    if t.k == 'KW' and t.v == 'enum':
        return parse_enum(c)
    if t.k == 'KW' and t.v == 'pub':
        c.step()
        u = c.peek()
        if u.k == 'KW' and u.v == 'fn':
            return ('pub', parse_fn(c))
        if u.k == 'KW' and u.v == 'enum':
            return ('pub', parse_enum(c))
        if u.k == 'KW' and u.v == 'trait':
            return ('pub', parse_trait(c))
        if u.k == 'KW' and u.v == 'impl':
            return ('pub', parse_impl(c))
        raise SyntaxError('pub wants fn/enum/trait/impl at line %d' % eline(c))
    if t.k == 'KW' and t.v in ('break', 'continue'):
        c.step()
        if c.peek().v == ';':
            c.step()
        return (t.v,)
    if t.k == 'KW' and t.v == 'match':
        return parse_match(c)
    if t.k == 'KW' and t.v == 'return':
        c.step()
        if c.peek().v in ('}', ';'):
            if c.peek().v == ';':
                c.step()
            return ('return', ('none',))
        e = parse_expr(c)
        if c.peek().v == ';':
            c.step()
        return ('return', e)
    if t.k == 'KW' and t.v == 'try':
        return parse_try(c)
    if t.k == 'KW' and t.v == 'spawn':
        c.step()
        c.want('{')
        b = parse_block(c)
        c.want('}')
        return ('spawn', b)
    if t.k == 'KW' and t.v == 'defer':
        c.step()
        e = parse_expr(c)
        if c.peek().v == ';':
            c.step()
        return ('defer', e)
    if t.k == 'KW' and t.v == 'import':
        c.step()
        parts = [c.step().v]
        while c.peek().v == '.':
            c.step()
            parts.append(c.step().v)
        if c.peek().v == ';':
            c.step()
        return ('import', '.'.join(parts))
    if t.k == 'ID' and c.i + 1 < len(c.t) and c.t[c.i + 1].v == '=':
        name = c.step().v
        c.step()
        e = parse_expr(c)
        if c.peek().v == ';':
            c.step()
        return ('set', name, e)
    if t.k == 'ID' and c.i + 1 < len(c.t) and c.t[c.i + 1].v in ('+=', '-=', '*=', '/=', '%='):
        name = c.step().v
        op = c.step().v[0]
        e = parse_expr(c)
        if c.peek().v == ';':
            c.step()
        return ('set', name, ('bin', op, ('var', name), e))
    e = parse_expr(c)
    if c.peek().v == '=' or c.peek().v in ('+=', '-=', '*=', '/=', '%='):
        op = c.step().v
        val = parse_expr(c)
        if c.peek().v == ';':
            c.step()
        if e[0] == 'idx':
            if op != '=':
                val = ('bin', op[0], ('idx', e[1], e[2]), val)
            return ('setidx', e[1], e[2], val)
        if e[0] == 'field':
            if op != '=':
                val = ('bin', op[0], e, val)
            return ('setfield', e[1], e[2], val)
        raise SyntaxError('bad assign target at line %d' % eline(c))
    if c.peek().v == ';':
        c.step()
    return ('expr', e)

def parse_block(c):
    stmts = []
    while c.peek().v != '}':
        r = parse_stmt(c)
        if isinstance(r, list):
            stmts.extend(r)
        else:
            stmts.append(r)
    return stmts

def parse_let(c, const=False):
    if not const:
        c.want('let')
    mut = False
    if c.peek().v == 'mut':
        c.step()
        mut = True
    if const and mut:
        raise SyntaxError('const cannot be mut at line %d' % eline(c))
    if c.peek().v == '[':
        if const:
            raise SyntaxError('const destructure unsupported at line %d' % eline(c))
        c.step()
        names = []
        while c.peek().v != ']':
            names.append(c.step().v)
            if c.peek().v == ',':
                c.step()
        c.want(']')
        c.want('=')
        e = parse_expr(c)
        if c.peek().v == ';':
            c.step()
        tm = fresh('d')
        out = [('let', tm, e, False)]
        for i, name in enumerate(names):
            if name == '_':
                continue
            out.append(('let', name, ('idx', ('var', tm), ('num', i)), mut))
        return out
    name = c.step().v
    ann = None
    if c.peek().v == ':':
        c.step()
        toks = []
        depth = 0
        while True:
            x = c.peek()
            if x.v == '=' and depth == 0:
                break
            if x.v == '[':
                depth += 1
            if x.v == ']':
                depth -= 1
            toks.append(x.v)
            c.step()
        ann = ann_text(toks)
    c.want('=')
    e = parse_expr(c)
    if c.peek().v == ';':
        c.step()
    if not const:
        return ('let', name, e, mut, False, ann)
    return ('let', name, e, False, True, ann)

def ann_text(toks):
    if toks and toks[0] in ('move', 'mut', 'ref'):
        toks = toks[1:]
    return ''.join(toks) if toks else None


def parse_params(c):
    params = []
    while c.peek().v != ')':
        pn = c.step().v
        mode = 'ref'
        if pn in ('move', 'mut', 'ref') and c.peek().v != ':':
            mode = pn
            pn = c.step().v
        ann = None
        if c.peek().v == ':':
            c.step()
            depth = 0
            first = None
            toks = []
            while True:
                x = c.peek()
                if x.v in (',', ')', '=') and depth == 0:
                    break
                if x.v == '[':
                    depth += 1
                if x.v == ']':
                    depth -= 1
                if first is None and depth == 0 and x.k in ('ID', 'KW'):
                    first = x.v
                toks.append(x.v)
                c.step()
            if first in ('move', 'mut', 'ref'):
                mode = first
            ann = ann_text(toks)
        default = None
        if c.peek().v == '=':
            c.step()
            default = parse_expr(c)
        params.append((pn, mode, ann, default))
        if c.peek().v == ',':
            c.step()
    seen_default = False
    for p in params:
        if p[3] is not None:
            seen_default = True
        elif seen_default:
            raise SyntaxError('param without default after default at line %d' % eline(c))
    return params


def parse_fn(c):
    c.want('fn')
    name = c.step().v
    tparams = []
    if c.peek().v == '<':
        c.step()
        while c.peek().v != '>':
            t = c.step()
            if t.k not in ('ID', 'KW'):
                raise SyntaxError('want type param have %r at line %d' % (t.v, eline(c)))
            tparams.append(t.v)
            if c.peek().v == ',':
                c.step()
        c.want('>')
    if len(set(tparams)) != len(tparams):
        raise SyntaxError('duplicate type param at line %d' % eline(c))
    c.want('(')
    params = parse_params(c)
    c.want(')')
    ret = None
    if c.peek().v == '->':
        c.step()
        if c.peek().v in ('move', 'ref'):
            if c.step().v == 'ref':
                raise SyntaxError('ref returns dangle; return owned at line %d' % eline(c))
        toks = []
        while c.peek().v != '{':
            toks.append(c.step().v)
        ret = ann_text(toks)
    c.want('{')
    b = parse_block(c)
    c.want('}')
    return ('fn', name, params, b, tparams, ret)


def parse_extern(c):
    # assumes 'extern' already consumed; optional lib string consumed by caller
    c.want('KW', 'fn') if False else None
    if not (c.peek().k == 'KW' and c.peek().v == 'fn'):
        raise SyntaxError('extern wants fn at line %d' % eline(c))
    c.step()  # fn
    name = c.step().v
    tparams = []
    c.want('(')
    params = parse_params(c)
    c.want(')')
    ret = None
    if c.peek().v == '->':
        c.step()
        toks = []
        while (c.peek().k != 'EOF' and not (c.peek().k == 'KW' and c.peek().v in ('fn', 'extern', 'let', 'if'))
               and c.peek().v not in ('{', ';')):
            toks.append(c.step().v)
        if toks:
            ret = ann_text(toks)
    if c.peek().v == ';':
        c.step()
    return ('extern', name, params, ret)

def parse_arena(c):
    """`arena { body }` becomes an if-block that enters the arena, defers the
    leave, and runs the body. Reusing the if and defer nodes gives every backend
    the same behavior, including on error unwinding. The escape rules that keep
    arena values inside the block live in typer.py."""
    c.step()
    c.want('{')
    body = parse_block(c)
    c.want('}')
    enter = ('expr', ('call', '__arena_enter', []))
    leave = ('defer', ('call', '__arena_leave', []))
    return ('if', ('bool', True), [enter, leave] + body, [])


def parse_if(c):
    c.want('if')
    if c.peek().k == 'KW' and c.peek().v == 'let':
        c.step()
        pat = parse_pattern(c)
        c.want('=')
        scrut = parse_expr(c)
        c.want('{')
        then = parse_block(c)
        c.want('}')
        els = None
        if c.peek().v == 'else':
            c.step()
            if c.peek().v == 'if':
                eb = parse_if(c)
                els = eb if isinstance(eb, list) else [eb]
            else:
                c.want('{')
                els = parse_block(c)
                c.want('}')
        tm = fresh('m')
        if pat is None:
            node = ('if', ('bool', True), then, els if els is not None else [])
        else:
            node = build_if(('var', tm), pat, then, els if els is not None else [])
        return [('let', tm, scrut, False), node]
    cond = parse_expr(c)
    c.want('{')
    then = parse_block(c)
    c.want('}')
    els = None
    if c.peek().v == 'else':
        c.step()
        if c.peek().v == 'if':
            eb = parse_if(c)
            els = eb if isinstance(eb, list) else [eb]
        else:
            c.want('{')
            els = parse_block(c)
            c.want('}')
    return ('if', cond, then, els)

def parse_while(c):
    c.want('while')
    if c.peek().k == 'KW' and c.peek().v == 'let':
        c.step()
        pat = parse_pattern(c)
        c.want('=')
        scrut = parse_expr(c)
        c.want('{')
        body = parse_block(c)
        c.want('}')
        tm = fresh('m')
        if pat is None:
            inner = ('if', ('bool', True), body, [('break',)])
        else:
            inner = build_if(('var', tm), pat, body, [('break',)])
        return [('while', ('bool', True), [('let', tm, scrut, False), inner])]
    cond = parse_expr(c)
    c.want('{')
    b = parse_block(c)
    c.want('}')
    return ('while', cond, b)

def parse_try(c):
    c.want('try')
    c.want('{')
    b = parse_block(c)
    c.want('}')
    c.want('catch')
    ename = None
    if c.peek().v != '{':
        ename = c.step().v
    c.want('{')
    h = parse_block(c)
    c.want('}')
    return ('try', b, h, ename)

def parse_pat_arg(c):
    t = c.peek()
    if t.k == 'ID' and t.v == '_':
        c.step()
        return ('wild',)
    if t.k == 'ID' and t.v == 'None':
        c.step()
        return ('none',)
    if t.k == 'KW' and t.v in ('true', 'false'):
        c.step()
        return ('lit', ('bool', t.v == 'true'))
    if t.k == 'ID' and c.i + 1 < len(c.t) and c.t[c.i + 1].v == '(':
        c.step()
        vname = t.v
        c.want('(')
        subs = []
        while c.peek().v != ')':
            subs.append(parse_pat_arg(c))
            if c.peek().v == ',':
                c.step()
        c.want(')')
        return ('variant', vname.lower(), subs)
    if t.k == 'NUM':
        c.step()
        return ('lit', ('num', _num_val(t.v)))
    if t.k == 'STR':
        c.step()
        return ('lit', _expand_str(t.v[1:-1], _parse_sub))
    if t.k == 'ID':
        c.step()
        return ('bind', t.v)
    raise SyntaxError('bad pattern arg %r at line %d' % (t.v, eline(c)))


def build_if(tm, pat, body, rest, guard=None):
    if pat is None or pat[0] == 'wild':
        if guard is not None:
            return ('if', guard, body, rest)
        return body
    if guard is not None:
        body = [('if', guard, body, rest)]
    if pat[0] == 'lit':
        return ('if', ('bin', '==', tm, pat[1]), body, rest)
    if pat[0] == 'none':
        return ('if', ('call', 'is_none', [tm]), body, rest)
    if pat[0] == 'variant':
        _, tag, subs = pat
        cond = ('bin', '==', ('call', 'tagof', [tm]), ('str', tag))
        pre = []
        inner = body
        for j, sp in reversed(list(enumerate(subs))):
            acc = ('call', '__at', [tm, ('num', j + 1)])
            if sp[0] == 'bind':
                pre.append(('let', sp[1], acc, False))
            elif sp[0] == 'wild':
                pass
            else:
                t = fresh('p')
                pre.append(('let', t, acc, False))
                inner = [build_if(('var', t), sp, inner, rest)]
        return ('if', cond, pre + inner, rest)
    if pat[0] == 'bind':
        return [('let', pat[1], tm, False)] + body
    raise SyntaxError('bad match pattern')

def space_total(pat):
    if pat is None or pat[0] in ('wild', 'bind'):
        return True
    if pat[0] == 'variant':
        o = _ENUMS_REV.get(pat[1])
        if o and len(_ENUMS[o]) == 1:
            return all(space_total(sp) for sp in pat[2])
        return False
    return False


def parse_match(c):
    ml = c.peek().line
    c.want('match')
    scrut = parse_expr(c)
    c.want('{')
    arms = []
    seen = []
    while c.peek().v != '}':
        pat = parse_pattern(c)
        guard = None
        if c.peek().k == 'KW' and c.peek().v == 'if':
            c.step()
            guard = parse_expr(c)
        c.want('=>')
        if pat is not None:
            key = repr(pat) + repr(guard)
            if key in seen:
                raise SyntaxError('duplicate match pattern %r at line %d' % (pat, eline(c)))
            seen.append(key)
        if c.peek().v == '{':
            c.step()
            body = parse_block(c)
            c.want('}')
        else:
            body = [parse_stmt(c)]
        arms.append((pat, guard, body))
        if c.peek().v == ',':
            c.step()
    c.want('}')
    if not arms:
        raise SyntaxError('match needs arms at line %d' % eline(c))
    owners = {}
    has_wild = False
    for pat, guard, _ in arms:
        if pat is None or pat[0] in ('wild', 'bind'):
            has_wild = True
            continue
        if pat[0] == 'variant':
            o = _ENUMS_REV.get(pat[1])
            if o and (pat[1] not in owners.setdefault(o, {}) or guard is None):
                owners[o][pat[1]] = (pat[2], guard)
        elif pat[0] == 'none':
            o = _ENUMS_REV.get('none')
            if o:
                owners.setdefault(o, {})['none'] = ([], guard)
    if len(owners) == 1 and not has_wild:
        owner = next(iter(owners))
        total = set()
        for tag, (subs, guard) in owners[owner].items():
            if guard is None and all(space_total(sp) for sp in subs):
                total.add(tag)
        missing = [t for t in _ENUMS[owner] if t not in total]
        if missing:
            raise SyntaxError('non-exhaustive match: missing %s in enum %s at line %d' % (', '.join(missing), owner, ml))
    if not owners and not has_wild:
        bools = set()
        onlybool = True
        for pat, guard, _ in arms:
            if guard is None and pat and pat[0] == 'lit' and pat[1][0] == 'bool':
                bools.add(pat[1][1])
            else:
                onlybool = False
        if onlybool and bools != {True, False}:
            raise SyntaxError('non-exhaustive match: missing %s at line %d' % ('false' if True in bools else 'true', ml))
    tm = fresh('m')
    node = None
    for pat, guard, body in reversed(arms):
        if pat is None:
            if guard is None:
                if node is not None:
                    raise SyntaxError('_ must be last at line %d' % eline(c))
                node = body
                continue
            rest = node if node is not None else []
            node = build_if(('var', tm), pat, body, rest, guard)
            continue
        if pat[0] == 'bind' and guard is None:
            if node is not None:
                raise SyntaxError('_ must be last at line %d' % eline(c))
            node = [('let', pat[1], ('var', tm), False)] + body
            continue
        rest = node if node is not None else []
        node = build_if(('var', tm), pat, body, rest, guard)
    if node == []:
        return [('expr', scrut)]
    out = [('let', tm, scrut, False)]
    if isinstance(node, list):
        out.extend(node)
    else:
        out.append(node)
    return out


def parse_pattern(c):
    t = c.peek()
    if t.k == 'ID' and t.v == '_':
        c.step()
        return None
    p = parse_pat_arg(c)
    if p[0] == 'bind':
        if p[1][:1].isupper():
            return ('variant', p[1].lower(), [])
        return ('bind', p[1])
    return p


def parse_enum(c):
    c.want('enum')
    tname = c.want_id('enum name').v
    c.want('{')
    variants = []
    while c.peek().v != '}':
        v = c.step()
        if v.k not in ('ID', 'KW'):
                raise SyntaxError('want variant have %r at line %d' % (v.v, eline(c)))
        fields = []
        if c.peek().v == '(':
            c.step()
            while c.peek().v != ')':
                f = c.step()
                if f.k not in ('ID', 'KW'):
                    raise SyntaxError('want field have %r at line %d' % (f.v, eline(c)))
                fields.append(f.v)
                if c.peek().v == ',':
                    c.step()
            c.want(')')
        variants.append((v.v, fields))
        if c.peek().v == ',':
            c.step()
    c.want('}')
    tags = []
    for vname, fields in variants:
        tag = 'none' if vname == 'None' and not fields else vname.lower()
        tags.append(tag)
        if tag in _ENUMS_REV and _ENUMS_REV[tag] != tname:
            _ENUMS_REV[tag] = None
        elif tag not in _ENUMS_REV:
            _ENUMS_REV[tag] = tname
    _ENUMS[tname] = tags
    return ('enum', tname, variants)

def parse_for(c):
    c.want('for')
    v = c.step()
    if v.k != 'ID':
        raise SyntaxError('want loop var have %r at line %d' % (v.v, eline(c)))
    c.want('in')
    start = parse_expr(c)
    if c.peek().v == '..':
        c.step()
        end = parse_expr(c)
        c.want('{')
        body = parse_block(c)
        c.want('}')
        return [('forrange', v.v, start, end, body)]
    c.want('{')
    body = parse_block(c)
    c.want('}')
    return [('foriter', v.v, start, body)]

def parse_sig(c):
    c.want('fn')
    name = c.step().v
    c.want('(')
    params = []
    while c.peek().v != ')':
        pn = c.step().v
        mode = 'ref'
        ann = None
        if c.peek().v == ':':
            c.step()
            depth = 0
            first = None
            toks = []
            while True:
                x = c.peek()
                if x.v in (',', ')') and depth == 0:
                    break
                if x.v == '[':
                    depth += 1
                if x.v == ']':
                    depth -= 1
                if first is None and depth == 0 and x.k in ('ID', 'KW'):
                    first = x.v
                toks.append(x.v)
                c.step()
            if first in ('move', 'mut', 'ref'):
                mode = first
            ann = ann_text(toks)
        params.append((pn, mode, ann))
        if c.peek().v == ',':
            c.step()
    c.want(')')
    ret = None
    if c.peek().v == '->':
        c.step()
        if c.peek().v in ('move', 'ref'):
            if c.step().v == 'ref':
                raise SyntaxError('ref returns dangle; return owned at line %d' % eline(c))
        toks = []
        while c.peek().v != '}' and not (c.peek().k == 'KW' and c.peek().v == 'fn'):
            toks.append(c.step().v)
        ret = ann_text(toks)
    return ('sig', name, params, ret)


def parse_trait(c):
    c.want('trait')
    name = c.step().v
    c.want('{')
    sigs = []
    while c.peek().v != '}':
        sigs.append(parse_sig(c))
    c.want('}')
    return ('trait', name, sigs)


def parse_impl(c):
    c.want('impl')
    first = c.want_id('impl name').v
    trait = None
    tname = first
    if c.peek().v == 'for':
        c.step()
        trait = first
        tname = c.want_id('impl type name').v
    c.want('{')
    fns = []
    while c.peek().v != '}':
        fns.append(parse_fn(c))
    c.want('}')
    return ('impl', trait, tname, fns)


def parse_expr(c):
    return parse_or(c)

def parse_or(c):
    e = parse_lor(c)
    while c.peek().v == 'or':
        c.step()
        e = ('or', e, parse_lor(c))
    return e

def parse_lor(c):
    e = parse_land(c)
    while c.peek().v == '||':
        c.step()
        e = ('bin', '||', e, parse_land(c))
    return e

def parse_land(c):
    e = parse_cmp(c)
    while c.peek().v == '&&':
        c.step()
        e = ('bin', '&&', e, parse_cmp(c))
    return e

def parse_cmp(c):
    e = parse_add(c)
    while c.peek().v in ('==', '!=', '<', '>', '<=', '>=', 'in'):
        op = c.step().v
        e = ('bin', op, e, parse_add(c))
    return e

def parse_add(c):
    e = parse_mul(c)
    while c.peek().v in ('+', '-'):
        op = c.step().v
        e = ('bin', op, e, parse_mul(c))
    return e

def parse_mul(c):
    e = parse_un(c)
    while c.peek().v in ('*', '/', '%'):
        op = c.step().v
        e = ('bin', op, e, parse_un(c))
    return e

def parse_un(c):
    if c.peek().v in ('!', '-'):
        op = c.step().v
        return ('un', op, parse_un(c))
    if c.peek().k == 'KW' and c.peek().v == 'move':
        c.step()
        return ('move', parse_un(c))
    return parse_post(c)

def parse_post(c):
    e = parse_prim(c)
    while True:
        if c.peek().v == '?':
            c.step()
            e = ('quest', e)
        elif c.peek().v == '[':
            c.step()
            ix = parse_expr(c)
            c.want(']')
            e = ('idx', e, ix)
        elif c.peek().v == '.':
            c.step()
            meth = c.want_id('method or field name').v
            if c.peek().v == '(':
                c.step()
                args = []
                while c.peek().v != ')':
                    args.append(parse_expr(c))
                    if c.peek().v == ',':
                        c.step()
                c.want(')')
                if e == ('var', 'trace') and c.peek().v == '{':
                    c.step()
                    blk = parse_block(c)
                    c.want('}')
                    args = args + [('closure', None, [], blk)]
                e = ('mcall', e, meth, args)
            else:
                e = ('field', e, meth)
        else:
            return e

def _parse_sub(text):
    """Parse an interpolation placeholder as an expression.

    A placeholder that does not parse stays literal text: `"{"a": 1}"` is a
    JSON blob, not an interpolation. That is the same rule Python uses -- an
    f-string is opt-in, and here only a placeholder that is a real expression
    counts as one.
    """
    try:
        c = Cur(lex(text))
        node = parse_expr(c)
        if c.peek().k != 'EOF':
            return None
        return node
    except SyntaxError:
        return None


def _expand_str(raw, src_expr):
    """`"a{x}b"` -> `("bin","+",("rstr","a"),("bin","+",x,("rstr","b")))`.

    The placeholder is parsed by the real parser, so anything that works outside
    a string works inside it: `{len(v)}`, `{p.x}`, `{m.get(k)}`, `{a + b}`.
    Braces that do not hold exactly one expression stay literal, which is what
    keeps JSON templates working: `"{\"a\": 1}"` is text, `"{len(v)}"` is not.
    A literal chunk is `rstr`, never `str`, so `str`'s grow() cannot touch it.
    """
    if '{' not in raw:
        return ('str', unesc(raw))
    parts = []
    buf = ''
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == '{':
            j = i + 1
            depth = 1
            while j < len(raw) and depth:
                if raw[j] == '\\' and j + 1 < len(raw):
                    j += 2
                    continue
                if raw[j] == '{':
                    depth += 1
                elif raw[j] == '}':
                    depth -= 1
                j += 1
            if depth == 0:
                sub = src_expr(unesc(raw[i + 1:j - 1]))
                if sub is not None:
                    if buf:
                        parts.append(('rstr', unesc(buf)))
                        buf = ''
                    parts.append(('expr', sub))
                    i = j
                    continue
                _note_stray(raw[i + 1:j - 1])
        buf += c
        i += 1
    if buf:
        parts.append(('rstr', unesc(buf)))
    if len(parts) == 1:
        return parts[0][1] if parts[0][0] == 'expr' else ('rstr', parts[0][1])
    node = parts[0][1] if parts[0][0] == 'expr' else ('rstr', parts[0][1])
    for kind, val in parts[1:]:
        node = ('bin', '+', node, val if kind == 'expr' else ('rstr', val))
    return node


def _note_stray(text):
    """Record a `{...}` that stayed literal but looks like it wanted to be one.

    JSON-shaped content (`{"a": 1}`) has a top-level colon and is left alone;
    so is anything without identifier characters (`{}`, `{ }`). What remains
    is code the parser could not read, which is what a typo looks like.
    """
    body = text.strip()
    if not body:
        return
    depth = 0
    for ch in body:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif ch == ':' and depth == 0:
            return
    if not any(ch.isalnum() or ch == '_' for ch in body):
        return
    _STRAY_BRACES.append((_CURLINE[0], body))


def take_stray_braces():
    """Drain the stray-placeholder findings (the checker prints them)."""
    out = list(_STRAY_BRACES)
    del _STRAY_BRACES[:]
    return out


def _num_val(tok):
    """`1` is an int, `1.0` and `1e3` are floats (mirrored in bratp.brat)."""
    if '.' in tok or 'e' in tok or 'E' in tok:
        return float(tok)
    return int(tok)


def parse_prim(c):
    t = c.step()
    if t.k == 'NUM':
        return ('num', _num_val(t.v))
    if t.k == 'STR':
        return _expand_str(t.v[1:-1], _parse_sub)
    if t.k == 'KW' and t.v in ('true', 'false'):
        return ('bool', t.v == 'true')
    if t.k == 'ID' and t.v == 'None':
        return ('none',)
    if t.v == '[':
        items = []
        while c.peek().v != ']':
            items.append(parse_expr(c))
            if c.peek().v == ',':
                c.step()
        c.want(']')
        return ('vec', items)
    if t.v == '{':
        pairs = []
        while c.peek().v != '}':
            k = parse_expr(c)
            c.want(':')
            pairs.append((k, parse_expr(c)))
            if c.peek().v == ',':
                c.step()
        c.want('}')
        return ('maplit', pairs)
    if t.k == 'KW' and t.v == 'fn':
        name = None
        if c.peek().k == 'ID' and c.i + 1 < len(c.t) and c.t[c.i + 1].v == '(':
            name = c.step().v
        c.want('(')
        params = parse_params(c)
        c.want(')')
        c.want('{')
        body = parse_block(c)
        c.want('}')
        return ('closure', name, params, body)
    if t.k in ('ID', 'KW'):
        if c.peek().v == '(':
            c.step()
            args = []
            while c.peek().v != ')':
                args.append(parse_expr(c))
                if c.peek().v == ',':
                    c.step()
            c.want(')')
            return ('call', t.v, args)
        if c.peek().v == '{' and t.k == 'ID' and t.v[:1].isupper():
            j = c.i + 1
            is_struct = False
            if j < len(c.t):
                if c.t[j].v == '}':
                    is_struct = True
                elif c.t[j].k == 'ID' and j + 1 < len(c.t) and c.t[j + 1].v == ':':
                    is_struct = True
            if is_struct:
                c.step()
                fields = []
                while c.peek().v != '}':
                    k = c.want_id('field name').v
                    c.want(':')
                    fields.append((k, parse_expr(c)))
                    if c.peek().v == ',':
                        c.step()
                c.want('}')
                return ('struct', t.v, fields)
        if t.k == 'ID' and t.v[:1].isupper():
            return ('call', t.v, [])
        return ('var', t.v)
    if t.v == '(':
        e = parse_expr(c)
        c.want(')')
        return e
    raise SyntaxError('stuck at %r at line %d' % (t.v, eline(c)))