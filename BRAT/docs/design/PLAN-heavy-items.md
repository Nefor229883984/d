Execution plan: the two heavy items

Written after the 2026-10-14 night session so the next run starts executing
instead of re-deriving. Each item states the failure, the chosen fix, why the
cheaper alternative was rejected, and how to know it worked. Measured numbers
only — every figure below was observed, not estimated.

---

Item 1 — the native backend leaks every string (§2.4) — **DONE (Stage 2)**

The failure, measured

`s = s + "x"` in a loop:

| n | address space consumed |
|---|---|
| 8000 | 32 MB (8000²/2) — matches `ulimit -v` threshold to the megabyte |
| 100 000 | ≈ 5 GB → 172 s wall, machine unresponsive on 3.7 GB RAM |

No reclamation exists at all: `grep -n "vfree\|refcount\|vdrop\|vrelease"`
over `crt.h` returns nothing but `vdel_at` (a map method). The interpreter is
immune because CPython reclaims the intermediate immediately, so the two
backends differ not in speed but in *whether the program finishes*.

Fix shipped — reference counting on `Val` (Stage 2)

Landed: `crt.h` heap headers gained a refcount (`BHead.rc` via
`balloc`/`bgrow_rc`); `vrc_inc` / `vrc_dec` / `vdrop` reclaim it. The emitter
releases a rebound binding's old value by default, and `brat build --move-rt`
(`BRAT_MOVE_RT=1`) adds scope-lifetime drops, retain-on-alias, and `move`
invalidation (`vmove` → `V_MOVED`). This is plain refcounting, not the URC
scheme below — the shape below is what was designed, the shipped code is the
subset the emitter can prove today. Cycle collection is documented as out of
scope for v0.1, not silently missing.

Verified: `lang/tests/test_runtime_reclaim.py` — `s = s + "x"` at n = 40 000
and 100 000, each native binary run under `RLIMIT_AS = 32 MiB` (the pre-fix
leak aborts there); `RECLAIM ALL GREEN (15 asserts)`. `test_diff` `diffs: 0`.

Fix chosen — URC-style deferred reference counting

Naive RC costs an increment on every pointer assignment, which is why it is
usually a net loss for tight loops. Blackburn & McKinley's *Ulterior Reference
Counting* (OOPSLA 2003, `cs.utexas.edu/~mckinley/papers/urc-oopsla-2003.pdf`)
removes that cost by **deferring and coalescing** count updates: they are applied
lazily at points where the compiler can prove the old value is dead, rather than
on every store. That is exactly the profile BRAT needs — the leak is on the hot
path (`+` on strings), so the fix must not add a branch to it.

Shape:

1. `Val` gains a `unsigned long rc` for heap-backed values (strings, vecs, maps,
   tags). Only four kinds are refcounted; ints, floats, bools and nil stay
   immediate.
2. `vstr`, `vstrn`, `vvec`, `vmap`, `vtagv` start with `rc = 1`.
3. Assignment and parameter passing go through `vreten`/`vrel`: retain before a
   new reference is stored, release when one is overwritten.
4. Updates are **coalesced**: in `a = a + b`, the release of the old `a` is
   folded into the same operation rather than being a separate write.
5. Cycle collection is out of scope for v0.1 — document the limitation rather
   than shipping a partial collector. BRAT's ownership model already forbids the
   aliasing patterns that create the common cycles, so this is a real gap, not
   an excuse.

Rejected alternatives

- **Free the LHS of `+` unconditionally.** Unsafe: `let a = x; x = x + "s"`
  leaves `a` dangling. BRAT permits that aliasing today.
- **Arena per statement.** Does not fix the loop case — the arena grows with the
  loop.
- **Tracing GC.** Introduces pauses, which is the one property the ownership
  model exists to avoid. Also needs a write barrier on every container write.

Verification

1. `s = s + "x"` at n = 8000 must run under `ulimit -v 32768` — the threshold
   that currently fits to the megabyte.
2. n = 100 000 must finish in seconds, not 172 s, with flat RSS.
3. `test_diff` must stay at `diffs: 0` — this is the gate that matters, because
   every string operation changes byte output if a lifetime is wrong.

Status: (1)/(2) met by `test_runtime_reclaim.py` (n = 40 000 and 100 000 under
a 32 MiB `RLIMIT_AS`); (3) confirmed, `diffs: 0`.

Ordering constraint

Do item 2 (unwinding) first, or at least first. The leak's failure mode routes
through the error path, and that path is currently the least trustworthy thing in
the runtime. Fixing the leak before the error path means debugging blind.

---

Item 2 — `vraise` through `longjmp` is undefined at `-O2` (§2.6) — **DONE (Stage 0)**

Was

Three independent triggers segfaulted at `-O2` and worked at `-O0` because
`vraise` did `longjmp(*vjmp, 1)` across frames with non-`volatile` locals.

Fix shipped

Error-as-value end-to-end:

- `crt.h`: no `#include <setjmp.h>`, no `jmp_buf` / `vjmp`. `vraise` /
  `vraisev` set TLS `_verr_pending` + `vuwind`; callers `return vunwind`.
- `codegen_c.py`: `err_bail` / `err_check_line` / `emit_err_check` after
  raises and after calls that can raise; try/catch via `err_goto` +
  `verr_take`; top-level unhandled prints and `return 1`.
- Tests: `test_codegen_c` asserts no setjmp and presence of `verr_pending`;
  `test_native` / `test_diff` green at `-O2` (raise + try/catch parity).

Item 1 (string leak / URC) is **DONE (Stage 2)** — see the "Fix shipped"
section above. Tight `ulimit -v` hangs on concat loops were symptoms of item 1,
not a leftover longjmp bug.

---

Item 3 — smaller, still real

| Item | Status |
|---|---|
| Shared containers are unsynchronised across threads; the interpreter survives only on the GIL. `vec_push` on a shared `Vec` from two threads loses writes (3/200 observed). | **Open.** LEDGER already defers it to "user responsibility, roadmap v0.2", which makes it silent rather than safe. |
| The interpreter is bounded by Python's `sys.setrecursionlimit`; the native backend is not. | **Fixed** — the budget is a language number: both backends count BRAT frames and refuse at `RECURSION_BUDGET` (default 2000, `BRAT_RECURSION_BUDGET`). The Python limit is now only a safety net. (Earlier `ced78cb` note claiming both backends agreed to depth 100 000 was not backed by a counter in C.) |
| `test_diff.py`'s `spawn_in_closure` was a data race by construction and passed only by luck. | **Fixed** — made deterministic with a join per spawn. |

---

Rule for continuing

One item, one commit, `test_diff` at `diffs: 0` after each. If a change cannot be
finished and verified in the session, leave the evidence for what was measured
and stop, rather than leaving a half-converted runtime behind.
