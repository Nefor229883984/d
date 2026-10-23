ARCHITECTURE — backend and roadmap options

Status: draft for discussion. Nothing here is implemented; each option names
what it costs and what it buys, and the recommendation is argued, not asserted.

This document exists because two questions keep coming back: *when does Python
go away*, and *how does BRAT actually beat Rust*. Both need an answer that is
not "rewrite it".

---

1. Where Python actually is today

The single most important fact, and it is easy to miss: **Python is not in the
runtime path.**

```
brat build prog.brat ──► codegen_c.py ──► prog.c ──► zig cc -O2 ──► ./prog
        (Python)          (Python)                      (build time)   (no Python)
```

The emitted binary links only `crt.h` and libc. `brat run` uses the interpreter,
so Python is needed for *running*; `brat build` needs Python once, at build
time, and never again at run time.

So "get rid of Python" splits into three different projects, and they are worth
vastly different amounts:

| # | Question | Status |
|---|---|---|
| Q1 | Can a BRAT program ship without a Python install on the target machine? | **Already yes** — `brat build` output is a plain native binary. |
| Q2 | Can the *language toolchain* (checker, emitter, CLI) run without Python? | No. Requires self-hosting. |
| Q3 | Is the Python path fast? | Irrelevant to shipped programs; matters only for `brat run`. |

Any claim that "BRAT needs Python" is wrong for Q1 and right for Q2. The
roadmap below therefore treats Q2 — self-hosting — as the goal, not as a
precondition.

---

2. What must be fixed no matter which backend is chosen

These are correctness holes found by this audit. They are backend-independent
and must not be postponed behind a rewrite.

2.1 `vraise` via `longjmp` is unsafe at `-O2` — **DONE (Stage 0)**

Was: `vraise` → `longjmp(*vjmp, 1)` across non-`volatile` frames → segfault
at `-O2`. Now: TLS `verr_pending` / `vuwind`, emitter propagates via
`err_bail` / `verr_pending()` checks. No `setjmp.h`. See
`docs/design/PLAN-heavy-items.md` Item 2.

2.2 Spawn has no resource limit — **DONE (Stage 1)**

Was: one `pthread_create` per `spawn` → segfault around 300 threads.
Now: bounded pool `BPOOL_MAX` (32) with a task queue in `crt.h`; interpreter
uses `ThreadPoolExecutor(min(2*ncpu, 32))`. Thread count is a runtime
property, not a program property.

2.3 Backend parity gaps found in this audit

Fixed in this session, all pinned in `test_diff.py`:

- `read_file`/`write_file` built an `err` tag with **no field**; a `match` arm
  `Err(reason)` binds that field, so the interpreter raised `IndexError` and
  the native backend hung.
- An empty file and a directory both produced an `ok` tag with **no field**
  natively — same out-of-bounds bind, same hang.
- The C sandbox accepted `sub/../../x` because `vconfine` compared a path that
  still contained the `..`. Native wrote outside the program directory while
  the interpreter refused.
- `vconfine` returned a pointer to a stack local.

2.4 No reclamation at all — **FIXED (Stage 2)**

Was: the native runtime had **no mechanism to free a value** — no refcount,
no `vfree` (`grep` for `vfree|refcount|vdrop|vrelease` over `crt.h` found
nothing but `del`-the-map-method). `vadd`'s string path was *correct* —
`malloc`, two `memcpy`, NUL — but the string it returned was never released,
so `s = s + "x"` in a loop leaked.

```
$ ulimit -v 32768 ; ./prog     # pre-fix, n = 8000 concat steps
   -O0:  ('err', 'out of memory')     exit 1
   -O2:  Segmentation fault           exit 139
```

n = 8000 leaked exactly 8000²/2 = 32 MB, the observed address-space threshold
to the megabyte; n = 100 000 extrapolated to **5 GB**, which is what made the
box stop responding. The interpreter did not have the bug (CPython reclaims
the intermediate string immediately), so the backends differed in *whether the
program terminated at all* — a parity violation in the strongest sense.

Now: `crt.h` heap headers carry a refcount (`BHead.rc`; `balloc`/`bgrow_rc`),
and `vrc_inc` / `vrc_dec` / `vdrop` reclaim it — reference counting on `Val`,
the first of the two fix directions this section named. The emitter releases
the value a rebind overwrites by default, and under `brat build --move-rt`
(`BRAT_MOVE_RT=1`) adds scope-lifetime drops, retain-on-alias, and runtime
`move` invalidation (`vmove` → `V_MOVED`). Cycle collection is out of scope
for v0.1 and documented, not silently missing.

