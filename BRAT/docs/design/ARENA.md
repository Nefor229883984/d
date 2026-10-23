Status: steps 1-4 implemented. Tests: lang/tests/test_arena_c.py (runtime, AddressSanitizer), test_arena_diff.py (both backends), test_arena_escape.py (escape rules), test_arena_request.py (request handler rolling back on Err, memory limit, AddressSanitizer). Decision A (escape out of a block): no implicit copy-out; the explicit form is own(x), a heap deep copy.

Step 1 as built: barena_enter / barena_leave (BMark) and balloc routed through the active arena. Arena objects carry BARENA_FLAG in their refcount, so vrc_dec never frees them one by one and vdrop returns early for them. Step 2 closed that gap: vector items and map arrays of an arena container also come from the arena. Decisions A-D below are not yet confirmed by the author; step 1 does not depend on them.

Why

A request handler allocates many small values that all die together: parsed fields, joined strings, temporary vectors. Today each one is a separate calloc/free pair, and each free goes through the refcount. An arena replaces that with a bump pointer and frees the whole block in one reset. The gain is real for short-lived work, and close to zero for long-lived structures, so the benchmark has to measure a request-shaped workload, not an average.

What exists now

Every heap value sits behind a BHead with a refcount (crt.h, balloc around line 95). Release goes through vdrop and calls free() when the count reaches zero. Errors unwind through the defer frame stack (vdfpush, vdfreset), which already runs cleanup code on every exit path, including error exits.

Syntax

    arena {
        let req = parse_request(raw)
        let body = join(req.parts, ",")
        send(body)
    }

Inside the block every heap allocation comes from the arena. On exit, normal or error, the arena resets to the mark it had at entry.

Escape rule

A value allocated inside the block must not outlive it. The checker rejects, at compile time:

- returning an arena value from the block or the enclosing function;
- assigning an arena value to a binding declared outside the block;
- passing an arena value into a container that lives outside the block (push, map insert);
- capturing an arena value in a spawn or in a closure that outlives the block.

The first three already fit the existing ownership checker, which tracks bindings and their scope. The spawn case is the same rule as for closures, so it reuses that code.

Runtime design

1. A thread-local arena: a list of chunks plus a current mark. balloc returns memory from the current arena when one is active, otherwise it falls back to calloc as now.
2. BHead gets a flag saying where the object came from. release must not call free() on an arena object. This is the same class of bug as the static-error crash fixed earlier, so the flag is part of the first step, not a later fix.
3. Reset is emitted through the defer frame: the block pushes a frame that resets the arena, so unwinding out of an error also resets it. Nothing else has to remember to do it.
4. Nested arenas stack: an inner block takes a mark, an outer one keeps its own.

Interaction with refcounting

An arena object still carries a refcount, because the same code path reads it. The reset does not decrement counts; it drops the whole chunk. That is only safe if the escape rule holds, which is why the checker has to be on by default for arena blocks. Running with --no-check would make the reset unsafe, so that flag has to refuse arena blocks.

Threads

An arena is thread-local. spawn inside an arena block is rejected by the escape rule, not by a runtime check. Sharing arena values across threads is out of scope for v1.

Interaction with WASM

The same runtime API, but the chunks come from linear memory instead of calloc. The C backend and the WASM backend differ only in the chunk source. This is a later step, after the C version is measured.

Steps

1. Runtime: chunk allocator, mark and reset, BHead flag, unit test that reset returns memory to the chunk and that release on an arena object does not call free.
2. Syntax and parser: arena block, interpreter support, differential tests against the C backend.
3. Checker: escape rule with a test per rejected case, and a test that the accepted cases compile.
4. Codegen: mark on entry, reset via defer frame, refuse under --no-check.
5. Benchmark: a request-shaped loop, memory after each request, time against the same code without arena. Report both, including where it is not faster.
6. Only then: WASM chunk source.

Decisions needed before step 1

A. Escape into outer containers: forbid completely in v1 (simpler, safer), or allow copying the value out explicitly with a clone that returns a non-arena value (more useful, more checker work)? Recommendation: forbid in v1, add an explicit copy-out later.

B. Chunk size and growth: fixed chunk size (for example 64 KiB) with a list of chunks, or doubling? Recommendation: fixed chunks, one large allocation if a single value exceeds the chunk size.

C. Does --no-check refuse arena blocks, or only warn? Recommendation: refuse, because the reset is unsafe without the check.

D. Is the arena a statement only, or can it return a value that is copied out? Recommendation: statement only in v1.

Step 2 as built: the parser desugars `arena { body }` into `if true { __arena_enter(); defer __arena_leave(); body }`, so every backend reuses existing defer handling, including error unwinding. Nesting is a stack of marks, capped at 64. Container arrays follow the container (barr_new / barr_grow). Test finding: `raise` inside a function returns an Err value and reaches a caller's try only through `?`, as SPEC.md says. Newcomers will meet this, so it belongs in the newcomer gate.

Step 3 as built (typer.py): inside an arena block, names declared there are inner; a value is an escape when it mentions an inner name and its static type is not a scalar (int, float, bool, none). Arithmetic and comparisons count as scalars whatever their operands are, and `+` does when one operand is a number. Rejected: returning such a value; assigning it to a name declared outside the block; storing it through an outer container (index, field, push or another method); passing it to a user function next to an outer binding; spawn or closure capturing it. A binding made from an outer container is an alias of it, so it is not inner. Known limits: rules are name- and type-based, not a full region analysis. Mutation through a called function that receives an outer container by a path other than a user-function argument is not tracked. The ownership checker already rejects several of these through its own aliasing rules; the arena rules do not depend on it.

Step 4 as built: own(x) returns a heap deep copy (arena allocation is suspended for the copy). The escape rule does not look inside own(), so
`return Err(own("bad {len(parts)}"))` is accepted and the message survives the arena's reset. Tag arguments built inside an arena are moved to
the heap by vtagv, so Ok/Err returned from a block never point into a reset arena. emit-c --no-check refuses source containing an arena block.
Measured with the request loop (3000 requests, 600 failing): without the arena the C build exceeds a 16 MiB limit; with it, it runs. LeakSanitizer
reports 2.3 MB still leaked against 25 MB without the arena; what remains is the pre-existing leaks listed below.

Known issues, not caused by arenas:
- Tag values (Ok, Err, user enum variants) are never released: the tag's name is strdup'd and its argument list is never freed.
- `brat run` does not run the checker, so a program that `build` rejects for an arena escape still runs in the interpreter. The interpreter has no
  arena memory, so the output is right, but the diagnostic is missing.
- `let mut x = y` makes `x` a mutable borrow of `y` in the ownership checker. Reassigning `y` while `x` is alive is rejected. Newcomers will hit this.

Fixed after step 4:
- A string rebuilt in a loop (`line = line + x` with `line` declared inside the loop) leaked per pass, because _reclaim_names skipped loop-declared
  bindings: a re-run `let` would have overwritten an unreleased value. The emitter now releases at the re-run `let` itself (relet set), so loop
  bindings join the ordinary reclaim path. Covered by lang/tests/test_loop_reclaim.py under RLIMIT_AS = 32 MiB and by AddressSanitizer.
- The 16 MiB request-loop claim is now checkable by anyone: `brat build --mem 16M` bakes the limit into the binary (RLIMIT_AS at startup), so the
  budget no longer depends on the test harness.
