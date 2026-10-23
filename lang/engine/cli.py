import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

try:
    from diag import Diagnostic, to_human, classify, _entries_to_diags
except ImportError:  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from diag import Diagnostic, to_human, classify, _entries_to_diags

from toolchain import CcError, resolve_cc

HERE = os.path.dirname(os.path.abspath(__file__))
_BRAT_MOD = None
_OWN_MOD = None
_CBACK2_MOD = None
_CBACK2_FAILED = False
_SCAFFOLD_MOD = None

def _load_mod(tag, filename):
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(tag, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load " + filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def brat_mod():
    global _BRAT_MOD
    if _BRAT_MOD is None:
        _BRAT_MOD = _load_mod("brat_engine_cli", "brat.py")
    return _BRAT_MOD

def own_mod():
    global _OWN_MOD
    if _OWN_MOD is None:
        _OWN_MOD = _load_mod("brat_borrow_cli", "borrow.py")
    return _OWN_MOD

def codegen_c_mod():
    global _CBACK2_MOD, _CBACK2_FAILED
    if _CBACK2_MOD is not None:
        return _CBACK2_MOD
    if _CBACK2_FAILED:
        raise ImportError("codegen_c unavailable")
    try:
        _CBACK2_MOD = _load_mod("brat_codegen_c_cli", "codegen_c.py")
    except Exception as e:
        _CBACK2_FAILED = True
        raise ImportError(str(e))
    return _CBACK2_MOD

def scaffold_mod():
    global _SCAFFOLD_MOD
    if _SCAFFOLD_MOD is None:
        _SCAFFOLD_MOD = _load_mod("brat_scaffold_cli", "scaffold.py")
    return _SCAFFOLD_MOD

def normalize_equals(line):
    out = []
    i = 0
    n = len(line)
    in_str = False
    while i < n:
        c = line[i]
        if in_str:
            out.append(c)
            if c == "\\":
                if i + 1 < n:
                    out.append(line[i + 1])
                    i += 2
                    continue
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and line[i + 1] == "/":
            out.append(line[i:])
            break
        if c == "=":
            prev = line[i - 1] if i - 1 >= 0 else ""
            nxt = line[i + 1] if i + 1 < n else ""
            if nxt == "=" or nxt == ">" or prev in ("=", "!", "<", ">", "+", "-", "*", "/", "%"):
                out.append(c)
                i += 1
                continue
            cur = "".join(out).rstrip(" \t")
            j = i + 1
            while j < n and line[j] in (" ", "\t"):
                j += 1
            if j >= n:
                if cur == "":
                    out = ["="]
                else:
                    out = [cur + " ="]
                i = j
                continue
            if cur == "":
                out = ["= "]
            else:
                out = [cur + " = "]
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)

def count_braces(line):
    opens = 0
    closes = 0
    in_str = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            i += 1
            continue
        if c == "/" and i + 1 < n and line[i + 1] == "/":
            break
        if c == "{":
            opens += 1
        elif c == "}":
            closes += 1
        i += 1
    return opens, closes

def fmt_src(src):
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    lines = src.splitlines()
    out = []
    depth = 0
    for raw in lines:
        stripped = raw.strip()
        if stripped == "":
            out.append("")
            continue
        norm = normalize_equals(stripped).strip()
        if norm == "":
            out.append("")
            continue
        orig = depth
        if norm.startswith("}"):
            depth = depth - 1 if depth > 0 else 0
        out.append("  " * depth + norm)
        o, c = count_braces(norm)
        depth = orig + (o - c)
        if depth < 0:
            depth = 0
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"

def print_usage():
    sys.stderr.write("usage: brat file.brat [args...]\n")
    sys.stderr.write("       brat run file.brat [-- args...]\n")
    sys.stderr.write("       brat check file.brat [more...]\n")
    sys.stderr.write("       brat fmt file.brat [more...]\n")
    sys.stderr.write("       brat emit-c file.brat [--bundle] [--mem N]\n")
    sys.stderr.write("       brat test file.brat [more...]\n")
    sys.stderr.write("       brat build file.brat [-o out.exe] [--target TRIPLET] [--static|--dynamic] [--move-rt] [--mem N]\n")
    sys.stderr.write("       brat dist  file.brat [-o out]   [--target TRIPLET] [--static|--dynamic]\n")
    sys.stderr.write("       brat pkg add <url> | install | list | remove <file>\n")
    sys.stderr.write("       brat new NAME [--kind service|tool]\n")


