# LEDGER — Rust pain points vs BRAT

Sources: rustc-dev-guide borrow-check (5 properties), Vale linear-aliasing, Matt Pocock codebase-design. Verified by runs, not words.

## Memory & ownership

| Rust pain | BRAT status | Proof |
|---|---|---|
| Lifetime annotations | None needed — inferred | `lang/examples/*.brat` has no `'a` |
| Borrow checker (E0502) | NLL-lite: borrow dies at last use | `own_good.brat` 0 errors |
| Use-after-move (E0382) | `move x` + `use of moved` | `own_move.brat` 2 errors |
| Move under borrow (E0505) | `cannot move while borrowed` (incl. via signatures) | `own_sig.brat` 1 error |
| `String` vs `&str` | One `str`, `ref` is a view | SPEC §2 |
| `unsafe` blocks | No `unsafe` keyword at all | parser doesn't know the word |
| Pin + self-referential structs | ROADMAP: regions; not supported yet | — |
| Double move | `use of moved` on repeat | `borrow.py: scan_moves` |
| Duplicate methods | `duplicate method T.m` at runtime and check | `borrow.py`, `runtime.py: hoist` |
| Dangling ref returns | Rejected by parser | `-> ref` → SyntaxError |

## Errors

| Rust pain | BRAT status | Proof |
|---|---|---|
| `?` only in Result-returning fns, unwrap panics | `?` everywhere, `or` defaults, `try/catch`, `is_*` | `result.brat` |
| anyhow/thiserror split | One `Ok/Err`, no libs | SPEC §5 |
| Debug-panic vs release-wrap (`/0`, overflow) | Always error: `/0` is RuntimeError | `runtime.py` |
| Infinite loops on bad input | Cur.step fails fast | fuzzer 2000/0 hangs |

## Types

| Rust pain | BRAT status | Proof |
|---|---|---|
| Traits + orphan rule + async traits | Minimal traits, conformance on both sides, no orphan rule (no crates) | `trait.brat` |
| GAT / associated types | ROADMAP | — |
| Generics monomorphization | Erasure (Java-style), duplicate `T` is error | `generic.brat` |
| Macros | None; methods+generics+UFCS cover 80% | documented decision |
| `dyn Trait` object safety | No trait objects (honestly v0.2) | SPEC |
| Integer zoo (u8..usize) | i64/f64/bool/str — simplicity as feature | SPEC §2 |

## Concurrency

| Rust pain | BRAT status | Proof |
|---|---|---|
| Async/sync split, Send/Sync/Pin, Tokio vs async-std | `spawn { }` + `join_all()` — real OS threads in both backends, no async keywords; thread error reaches join via TLS pending-err | `spawn.brat`, `crt.h: vspawn` |
| Data races | Partial: ref-escape in spawn is checker error; `let mut` rule applies inside `spawn`; shared mutable state is user's responsibility (move-only capture — roadmap v0.2) | `own_bad.brat`, `spawn.brat` |

## Tooling & ecosystem

| Rust pain | BRAT status | Proof |
|---|---|---|
| Slow compile | Interpreter instant; native via C emitter (~2 s) | `test_native.py` |
| Cargo weight, supply chain | `brat pkg` (URL + lock + sha256, vendor/) + `brat test`/`brat build`; LSP/registry roadmap | `cli.py`, `test_pkg.py` |
| Cryptic errors | Words + lines + snippets (`file:line: error`); match exhaustiveness; static islands in typer | `borrow.py: report`, `parser.py`, `typer.py` |
| Edition churn | No editions, compatibility by tests | this file |
| No `mod`/`extern`/FFI | Roadmap, SPEC doesn't lie | SPEC §9 |
| Privacy (`pub`, flat like C, checker catches) | `modapi/modapp/own_priv.brat` | `test_lang.py` |

## Speed

- **No LLVM backend.** `codegen_llvm.py` builds IR for 4 test functions only. Old "x6268" number was wrong. Removed.
- **Native C backend** (`codegen_c.py` + `crt.h`): 58/58 programs byte-identical to interpreter. Zero `#error` paths.
- **test_diff.py**: ~50 small programs, interpreter vs native, stdout + exit code. Caught i64 truncation, map key typing, `index_of` by bytes, `lines` split, `join` non-strings, container sort, parse_int/sum overflow, Unicode case.
- **Mono fast-path**: `i64`-annotated pure-integer functions compile to raw C `long` (floor-div like Python, wrap on overflow like Rust). No benchmarks yet — see `bench/README.md`.

## Known holes (honest)

- `move` doesn't invalidate the value at runtime; check is static and intra-function only.
- Channels, C-interop, `scope` not implemented (see `docs/design/ARENA.md`).
- LLVM backend is proof-of-concept.
- Ecosystem: `brat pkg` does URL + sha256 + vendor only. No registry.
- Tag values (Ok/Err/enum) refcount: interned names, args freed on rc=0 (no recursion into elements — by design to avoid UAF on aliases).
- `brat run` now gates arena programs (ownership checker runs first).
- `let mut x = y` makes x a mutable borrow of y in the checker; reassigning y while x is alive is rejected.

## Arena

- Steps 1–4 implemented. Tests: `test_arena_c.py` (runtime, ASan), `test_arena_diff.py` (both backends), `test_arena_escape.py` (escape rules), `test_arena_request.py` (request handler rollback on Err, memory limit, ASan).
- Bench: 3000 requests (600 failing). Without arena C build exceeds 16 MiB; with arena it fits. LeakSanitizer: 2.3 MB vs 25 MB.
- Known: tag values never released was fixed (interned names). `brat run` now gates arena programs.
- Chunk allocator: fixed 64 KiB chunks + fallback for large values + doubling.
- Checker: taint tracking for outer containers holding arena memory.
- Region analysis: full rewrite, region ids instead of name sets. All arena tests green.
