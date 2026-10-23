import re


TOKEN_RE = re.compile(r'(?P<WS>\s+)|(?P<COMMENT>//[^\n]*)|(?P<STR>"(?:\\.|[^"\\])*")|(?P<NUM>\d+\.\d+(?:[eE][+-]?\d+)?|\d+(?:[eE][+-]?\d+)?)|(?P<DOT>\.(?!\.))|(?P<ID>[A-Za-z_][A-Za-z0-9_]*)|(?P<OP>\.\.|=>|->|==|!=|<=|>=|&&|\|\||\+=|-=|\*=|/=|%=|[+\-*/%<>=!{}()\[\],:;?])')
KEYWORDS = {'let', 'mut', 'fn', 'if', 'else', 'while', 'return', 'true', 'false', 'or', 'try', 'catch', 'spawn', 'match', 'for', 'in', 'import', 'defer', 'move', 'break', 'continue', 'impl', 'self', 'trait', 'enum', 'const', 'pub', 'extern', 'unsafe'}

class Tok:
    __slots__ = ('k', 'v', 'line')
    def __init__(self, k, v, line=1):
        self.k = k
        self.v = v
        self.line = line
    def __repr__(self):
        return '%s:%s@%d' % (self.k, self.v, self.line)


def tok_line(nls, pos):
    import bisect
    return bisect.bisect_right(nls, pos) + 1


def _str_end(src, i):
    """Index of the closing quote of the string literal starting at `i`.

    A `{...}` placeholder may contain quotes -- `"{v.join("|")}"` is one string,
    not three tokens -- so the scan tracks how deep it is inside placeholders
    and inside the parentheses of the expression it is reading. A quote only
    ends the literal at placeholder depth 0, or at placeholder depth > 0 with no
    unclosed `(`, because that is what a lone `"{"` looks like: a JSON template
    whose braces are meant literally.
    """
    n = len(src)
    j = i + 1
    depth = 0
    paren = 0
    while j < n:
        c = src[j]
        if c == '\\' and j + 1 < n:
            j += 2
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            if depth > 0:
                depth -= 1
        elif c == '(':
            paren += 1
        elif c == ')':
            if paren > 0:
                paren -= 1
        elif c == '"':
            if depth == 0 or paren == 0:
                return j
        j += 1
    return None


def lex(src):
    nls = [m.start() for m in re.finditer('\n', src)]
    out = []
    pos = 0
    n = len(src)
    while pos < n:
        m = TOKEN_RE.match(src, pos)
        if m is None:
            pos += 1
            continue
        k = m.lastgroup
        v = m.group()
        start = m.start()
        if k == 'STR':
            end = _str_end(src, pos)
            if end is not None:
                v = src[pos:end + 1]
                pos = end + 1
            else:
                v = src[pos:]
                pos = n
            out.append(Tok('STR', v, tok_line(nls, start)))
            continue
        pos = m.end()
        if k in ('WS', 'COMMENT'):
            continue
        if k == 'ID' and v in KEYWORDS:
            k = 'KW'
        out.append(Tok(k, v, tok_line(nls, start)))
    return out


def eline(c):
    t = c.peek()
    if t.k != 'EOF':
        return t.line
    if c.t:
        return c.t[-1].line
    return 1


class Cur:
    def __init__(self, t):
        self.t = t
        self.i = 0
    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else Tok('EOF', '')
    def step(self):
        if self.i >= len(self.t):
            ln = self.t[-1].line if self.t else 1
            raise SyntaxError('unexpected end of file at line %d' % ln)
        x = self.t[self.i]
        self.i += 1
        return x
    def want(self, v):
        x = self.step()
        if x.v != v:
            raise SyntaxError('want %r have %r at line %d' % (v, x.v, x.line))
        return x
    def want_id(self, what='identifier'):
        """Step, but only accept a name-shaped token (ID or keyword)."""
        x = self.step()
        if x.k not in ('ID', 'KW'):
            raise SyntaxError('want %s (identifier) have %r at line %d'
                              % (what, x.v, x.line))
        return x