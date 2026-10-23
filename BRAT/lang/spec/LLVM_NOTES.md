LLVM_NOTES

Construct map (planned, not yet implemented)

- `let x = <i64>` -> `%x = alloca i64` + `store i64 <val>, ptr %x`
- int literal -> `i64` constant
- `a + b` (i64) -> `add i64 %a, %b`
- `if c { } else { }` -> `icmp` + `br i1` + two blocks + `phi`
- `while c { }` -> `br` loop header with `icmp` + body + latch `br`
- `fn add(a: i64, b: i64) -> i64 { return a + b }` -> `define i64 @add(i64 %a, i64 %b)` with `ret i64`

Probes (2026-10-01, host)

- `python --version`: Python 3.14.7
- `sys.version`: 3.14.7 MSC v.1944 64 bit (AMD64)
- `pip list`: pip 26.2.1, no `llvmlite`, no `llvm` entry
- `python -c "import llvmlite"`: ModuleNotFoundError, absent
- `Get-Command lli/clang/gcc`: none found on PATH

Spike result

- `compiler\llvm_spike.py` prints `NO_LLVM` plus the exact IR string it would emit.
- The IR string was not assembled, verified, or executed on this host.

What blocks native codegen

1. No `llvmlite` installed, so no IR construction or binding verification available.
2. No `lli`/`llvm-as`/C compiler on PATH, so emitted IR/C cannot be executed here.
3. `brat.py to_c` is a stub (emits `long x = 0;` and `/* fn */`, drops values and bodies).
4. Interpreter is dynamically typed (int/float/str/bool, `+` concatenates strings, `grow` interpolation, dynamic `print`/`log.*`, `or`/`?`/`try`/`spawn`); no static type lowering exists yet.
