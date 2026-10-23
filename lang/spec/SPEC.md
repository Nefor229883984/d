BRAT SPEC v0.1 — production language without Rust's pain

Coverage: `brat.py` — unified runtime. `front2.py` — historical prototype of match/for, merged into core 01.10.2026.

1. Goals

Safety = Rust (no UAF, no data race, no null), speed = C, ergonomics = Python/Go.
Rule: if in Rust you have to write `'a`, `PhantomData`, `Arc<Mutex<>>` — in BRAT this is inferred.

2. Types

```
i32, i64, u64, f64, bool, str, bytes
vec[T], map[K]V
Option[T] = Some(T) | None
Result[T,E] = Ok(T) | Err(E)
ref T — borrow (view), mut ref — mut-borrow
```

Literal: `let codes = [200, 404, 500]`, length `len(codes)`, index inside `for`.
String interpolation: `"{expr}"` — any expression parsed by the same parser (`"{len(v)}"`, `"{p.x}"`, `"{n + 1}"`, `"{m.get(k)}"`). Braces that do not contain exactly one expression remain text — therefore `"{\"a\": 1}"` works as a JSON template without escaping.
Strings are UTF-8: `len(s)` — code points (not bytes), `s[i]` — i-th code point, `chars()` — by code points, `slice(a, b)` — code point boundaries, identical in both backends. Escapes in literals: `\n \t \r \\ \" \' \0`.
map: `let m = {"a": 1}` — read `m["a"]`, write `m["a"] = 2` and `v[0] = x`, methods `m.get(k)` (value or None), `m.has(k)`, `m.keys()`, `m.del(k)`, `m.set(k, v)`. `len(m)` — number of keys, `for k in m` — traversal of keys in insertion order. Miss on key — error, not null. Keys str/int/bool/f64, typed in both backends (in C the key is stored as `Val`, comparison via `veq` — `m[1]` and `m["1"]` are different keys).
No `String vs &str`. There is `str` (ownership) + `ref str` (view). Conversion is automatic.
Copying: small types (`i32..f64,bool`) — Copy. Everything else — move by default.

3. Ownership without annotations (killer feature)

Model — Vale linear-aliasing (vale.dev/linear-aliasing-model): single ownership + moves, without borrow checker syntax. Checks — subset of rustc-dev-guide borrow-check: initialization, no double move, no move while borrowed, no access under mut-borrow.

- `let y = x` — borrow (as before). Calls also borrow, do not move.
- `move x` — annotation of ownership transfer, checked STATICALLY. Reuse — compile error `use of moved` (E0382), moving while borrowed — `cannot move while borrowed` (E0505). **By default there is no runtime effect:** the interpreter and the ordinary C build treat `move x` as an ordinary read of `x` and create a second live descriptor of the same buffer. Exception — the C backend under `brat build --move-rt`: there `move` invalidates the source (`vmove` → `V_MOVED`, see §11); the interpreter does not invalidate even in this mode. Therefore ownership currently rests mainly on the static checker, which is intra-functional and incomplete (see §11). Implementation of real `move` at runtime — v0.2, migration order in §12.
- Signatures carry ownership: `fn eat(s: move str)` takes ownership, `fn peek(s: ref str)` borrows for the duration of the call (NLL: the call borrow does not outlive the call). Without annotation a parameter is `ref`. Checks at the call site: passing to a `move`-parameter moves and is forbidden under a live borrow.
- Immutability by default (as in Rust): assignment (`=`, `+=`, `v[i] =`, `m[k] =`, `p.x =`) only in `let mut` or `mut`-parameter — otherwise `assign to immutable` with a line. Writing via a builtin mutator (`push`, `pop`, `sort`, `sort_by`, `ins`, `del`, `set`) is the same kind of write: `let v = []; v.push(1)` — error `cannot mutate immutable`. `const` — same prohibition always. `let mut x = y` — mut-borrow; live mut-borrow + any second — error `double mut borrow`. Passing to a `mut`-parameter requires a mut-place without live borrows and without duplicates in a single call (Law of Exclusivity). Borrows are isolated by if/while/try/spawn branches (NLL + scopes).
- `let mut` one writer OR N readers; duplicate `mut` — error.
- NLL-lite: a borrow dies at the last use of the borrower, not at the end of scope (liveness over direct blocks; through branches — conservative).
- `ref` from `return`/`spawn` — escape, error with name.

