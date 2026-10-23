# FFI

BRAT calls C through `extern`. Both backends (interpreter and native) support
the same syntax and agree byte-for-byte.

## Declaring

```brat
extern fn puts(s: str) -> i32;

extern "libm.so" {
    extern fn sin(x: f64) -> f64;
    extern fn cos(x: f64) -> f64;
}

extern struct Point { x: i64, y: f64 }
```

Types: `i32`, `i64`, `f64`, `str`, `ptr`, `none`, and `extern struct` names.
`..T` marks a typed variadic tail: `extern fn printf(fmt: str, ..ptr)`.

Without a library, libc / the main handle is used.

## Strings

BRAT strings are null-terminated in both backends, so they pass straight to C
as `const char *`. A C string returned as `str` is adopted without copying.

## Structs

`extern struct` fields are marshalled by value in both directions: BRAT struct
literals become C structs, and C struct returns become BRAT maps.

## Callbacks

Pass a BRAT function by name:

```brat
extern "libcb.so" fn apply_cb(x: i64, cb: fn(i64) -> i64) -> i64;

fn double_it(x: i64) -> i64 { return x * 2; }

fn main() -> i64 {
    return apply_cb(21, "double_it");   // 42
}
```

The compiler generates a `brat_cb_<name>` C wrapper; no manual glue.

## Pointers

`ptr_null`, `ptr_is_null`, `ptr_offset`, `ptr_read_*`, `ptr_write_*`
(`i64`, `f64`, `ptr`), `ptr_read_str`. Null dereferences raise in both
backends.

## errno

```brat
extern fn fopen(path: str, mode: str) -> ptr;
let f = fopen("/no/such", "r");
print(errno());          // 2
print(strerror(errno())); // No such file or directory
```

## Generating bindings

```sh
brat bindgen header.h -o bindings.brat
```

Parses function prototypes and `struct`/`typedef struct` declarations into
`extern`/`extern struct` blocks. Pointers map to `ptr`, arrays to `ptr`,
`const char *` to `str`.

## Exporting BRAT as a C library

```brat
export fn add(a: i64, b: i64) -> i64 { return a + b; }
```

```sh
brat build lib.brat --shared -o libbrat.so
```

The resulting shared library exports `add` with C linkage. Supported types for
exported functions: `i32`, `i64`, `f64`, `str`, `ptr`, and `extern struct`.

## Notes

- FFI calls agree between interpreter and native (exit code + stdout).
- `dlopen`-style loading: libraries are loaded at runtime in the interpreter
  and linked at build time for native.