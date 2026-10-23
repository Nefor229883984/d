import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENG = os.path.join(ROOT, "lang", "engine")
CLI = os.path.join(ENG, "cli.py")
TMP = tempfile.mkdtemp(prefix="brat-ffi-")

CASES = {
    "puts": (
        'extern fn puts(s: str) -> i32;\n\n'
        'fn main() -> i32 {\n'
        '    puts("ffi puts");\n'
        '    return 0;\n'
        '}\n'
    ),
    "malloc_free": (
        'extern fn malloc(size: i64) -> ptr;\n'
        'extern fn free(p: ptr);\n\n'
        'fn main() -> i32 {\n'
        '    let p = malloc(16);\n'
        '    free(p);\n'
        '    return 0;\n'
        '}\n'
    ),
    "getenv": (
        'extern fn getenv(name: str) -> str;\n\n'
        'fn main() -> i32 {\n'
        '    if getenv("PATH") == "" { return 1; }\n'
        '    return 0;\n'
        '}\n'
    ),
}


def run_interp(src):
    path = os.path.join(TMP, "t.brat")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    p = subprocess.run([sys.executable, CLI, "run", path], capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout.replace("\r\n", "\n")


def run_native(src, tag):
    path = os.path.join(TMP, tag + ".brat")
    exe = os.path.join(TMP, tag + ".exe")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    p = subprocess.run([sys.executable, CLI, "build", path, "-o", exe],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, "build failed for %s:\n%s" % (tag, (p.stdout + p.stderr)[-1000:])
    q = subprocess.run([exe], capture_output=True, text=True, timeout=120)
    return q.returncode, q.stdout.replace("\r\n", "\n")


def main():
    n = 0
    for name, src in CASES.items():
        ri, oi = run_interp(src)
        rn, on = run_native(src, name)
        assert ri == rn, "%s exit: interp=%d native=%d" % (name, ri, rn)
        assert oi == on, "%s stdout differs:\ninterp=%r\nnative=%r" % (name, oi, on)
        n += 1
        print("ffi ok: %s" % name)

    # Custom shared library (backend agreement via dynamic linking).
    lib = "/tmp/opencode/libtest/libtest.so"
    if os.path.exists(lib):
        src = (
            'extern "%s" fn add42(x: i64) -> i64;\n\n'
            'fn main() -> i64 {\n'
            '    return add42(1);\n'
            '}\n' % lib
        )
        ri, oi = run_interp(src)
        rn, on = run_native(src, "custom_add42")
        assert ri == rn == 43, "custom exit: interp=%d native=%d" % (ri, rn)
        assert oi == on, "custom stdout differs"
        n += 1
        print("ffi ok: custom_add42")

        src = (
            'extern "%s" fn greet(s: str) -> str;\n\n'
            'fn main() -> i32 {\n'
            '    if greet("hi") == "hi" { return 0; }\n'
            '    return 1;\n'
            '}\n' % lib
        )
        ri, oi = run_interp(src)
        rn, on = run_native(src, "custom_greet")
        assert ri == rn == 0, "greet exit: interp=%d native=%d" % (ri, rn)
        assert oi == on
        n += 1
        print("ffi ok: custom_greet")

        src = (
            'extern "/tmp/opencode/libtest/libvar.so" fn varsum(n: i64, ..i64) -> i64;\n\n'
            'fn main() -> i64 {\n'
            '    return varsum(3, 10, 20, 30);\n'
            '}\n'
        )
        ri, oi = run_interp(src)
        rn, on = run_native(src, "custom_varargs")
        assert ri == rn == 60, "varargs exit: interp=%d native=%d" % (ri, rn)
        assert oi == on
        n += 1
        print("ffi ok: custom_varargs")

        src = (
            'extern struct Point { x: i64, y: i64 }\n\n'
            'extern "/tmp/opencode/libtest/libstruct.so" fn make_point(x: i64, y: i64) -> Point;\n'
            'extern "/tmp/opencode/libtest/libstruct.so" fn add_point(a: Point, b: Point) -> Point;\n'
            'extern "/tmp/opencode/libtest/libstruct.so" fn sum_point(p: Point) -> i64;\n\n'
            'fn main() -> i64 {\n'
            '    let p = make_point(1, 2);\n'
            '    let q = make_point(3, 4);\n'
            '    let r = add_point(p, q);\n'
            '    if sum_point(r) != 10 { return 1; }\n'
            '    if r.x != 4 { return 2; }\n'
            '    if r.y != 6 { return 3; }\n'
            '    return 0;\n'
            '}\n'
        )
        ri, oi = run_interp(src)
        rn, on = run_native(src, "custom_struct")
        assert ri == rn == 0, "struct exit: interp=%d native=%d" % (ri, rn)
        assert oi == on
        n += 1
        print("ffi ok: custom_struct")

    print("FFI ALL GREEN (%d cases)" % n)


if __name__ == "__main__":
    main()