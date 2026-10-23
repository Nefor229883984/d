# Execution plan: heavy items

Written after the 2026-10-14 night session. Measured numbers only.

## Item 1 — native backend leaks every string — DONE (Stage 2)

Failure: `s = s + "x"` in a loop. n=8000 consumed 32 MB; n=100000 ≈ 5 GB → 172 s, machine unresponsive. No reclamation at all.

Fix: reference counting on `Val`. `BHead.rc` via `balloc`/`bgrow_rc`; `vrc_inc`/`vrc_dec`/`vdrop` reclaim. Emitter releases rebound binding's old value by default. `brat build --move-rt` adds scope-lifetime drops, retain-on-alias, `move` invalidation (`vmove` → `V_MOVED`). Plain refcounting, not URC. Cycle collection out of scope for v0.1.

Verified: `test_runtime_reclaim.py` — `s = s + "x"` at n=40000 and 100000 under `RLIMIT_AS = 32 MiB`. `RECLAIM ALL GREEN (15 asserts)`. `test_diff` `diffs: 0`.

## Item 2 — `vraise` through `longjmp` undefined at `-O2` — DONE (Stage 0)

Was: three triggers segfaulted at `-O2` (worked at `-O0`) because `vraise` did
`longjmp(*vjmp, 1)` across frames with non-`volatile` locals.

Fix: error-as-value end-to-end. `crt.h` has no `setjmp.h`, no `jmp_buf`. `vraise` sets TLS `_verr_pending` + `vuwind`; callers `return vunwind`. `codegen_c.py` emits `err_bail`/`err_check_line` after raises and calls; try/catch via `err_goto` + `verr_take`. Tests: `test_codegen_c` asserts no setjmp and presence of `verr_pending`; `test_native`/`test_diff` green at `-O2`.

## Item 3 — smaller, still real

| Item | Status |
|---|---|
| Shared containers unsynchronised across threads; interpreter survives on GIL. `vec_push` on shared `Vec` from two threads loses writes (3/200). | **Open.** LEDGER defers to user responsibility, roadmap v0.2. |
| Interpreter bounded by Python's `sys.setrecursionlimit`; native backend is not. | **Fixed** — both backends count BRAT frames and refuse at `RECURSION_BUDGET` (default 2000, `BRAT_RECURSION_BUDGET`). Python limit is safety net. |
| `test_diff.py`'s `spawn_in_closure` was a data race by construction. | **Fixed** — made deterministic with a join per spawn. |

## Rule for continuing

One item, one commit, `test_diff` at `diffs: 0` after each. If a change cannot
be finished and verified, leave evidence and stop rather than leaving a
half-converted runtime.
