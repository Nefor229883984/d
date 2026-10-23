# BRAT tour: full language in an hour

BRAT is v0.1, not production-ready. Rule: if code and docs disagree, code wins.
All snippets are real files from `lang/examples/`. Commands from repo root.
Python 3.12+, stdlib only.

Two commands used throughout:

```
python3 lang/engine/cli.py check <file>    # ownership + types
python3 lang/engine/brat.py <file>         # run (interpreter)
```

`<ts>` in output is a timestamp; it changes per run. Diagnostics print absolute
paths; shortened to filename here.

## 1. Hello and exit code

`lang/examples/hello.brat`:

```brat
let host: str = "127.0.0.1:9001"
let r: ref str = host
log.info("hello {host}")
print(r)
fn add(a: i64, b: i64) -> i64 {
  return a + b
}
let x = add(40, 2)?
print(x)
```

```
python3 lang/engine/cli.py check lang/examples/hello.brat
python3 lang/engine/brat.py lang/examples/hello.brat
```

`check`:

```
lang/examples/hello.brat: 0 error(s), 0 warning(s)
lang/examples/hello.brat: 0 type error(s)
```

Run:

```
{"ts": <ts>, "lvl": "INFO", "msg": "hello 127.0.0.1:9001"}
127.0.0.1:9001
42
```

No `main` needed — top level runs as a script. `?` unwraps `add` (42). `ref str`
is a view on `host`. Telemetry is plain calls. Zero `'a`.

With `main`, its `i64` return is the exit code. Verified on `exitcode.brat`
(outside repo):

```
python3 lang/engine/brat.py exitcode.brat; echo $?   # 7
```

## 2. Types and literals

Four base types: `i64`, `f64`, `bool`, `str`. Containers: `vec[T]`, `map[K,V]`.
Structs and variants (enums) are user-defined. `none` is the nil value.

## 3. Ownership in 10 lines

```
let s = "x"
let r = s          // borrow, r is ref str
let m = move s     // s moved; s is gone
// r still valid (NLL: borrow released at last use)
print(m)
```

`move` is a read with no runtime effect. The checker is the only gate.
`let mut` makes a mutable binding; reassigning requires `mut`.

## 4. Errors as values

```
let p = s.parse_int()    // returns Ok(n) or Err(reason)
if is_err(p) { return 8080 }
return unwrap(p)
```

`?` propagates `Err`. `or` defaults only for `None`. `try/catch` catches `Err`
and runtime errors in both backends.

## 5. Telemetry

```
log.info("up {host} ok")
trace.span("request") {
  // nested code
}
```

Output is JSON on stdout. No SDK, no macros.

## 6. Arenas

```
arena {
  let v = [1, 2, 3]
  // memory reset on block exit
}
```

`own(x)` copies a value out. Escape rules are checked by the typer.

## 7. Concurrency

```
spawn {
  print("hello from thread")
}
join_all()
```

Real OS threads in both backends. No async/await. No `Send`/`Sync`.

## 8. Match

```
match x {
  Ok(v) => { print(v) },
  Err(r) => { print(r) }
}
```

## 9. Defer

```
defer { cleanup() }
```

Runs on every exit path, including errors.

## 10. What's not in v0.1

See `lang/spec/SPEC.md` §11. Channels, C-interop, `scope`, LLVM backend
(proof-of-concept) are not implemented.

## Running examples

Every file in `lang/examples/` is runnable:

```
python3 lang/engine/cli.py run lang/examples/hello.brat
python3 lang/engine/cli.py check lang/examples/own_bad.brat
```

Full tour: `lang/examples/` has 60+ programs covering all features.
