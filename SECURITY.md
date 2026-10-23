# Security

BRAT is a compiler and a runtime. Its attack surface is both: the code it
runs *and* the C code it generates.

## In scope

- **Arbitrary code execution via generated C.** The C emitter splices program
  names into source text without escaping. A `.brat` file can execute
  arbitrary code at build time — even when `brat check` reports zero errors.
  This includes unescaped struct field keys, method names, and type names.
- **Ownership-checker bypass.** Any way to silence the checker is a
  vulnerability, not a feature. Ownership guarantees must be unconditional.
- **Memory safety in the C runtime** (`crt.h`): out-of-bounds, use-after-free,
  data races.
- **Runtime sandbox.** `read_file`, `write_file`, and `env_get` must stay
  within the program's directory.
- **Denial of service.** Unbounded allocation, catastrophic backtracking,
  exponential parsing of untrusted input.

## Out of scope

- Language errors documented in `docs/project/LEDGER.md` and `SPEC.md`.
- Code already guarded by a boundary (e.g. `brat pkg` outside the program dir).
- Anything requiring root or an existing shell on the target.

## Reporting

Send a description plus a **minimal `.brat` file** that reproduces it. The
most useful payload proves itself: writes a marker file to a temp directory
and exits, with no network access and no destruction.

I verify by reproduction, not by reading. If I can't reproduce it, I'll say so
plainly.

## Known, unfixed

The honest list lives in the audit reports and in `docs/project/LEDGER.md`.
Critical findings stay open until a fix ships *and* a test catches the bypass.

## Contact

Open a private issue or reach out directly. I respond fast, and I don't
shoot the messenger.