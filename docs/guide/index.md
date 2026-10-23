# BRAT — language without lifetime annotations

## What it is

BRAT is a statically ownership-checked imperative language. Three ideas in code:
borrows without `'a` lifetimes, errors as values, telemetry as syntax. One
runtime, two backends: tree-walking Python and C99 emitter.

One sentence: **ownership without annotations, errors without panics, telemetry
without SDK.**

Apache-2.0, open from day one.

## 60-second hello

Save as `hello.brat`:

```brat
fn add(a: i64, b: i64) -> i64 {
  return a + b
}

fn parse_port(s: str) -> i64 {
  let p = s.parse_int()
  if is_err(p) {
    return 8080
  }
  return unwrap(p)
}

fn main() -> i64 {
  let host = "127.0.0.1:9001"
  print(host)
  log.info("up {host} ok")
  print(add(40, 2)?)
  print(parse_port("nope") or 8080)
  let mut codes = [200, 404, 500]
  codes.push(503)
  for c in codes {
    if c == 200 {
      log.info("hit {c}")
    }
  }
  return 0
}
```

Check:

```
python3 lang/engine/cli.py check hello.brat
```

Output:

```
hello.brat: 0 error(s), 0 warning(s)
hello.brat: 0 type error(s)
```
