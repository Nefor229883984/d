import os, subprocess, sys, glob, re

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, '..', 'engine')
EXAMPLES = os.path.join(HERE, '..', 'examples')
BRAT = os.path.join(ENGINE, 'brat.py')
BRATB = os.path.join(EXAMPLES, 'bratb.brat')
OWN = os.path.join(ENGINE, 'borrow.py')

ENV = dict(os.environ)
ENV['BRAT_FILES'] = '1'

n = 0
for path in sorted(glob.glob(os.path.join(EXAMPLES, '*.brat'))):
    name = os.path.basename(path)
    if name in ('bratb.brat',):
        continue
    py = subprocess.run([sys.executable, OWN, path], capture_output=True, text=True,
                        timeout=180, env=ENV)
    br = subprocess.run([sys.executable, BRAT, BRATB, path], capture_output=True, text=True,
                        timeout=180, env=ENV)
    pm = re.search(r'(\d+) error\(s\)', py.stdout)
    bm = re.search(r'(\d+) error\(s\)', br.stdout)
    pc = int(pm.group(1)) if pm else -1
    bc = int(bm.group(1)) if bm else -1
    assert pc == bc, '%s: py %d errors vs brat %d errors\npy: %s\nbrat: %s' % (name, pc, bc, py.stdout[-400:], br.stdout[-400:])
    py_errs = sorted(re.findall(r':\d+: error: (.+)$', py.stdout, re.M))
    br_errs = sorted(re.findall(r':\d+: error: (.+)$', br.stdout, re.M))
    assert py_errs == br_errs, '%s message mismatch:\npy: %s\nbrat: %s' % (name, py_errs, br_errs)
    n += 1
    print('bratb match: %s (%d errors)' % (name, pc))

print('BRATB ALL GREEN (%d files)' % n)