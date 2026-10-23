"""`arena { }` as a request handler that rolls back on Err.

The handler builds its working state inside an arena, returns Err with a
message copied out with own(), and the loop runs many requests, most of them
successful. Checked here:

- both backends print the same output;
- under a 16 MiB address-space limit the C build finishes, while the same
  program without the arena does not (so the arena is what reclaims memory,
  including on the Err path);
- AddressSanitizer reports no memory errors: the Err message and the tag
  arguments are not left pointing into a released arena.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, '..', 'engine')
BRAT = os.path.join(ENGINE, 'brat.py')
CLI = os.path.join(ENGINE, 'cli.py')

PROGRAM = '''enum Res {
  Ok(total),
  Err(reason)
}

fn handle(req: str) -> Res {
  arena {
    let parts = req.split(",")
    let mut total = 0
    for p in parts {
      if p == "" {
        return Err(own("empty field among {len(parts)} fields"))
      }
      total = total + len(p)
    }
    return Ok(total)
  }
  return Err("unreachable")
}

fn main() -> i64 {
  let mut base = "ab"
  let mut f = 0
  while f < 150 {
    base = base + ",cd"
    f = f + 1
  }
  let mut ok = 0
  let mut failed = 0
  let mut last = ""
  let mut i = 0
  while i < 3000 {
    let mut suffix = ""
    if i % 5 == 0 {
      suffix = ",,"
    }
    let line = base + suffix
    match handle(line) {
      Ok(t) => { ok = ok + 1 },
      Err(r) => {
        failed = failed + 1
        last = r
      }
    }
    i = i + 1
  }
  print("ok {ok} failed {failed}")
  print("last: {last}")
  return 0
}
'''

WANT = 'ok 2400 failed 600\nlast: empty field among 153 fields\n'
LIMIT = 16 * 1024 * 1024

n = 0


def check(cond, msg):
    global n
    assert cond, msg
    n += 1


def limited(bytes_):
    def apply():
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (bytes_, bytes_))
    return apply


tmp = tempfile.mkdtemp(prefix='brat-arena-request-')
try:
    src = os.path.join(tmp, 'req.brat')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(PROGRAM)
    noarena = os.path.join(tmp, 'noarena.brat')
    with open(noarena, 'w', encoding='utf-8') as f:
        f.write(PROGRAM.replace('  arena {', '  if true {'))

    ip = subprocess.run([sys.executable, BRAT, src], capture_output=True, text=True, timeout=600)
    check(ip.returncode == 0 and ip.stdout == WANT, 'interp: rc=%d out=%r' % (ip.returncode, ip.stdout))

    exe = os.path.join(tmp, 'req_bin')
    b = subprocess.run([sys.executable, CLI, 'build', src, '-o', exe],
                       capture_output=True, text=True, timeout=600)
    check(b.returncode == 0, 'build failed: %s' % (b.stdout + b.stderr)[-500:])
    cr = subprocess.run([exe], capture_output=True, text=True, timeout=600, preexec_fn=limited(LIMIT))
    check(cr.returncode == 0 and cr.stdout == WANT,
          'C under 16MiB: rc=%d out=%r err=%r' % (cr.returncode, cr.stdout, cr.stderr[-300:]))

    exe_na = os.path.join(tmp, 'noarena_bin')
    b2 = subprocess.run([sys.executable, CLI, 'build', noarena, '-o', exe_na],
                        capture_output=True, text=True, timeout=600)
    check(b2.returncode == 0, 'noarena build failed: %s' % (b2.stdout + b2.stderr)[-500:])
    na = subprocess.run([exe_na], capture_output=True, text=True, timeout=600, preexec_fn=limited(LIMIT))
    check(na.returncode != 0 and 'out of memory' in na.stderr,
          'without the arena the same limit should be exceeded: rc=%d err=%r'
          % (na.returncode, na.stderr[-200:]))

    emitted = os.path.join(tmp, 'req_bundle.c')
    e = subprocess.run([sys.executable, CLI, 'emit-c', '--bundle', src],
                       capture_output=True, text=True, timeout=600)
    check(e.returncode == 0, 'emit-c failed: %s' % e.stderr[-300:])
    with open(emitted, 'w', encoding='utf-8') as f:
        f.write(e.stdout)
    asan_exe = os.path.join(tmp, 'req_asan')
    cc = subprocess.run(['gcc', '-O1', '-g', '-I', ENGINE, emitted, '-o', asan_exe,
                         '-lm', '-lpthread', '-fsanitize=address'],
                        capture_output=True, text=True, timeout=600)
    if cc.returncode == 0:
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
        ar = subprocess.run([asan_exe], capture_output=True, text=True, timeout=600, env=env)
        check(ar.returncode == 0 and ar.stdout == WANT and 'AddressSanitizer' not in ar.stderr,
              'ASan: rc=%d out=%r err=%r' % (ar.returncode, ar.stdout, ar.stderr[-600:]))
        asan_note = 'AddressSanitizer clean'
    else:
        asan_note = 'AddressSanitizer skipped (no gcc -fsanitize)'
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print('ARENA REQUEST ALL GREEN (%d asserts, %s)' % (n, asan_note))