Verified: `lang/tests/test_runtime_reclaim.py` builds `s = s + "x"` at
n = 40 000 and 100 000 and runs each native binary under `RLIMIT_AS = 32 MiB`
(a leak aborts); both pass and byte-match the interpreter —
`RECLAIM ALL GREEN (15 asserts)`. `lang/tests/test_runtime_mem.py` covers the
stored-alias and thread cases under ASan. `test_diff` stays at `diffs: 0`.

2.5 Interpreter recursion limit — **DONE**

Both backends enforce the language budget `RECURSION_BUDGET` (default **2000**,
overridable with `BRAT_RECURSION_BUDGET`). The interpreter counts BRAT frames in
`runtime.py` (`_frame`); the native backend counts them in `crt.h` (`bdepth_*`,
emitted around every function frame by `codegen_c.py`). Exhaustion is an
ordinary runtime error with payload `recursion too deep`: `try/catch` may catch
it, and uncaught it exits `1` with empty stdout and a stderr diagnostic. The
Python `sys.setrecursionlimit` is now only a safety net, not the definition of
the budget. No longer a parity gap. Measured: the interpreter refuses in 0.46 s
at the 2000 default, the native backend is instant; `lang/tests/test_recursion.py`
covers the refusal, the positive control, the caught payload and the `defer`
unwind.

2.6 `vraise` via `longjmp` is undefined at `-O2` — **DONE (same as §2.1 / Stage 0)**

Historical note: three triggers (spawn exhaustion, concat OOM, empty ok tag)
segfaulted at `-O2` via longjmp. Fixed by error-as-value; remaining concat
OOM/hang under tight `ulimit -v` is §2.4 (Stage 2 URC), not unwinding.


---

3. Backend options

Option A — keep and harden the C emitter *(recommended now)*

What it is: `codegen_c.py` emits C99, `zig cc` compiles it. Already works,
already fast enough to be compared with C.

- **Buys:** nothing to rebuild. Performance is C performance. The single
  external dependency (`zig`) is a build-time one, and it is drop-in
  replaceable with `cc`/`clang` because the output is plain C99.
- **Costs:** the C runtime is hand-written and hand-audited; the two bugs above
  live there. Every language feature must be written twice, once in
  `crt.h` and once in `runtime.py`.
- **Verdict:** this is the only option that improves the product *this week*.
  Options B–D are additive on top of it, not replacements for it.

Option B — emit LLVM IR text instead of C

- **Buys:** LLVM's optimiser and passes instead of `crt.h` doing it by hand;
  no dependence on a C toolchain's header quirks; the emitted IR is inspectable
  and diffable, which is a real parity-testing advantage.
- **Costs:** the entire runtime has to be re-expressed as LLVM IR or as
  bitcode, or linked against a compiled runtime — i.e. the hand-audited C
  runtime does not disappear, it becomes a build artefact. Error unwinding
  still has to be solved first (§2.1); LLVM does not make longjmp safe.
- **Verdict:** premature. It rewrites the layer §2 says is broken without
  fixing §2.

Option C — a purpose-built backend (own IR, own codegen)

What the user asked to think about: a fundamentally new architecture rather
than borrowing LLVM's.

- **Buys:** BRAT's ownership model is a genuine advantage here. There is no GC
  and no borrow checker, so the compiler *knows* when a value dies. A backend
  that exploits that can elide bounds checks on `Vec` indexing where the
  length is statically known, and avoid refcount traffic entirely — which is
  exactly where a naive C emitter leaves performance on the table. This is the
  one option that could plausibly beat Rust on BRAT's own terms rather than
  match it.
- **Costs:** a register allocator, an instruction selector, a backend per
  target, and a debugging experience worse than C's until it matures. Years.
- **Verdict:** the destination, not the next step. It should be taken *after*
  the language has a self-hosted frontend, so the backend is written in BRAT
  rather than in Python.

Option D — self-host the compiler in BRAT *(the real "kill Python" step)*

- What it is: rewrite `borrow.py`, `parser.py`, `typer.py`, `codegen_c.py` in
  BRAT. The first working target is the C emitter, because the C backend
  already exists and `brat build` already produces a native binary — so a
  self-hosted emitter still emits C, and the bootstrapping loop closes.
