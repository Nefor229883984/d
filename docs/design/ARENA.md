# Arena design

Status: steps 1-4 implemented. Tests: `test_arena_c.py` (runtime, ASan),
`test_arena_diff.py` (both backends), `test_arena_escape.py` (escape rules),
`test_arena_request.py` (request handler rollback on Err, memory limit, ASan).
Decision A: no implicit copy-out; explicit form is `own(x)`, a heap deep copy.

## Why

A request handler allocates many small values that all die together: parsed
fields, joined strings, temporary vectors. Today each is a separate
calloc/free pair through the refcount. An arena replaces that with a bump
pointer and frees the whole block in one reset. Gain is real for short-lived
work, close to zero for long-lived structures — benchmark a request-shaped
workload, not an average.

## What exists now

Every heap value sits behind a `BHead` with a refcount (`crt.h`, `balloc`).
Release goes through `vdrop` and calls `free()` when the count reaches zero.
Errors unwind through the defer frame stack (`vdfpush`, `vdfreset`), which runs
cleanup on every exit path, including error exits.

## Syntax

```
arena {
    let req = parse_request(raw)
    let body = join(req.parts, ",")
    send(body)
}
```

Inside the block every heap allocation comes from the arena. On exit (normal or
error) the arena resets to the mark it had at entry.

## Escape rule

A value allocated inside the block must not outlive it. The checker rejects,
at compile time:

- returning an arena value from the block or enclosing function;
- assigning an arena value to a binding declared outside the block;
- passing an arena value into a container that lives outside the block (push,
  map insert);
- capturing an arena value in a spawn or closure that outlives the block.

The first three fit the existing ownership checker (bindings and scope). The
spawn case reuses the closure rule.

## Runtime design

1. Thread-local arena: list of chunks + current mark. `balloc` returns memory
   from the current arena when active, otherwise falls back to calloc.
2. `BHead` carries a flag saying where the object came from. Release must not
   call `free()` on an arena object. The flag is part of step 1, not a later fix.
3. Reset is emitted through the defer frame: the block pushes a frame that resets
   the arena, so unwinding out of an error also resets it.
4. Nested arenas stack: inner block takes a mark, outer keeps its own.

## Interaction with refcounting

An arena object still carries a refcount (same code path reads it). Reset
doesn't decrement counts; it drops the whole chunk. Safe only if the escape
rule holds — that's why the checker is on by default for arena blocks.
`--no-check` refuses arena blocks (reset is unsafe without the check).

## Threads

Arena is thread-local. `spawn` inside an arena block is rejected by the escape
rule, not a runtime check. Sharing arena values across threads is out of scope
for v1.

## WASM

Same runtime API, but chunks come from linear memory instead of calloc. C and
WASM backends differ only in chunk source. Later step, after C version measured.

## Steps

1. Runtime: chunk allocator, mark/reset, BHead flag, unit test.
2. Syntax and parser: arena block, interpreter support, differential tests.
3. Checker: escape rule with test per rejected case + accepted cases.
4. Codegen: mark on entry, reset via defer frame, refuse under `--no-check`.
5. Benchmark: request-shaped loop, memory after each request, time vs no arena.
6. Only then: WASM chunk source.

## Decisions

A. Escape into outer containers: forbid in v1; add explicit copy-out (`own`) later.
B. Chunk size: fixed 64 KiB + list; one large allocation if a single value
   exceeds the chunk; doubling for medium allocations.
C. `--no-check`: refuse arena blocks (reset unsafe without check).
D. Arena is a statement only in v1.

## As built

Step 2: parser desugars `arena { body }` into
`if true { __arena_enter(); defer __arena_leave(); body }`. Every backend reuses
existing defer handling, including error unwinding. Nesting capped at 64.
Container arrays follow the container (`barr_new`/`barr_grow`).

Step 3 (typer.py): inside an arena block, names declared there are inner; a
value escapes when it mentions an inner name and its static type is not a scalar
(int, float, bool, none). Arithmetic/comparisons yield scalars; `+` does when
one operand is a number. Rejected: return, assign to outer, store in outer
container (index, field, push, method), pass to user function next to an outer
binding, spawn/closure capture. A binding from an outer container is an alias,
not inner. Known limits: region analysis implemented (region ids, no name sets). Mutation through paths other than direct user-function args is now tracked.
Mutation through a called function receiving an outer container by a path other
than a user-function argument is not tracked.

Step 4: `own(x)` returns a heap deep copy (arena allocation suspended for the
copy). Escape rule doesn't look inside `own()`, so
`return Err(own("bad {len(parts)}"))` is accepted and survives reset. Tag
arguments built inside an arena are moved to the heap by `vtagv`. `emit-c
--no-check` refuses programs with arena blocks.

Bench: request loop (3000 requests, 600 failing). Without arena C build exceeds
16 MiB; with arena it fits. LeakSanitizer: 2.3 MB vs 25 MB.

## Known issues (not caused by arenas)

- `brat run` does not run the checker (fixed: now gates arena programs).
- `let mut x = y` makes `x` a mutable borrow of `y` in the checker; reassigning
  `y` while `x` is alive is rejected. Newcomers hit this.
- Tag values: interned names (immortal), args freed on rc=0 (no recursion into
  elements — by design to avoid UAF on aliases).
