# BRAT docs

Public documentation for BRAT. All files here are about the language.

## Reading order

1. **[guide/index.md](guide/index.md)** — what it is, 60-second hello, honest status, quick start.
2. **[guide/why.md](guide/why.md)** — why BRAT, comparison with Rust, Go, Zig, OCaml, Swift. Read "Where BRAT loses to Rust" first.
3. **[guide/tour.md](guide/tour.md)** — full language tour in an hour. Snippets are real files from `lang/examples/`.
4. **[guide/ownership.md](guide/ownership.md)** — ownership model: what is checked, real diagnostics, known holes with repros. Read before touching `move`.
5. **[guide/ffi.md](guide/ffi.md)** — C interop: extern, structs, callbacks, pointers, bindgen, export fn.
6. **[../bench/README.md](../bench/README.md)** — what was measured, how to reproduce, what was never measured.
6. **[../CONTRIBUTING.md](../CONTRIBUTING.md)** — repo rules: no change without a run, honesty over percentages.

## Related docs outside `docs/`

- `lang/spec/SPEC.md` — v0.1 spec, incl. §11 "not in v0.1".
- `lang/spec/CHECKER_V2.md` — next checker design (path projections, Law of Exclusivity, Maranget usefulness).
- `docs/project/LEDGER.md` — Rust pain points vs BRAT status.

## What these docs don't promise

No file in `docs/` contains numbers not in `docs/project/LEDGER.md`, and no
soundness claim not backed by a run. Where checking is incomplete, it says so
and gives a reproducible example. See `docs/guide/ownership.md`, "Known holes".
