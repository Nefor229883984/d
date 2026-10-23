import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(os.path.dirname(HERE), 'engine', 'cli.py')
ENGINE = os.path.join(os.path.dirname(HERE), 'engine')

n = 0


def check(cond, msg):
    global n
    assert cond, msg
    n += 1


tmp = tempfile.mkdtemp(prefix='brat-pkg-')
lib = os.path.join(tmp, 'mymod.brat')
with open(lib, 'w', encoding='utf-8') as f:
    f.write('fn twice(x: i64) -> i64 {\n  return x * 2\n}\n')
url = __import__('pathlib').Path(lib).as_uri()

r = subprocess.run([sys.executable, CLI, 'pkg', 'add', url], capture_output=True,
                   text=True, timeout=60, cwd=tmp)
check(r.returncode == 0, 'pkg add: ' + (r.stdout + r.stderr)[-300:])
check(os.path.isfile(os.path.join(tmp, 'vendor', 'mymod.brat')), 'vendored')
lock = json.load(open(os.path.join(tmp, 'brat.lock'), encoding='utf-8'))
check(len(lock) == 1 and len(lock[0]['sha256']) == 64, 'lock: %r' % (lock,))

main = os.path.join(tmp, 'app.brat')
with open(main, 'w', encoding='utf-8') as f:
    f.write('import mymod\nfn main() -> i64 {\n  print(twice(21))\n  return 0\n}\n')
r = subprocess.run([sys.executable, CLI, 'run', main], capture_output=True,
                   text=True, timeout=60, cwd=tmp)
check(r.returncode == 0 and '42' in r.stdout, 'vendor import: ' + (r.stdout + r.stderr)[-300:])

os.unlink(os.path.join(tmp, 'vendor', 'mymod.brat'))
r = subprocess.run([sys.executable, CLI, 'pkg', 'install'], capture_output=True,
                   text=True, timeout=60, cwd=tmp)
check(r.returncode == 0 and os.path.isfile(os.path.join(tmp, 'vendor', 'mymod.brat')),
      'pkg install: ' + (r.stdout + r.stderr)[-300:])

lock[0]['sha256'] = '0' * 64
json.dump(lock, open(os.path.join(tmp, 'brat.lock'), 'w', encoding='utf-8'))
r = subprocess.run([sys.executable, CLI, 'pkg', 'install'], capture_output=True,
                   text=True, timeout=60, cwd=tmp)
check(r.returncode == 1 and 'HASH MISMATCH' in (r.stdout + r.stderr), 'mismatch detected')

import shutil
shutil.rmtree(tmp, ignore_errors=True)
print('PKG ALL GREEN (%d asserts)' % n)