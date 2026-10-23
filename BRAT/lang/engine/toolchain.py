"""C compiler resolution for `brat build`.

Order: $BRAT_CC, then a real zig, clang, cc, gcc. Python never appears in the
compiler argv: the build backend is a C compiler, not the interpreter.

Resolve once per `brat build`; `CcSpec.argv` assembles the invocation, so the
CLI never needs to know which compiler can cross-compile or accept -static.
"""

import os
import shutil
import subprocess


class CcError(Exception):
    """No usable C compiler, or a request the resolved compiler cannot fulfil."""


class CcSpec:
    """One resolved compiler: its argv head and the triplets it targets."""

    def __init__(self, exe, kind, targets, static):
        self.exe = exe          # resolved executable path
        self.kind = kind        # 'zig' | 'clang' | 'gcc' | 'cc'
        self.targets = set(targets)   # triplets it can cross-compile to
        self.static = static    # accepts -static

    def argv(self, cfile, out, target=None, static=False):
        """Command line to compile `cfile` into `out`.

        Never contains the Python interpreter. A `target` outside `targets`
        is refused (zig ships its own targets and is exempt).
        """
        if self.kind == "zig":
            head = [self.exe, "cc"]
        else:
            head = [self.exe]
        args = list(head) + [cfile, "-o", out]
        if target is not None:
            if self.kind == "zig":
                args += ["-target", target]
            elif target in self.targets:
                args += ["-target", target]
            else:
                raise CcError(
                    "%s cannot target %s; known targets: %s"
                    % (self.exe, target, ", ".join(sorted(self.targets))))
        if static and self.static:
            args.append("-static")
        return args


# Triplets zig can target out of the box (it ships its own linker/libc).
_ZIG_TARGETS = {
    "x86_64-linux-gnu", "aarch64-linux-gnu", "riscv64-linux-gnu",
    "s390x-linux-gnu", "arm-linux-gnueabihf", "i686-linux-gnu",
    "x86_64-macos", "aarch64-macos", "x86_64-windows", "aarch64-windows",
}


def _kind(exe):
    """'zig' | 'clang' | 'gcc' | 'cc' for `exe`, or None when it isn't one.

    `zig cc --version` reports a clang version string, so the basename is
    checked first: `zig ...` wins over the version text.
    """
    b = os.path.basename(exe).lower()
    if b.startswith("zig"):
        return "zig"
    try:
        out = subprocess.run([exe, "--version"], capture_output=True,
                             text=True, timeout=15)
    except OSError:
        return None
    text = (out.stdout + out.stderr).lower()
    if "clang" in text:
        return "clang"
    if "gcc" in text:
        return "gcc"
    if "clang" in b:
        return "clang"
    if "gcc" in b:
        return "gcc"
    return "cc"


def _host_triplet(exe):
    """The triplet this compiler produces by default, or 'unknown'."""
    try:
        out = subprocess.run([exe, "-dumpmachine"], capture_output=True,
                             text=True, timeout=15)
    except OSError:
        return "unknown"
    return (out.stdout or "").strip() or "unknown"


def _probe(exe):
    """CcSpec for `exe`, or None when `exe` is not a usable C compiler."""
    kind = _kind(exe)
    if kind is None:
        return None
    if kind == "zig":
        return CcSpec(exe, "zig", _ZIG_TARGETS, True)
    return CcSpec(exe, kind, {_host_triplet(exe)}, True)


def resolve_cc(env=None):
    """Resolve a C compiler: $BRAT_CC, zig, clang, cc, gcc.

    Raises CcError listing everything that was searched when nothing usable
    exists; the caller turns that into exit 1. Python never ends up in argv.
    """
    if env is None:
        env = os.environ
    cc = env.get("BRAT_CC")
    if cc is not None:
        spec = _probe(cc)
        if spec is None:
            raise CcError("BRAT_CC=%r is not a usable C compiler" % cc)
        return spec
    tried = []
    for name in ("zig", "clang", "cc", "gcc"):
        exe = shutil.which(name)
        if exe is None:
            tried.append(name)
            continue
        spec = _probe(exe)
        if spec is None:
            tried.append("%s (%s: not a C compiler)" % (name, exe))
            continue
        return spec
    raise CcError("no C compiler found; searched: %s" % ", ".join(tried))