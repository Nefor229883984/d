# AGENTS.md — BRAT

## Project

BRAT is a small systems language with ownership checking, compiled to C.
Two backends: a Python interpreter and native C (`codegen_c.py` + `crt.h`).
Tests compare their output and exit codes byte-for-byte.

- Project root: `lang/`
- Engine: `lang/engine/` (parser, checker, interpreter, C codegen, runtime)
- Examples: `lang/examples/`
- Spec: `lang/spec/SPEC.md`
- Tests: `lang/tests/test_*.py`
- Docs: `docs/`

## Commands

```sh
python3 lang/engine/cli.py run lang/examples/hello.brat
python3 lang/engine/cli.py check lang/examples/own_bad.brat
python3 lang/engine/cli.py build lang/examples/hello.brat -o hello
python3 lang/tests/test_lang.py
python3 lang/tests/test_native.py
python3 lang/tests/test_diff.py
```

## Core rules

1. Honesty over percentages. No unsoundness claims. Known holes are listed in `docs/guide/ownership.md`.
2. No change without a run. Any change in `lang/engine/` runs at least `test_lang.py` + `test_native.py` + `test_diff.py`.
3. No slop. No speculative abstractions, no new files without need, no refactoring without request. Smallest diff that solves the task.
4. Two backends must agree. Anything that works in the interpreter must work in native and vice versa.
5. Do not change `crt.h` silently. ~130 functions on tagged union `Val`. Change both sides.
6. Do not lie about LLVM. `codegen_llvm.py` is a 4-function proof-of-concept, not a backend.
7. No generated files in the repo: `*.c`, `*.o`, `*.exe`, `*.zip`, `/tmp`, `/out`, `/build`.

## Architecture

- `parser.py` — parser (BRAT -> AST)
- `typer.py` — static type checks
- `borrow.py` — ownership checker
- `interp.py` / `runtime.py` — interpreter
- `codegen_c.py` — C codegen
- `crt.h` — C runtime
- `cli.py` — CLI: run/check/build/pkg/test

## Known limitations

- `move` has no runtime effect; check is static and intra-function.
- Channels, C-interop, `scope` not implemented (see `docs/design/ARENA.md`).
- LLVM backend is proof-of-concept.
- Ecosystem: `brat pkg` supports URL + sha256 + vendor only. No registry.

## Skills

- `ponytail` — simplest working solution.
- `grilling` — stress-test before a feature.
- `tdd` — red-green-refactor.
- `diagnosing-bugs` — reproduce first.
- `verification-before-completion` — evidence before claims.
- `code-review` — second eyes.
- `improve-codebase-architecture` — when design is bad.

## Red lines (do not touch without explicit request)

- `lang/engine/crt.h` — runtime, ~130 functions.
- `lang/spec/SPEC.md` — spec.
- `docs/project/LEDGER.md` — status ledger.
- `test_*.py` — do not weaken tests.

## Links

- `docs/README.md` — docs map.
- `docs/guide/ownership.md` — ownership model, known holes.
- `docs/guide/why.md` — why BRAT, where it loses to Rust.
- `docs/project/LEDGER.md` — Rust pain points vs BRAT.
- `docs/design/ARENA.md` — arenas.
- `bench/README.md` — benchmarks.
- `CONTRIBUTING.md` — repo rules.