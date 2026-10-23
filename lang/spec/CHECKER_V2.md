CHECKER v2 — design doc for future borrow checker

Goal: faster and better than rustc borrowck for BRAT (our language is smaller —
no lifetimes at all, so the task is an order of magnitude simpler than Rust).

Sources (read, not claimed):
- Maranget, "Warnings for Pattern Matching" (JFP 2007) — usefulness matrix;
  exhaustiveness = usefulness of `_`; redundancy falls out of the same algorithm.
  In rustc: `rustc_pattern_analysis::usefulness`, rustc-dev-guide
  "Pattern and exhaustiveness checking". Already in prod v1: heuristic for a single
  enum (parser.py, test in test_lang).
- Hylo (ex-Val), mutable value semantics (Racordon et al., hylo-lang.org):
  NO references as a class. There are projections (`x.f`, `x[i]`) and Law of Exclusivity:
  a mutable projection makes the base inaccessible for the duration of its lifetime;
  check is at call-site, without lifetime variables. This is our v2 model.

v2 decisions

1. Paths instead of variables. Checker tracks not `x` but a tree of projections:
   `x`, `x.f`, `x.f.g`, `x[i]` (index — `x.*`). Move of `x.f` invalidates
   `x.f*`, but not `x.g`. IN PROD (borrow.py: path_of/paths_conflict,
   own_paths.brat + own_paths_bad.brat).
2. Law of Exclusivity at call-site: a call with a `mut` parameter captures the
   argument path exclusively for the duration of the call; N readers or 1 writer —
   by paths, not variables. Analogue of Hylo inout, we already have `mut` mode.
3. Typed AST as input (same typed IR needed for monomorphization):
   one pass — types and ownership together, not two checkers.
4. Interprocedural summaries (v0.2 promise): signature carries ownership effect
   (`move`/`ref`/`mut` already in syntax) — bodies of other functions not
   needed, analysis O(function), not O(program). rustc does same (fn signatures),
   but without NLL-lifetime inference — we have nothing to infer.
5. Match: full usefulness per Maranget when types land (pattern matrix,
   specialization by constructors, counterexamples in diagnostics
   `missing: Some(Ok(_))`).
6. Diagnostics: primary span + related span ("`x` moved here", "used
   here") — at least two spans, like rustc; snippets already exist (borrow.py).

Performance notes for backend (not checker)

- Perceus (Koka, FBIP): if reference is unique at update point — update in place.
  For C backend: reuse analysis on vec push in loops, later.

v1 debt: greedy dots in `lexer.ID`

`lexer.ID` (`lang/engine/brat/lexer.py`) greedily eats dots, so `v.push(x)`
arrives from parser as `('call', 'v.push', [x])`, not as method call
`('mcall', ('var','v'), 'push', [x])`. Checker compensates with one helper
`name_root` (borrow.py): it takes root of name — both as place and as call head —
instead of parsing dot in every place. Proper fix is exactly one: lexer emits `.`
as separate token, parser builds single `mcall` for `base.meth(...)`. It is NOT
done in one pass over frontend, because it changes AST shared by both backends
(`runtime.py`, `codegen_c.py`) and BRAT-port parser (`lang/examples/bratp.brat`,
mirror of lexer+parser in `test_bratp.py`). Need synchronous edits to
runtime/codegen and bratp.brat. Identity of `parse_prog` output is contract of
`test_bratp`.

What we do NOT do

- No lifetime annotations ever (that's the point of the language).
- No or-patterns in v1 match — usefulness simpler.
- No regions/HRTB/GAT.
" path="/workspaces/d/lang/spec/CHECKER_V2.md">
<|end|><|start|>tool to=assistant<|channel|>analysis<|message|>The user asked me to translate the file. I've written the translation. Let me verify no Russian text remains.<|end|><|start|> assistant to=shell<|channel|>analysis <|constrain|>xml<|message|><rfl:parameter name="command">
cd /workspaces/d && grep -P '[\x{0400}-\x{04FF}]' lang/spec/CHECKER_V2.md || echo "NO RUSSIAN FOUND"