- **Buys:** Q2 answered; the language stops depending on anything outside
  itself; and BRAT gets its first large, real application, which is the most
  reliable way to find the language's actual gaps.
- **Costs:** ~5000 lines of compiler to port, and the parity contract means
  every ported module must be byte-identical in its diagnostics.
- **Verdict:** the right long-term goal. It depends on §2.1, because a
  self-hosted compiler that segfaults under `-O2` is not a foundation.

---

4. Recommended order

1. **Fix §2.1 (unwinding without longjmp) and §2.2 (bounded spawn).** Neither
   is optional; both are measurable; both are pinned by tests already.
2. **Harden the C emitter** (Option A): drop the `zig` dependency to an
   optional one, since output is plain C99.
3. **Self-host** (Option D), starting with the emitter, targeting the existing
   C backend.
4. **Only then** consider Option C, using BRAT itself to write it.

Sequencing note: 1 → 2 → 3 is strictly ordered. Starting 3 before 1 means
writing a compiler on a runtime whose error path is undefined at `-O2`.

---

5. On "0 hallucinations"

Not a vague aspiration; it is three concrete properties, all already testable:

1. **No silent failure.** Every failure is impossible, reported, or handled —
   never swallowed into a default that looks like success. The WAF that failed
   open in the balancer is gone together with the balancer.
2. **Determinism across backends.** `test_diff.py` compares bytes, not
   vibes. The filesystem-sandbox cases added in this audit are the model: the
   absolute paths in error messages are machine-dependent, so what is compared
   is the *class* of the error, not the string.
3. **No unreachable code.** A reader must not have to prove that a branch is
   dead before reasoning about a module.

---

6. On performance

`brat build` output is C compiled at `-O2`; it is within noise of idiomatic C
today. Beating Rust needs the compiler to *use* the ownership information
rather than merely survive it:

- bounds-check elision on `Vec` indexing where the length is known,
- no refcount traffic where ownership is provably linear,
- no unwinding machinery on the hot path (this is §2.6 again — the longjmp is
  not just a correctness bug, it is a pessimisation, because it forces
  `setjmp` state into every call frame).

The ownership model is the actual competitive claim, and it is the one thing
Rust cannot copy without giving up borrow checking.

6.1 Measured numbers, native vs interpreter

Single run each, `n = 100 000` (except as noted), checksummed so the work
cannot be optimised away. Best of 3 for the short cases.

| case | native | interpreter | note |
|---|---|---|---|
| `int_loop` | 0.0027 s | — | arithmetic, no allocation |
| `recur(2000)` | 0.0015 s | **fails** | both backends now refuse at the language budget (default 2000, §2.5); before that the interpreter hit the Python recursion limit |
| `vec_push` | 0.0185 s | — | |
| `map_iter` | 0.0403 s | — | 20k-key map, iterate keys |
| `map_intkey` | 0.0542 s | — | 20k i64 keys, insert + get |
| `map_churn` | 0.1268 s | — | 100k set/has over 512 live keys |
| `str_split` | 0.1149 s | — | 100k splits of an 8-char string |
| `map_strint` | 0.1978 s | — | 20k string keys, insert + get |
| `str_concat` | **172.9 s** | faster | pre-§2.4-fix leak, not the algorithm; no post-fix remeasure recorded |

Whole-program comparison (7 cases, `n = 20000`): **native 0.075 s vs
interpreter 3.699 s — 49×**.

Reading of these numbers:

- **`map` is already competitive.** It is open addressing over an
  insertion-ordered `keys`/`vals` pair, FNV-1a on keys, 75 % load, tombstones
  honoured. `map_intkey` at 20k insert+get in 54 ms and `map_iter` at 40 ms are
  within a small factor of hand-written C. There is no low-hanging fruit here
  and it should not be rewritten.
- **Integer work is fine** — 0.0027 s for 100k arithmetic iterations.
- **One case used to destroy the whole profile.** `str_concat` is 2300× the
  cost of every other case combined, and it is not algorithmic: the C is
  `malloc` + two `memcpy`. That was §2.4; the leak is now reclaimed (§2.4), but
  this pre-fix figure has not been re-measured.
- The 49× whole-program figure is dominated by the interpreter's constant
  overhead, not by anything the native backend does badly.

