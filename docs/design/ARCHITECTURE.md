# Architecture — backend and roadmap

Status: draft. Nothing here is implemented; each option names cost and benefit.

## 1. Where Python actually is

Python is **not in the runtime path.**

```
brat build prog.brat -> codegen_c.py -> prog.c -> zig cc -O2 -> ./prog
      (Python)        (Python)                      (build time)  (no Python)
```

The emitted binary links only `crt.h` and libc. `brat run` uses the interpreter
(Python needed for running); `brat build` needs Python once at build time.

So "get rid of Python" splits into three:

| # | Question | Status |
|---|---|---|
| Q1 | Ship without Python on target? | **Already yes** — `brat build` output is a native binary. |
| Q2 | Toolchain (checker, emitter, CLI) without Python? | No. Requires self-hosting. |
| Q3 | Is the Python path fast? | Irrelevant to shipped programs; matters only for `brat run`. |

Q2 — self-hosting — is the goal, not a precondition.

## 2. Self-hosting plan

1. Port `bratb.brat` (parser + checker) to BRAT itself. It already exists as
   `lang/examples/bratb.brat` and `test_bratb.py` checks parity.
2. Port `brat.py` (interpreter) — harder, needs runtime calls.
3. Port `codegen_c.py` — needs string building, file I/O.
4. `brat build` itself becomes a BRAT program.

Cost: months. Benefit: zero Python dependency for the toolchain.

## 3. How BRAT beats Rust

BRAT doesn't beat Rust on raw speed or soundness. It wins on:

- **Zero lifetime annotations** — no lifetimes, no elision rules.
- **Errors as values** — no Result hierarchy, `?` everywhere.
- **Telemetry as syntax** — no tracing SDK.
- **No async colors** — OS threads, no `Send`/`Sync`.
- **Fast iteration** — interpreter instant, native ~2 s.

Rust wins on: ecosystem, soundness, tooling, community, CPU-bound throughput.

## 4. What's not worth doing

- **Replacing Python in runtime** — already done (C backend).
- **Formal soundness proof** — out of scope for v0.1.

## 5. Roadmap

Future goals only; completed items are tracked in `docs/project/LEDGER.md`.

v0.2:
- modules (`mod`) and namespaces
- LSP: diagnostics, completion, go-to-definition
- channels and `scope`
- self-hosting: checker in BRAT
- generics monomorphization and trait objects
- exhaustive match checking

v0.3:
- package registry, transitive deps, versioning
- debugger and profiler integration
- richer FFI (stable ABI, cross-boundary callbacks)

v1.0:
- formal soundness story and verification
- production hardening and ecosystem

Non-goals: async/await colors, lifetime annotations, integer zoo.