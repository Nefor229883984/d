import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "..", "engine", "cli.py")
CLI = os.path.abspath(CLI)
EXAMPLES = os.path.abspath(os.path.join(HERE, "..", "examples"))
BRATS = sorted([f for f in os.listdir(EXAMPLES) if f.endswith(".brat")])

NO_ARG_EXIT0_OK = {"own_bad.brat", "logstat.brat"}

def run_cli(*args):
    return subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True, timeout=60)

n = 0

def check(cond, msg):
    global n
    assert cond, msg
    n += 1

check(len(BRATS) >= 7, "want >=7 examples have %r" % (BRATS,))

p = run_cli("run", os.path.join(EXAMPLES, "hello.brat"))
check(p.returncode == 0, "run hello exit=%d stderr=%s" % (p.returncode, p.stderr[-500:]))

p = run_cli("check", os.path.join(EXAMPLES, "own_bad.brat"))
check(p.returncode == 1, "check own_bad exit=%d out=%s" % (p.returncode, (p.stdout + p.stderr)[-500:]))
check("4 error(s)" in p.stdout, "check own_bad wants 4 error(s) have %r" % p.stdout[-500:])

tmp = tempfile.mkdtemp()
try:
    for f in BRATS:
        shutil.copy(os.path.join(EXAMPLES, f), os.path.join(tmp, f))
    for f in BRATS:
        tp = os.path.join(tmp, f)
        p = run_cli("fmt", tp)
        check(p.returncode == 0, "fmt %s exit=%d stderr=%s" % (f, p.returncode, p.stderr[-500:]))
        first = open(tp, encoding="utf-8").read()
        check(first.endswith("\n") and not first.endswith("\n\n"), "fmt %s trailing newline" % f)
        p = run_cli("fmt", tp)
        check(p.returncode == 0, "fmt2 %s exit=%d" % (f, p.returncode))
        second = open(tp, encoding="utf-8").read()
        check(first == second, "fmt not idempotent for %s" % f)
    for f in BRATS:
        orig = os.path.join(EXAMPLES, f)
        tp = os.path.join(tmp, f)
        r1 = run_cli("run", orig)
        r2 = run_cli("run", tp)
        check(r1.returncode == r2.returncode, "fmt broke %s orig=%d fmt=%d err=%s" % (f, r1.returncode, r2.returncode, r2.stderr[-500:]))
        if f not in NO_ARG_EXIT0_OK:
            check(r2.returncode == 0, "formatted %s exit=%d err=%s" % (f, r2.returncode, r2.stderr[-500:]))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

tmp = tempfile.mkdtemp()

p = run_cli("emit-c", os.path.join(EXAMPLES, "hello.brat"))
check(p.returncode == 0, "emit-c exit=%d err=%s" % (p.returncode, p.stderr[-500:]))
check("printf" in p.stdout, "emit-c wants printf have %r" % p.stdout[:500])

p = run_cli("emit-c", os.path.join(EXAMPLES, "hello.brat"), "--bundle")
check(p.returncode == 0, "emit-c --bundle exit=%d err=%s" % (p.returncode, p.stderr[-500:]))
check('#include "crt.h"' not in p.stdout, "emit-c --bundle inlines crt.h")
check("int main(" in p.stdout, "emit-c --bundle keeps int main(")

hello = os.path.join(EXAMPLES, "hello.brat")
p = run_cli("build", hello, "-o", os.path.join(tmp, "hello.exe"))
check(p.returncode == 0, "build hello exit=%d err=%s" % (p.returncode, p.stderr[-500:]))
ldd = subprocess.run(["ldd", os.path.join(tmp, "hello.exe")], capture_output=True,
                     text=True, timeout=60,
                     env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "/usr/bin:/bin")})
ldo = (ldd.stdout + ldd.stderr)
check("statically linked" in ldo or "not a dynamic executable" in ldo,
      "build hello ldd static: %r" % ldo[-300:])

p = run_cli("dist", hello, "-o", os.path.join(tmp, "dist"))
check(p.returncode == 0, "dist exit=%d err=%s" % (p.returncode, p.stderr[-500:]))
manifest_path = os.path.join(tmp, "dist", "manifest.json")
check(os.path.isfile(manifest_path), "dist manifest.json exists")
manifest = json.load(open(manifest_path, encoding="utf-8"))
arts = manifest.get("artifacts")
check(isinstance(arts, list) and len(arts) >= 1, "dist artifacts: %r" % (manifest,))
for art in arts:
    ap = os.path.join(tmp, "dist", art.get("artifact", ""))
    check(os.path.isfile(ap), "dist artifact %s exists" % art.get("artifact"))
    data = open(ap, "rb").read()
    digest = art.get("sha256", "")
    check(len(digest) == 64 and all(c in "0123456789abcdef" for c in digest),
          "dist sha256 %r" % (digest,))
    check(digest == hashlib.sha256(data).hexdigest(),
          "dist sha256 matches %s" % art.get("artifact"))
    check(art.get("size") == len(data),
          "dist size matches %s" % art.get("artifact"))
    rr = subprocess.run([ap], capture_output=True, text=True, timeout=60)
    check(rr.returncode == 0 and "42" in rr.stdout,
          "dist artifact runs: %r" % (rr.stdout + rr.stderr)[-300:])

p = run_cli("dist", hello, "-o", os.path.join(tmp, "dist-bad"), "--target", "nope-arch")
check(p.returncode != 0, "dist bad target exit=%d" % p.returncode)

shutil.rmtree(tmp, ignore_errors=True)

p = run_cli("bogus-cmd")
check(p.returncode == 2, "unknown exit=%d" % p.returncode)
check("usage" in (p.stdout + p.stderr).lower(), "unknown wants usage have %r" % ((p.stdout + p.stderr)[-500:]))

print("CLI ALL GREEN (%d asserts)" % n)