3.5 Static islands (typer)

`brat check` = borrow + typer. Functions with an annotated return (`-> i64/f64/bool/str/vec[..]/map`) are checked STATICALLY: expression types are inferred (literals, parameter annotations and `let x: i64`, call signatures), mismatch — error with snippet: `return: want i64, got str`, `arg 1 of `g`: want i64, got str`, `let `y`: annotated i64, got f64`, `assign to `a`: want i64, got str`. Top-level is also checked (calls to typed functions with explicit literals).
Unannotated — dynamic island, `dyn` compatible with everything; `Ok/Err/None` — tags, always compatible (error channel untyped until v0.2).
Rules: `int+int→int`, `float?_→float`, `str+T→str` (concatenation as in runtime), `str*int→str`, comparisons and `in` → `bool`, `/` over int → int (floor).
Lies in annotations are no longer decoration — parse.brat fixed honestly (`-> vec`).

4. Functions

```
fn route(req: Request) -> Response {
  let up = pick_upstream()
  return forward(up, req)?
}

fn add(a: i64, b: i64) -> i64 {
  return a + b
}
```

- `mut` only where needed: `fn inc(x: mut i64) -> i64`
- default parameters: `fn greet(name: str = "world") -> str` — suffix of parameters, skipping from the tail substitutes the default (computed at call); `fn f(a, b = 1)` after a default all following must be default. In native — for direct calls (dynamic — arity as before).
- generics v0.1 minimum: `fn first(v: vec[i64]) -> i64` (full generics in v0.2)

5. Errors — real values

`Ok(v)` / `Err(e)` / `Some(v)` / `None` — runtime tags. `?` unwraps `Ok`/`Some`, propagates `Err` from the function, bare values pass through. `or` — default only for `None` (not for `Err`). `unwrap(x)` — value or propagate `Err` or panic on `None`. `is_err(x)`, `is_none(x)` — predicates. Uncaught `Err` at top — `unhandled` + exit 1. Integer division — integer, `/0` — error, not zero.

`try/catch` catches ONLY errors: `?`-unwinding of `Err` and runtime errors (div by zero, index out of range, no key — in BOTH backends these are channel values, not exit). `catch e` binds `e` to the error payload (message string for runtime errors). `return`/`break`/`continue` through `try` pass freely — catch does NOT swallow them (return-unwind is a separate channel). Uncaught at top: `unhandled: ('err', msg)` in native, `RuntimeError: msg at line N` in interpreter, exit 1 in both.

```
let resp = probe(up)?
let port = parse_port(s) or 8080
try {
  forward(up, req)?
} catch e {
  log.info("fail {e}")
  return serve_default()
}
```

6. Control

```
if is_up(up) { route(up) } else { mark_down(up) }
while running { serve_once() }        // serve_once, check: ordinary fn
match code { 200 => ok(), _ => retry() }
for x in backends { check(x) }
for i in 0..n { print(i) }
```

`match` — desugars into an `if`-chain on equality, `_` last, duplicates — error. Guards: `Ok(v) if v > 100 => ...` — guard failure leads to the next arm. Bare name in pattern: lowercase — binding scrutin (`x => ...` — same as `_`, but named), Uppercase — nullary variant. A guarded arm is not counted as total in exhaustiveness (guard may fail — need `_` or a full arm). Exhaustiveness as in Rust: if all variant-arms belong to one enum declared in the same file, each variant must have a total arm (sub-patterns — bind or `_`) or a trailing `_` is needed — otherwise parse error `non-exhaustive match: missing blue in enum Color` with line number. bool — closed domain: `match b { true => .., false => .. }` is exhaustive without `_`; a single literal — `missing false`. Arm after catch-all — error `_ must be last`. Literal arms without `_` allowed (dynamic), mixed enums — outside check.
`for x in v` — native traversal of vec, `for i in a..b` — counter `[a, b)`. `break`/`continue` work in both; `continue` in range does not break the step. Return `-> ref` forbidden by parser (dangling); `-> move` and plain — ownership outward.

7. Concurrency without colors

```
spawn { check(up) }
join_all()
```

