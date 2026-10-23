# Why BRAT

## Short answer

Rust solves ownership at the cost of annotations and rules you must remember.
BRAT solves the same problem differently: no lifetime annotations at all, and
checking that is incomplete but honestly admitted. Errors are values, not panics.
Telemetry is syntax, not a dependency.

This is v0.1. Below: where this trade wins, and — in a separate section —
where it loses to Rust. Read the losing section first if you think about
replacing Rust in prod.

## What BRAT is today, without marketing

- **Zero lifetime annotations.** No `'a`, no elision rules, no HRTB, no
  `PhantomData`. `lang/examples/*.brat` has zero lifetimes (checked by grep).
  Instead the checker counts **paths**: `x`, `x.f`, `x.f.g`, `x[i]` (`x.*`).
- **Intra-function linear alias checker.** Closer to Vale linear-aliasing than
  rustc: single ownership + moves, no borrow-checker syntax. One function at a
  time. Guarantees are intra-function; holes are listed with repros in
  `docs/guide/ownership.md`.
- **Errors as values.** `Ok`/`Err`/`Some`/`None` are runtime tags. `?` unwraps
  `Ok`/`Some` and propagates `Err`. `or` defaults only for `None` (not `Err`:
  `Err("x") or 5` prints `('err', 'x')`). `unwrap` propagates or errors.
  `try/catch` catches `Err` and runtime errors (`/0`, out of bounds, missing
  key) in **both** backends — error-channel values, not `exit`. Unhandled `Err`
  at top: `unhandled: ('err', 'bad')`, exit 1.
- **Telemetry as syntax.** `log.info`, `log.warn`, `log.error`, `trace.span` are
  AST calls, not SDK. `{x}` interpolation. JSON on stdout:
  `{"ts":..., "lvl":"INFO", "msg":"..."}` for logs, `{"ts":..., "span":[...]}`
  for traces.
- **Working C backend.** `codegen_c.py` + `crt.h`: ~130 runtime functions,
  tagged `Val` union (`V_NIL`/`V_BOOL`/`V_INT`/`V_FLOAT`/`V_STR`/`V_VEC`/
  `V_STRUCT`/`V_TAG`), error unwinding as values (TLS `verr_pending`/`vuwind`,
  no `setjmp`), `spawn` via `CreateThread`/`pthread_create` with thread-local
  unwinding. 58/58 programs give byte-identical stdout and exit code (only `"ts"`
  field and CRLF normalized). This is a **correctness** claim, not speed.
- **No `unsafe` at all.** Parser doesn't know the word. `-> ref` is SyntaxError
  (no dangling returns). `extern`/`mod` not supported: no FFI, no crates, no
  orphan rule.

## Against Rust

### Where BRAT wins

- **Zero lifetime annotations.** Rust's `'a`, elision, HRTB force `fn longest<'a>(...)`.
  BRAT has none. Inference is a language rule, not the programmer's job.
- **Errors need no Result hierarchy.** Rust makes you pick `anyhow`/`thiserror`/
  `Box<dyn Error>`. BRAT has one `Ok`/`Err`, `?` works everywhere, `unwrap` is
  not a panic, `/0` is always an error (not wrap-in-release + panic-in-debug).
- **Telemetry without deps.** Rust needs `tracing` + `opentelemetry` + macros.
  Here `log.info("...")` and `trace.span` are AST-visible calls.
- **No async colors.** `spawn { }` + `join_all()`, OS threads in both backends,
  no `async`/`await`/`Future`/`Pin`/`Send`/`Sync`. Caveat: `spawn` doesn't
  protect against races.
- **No integer zoo.** `i64`, `f64`, `bool`, `str`. No `u8..usize`, no
  `NonZeroU32`, no `wrapping_add`. Integer division is always integer
  (floor, Python-style).

### Where BRAT loses to Rust

This is the most important section. No marketing.

- **Ecosystem size.** Main loss, incomparable. Rust has crates.io (hundreds of
  thousands), `cargo` for deps, publish, versions, vendoring, audit. BRAT has
  `brat pkg`: download a file by URL, put it in `vendor/`, write sha256 to
  `brat.lock`. No registry, search, versions, transitive deps, feature flags,
  LSP, IDE support beyond one `tmLanguage.json`, no CI integration. For code
  with dozens of deps, BRAT is unusable today. Not "when they add it" — the
  infrastructure doesn't exist.
- **Formal soundness history.** Rust has validated core (`rustc` + MIR + NLL),
  Miri, Creak, years of CVEs fixed in the compiler itself. BRAT has none: checker
  is intra-function and incomplete. Its own suite is green on 87 cases, but that's
  known cases, not a soundness proof. Nobody proved BRAT has no use-after-free.
  Known holes are listed and reproducible — see `docs/guide/ownership.md`.
- **CPU-bound throughput.** Interpreter is tree-walking Python with GIL. C
  backend removes interpreter overhead but is still C over a tagged union, not
  compiled machine code with an optimizer. Exception: mono fast-path — functions
  with all `i64` (or all `f64`, or `i64`/`vec[i64]`) params and provably numeric
  bodies compile to raw C `long`/`double` without `Val` boxing. No benchmarks
  for that path — see `bench/README.md`. Rust wins on number-crunching; BRAT
  doesn't contest it.
- **Years of production hardening.** Rust is a decade in production. Every
  `unsafe` CVE, every compiler segfault, every compiler bug is a lesson. BRAT has
  none of that history.
- **Tooling.** Rust has rustfmt, clippy, rust-analyzer, miri, cargo. BRAT has
  `brat fmt` (basic), `brat check`, no clippy-equivalent, no LSP, no debugger,
  no profiler integration. `brat fmt` reformats; nothing else.
- **Community and hiring.** Rust has a community, conferences, jobs. BRAT has
  one person and a repo.

## Against Go / Zig / OCaml / Swift

- **Go**: BRAT is slower to compile? No — Go compiles fast, BRAT interprets
  instantly. Go has GC, BRAT has arenas + refcount. Go has goroutines, BRAT has
  `spawn` (OS threads). Go's simplicity is the model; BRAT copies it (unified
  CLI, few types).
- **Zig**: comptime, C-interop out of the box. BRAT has no FFI yet (roadmap).
  Zig's simplicity is the model.
- **OCaml**: fast GC, pattern matching. BRAT has match but no exhaustiveness
  proof (heuristic).
- **Swift**: ARC, value types. BRAT has arenas + refcount.

## Honest summary

BRAT is an experiment in "ownership without annotations." It works for small
programs, two backends agree, arenas reclaim memory. It is not a Rust replacement
for production. It is a bet that a smaller, simpler language with honest limits
can be useful for a niche.
