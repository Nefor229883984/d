bench/ — measured, not asserted

Every number here is the output of a real run on this machine (2 cores, 3.7 GB
RAM). Re-run before quoting them; if a number cannot be reproduced it gets
deleted, not carried forward.

strbench.brat — building a string

Run:

```
python lang/engine/cli.py build bench/strbench.brat -o sb && ./sb
python lang/engine/cli.py run bench/strbench.brat
```

Result at `n = 40000`, which is the point where the difference stops being noise:

| backend | `s = s + "x"` | `parts.push` + `join("")` |
|---|---|---|
| native (C, `-O2`) | **668 ms** | **4 ms** |
| interpreter | 605 ms | 749 ms |

Two separate findings, and they are not the same thing.

1. The native backend is quadratic in string concat

Same source, doubling `n`:

| n | native `concat` | growth | native `join` | growth |
|---|---|---|---|---|
| 5000 | 14 ms | – | 5 ms | – |
| 10000 | 58 ms | 4.1x | 3 ms | 0.6x |
| 20000 | 200 ms | 3.4x | 3 ms | 1.3x |
| 40000 | 818 ms | 4.1x | 5 ms | 1.5x |

Quadrupling per doubling is O(n²). `vadd` allocates a fresh buffer and copies
both operands every time, so a loop of n appends copies n²/2 bytes. `push` +
`join` allocates each piece once and copies once, which is O(n).

**The O(n) primitive already exists in the language**, so this is a pitfall
rather than a missing feature — but it is a pitfall a newcomer from Python or Go
walks straight into, and #2 is why.

2. The two backends disagree about which one is fast

The interpreter is *faster* at `concat` (605 ms vs 668 ms) and *slower* at `join`
(749 ms vs 4 ms). Both produce identical output, so `test_diff` stays green —
this is a parity break in performance only.

Cause: CPython grows a string in place when its refcount is 1, which makes
`s = s + "x"` amortised O(n) there for free. The C runtime has no such
optimisation and no refcount to consult.

Consequence for the portability promise: a program tuned on one backend is
mistuned on the other. That is worse than being uniformly slow, because it is
invisible in tests.

What would fix it

Give strings a header carrying capacity, and let `vadd` absorb its left operand
in place when the buffer is uniquely owned — which is exactly what CPython does
and exactly what the ownership model is for. That turns native `concat` from
O(n²) to amortised O(n) *and* removes the per-iteration allocation, which is
also the memory leak reported in `docs/design/ARCHITECTURE.md` §2.4.

It cannot be done by checking capacity alone: `let t = s; s = s + "x"` aliases,
and growing the buffer would change what `t` sees. Uniqueness has to come from
the ownership checker, which already computes it — so the fix belongs in the
interaction between `borrow.py` and the emitter, not in the runtime.

Until then: `push` + `join` is the documented way to build a string in a loop,
and this benchmark is what pins that.
