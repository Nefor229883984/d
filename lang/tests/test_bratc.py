import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, '..', 'engine')
EXAMPLES = os.path.join(HERE, '..', 'examples')
BRAT = os.path.join(ENGINE, 'brat.py')
BRATC = os.path.join(EXAMPLES, 'bratc.brat')

sys.path.insert(0, ENGINE)
from brat.lexer import lex

ENV = dict(os.environ)
ENV['BRAT_FILES'] = '1'

FILES = ['hello.brat', 'control.brat', 'map.brat', 'parse.brat', 'bratc.brat',
         'jsonlib.brat', 'guards.brat', 'fastvec.brat', 'utf8.brat', 'tok.brat']


def dump_py(path):
    src = open(path, encoding='utf-8').read()
    out = []
    for t in lex(src):
        out.append('%s:%s' % (t.k, t.v))
    out.append('EOF')
    return out


n = 0
for f in FILES:
    path = os.path.join(EXAMPLES, f)
    py = dump_py(path)
    p = subprocess.run([sys.executable, BRAT, BRATC, path], capture_output=True, timeout=120, env=ENV)
    got = p.stdout.decode('utf-8', 'replace').splitlines()
    assert p.returncode == 0, 'bratc exit %d: %s' % (p.returncode, p.stderr.decode('utf-8', 'replace')[-300:])
    assert py == got, 'lexer mismatch on %s (py %d vs brat %d)' % (f, len(py), len(got))
    n += 1
    print('bratc match: %s (%d tokens)' % (f, len(py)))

print('BRATC ALL GREEN (%d files)' % n)