Interpreter: `spawn` executes the block in a real thread (OS pool), environment — snapshot of frames at spawn time; shared vec/map visible (GIL atomic on write, data races — user responsibility, checker v0.2). `join_all()` waits for all tasks of the generation and propagates the first error. End of program does an implicit `join_all` — no lost threads. C-backend: real OS threads (CreateThread/pthread). Spawn block is lifted into a closure (same capture machinery). Error unwinding — as values (TLS `verr_pending`/`vuwind`, no `setjmp`), so it is thread-local: an error in a thread reaches `join_all` and is propagated to the main thread. `?`/unwind inside a thread does not cross foreign stacks.

8. Telemetry — keywords

```
log.info("up {host} ok")
log.warn("slow {ms}ms")
trace.span("forward") { forward(up, req)? }
```

Export: stdout JSON + OTel OTLP (runtime v0.3). Format: `{ts, lvl, msg, trace_id, attrs}`.

9. Modules, FFI

`import lib` loads `lib.brat` next to the file: functions merge in, top level executes once (cycle protection). `borrow.py` walks imports itself and collects function names of all files. `pub fn` / `pub enum` — visible via import; without `pub` — private (checker catches, runtime registers all: privacy as in Python, but checked). Namespace is flat, as in C.

```
import net
```

`mod` — roadmap (not supported). `extern ["lib"] fn name(params, ..T) -> type;`, `extern ["lib"] { extern ... }` blocks, and `extern struct Name { field: type; ... }` are supported for C functions/structs; types: `i32`, `i64`, `f64`, `str`, `ptr`, `none`, and extern struct names. `..T` marks a typed variadic tail. Without `lib`, libc/main handle is used. Index: `v[0]` with bounds check. `len(v)` for vec and str. Methods via dot:
str: `split([sep])`, `lines()`, `trim()`, `lower()`, `upper()`,
  `starts_with(p)`, `ends_with(s)`, `contains(sub)`, `parse_int()`,
  `replace(a, b)`, `index_of(sub)` (index or None), `repeat(n)`,
  `split_once(sep)` (`[head, tail]` or None), `copy()`, `chars()`,
  `parse_float()` (`Ok`/`Err`).
vec: `join(sep)`, `push(x)` (length), `pop()`, `first()`/`last()` (None on
  empty), `sort()` (in-place, homogeneous), `rev()` (new), `ins(i, x)`,
  `del(i)` (value or error), `slice(a, b)`, `sum()` (0 on empty),
  `min()`/`max()` (None on empty), `enumerate()` (`[[i, x]...]`),
  `copy()` (shallow copy), `zip(v)`, `chunks(n)`.
map: `get(k)` (or None), `has(k)`, `keys()`, `del(k)`, `set(k, v)`,
  `items()` (`[[k, v]...]`), `vals()`, `copy()`.
