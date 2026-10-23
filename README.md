# BRAT

Small systems language with ownership checking, compiled to C. Errors are values,
`try/catch` catches them without exceptions, `log` and `trace` are syntax.

Status: v0.1, experimental. Programs run in two backends (Python interpreter and C);
tests compare their output and exit codes byte-for-byte.

## What works

- `fn`, `let`, `mut`, `if`, `while`, `match`, `for`, `vec`, `map`, structs, variants.
- `?`, `or`, `try/catch`, `defer`.
- `log.info`, `trace.span` without imports.
- Ownership checking (`move`, borrows) inside functions. Errors include file, line, hint.
- Region analysis for arenas: arena memory cannot escape its block (return, assign, store, pass).
- Arenas: `arena { ... }` resets memory on exit; `own(x)` copies out. Escape rules checked.
- Native binary: `brat build`, no runtime, no install on target.
- `brat build --mem 16M` bakes a hard address-space limit into the binary.

## What doesn't work yet

- `move` doesn't invalidate at runtime; check is static and intra-function.
- Channels, C-interop, `scope` not implemented (see `docs/design/ARENA.md`).
- LLVM backend is proof-of-concept; tests skip without `llvmlite`.

## Quick start

Interpreter:
    python3 lang/engine/cli.py run lang/examples/hello.brat

Check without running:
    python3 lang/engine/cli.py check lang/examples/own_bad.brat

Native binary:
    python3 lang/engine/cli.py build lang/examples/hello.brat -o hello

## Structure

- `lang/engine` — parser, checker, interpreter, C codegen, runtime `crt.h`.
- `lang/examples` — working programs, both backends tested.
- `lang/spec/SPEC.md` — v0.1 spec.
- `lang/tests` — `test_lang.py`, `test_native.py`, `test_diff.py`, etc.
- `bench` — benchmarks, every number reproducible.
- `docs` — docs, index in `docs/README.md`.

## Tests

    python3 lang/tests/test_lang.py

Full suite runs each `lang/tests/test_*.py` separately. ~3 min on one core.

License: Apache-2.0, see LICENSE.
