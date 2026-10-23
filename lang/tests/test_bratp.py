import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, '..', 'engine')
EXAMPLES = os.path.join(HERE, '..', 'examples')
BRAT = os.path.join(ENGINE, 'brat.py')
BRATP = os.path.join(EXAMPLES, 'bratp.brat')

sys.path.insert(0, ENGINE)
from brat.lexer import Cur, lex
from brat.parser import parse_prog
import brat.parser as _P


def sx(node):
    if isinstance(node, tuple):
        return '(' + ' '.join(sx(x) for x in node) + ')'
    if isinstance(node, list):
        return '[' + ' '.join(sx(x) for x in node) + ']'
    if isinstance(node, bool):
        return 'true' if node else 'false'
    if node is None:
        return 'None'
    if isinstance(node, float):
        return repr(node)
    if isinstance(node, int):
        return str(node)
    if isinstance(node, str):
        out = node.replace('\\', '\\\\').replace('\n', '\\n').replace('\t', '\\t').replace('\r', '\\r').replace('"', '\\"')
        return '"%s"' % out
    return repr(node)


ENV = dict(os.environ)
ENV['BRAT_FILES'] = '1'

FILES = ['hello.brat', 'control.brat', 'map.brat', 'parse.brat', 'bratc.brat',
         'jsonlib.brat', 'guards.brat', 'fastvec.brat', 'utf8.brat', 'tok.brat',
         'trait.brat', 'closure.brat', 'nested.brat', 'defaults.brat',
         'compound.brat', 'trycatch.brat', 'enum.brat', 'funclib.brat',
         'jsontest.brat', 'rematch.brat', 'span.brat', 'precedence.brat']

n = 0
for f in FILES:
    path = os.path.join(EXAMPLES, f)
    _P._seq = 0
    want = sx(parse_prog(Cur(lex(open(path, encoding='utf-8').read()))))
    p = subprocess.run([sys.executable, BRAT, BRATP, path], capture_output=True, timeout=180, env=ENV)
    got = p.stdout.decode('utf-8', 'replace').strip()
    assert p.returncode == 0, 'bratp exit %d: %s' % (p.returncode, p.stderr.decode('utf-8', 'replace')[-300:])
    assert want == got, 'parser mismatch on %s (py %d vs brat %d)' % (f, len(want), len(got))
    n += 1
    print('bratp match: %s (%d chars)' % (f, len(got)))

print('BRATP ALL GREEN (%d files)' % n)