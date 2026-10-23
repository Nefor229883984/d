# Ownership checker — what it checks

Single source of truth: `borrow.py`. The BRAT port is `bratb.brat`; `test_bratb.py`
runs both on every corpus file and requires byte-identical messages. Divergence
is a bug in the port.

## How to run

```
python3 lang/engine/cli.py check file.brat    # ownership + types
python3 lang/tests/test_ownership.py           # 87 cases (57 reject + 30 accept, 177 asserts)
python3 lang/tests/test_bratb.py               # checker vs checker
```

## Model

Vale linear-aliasing: single ownership, moves, no borrow-checker syntax. The
checker is intra-function: guarantees don't cross function boundaries (except
mode checks at call sites — `fn eat(s: move str)` moves the argument, `fn peek(s: ref str)`
borrows for the call).

A **place** is a path: `x`, `x.f`, `x.f.g`, `x[i]` (index is `x.*`). Ownership
is tracked per path, not per name: `move p.x` invalidates only `p.x`, sibling
fields stay alive.

## What is checked

| Rule | Diagnostic |
|---|---|
| use-after-move | `use of moved `x`` + `note: `x` moved here` |
| double move | `use of moved `x`` |
| move under borrow | `cannot move `x` while borrowed by `y`` |
| assign to immutable | `assign to immutable `x` (want `let mut` or mut param)` |
| assign to const | `assign to const `x`` |
| mutate via builtin method | `cannot mutate immutable `x`: `x.push` writes through it` |
| double mut borrow | `double mut borrow of `x`...` |
| mut self on immutable receiver | `cannot mutate immutable `x`: `f` takes mut self` |
| Law of Exclusivity at call | `law of exclusivity: ...` |
| pass immutable as mut param | `pass immutable `x` as mut param `p`` |
| `ref` escape via return | `ref `x` escapes via return` |
| `ref` escape via spawn | `ref `x` escapes via spawn` |
| write through mut alias of owner | `write through `y` mutates immutable `x`` |
| element write on immutable | `assign to immutable `m`...` |
| undeclared var/function | `use of undeclared var `x`` |
| trait conformance | `impl Shout for Point missing loud` |

## NLL-lite

A borrow lives until the last use of the borrower, not to the end of the block.
A holder that never appears again releases the borrow at its own statement:

```
let s = "x"
let _r = s          // borrow, _r never used again
let m = move s      // legal: _r released the borrow
```

(Cases b04/b05 check exactly this. Before the fix, an unused holder pinned
the borrow to the end of the block and legal code was rejected.)

Branches: if/try walked twice, merged conservatively — a move in one branch
makes the variable moved after. Loops walked twice: a move legal on the first
pass becomes use-after-move on the second.

## Soundness status by hole

Each hole is either closed by code or marked sound with justification.

1. **Returning a bare parameter (a13) — provably sound.** `return <param>`
   returns a *view* into the caller's storage: the caller owns it and it outlives
   the call, so the reference doesn't dangle. BRAT has no `-> ref` return type,
   so this is treated as a view, not ownership transfer, and is accepted (tests
   `a13`/`a14` require acceptance). Many returns in the corpus (incl.
   `bratb.brat`) are legal views.
2. **`move` has no runtime effect — soundness is fully on the checker.** In all
   backends `move x` is a read of `x`, so a checker error means silent buffer
   aliasing, not a crash. The checker conservatively rejects everything
   unproven: use-after-move, partial path moves (`moved_paths`), move under a
   live borrow. Unproven moves are rejected.
3. **`spawn` and data races — soundness boundary.** The checker rejects two
   specific threats: (a) `ref` escape via `spawn` (`B0521`) and (b) write to a
   captured binding inside the frame (lost write, `spawn_frozen`). Shared mutable
   state between threads is the user's responsibility: v0.1 has no `Send`/`Sync`,
   the frame is copied, so a shared non-Copy binding in `spawn` is an agreement,
   not something the checker can prove.
4. **`ref` escape via closure — CLOSED.** `escaping_refs` runs on `return`
   inside a closure body (via `walk_closure_arg` → `walk_block`). A `ref` to a
   local escaping through a closure return is rejected; a `ref` to a parameter
   is allowed (caller's storage).
5. **Write through field of immutable struct — CLOSED.** One rule for `.f` and
   `[i]`: the root path's `mut` is checked (`set` and `setidx` both strict on
   root). `p.items[0] = 9` and `p.items = [3]` on immutable `let p` are both
   errors (`assign to immutable`).
6. **Inter-procedural — by signature.** The checker doesn't infer invariants
   across calls: what `f(a)` does to `a` is declared by the signature
   (`move`/`mut`/`ref`). No hidden mutation through `ref` params; everything
   else is an agreement between caller and callee.

## What "incomplete" means

"Incomplete" = programs the checker accepts that it arguably should reject
(holes 2, 3, 6 above — soundness boundary, documented). The reverse doesn't
happen: all reject cases in `test_ownership.py` are green (exit≠0 and expected
diagnostic). ACCEPT cases grew after the NLL fix: previously the checker
rejected legal code.