Method precedence: user `impl`-methods ALWAYS above builtins (like inherent impls in Rust), identical in both backends. Operator `in`: `x in v`, `"k" in m` (key), `"sub" in s`. Duplicate literals in `match` — parse error. `defer expr` — LIFO; expr captures binding values at registration (like a closure), cleared on block exit. Assignments: `x += e`, `-=`, `*=`, `/=`, `%=` (incl. `v[i] += e`, `m[k] *= e`, `p.x += e`); multi-level write `a.b.c = v`. Destructuring: `let [a, b] = pair` (length checked at runtime, `_` skips slot). Structs: `Point { x: 1, y: 2 }` (type — Uppercase), access `p.x`, nesting `a.b.c`. Moving a struct `move p` invalidates fields too. Methods: `impl Point { fn sum(self) -> i64 { ... } }`, call `p.sum()`. `self` — first parameter, ordinary ownership. Traits: `trait Shout { fn loud(self) -> i64 }` — signatures only; `impl Shout for Point { ... }` must provide all methods, otherwise error `impl Shout for Point missing loud` (checked by both runtime and borrow). Trait objects (`let s: Shout`) — none, v0.2. Enums: `enum Res { Ok(v), Err(e) }` registers constructors; `Ok`/`Err`/`Some` from prelude — same tags. Empty variant — bare name (`Red`). Matching with binding: `Ok(v) => ...`, `None => ...`, duplicates — error. Nesting: `Has(Ok(v))`, `Has(_)`; bare name in sub-position — bind. `tagof(x)` returns the tag. Enum methods: `impl Color { fn code(self) -> i64 { match self { ... } } }`, call `g.code()` and `Red.code()` (nullary constructor + method). Duplicate methods in one `impl` — error on both sides. Generics by erasure (like Java): `fn first<T>(v) { return v[0] }` works for vec and str without monomorphization; duplicate `T` — error. Body checking is unified, turbofish not needed. Monomorphization — v0.2. Closures by copy-capture: `let f = fn(x) { return x + 1 }`, named for recursion `fn fib(n) {...}`, `v.map(f)` / `v.filter(f)` with global fns and inline closures. Capture — copy of bindings, heap shared. `ref` escape via closure return — v0.2 (same hole as spawn, documented). Files and strings for self-compilation: `read_file(path)` gives `Ok(text)`/`Err`, `write_file(path, text)` gives `Ok(n)/Err`, `str(x)` converts int/float/bool to string. Stage 1 done: `tok.brat` — mini-lexer in BRAT itself. Stage 2 done: `parse.brat` — parser + evaluator. Stage 3 done: `emit.brat` — C emitter `(2 + (3 * 4))` in BRAT itself. Stage 4 started: `bratc.brat` — FULL lexer of the language in BRAT itself, diff-test against Python lexer: 61/61 files byte-for-byte (`test_bratc.py`). `bratp.brat` — FULL parser of the language in BRAT itself (full grammar: fn/impl/trait/enum/match+guards/destructure/try/spawn/compound/map/struct/closure/generics/defaults), AST diff-test: 62/62 byte-for-byte (`test_bratp.py`). stdout/stderr of interpreter — always UTF-8 (like Go/Rust: bytes, no encoding failures). Next: checker in BRAT, then bootstrap (compiler compiles itself). Exit codes: `main` returns i64 in exit code. `const X = v` — immutable: assignment to `const` — checker error. `brat build f.brat -o f.exe` — single binary via C + zig cc. `brat pkg add <url>` — vendor/ + `brat.lock` with sha256; `brat pkg install` verifies hashes. `import` finds `vendor/` itself. Standard library in BRAT itself: `strlib`, `veclib` (21 asserts), `json` (esc/jstr/jarr/jobj, 7 asserts). Library code — `pub` only. Parser errors — always with a line (`stuck at 'x' at line 7`); end of file inside a construct — also an error, never hangs. `assert(cond)` / `assert(cond, msg)` — panic with message; `brat test` runs files and counts ok/FAIL by exit code. Parser recursion — limit 10000, fuzzer on 2000 inputs: 0 hangs. System builtins: `args()` (vec[str] of arguments), `exit(code)`, `eprint(...)`, `raise(x)` (Err-unwinding with payload x), `time_ms()`, `sleep_ms(n)`, `env_get(name)` (value or None), `re_match(s, pat)` — regex subset (literals, `.`, `*`, `+`, `?`, `[..]`, `[^..]`, ranges, `^`, `$`, `\d \D \w \W \s \S \n \t \r`, groups `( )` and alternation `|`), engine identical in both backends. Interpreter — backtracking over tree, native — set of states simulation (Pike VM), so patterns like `(a+)+b` do not explode exponentially. Not supported: `{n}` and POSIX classes `[[:alpha:]]`. Bad pattern — error `bad regex pattern`, not a silent literal. HoF over vec: `fold(init, f(acc,x))`, `any(f)`, `all(f)`, `find(f)` (or None), `sort_by(f)` (by key), `pos(x)` (index or None). map: `get_or(k, d)`. `&&`/`||` — short-circuit semantics (right side not evaluated) in both backends. JSON parser in BRAT itself: `import jsonlib; json_parse(s)?` gives value (None/bool/i64/f64/str/vec/map) or Err. Parser errors — always with a line (`stuck at 'x' at line 7`) and source snippet (`--> file:line` + line). Module errors marked `(module name)`. Runtime errors — also with a line: `RuntimeError: div by zero at line 3` (line of the operator where the error occurred, including inside functions). Checker prints Rust-style diagnostics: `file:line: error: msg` + snippet of the line below. `if let Ok(v) = e { } else { }` and `while let Some(x) = e { }` — desugar through the same mechanism as `match` (tags, bindings, `_` allowed).

10. Mono fast-path: C speed without annotation pain