def _read_src(path):
    """Source text for a diagnostic snippet, or "" when the file is unreadable.

    Every caller is already on its way to exit 1, so this reports the read
    failure instead of raising in the middle of building a diagnostic -- but it
    does not stay silent: an empty source otherwise prints a parse error for a
    file nobody managed to read.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        sys.stderr.write("brat: cannot read %s: %s\n" % (path, e))
        return ""


def parse_error_line(exc):
    """Line number of a BRAT lexer/parser error, or None if `exc` is not one.

    BRAT raises a bare `SyntaxError('... at line N')` with no filename, so a
    Python SyntaxError raised while compiling brat.py itself is told apart by
    its populated `filename`.
    """
    if getattr(exc, "filename", None) is not None:
        return None
    m = re.search(r"\bat line (\d+)\s*\Z", str(exc))
    return int(m.group(1)) if m else None


def syntax_error(path, src, exc):
    """SPEC.md 9: a parse error is always `file:line: error: ...` plus the
    offending source line -- never a Python traceback. Returns 1."""
    ln = parse_error_line(exc)
    diags = [Diagnostic(path, ln, "error", "B0000", str(exc))]
    sys.stderr.write(to_human(path, src or "", diags))
    return 1


def do_test(args):
    files = [a for a in args if a != "--"]
    if not files:
        print_usage()
        return 2
    failed = 0
    for path in files:
        p = subprocess.run([sys.executable, os.path.join(HERE, "brat.py"), path],
                           capture_output=True, text=True, timeout=300)
        if p.returncode == 0:
            sys.stdout.write("ok %s\n" % path)
        else:
            failed += 1
            tail = (p.stdout + p.stderr)[-300:]
            sys.stdout.write("FAIL %s exit=%d %s\n" % (path, p.returncode, tail))
    sys.stdout.write("%d passed, %d failed\n" % (len(files) - failed, failed))
    return 1 if failed else 0

def blame_parse_error(brat, files, exc):
    """Find which file a parser error came from and report it with a snippet.

    Only reached after borrow.py/typer.py already blew up, so the extra parse
    costs nothing on the happy path and its `_LINES` pollution cannot be
    observed: we are on our way to exit 1.
    """
    for path in files:
        src = _read_src(path)
        try:
            brat.parse_prog(brat.Cur(brat.lex(src)))
        except SyntaxError as inner:
            return syntax_error(path, src, inner)
    first = files[0] if files else ""
    return syntax_error(first, _read_src(first), exc)


def do_run(args):
    if "--" in args:
        files = args[:args.index("--")]
        extra = args[args.index("--") + 1:]
    else:
        files = args[:1]
        extra = args[1:]
    if not files:
        print_usage()
        return 2
    path = files[0]
    try:
        src = open(path, encoding="utf-8").read()
    except OSError as e:
        sys.stderr.write("brat run: cannot read %s: %s\n" % (path, e))
        return 1
    # Arena blocks rely on the escape rules, which only the checker applies.
    # The interpreter has no arena memory, so the output would be right, but an
    # escaping value would silently survive the (non-existent) reset. Gate arena
    # programs the same way `brat build` does.
    if re.search(r"\\barena\\s*\\{", src):
        diags = _gate_ownership(path)
        if diags:
            sys.stderr.write(
                "brat run: refusing to run %s: the ownership checker reported "
                "errors above. Arena blocks depend on the escape rules.\n" % path)
            return 1
    return brat_mod().run_file(path, extra)

def do_check(args):
    files = [a for a in args if a != "--"]
    if not files:
        print_usage()
        return 2
    brat = brat_mod()
    own = own_mod()
    own.load_brat = lambda path=None: brat
    try:
        rc = own.main(["check"] + files)
    except SyntaxError as e:
        return blame_parse_error(brat, files, e)
    try:
        tp = _load_mod("brat_typer_cli", "typer.py")
        tp.load_brat = lambda path=None: brat
        rc2 = tp.main(["check"] + files)
    except SyntaxError as e:
        return blame_parse_error(brat, files, e)
    except Exception as e:
        sys.stderr.write("brat check: typer failed, result is not a clean "
                         "bill: %s: %s\n" % (type(e).__name__, e))
        return rc or 1
    return rc or rc2

def do_fmt(args):
    files = [a for a in args if a != "--"]
    if not files:
        print_usage()
        return 2
    code = 0
    for path in files:
        try:
            src = open(path, encoding="utf-8").read()
        except OSError as e:
            sys.stderr.write("brat fmt: cannot read %s: %s\n" % (path, e))
            code = 1
            continue
        formatted = fmt_src(src)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(formatted)
        except OSError as e:
            sys.stderr.write("brat fmt: cannot write %s: %s\n" % (path, e))
            code = 1
    return code

def do_emit_c(args):
    no_check = "--no-check" in args
    bundle = "--bundle" in args
    mem = None
    files = []
    rest = [a for a in args if a != "--"]
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--no-check", "--bundle"):
            i += 1
            continue
        if a == "--mem" or a.startswith("--mem="):
            val = rest[i + 1] if a == "--mem" and i + 1 < len(rest) else a[6:] if a.startswith("--mem=") else ""
            mem = _parse_mem(val)
            if mem is None:
                sys.stderr.write(
                    "brat emit-c: bad --mem %r (want e.g. 16777216, 512K, 16M, 1G)\n"
                    % val)
                return 2
            i += 2 if a == "--mem" else 1
            continue
        files.append(a)
        i += 1
    if not files:
        print_usage()
        return 2
    path = files[0]
    try:
        src = open(path, encoding="utf-8").read()
    except OSError as e:
        sys.stderr.write("brat emit-c: cannot read %s: %s\n" % (path, e))
        return 1
    if no_check and re.search(r"\barena\s*\{", src):
        sys.stderr.write(
            "brat emit-c: refusing --no-check for %s: arena blocks depend on the "
            "escape rules, which only the checker applies.\n" % path)
        return 1
    if not no_check:
        diags = _gate_ownership(path)
        if diags:
            sys.stderr.write(
                "brat emit-c: refusing to emit %s: the ownership checker "
                "reported errors above. Emitted C skips every check `brat "
                "build` applies.\nFix it, or pass --no-check to override "
                "deliberately.\n" % path)
            return 1
    try:
        cb = codegen_c_mod()
        out = cb.emit_file(path, bundle=bundle)
    except Exception as e:
        if parse_error_line(e) is not None:
            return syntax_error(path, src, e)
        sys.stderr.write("brat emit-c: %s: %s\n" % (type(e).__name__, e))
        return 1
    if no_check:
        out = "/* BRAT: UNVERIFIED -- ownership not checked */\n" + out
    if mem is not None:
        out = "#define BRAT_MEM_BYTES %d\n" % mem + out
    sys.stdout.write(out)
    if not out.endswith("\n"):
        sys.stdout.write("\n")
    return 0

def lock_path():
    return os.path.join(os.getcwd(), "brat.lock")


def read_lock():
    """Contents of brat.lock, or [] when there is no lockfile yet.

    A lockfile that exists but cannot be read or parsed is an error, not an
    empty lock. `brat pkg install` would otherwise proceed as if nothing were
    pinned and stop honouring the hashes it exists to enforce.
    """
    try:
        f = open(lock_path(), encoding="utf-8")
    except FileNotFoundError:
        return []
    with f:
        data = f.read()
    import json as _json
    try:
        items = _json.loads(data)
    except ValueError as e:
        raise SystemExit("brat pkg: %s is not valid JSON: %s" % (lock_path(), e))
    if not isinstance(items, list):
        raise SystemExit("brat pkg: %s wants a JSON list, have %s"
                         % (lock_path(), type(items).__name__))
    return items


def write_lock(items):
    import json as _json
    with open(lock_path(), "w", encoding="utf-8", newline="\n") as f:
        f.write(_json.dumps(items, indent=2, ensure_ascii=False) + "\n")


def fetch_url(url):
    import hashlib as _hl
    import urllib.request as _ur
    with _ur.urlopen(url, timeout=30) as r:
        body = r.read()
    try:
        text = body.decode("utf-8")
    except ValueError:
        raise RuntimeError("non-utf8 dep " + url)
    digest = _hl.sha256(body).hexdigest()
    return text, digest


def do_pkg(args):
    if not args:
        print_usage()
        return 2
    sub = args[0]
    if sub == "add" and len(args) == 2:
        url = args[1]
        name = url.rstrip("/").rsplit("/", 1)[-1]
        if not name.endswith(".brat"):
            sys.stderr.write("brat pkg: url must end with .brat\n")
            return 1
        try:
            text, digest = fetch_url(url)
        except Exception as e:
            sys.stderr.write("brat pkg: fetch %s: %s\n" % (url, e))
            return 1
        vendor = os.path.join(os.getcwd(), "vendor")
        try:
            os.makedirs(vendor, exist_ok=True)
            with open(os.path.join(vendor, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError as e:
            sys.stderr.write("brat pkg: write %s\n" % e)
            return 1
        items = [it for it in read_lock() if it.get("file") != name]
        items.append({"file": name, "url": url, "sha256": digest})
        write_lock(items)
        sys.stdout.write("add %s %s\n" % (name, digest[:12]))
        return 0
    if sub == "install" and len(args) == 1:
        items = read_lock()
        if not items:
            sys.stderr.write("brat pkg: empty lock\n")
            return 1
        vendor = os.path.join(os.getcwd(), "vendor")
        os.makedirs(vendor, exist_ok=True)
        code = 0
        for it in items:
            try:
                text, digest = fetch_url(it["url"])
            except Exception as e:
                sys.stderr.write("brat pkg: fetch %s: %s\n" % (it.get("url"), e))
                code = 1
                continue
            if digest != it.get("sha256"):
                sys.stderr.write("brat pkg: HASH MISMATCH %s\n" % it.get("file"))
                code = 1
                continue
            with open(os.path.join(vendor, it["file"]), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            sys.stdout.write("ok %s\n" % it["file"])
        return code
    if sub == "list":
        items = read_lock()
        if not items:
            sys.stdout.write("empty lock\n")
            return 0
        for it in items:
            sys.stdout.write("%s  %s  %s\n" % (it.get("file"), it.get("sha256", "")[:12], it.get("url")))
        return 0
    if sub == "remove" and len(args) == 2:
        name = args[1]
        items = read_lock()
        new = [it for it in items if it.get("file") != name]
        if len(new) == len(items):
            sys.stderr.write("brat pkg: not in lock: %s\n" % name)
            return 1
        write_lock(new)
        vendor = os.path.join(os.getcwd(), "vendor", name)
        try:
            os.remove(vendor)
        except OSError:
            pass
        sys.stdout.write("removed %s\n" % name)
        return 0
    print_usage()
    return 2


def do_new(args):
    """`brat new NAME [--kind service|tool]`: a project that runs on the first
    try. Exit 2 on bad usage, 1 when the target directory is refused."""
    sc = scaffold_mod()
    rest = [a for a in args if a != "--"]
    name = None
    kind = sc.DEFAULT_KIND
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--kind" and i + 1 < len(rest):
            kind = rest[i + 1]
            i += 2
            continue
        if a.startswith("--kind="):
            kind = a.split("=", 1)[1]
            i += 1
            continue
        if a.startswith("-"):
            sys.stderr.write("brat new: unknown option %r\n" % a)
            print_usage()
            return 2
        if name is not None:
            sys.stderr.write("brat new: unexpected argument %r\n" % a)
            print_usage()
            return 2
        name = a
        i += 1
    if name is None:
        sys.stderr.write("brat new: NAME is required\n")
        print_usage()
        return 2
    if kind not in sc.KINDS:
        sys.stderr.write("brat new: unknown kind %r (want %s)\n"
                         % (kind, "|".join(sc.KINDS)))
        print_usage()
        return 2
    try:
        written = sc.write_project(name, kind, os.getcwd())
    except sc.ScaffoldError as e:
        sys.stderr.write("brat new: %s\n" % e)
        return 1
    sys.stdout.write("new %s (%s)\n" % (name, kind))
    for p in written:
        sys.stdout.write("  %s\n" % p)
    sys.stdout.write("\nnext:\n")
    for line in sc.run_hint(name, os.path.join(HERE, "cli.py"), os.getcwd()):
        sys.stdout.write("  %s\n" % line)
    return 0


def _gate_ownership(path, stream=sys.stderr):
    """Run the ownership checker before producing a native binary.

    Returns the list of diagnostics (empty == code is clean). Diagnostics are
    printed to `stream` so callers can say "errors above".

    `move` carries no runtime effect in any backend (SPEC.md 3), so the static
    checker is the only thing standing between a mistake and a silent alias.
    `brat run` deliberately does NOT gate, because the self-hosted toolchain
    (`bratb.brat`, `bratp.brat`) is run through it and does not pass its own
    rules yet. `brat build` is the production path, so it does.
    """
    brat = brat_mod()
    own = own_mod()
    own.load_brat = lambda path=None: brat
    path = os.path.abspath(path)

    all_diags = []

    class _Stop(Exception):
        pass

    def add_file(f):
        f = os.path.abspath(f)
        if f in seen:
            return
        seen.add(f)
        try:
            with open(f, encoding="utf-8") as fh:
                fsrc = fh.read()
        except OSError as e:
            stream.write("brat build: cannot read %s: %s\n" % (f, e))
            raise _Stop()
        try:
            fprog = brat.parse_prog(brat.Cur(brat.lex(fsrc)))
        except SyntaxError as e:
            diags = [Diagnostic(f, parse_error_line(e), "error", "B0000",
                                str(e))]
            stream.write(to_human(f, fsrc, diags))
            all_diags.extend(diags)
            raise _Stop()
        srcs[f] = fsrc
        progs.append((f, fprog))
        base = os.path.dirname(f)
        for s in fprog[1]:
            if isinstance(s, tuple) and s and s[0] == "import":
                add_file(os.path.join(base, *s[1].split(".")) + ".brat")

    progs = []
    srcs = {}
    seen = set()
    try:
        add_file(path)
    except _Stop:
        return all_diags

    def pub_sigs(fprog):
        out = {}
        for s in fprog[1]:
            node = s[1] if (isinstance(s, tuple) and s and s[0] == "pub") else s
            if isinstance(node, tuple) and node and node[0] == "fn":
                out[node[1]] = own.fn_modes(node[2])
            elif isinstance(node, tuple) and node and node[0] == "enum":
                for vname, _f in node[2]:
                    out[vname] = []
        return out

    per_file = [pub_sigs(fp) for _, fp in progs]
    for idx, (f, fprog) in enumerate(progs):
        merged = {}
        for j, ps in enumerate(per_file):
            if j != idx:
                merged.update(ps)
        errs, warns = own.check(fprog, merged)
        file_diags = _entries_to_diags(f, errs, "error")
        for wln, wmsg in warns:
            file_diags.append(Diagnostic(f, wln, "warning", classify(wmsg),
                                         wmsg))
        all_diags.extend(file_diags)
        stream.write(to_human(f, srcs[f], file_diags))
    return all_diags


def _parse_mem(s):
    """`--mem` value in bytes: a positive integer with optional K/M/G suffix
    (1024-based). None when the value is malformed or zero."""
    m = re.match(r"^(\d+)([KMG])?$", s.strip().upper())
    if not m:
        return None
    n = int(m.group(1)) << {"K": 10, "M": 20, "G": 30}.get(m.group(2) or "", 0)
    return n or None


def do_build(args):
    rest = [a for a in args if a != "--"]
    rest = [a for a in rest if a != "--no-check"]
    out = None
    files = []
    target = None
    static = None          # None -> static by default where the compiler supports it
    move_rt = False
    mem = None
    i = 0
    while i < len(rest):
        if rest[i] == "-o" and i + 1 < len(rest):
            out = rest[i + 1]
            i += 2
            continue
        if rest[i] == "--move-rt":
            move_rt = True
            i += 1
            continue
        if rest[i] == "--mem" and i + 1 < len(rest):
            mem = _parse_mem(rest[i + 1])
            if mem is None:
                sys.stderr.write(
                    "brat build: bad --mem %r (want e.g. 16777216, 512K, 16M, 1G)\n"
                    % rest[i + 1])
                return 2
            i += 2
            continue
        if rest[i].startswith("--mem="):
            mem = _parse_mem(rest[i].split("=", 1)[1])
            if mem is None:
                sys.stderr.write(
                    "brat build: bad --mem %r (want e.g. 16777216, 512K, 16M, 1G)\n"
                    % rest[i])
                return 2
            i += 1
            continue
        if rest[i] == "--target" and i + 1 < len(rest):
            target = rest[i + 1]
            i += 2
            continue
        if rest[i].startswith("--target="):
            target = rest[i].split("=", 1)[1]
            i += 1
            continue
        if rest[i] == "--static":
            static = True
            i += 1
            continue
        if rest[i] == "--dynamic":
            static = False
            i += 1
            continue
        files.append(rest[i])
        i += 1
    if len(files) != 1:
        print_usage()
        return 2
    src = files[0]
    if out is None:
        out = os.path.splitext(src)[0] + ".exe"
    diags = _gate_ownership(src, sys.stderr)
    if diags:
        sys.stderr.write(
            "brat build: refusing to build %s: the ownership checker reported "
            "errors above. `move` has no runtime effect (SPEC.md 3), so this is\n"
            "the only gate before a native binary. Fix it.\n" % src)
        return 1
    if move_rt:
        os.environ["BRAT_MOVE_RT"] = "1"
    try:
        codegen = codegen_c_mod()
    except ImportError as e:
        sys.stderr.write("brat build: backend unavailable: %s\n" % e)
        return 1
    env = os.environ
    fd, cfile = tempfile.mkstemp(prefix="brat-build-", suffix=".c")
    os.close(fd)
    try:
        with open(cfile, "w", encoding="utf-8") as f:
            f.write(codegen.emit_file(os.path.abspath(src)))
    except Exception as e:
        if parse_error_line(e) is not None:
            return syntax_error(src, _read_src(src), e)
        sys.stderr.write("brat build: emit %s: %s\n" % (type(e).__name__, e))
        return 1
    try:
        spec = resolve_cc()
    except CcError as e:
        try:
            os.unlink(cfile)
        except OSError:
            pass
        sys.stderr.write("brat build: %s\n" % e)
        return 1
    if static is None:
        static = True
    if static and not spec.static:
        sys.stderr.write(
            "brat build: %s does not support static linking (-static); "
            "build a dynamic binary: brat build %s --dynamic\n"
            % (spec.exe, src))
        try:
            os.unlink(cfile)
        except OSError:
            pass
        return 1
    argv = spec.argv(cfile, out, target=target, static=static)
    if mem is not None:
        argv.append("-DBRAT_MEM_BYTES=%d" % mem)
    try:
        _ffi_libs = codegen.extern_libs(os.path.abspath(src))
    except Exception:
        _ffi_libs = []
    _link_args = []
    _seen_libs = set()
    for _lib in _ffi_libs:
        if not _lib or _lib in _seen_libs:
            continue
        _seen_libs.add(_lib)
        if os.path.isabs(_lib) or os.path.exists(_lib):
            _link_args.append(_lib)
        else:
            _link_args.append("-l:" + _lib)
    if _ffi_libs and static and "--static" not in args:
        static = False
        argv = spec.argv(cfile, out, target=target, static=static)
    r = subprocess.run(argv + ["-O2", "-I", HERE, "-lm"] + _link_args,
                       capture_output=True, text=True, timeout=300, env=env)
    try:
        os.unlink(cfile)
    except OSError:
        pass
    if r.returncode != 0:
        sys.stderr.write("brat build: cc failed:\n%s\n" % ((r.stdout + r.stderr)[-2000:]))
        return 1
    sys.stdout.write("build ok %s\n" % out)
    return 0


def _dist_artifact_name(stem, target):
    name = stem if target is None else "%s-%s" % (stem, target)
    if target and "windows" in target.lower():
        name += ".exe"
    return name


def do_dist(args):
    """`brat dist file.brat [-o dir] [--target t1,t2,...] [--static|--dynamic]`

    One C emission, one compilation per target; writes manifest.json with the
    sha256 and size of every artifact.
    """
    rest = [a for a in args if a != "--"]
    rest = [a for a in rest if a != "--no-check"]
    out = None
    files = []
    target = None
    static = None
    i = 0
    while i < len(rest):
        if rest[i] == "-o" and i + 1 < len(rest):
            out = rest[i + 1]
            i += 2
            continue
        if rest[i] == "--target" and i + 1 < len(rest):
            target = rest[i + 1]
            i += 2
            continue
        if rest[i].startswith("--target="):
            target = rest[i].split("=", 1)[1]
            i += 1
            continue
        if rest[i] == "--static":
            static = True
            i += 1
            continue
        if rest[i] == "--dynamic":
            static = False
            i += 1
            continue
        files.append(rest[i])
        i += 1
    if len(files) != 1:
        print_usage()
        return 2
    src = files[0]
    if out is None:
        out = "dist"
    diags = _gate_ownership(src, sys.stderr)
    if diags:
        sys.stderr.write(
            "brat dist: refusing to dist %s: the ownership checker reported "
            "errors above.\n" % src)
        return 1
    try:
        codegen = codegen_c_mod()
    except ImportError as e:
        sys.stderr.write("brat dist: backend unavailable: %s\n" % e)
        return 1
    try:
        spec = resolve_cc()
    except CcError as e:
        sys.stderr.write("brat dist: %s\n" % e)
        return 1
    if static is None:
        static = True
    if static and not spec.static:
        sys.stderr.write(
            "brat dist: %s does not support -static; build dynamic "
            "artifacts: brat dist ... --dynamic\n" % spec.exe)
        return 1
    targets = [t.strip() for t in target.split(",")] if target else [None]
    targets = [t for t in targets if t != ""]
    try:
        os.makedirs(out, exist_ok=True)
    except OSError as e:
        sys.stderr.write("brat dist: cannot create %s: %s\n" % (out, e))
        return 1
    fd, cfile = tempfile.mkstemp(prefix="brat-dist-", suffix=".c")
    os.close(fd)
    try:
        with open(cfile, "w", encoding="utf-8") as f:
            f.write(codegen.emit_file(os.path.abspath(src)))
    except Exception as e:
        try:
            os.unlink(cfile)
        except OSError:
            pass
        if parse_error_line(e) is not None:
            return syntax_error(src, _read_src(src), e)
        sys.stderr.write("brat dist: emit %s: %s\n" % (type(e).__name__, e))
        return 1
    try:
        _pre_libs = codegen.extern_libs(os.path.abspath(src))
    except Exception:
        _pre_libs = []
    if _pre_libs and static and "--static" not in args:
        static = False
    stem = os.path.splitext(os.path.basename(src))[0]
    artifacts = []
    failed = False
    for t in targets:
        out_name = _dist_artifact_name(stem, t)
        out_path = os.path.join(out, out_name)
        try:
            argv = spec.argv(cfile, out_path, target=t, static=static)
        except CcError as e:
            sys.stderr.write("brat dist: %s\n" % e)
            failed = True
            continue
        try:
            _ffi_libs = codegen.extern_libs(os.path.abspath(src))
        except Exception:
            _ffi_libs = []
        _link_args = []
        _seen_libs = set()
        for _lib in _ffi_libs:
            if not _lib or _lib in _seen_libs:
                continue
            _seen_libs.add(_lib)
            if os.path.isabs(_lib) or os.path.exists(_lib):
                _link_args.append(_lib)
            else:
                _link_args.append("-l:" + _lib)
        r = subprocess.run(argv + ["-O2", "-I", HERE, "-lm"] + _link_args,
                           capture_output=True, text=True, timeout=300, env=os.environ)
        if r.returncode != 0:
            sys.stderr.write("brat dist: cc %s failed:\n%s\n"
                             % (t or "host", (r.stdout + r.stderr)[-2000:]))
            failed = True
            continue
        try:
            with open(out_path, "rb") as f:
                data = f.read()
        except OSError as e:
            sys.stderr.write("brat dist: hash %s: %s\n" % (out_path, e))
            failed = True
            continue
        artifacts.append({
            "target": t if t is not None else "host",
            "artifact": out_name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        })
    try:
        os.unlink(cfile)
    except OSError:
        pass
    manifest = {"source": os.path.abspath(src), "artifacts": artifacts}
    try:
        with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8",
                  newline="\n") as f:
            f.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    except OSError as e:
        sys.stderr.write("brat dist: write manifest: %s\n" % e)
        return 1
    if failed:
        sys.stderr.write("brat dist: some targets failed\n")
        return 1
    sys.stdout.write("dist ok %s (%d artifact(s))\n" % (out, len(artifacts)))
    return 0


def main(argv=None):
    argv = sys.argv if argv is None else argv
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    if len(argv) < 2:
        print_usage()
        return 2
    cmd = argv[1]
    rest = argv[2:]
    if cmd == "run":
        return do_run(rest)
    if cmd == "check":
        return do_check(rest)
    if cmd == "fmt":
        return do_fmt(rest)
    if cmd == "emit-c":
        return do_emit_c(rest)
    if cmd == "test":
        return do_test(rest)
    if cmd == "build":
        return do_build(rest)
    if cmd == "dist":
        return do_dist(rest)
    if cmd == "pkg":
        return do_pkg(rest)
    if cmd == "new":
        return do_new(rest)
    sys.stderr.write("brat: unknown command %r\n" % cmd)
    print_usage()
    return 2

if __name__ == "__main__":
    sys.exit(main())