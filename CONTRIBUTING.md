# Contributing to BRAT

Thanks for showing up. BRAT is small, honest, and fast-moving — the bar is
low, but it is real. If you can run a command and read its output, you can
contribute.

## The two rules

1. **A change ships with a run.** No edit is accepted without executing the
   test suite and pasting the real output. A test that prints `PASS` and
   exits 0 while failing is a broken test, not a success — we've caught those.
2. **Honesty over percentages.** `docs/project/LEDGER.md` and
   `docs/project/CHANGELOG.md` are not marketing. If a feature is half-done,
   it says so. Benchmarks that can't be reproduced are deleted, not carried
   forward.

## How to get a PR merged

1. Branch off `main`. One topic per PR.
2. Run the battery:
   ```sh
   python3 lang/tests/test_lang.py
   python3 lang/tests/test_ownership.py
   ```
   Native path touched? Also:
   ```sh
   python3 lang/tests/test_native.py
   python3 lang/tests/test_diff.py      # needs `python -m ziglang`
   ```
3. Update `docs/project/CHANGELOG.md`: what changed and how it was verified.
   Not "improved" — the actual result and the actual run.
4. Paste the real command output in the PR. Not a summary of it.

## What we don't want

- Optimizations without a number. "Before" first, then "after."
- Features that can't be exercised by a test.
- README claims the code doesn't back. When docs and code disagree, fix the
  docs — silently is not an option.

## Where to start

- `docs/guide/index.md` — what BRAT is, in 60 seconds.
- `docs/guide/ownership.md` — the ownership model and its known holes.
- `docs/project/LEDGER.md` — what's done, what isn't, what's honest.
- `AGENTS.md` — the red lines. Read before touching `crt.h` or `SPEC.md`.

Questions? Open an issue or just start a PR. We answer fast.