A function whose ALL parameters are annotated `i64` (or ALL `f64`) and whose body is provably numeric (arithmetic, comparisons, `&&`/`||`, `if`/`while`/`forrange`, calls to such fast-functions of the same kind) compiles to raw C `long`/`double` without `Val` boxing — Rust/C speed on number crunching. Third kind — `i64v`: parameters `i64`/`vec[i64]` in any mix. vec arrives as `Vec *` directly: `v[i]` — element read without boxing (bounds + tag check), `v[i] = x` — raw write, `len(v)` — field `->len`, `for x in v` — raw loop over `items`. Element not `i64` at runtime — `vec elem not int`. Wrapper checks tags: unsuitable arguments go to the general path, interpreter semantics preserved 1:1. Division `/` — floor as in Python (not C truncation), `%` — sign of divisor; in f64-functions `/` and `%` over purely-integer subexpressions are forbidden (interpreter would treat them as integer — divergence unacceptable). `forrange` bounds — integers in both kinds. `i64` overflow — wraps like Rust release, in BOTH backends (interpreter holds no bignums: parse_int, sum and unary minus also wrap). Returning bool from a numeric fast-function — 1/0. Mixing: fast-functions are called from dynamic for free, dynamic from fast — via the general path (then the function is not fast). `print` works inside fast: arguments i64/f64/str-literals with interpolation `{x}`; bool-producing expressions (comparisons, `&&`, `!`) forbidden — otherwise 1/0 vs True/False.

Copy-types (`i32/i64/u64/f64/bool` by parameter annotation) are not borrowed by the checker: `let x = n; return x` on an annotated scalar — copy, not ref escape. Without annotation the type is not fixed (dynamic v0.1) — conservative borrow.

11. What is NOT in v0.1 (honestly for Habr)

- No full generics, traits, macros, async, borrow across threads
- Ownership checks — simplified (intra-functional), cross-file analysis in v0.2
- **Runtime ownership control — partial.** `move` now invalidates the source at runtime in both backends (`vmove` -> `V_MOVED`); scope drops and retain-on-alias still require `brat build --move-rt`. Memory: refcount (`BHead.rc`, `vrc_inc`/`vrc_dec`) plus `vdrop`; reassignment frees the old value. Cycle collection — v0.2. A checker error still means silent buffer aliasing, not a crash.
- **Checker is incomplete, but the fixed open case is closed.** Returning a bare parameter — `fn f(a) -> i64 { return a }` — is legal: `return <parameter>` returns a view on the caller's storage, which outlives the call. `test_ownership.py` green on all 87 cases (`a13`/`a14` — accept); corpus examples and both self-compilers are built on this. Escape of a local borrow is still an error (`own_bad.brat`).
- `spawn` does not protect from races: the frame is copied, writes to a mutable capture are lost. The checker rejects such writes; shared mutability across threads — user responsibility.
- Closures capture by copy; `ref` escape through a closure — known hole.
- `let` — Rust semantics (decision made, corpus migrated): `let v = [1,2]; v.push(3)` — error `cannot mutate immutable`, need `let mut v`. Writing via builtin mutator (`push`/`sort`/`set`/`del`/`ins`/`sort_by`) counts as a write and requires a mut-place.

This is exactly what gives speed: v0.1 can be learned in an evening.

12. Migration order to real ownership

Do not change without full suite runs. Mandatory order — otherwise current "silent aliasing" turns into real use-after-free:

1. `crt.h`: add tag `V_MOVED` to `VTag`. Behavior unchanged.
2. `runtime.py`: introduce sentinel for "dead" binding next to `_MISS` (`runtime.py:30`), check in `find` (`runtime.py:40-44`) and invalidation in `move` node (`runtime.py:101-102`). First behind flag `BRAT_MOVE=0` by default — to run corpus in both modes and get the delta.
3. Restorations (`let`, `set`, loop iterators `foriter`/`forrange`, `try`/`catch` bindings) must clear the mark: otherwise already-accepted code like `let y = move x` inside `for` breaks.
4. Paths (`move p.x`, `move cfg.net.host`) require threading the path through `var`/`field`/`idx` in `ev` and `grow`. Without this the rule for fields doesn't work at all.
5. `codegen_c.py`: invalidate source; sync with `crt.h`.
6. **Only after this** — memory freeing in `crt.h`. Free first, then move — that's use-after-free.
7. And only after a clean corpus run is the flag enabled by default.

Ownership binding at call site (`fn f(s: move str)`) is fundamentally irrecoverable at runtime: it's static analysis, it stays with `borrow.py`.
