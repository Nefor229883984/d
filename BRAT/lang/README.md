lang — BRAT language

Engine (`engine/`)

| File | Role |
|------|------|
| `brat.py` | CLI entry (thin shim, run + transpile-c) |
| `brat/lexer.py` | Tokens: `lex`, `Cur` |
| `brat/parser.py` | Parser: `parse_prog`, match/for desugar |
| `brat/runtime.py` | Tree-walk eval, ownership runtime, `run`, `to_c` |
| `borrow.py` | Ownership checker, `check(prog, extra)` |
| `codegen_c.py` | Full C99 emitter |
| `cli.py` | `brat run/check/fmt/emit-c` |
| `codegen_llvm.py` | LLVM MCJIT proof (`add`, `fib` native) |

Examples (`examples/`)

`hello.brat`, `control.brat` (match/for), `result.brat` + `lib.brat` (import, Ok/Err),
`span.brat` (trace.span block + интерполяция пути), `own_good.brat`,
`own_bad.brat` (must fail with 4 errors).

Tests (`tests/`)

`test_lang.py` — single entry: examples, ownership/typer counts, and the
codegen_c/LLVM/bratc/bratp/bratb/language-edges sub-suites. `test_diff.py` and
`test_native.py` compare the two backends directly; `test_ownership.py` runs the
checker against its reject/accept corpus.

Spec (`spec/SPEC.md`)

Spec follows code. New syntax lands with a runnable example first.
