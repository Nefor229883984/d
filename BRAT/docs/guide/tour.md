Тур по BRAT: язык целиком за час

BRAT — это v0.1, до продакшена не готово. Тур держит правило репозитория:
если код и документация расходятся, прав код. Все сниппеты — реальные файлы
из `lang/examples/`; рядом сказано, что именно прогнано. Команды — из корня
репозитория. Нужен Python 3.12+, только стандартная библиотека.

Две команды, которыми тур пользуется:

```
python3 -X utf8 lang/engine/cli.py check <file>    # владение + типы
python3 -X utf8 lang/engine/brat.py <file>         # запуск (интерпретатор)
```

В выводах ниже `<ts>` — метка времени, она меняется от запуска к запуску.
Диагностики `check` печатают абсолютный путь; здесь он сокращён до имени
файла.

1. Hello и код выхода

`lang/examples/hello.brat` целиком:

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
python3 -X utf8 lang/engine/cli.py check lang/examples/hello.brat
python3 -X utf8 lang/engine/brat.py lang/examples/hello.brat
```

`check` печатает две строки — владение и типы:

```
lang/examples/hello.brat: 0 error(s), 0 warning(s)
lang/examples/hello.brat: 0 type error(s)
```

Запуск:

```
{"ts": <ts>, "lvl": "INFO", "msg": "hello 127.0.0.1:9001"}
127.0.0.1:9001
42
```

Видно сразу: файла без `main` достаточно — верхний уровень исполняется как
скрипт. `?` на вызове `add` (строка `42` — развёрнутое значение), `ref str` —
вью на `host`, телеметрия — обычные вызовы, ни одного `'a`.

Есть `main` — она возвращает `i64`, и это код выхода. Проверено на файле
`exitcode.brat` (вне репозитория):

```brat
fn main() -> i64 {
  print("bye")
  return 3
}
```

```
bye
echo $?   # 3
```

2. Типы

Рабочий набор: `i64`, `f64`, `bool`, `str`. Чекер (`typer.py`) принимает ещё
`i32`/`u64` как написания целого, но примеры и `docs/project/LEDGER.md` держатся
`i64`/`f64`/`bool`/`str`. `bytes` упомянут в `SPEC.md` §2, но в коде его нет.

- Одна строка: `str` — владение, `ref str` — вью. Нет `String` vs `&str`.
- Один vec и один map: `vec[T]`, `map[K]V`; литералы `[1, 2, 3]` и `{"a": 1}`
  (`lang/examples/map.brat`).
- `Some(T)`/`None` и `Ok(T)`/`Err(E)` — тэги рантайма, не отдельные типы.
- `null` в языке нет. `None` — значение, а не отсутствие.

Из `lang/examples/control.brat` и `map.brat`:

```brat
let codes = [200, 404, 500]
let mut m = {"a": 1, "b": 2}
m["c"] = 3
let mut v = [10, 20, 30]
v[1] = 99
```

3. Владение без аннотаций времён

В BRAT нет `'a`, elision-правил и HRTB. Чекер считает **места** (paths), а не
имена: `x`, `x.f`, `x[i]`. Проверки — внутрифункциональные.

Как это выглядит на валидном коде — `lang/examples/own_good.brat` даёт
`0 error(s), 0 warning(s)`:

```brat
fn add(a: i64, b: i64) -> i64 {
  return a + b
}
fn show(a) {
  let r = a
  print(r)
  return a
}
let host: str = "127.0.0.1:9001"
print(host)
let x = add(40, 2)?
print(x)
print(show(7))
```

`let r = a` — заём, а не перемещение. Перемещение — явное `move`.

Три правила на реальных диагностиках. Мутация под займом и побег `ref`
(`own_bad.brat` — по `lang/README.md`, «must fail with 4 errors», поэтому и
красный):

```brat
fn leak() {
  let a = "s"
  let r = a
  return r
}
let mut m = "s"
let a1 = m
let mut a2 = m
print(nope + 1)
spawn {
  print(a1)
}
```

```
own_bad.brat:4: error: ref `r` escapes via return
own_bad.brat:8: error: double mut borrow of `m`: `a2` clashes with `a1`
own_bad.brat:9: error: use of undeclared var `nope`
own_bad.brat:10: error: ref `a1` escapes via spawn
```

`move` инвалидирует место (`own_move.brat`, `2 error(s)`; фрагмент):

```brat
let h = "hi"
let m = move h
print(h)          // use of moved `h`
let p = Point { x: 1 }
let q = move p
print(p.x)        // use of moved `p`
```

`move` и `ref` живут и в сигнатурах (`own_sig.brat`, `1 error(s)`;
фрагмент): без
аннотации параметр — `ref`, `fn eat(s: move str)` забирает владение, поэтому
повторное использование аргумента отвергается:

```brat
fn eat(s: move str) -> i64 { ... }
fn peek(s: ref str) -> i64 { ... }
let h = "hi"
let r = eat(h)   // `h` moved here
print(h)         // use of moved `h`
```

Иммутабельность по умолчанию, как в Rust: запись только в `let mut` (или в
`mut`-параметр). `let v = [1, 2]; v.push(3)` — ошибка `cannot mutate
immutable`.

Честная граница: `move` — **статическая аннотация**, в рантайме эффекта нет
(`SPEC.md` §3). Ни один бэкенд не инвалидирует источник, поэтому владение
держится только на чекере, а чекер внутрифункциональный и неполный. Что
именно проверяется и где дыры — `docs/guide/ownership.md`.

4. Ошибки как значения

`?` разворачивает `Ok`/`Some` и пробрасывает `Err`; `or` даёт дефолт только
для `None`; `unwrap` разворачивает или пробрасывает; `is_err`/`is_none` —
предикаты. `null` нет, поэтому «нет значения» всегда явное.

`lang/examples/lib.brat` — библиотека на `pub`:

```brat
pub fn safe_div(a: i64, b: i64) -> i64 {
  if b == 0 {
    return Err("div by zero")
  }
  return Ok(a / b)
}
pub fn pick_first(v: vec[i64]) -> i64 {
  if len(v) == 0 {
    return None
  }
  return Some(v[0])
}
```

`lang/examples/result.brat` использует её:

```brat
import lib
fn main() -> i64 {
  let good = safe_div(10, 2)?
  print(good)
  let bad = safe_div(1, 0)
  if is_err(bad) {
    log.warn("caught div by zero")
  }
  let fallback = pick_first([]) or 99
  print(fallback)
  let maybe = pick_first([7, 8])
  print(unwrap(maybe))
  let empty = pick_first([])
  print(is_none(empty))
  return 0
}
```

Реальный вывод:

```
5
{"ts": <ts>, "lvl": "WARN", "msg": "caught div by zero"}
99
7
True
```

`or` спасает только `None`. На `Err` он ничего не делает — проверено:
`Err("boom") or 5` печатает `('err', 'boom')`.

`try/catch` ловит и `Err`, и рантайм-ошибки (в обоих бэкендах это значения
канала ошибок, а не `exit`). `lang/examples/trycatch.brat`, фрагмент:

```brat
try {
  let r = fetch(1)?
  got = r
} catch e {
  got = -1
  print("caught: {e}")
}
try {
  let z = 1 / 0
} catch e {
  emsg = e          // "div by zero"
}
try {
  let v = [1]
  print(v[5])
} catch e {
  oob = e           // "index out of range"
}
```

Реальный вывод: `caught: net down`, затем `body`, `try ok`. Непойманный `Err`
наверху даёт `unhandled` и exit 1.

5. Управление и `match` с деструктуризацией

`lang/examples/control.brat` (фрагмент): `match`, `for`, `if`, `?`.

```brat
fn status(code: i64) -> i64 {
  match code {
    200 => { return 1 },
    404 => { return 0 },
    _ => { return 0 }
  }
  return 0
}
fn main() -> i64 {
  let codes = [200, 404, 500]
  let mut ok = 0
  for c in codes {
    let r = status(c)?
    ok = ok + r
  }
  print(ok)
  return 0
}
```

`match` — десахар в `if`-цепочку на равенство, `_` последний, дубли и
неисчерпывающие енамы — ошибка парсинга. Скрутини разбирается в варианты
(`lang/examples/enum.brat`, фрагмент):

```brat
enum Color { Red, Green(r), Blue(r, g) }
match c {
  Red => { ... },
  Green(v) => { return v },
  _ => { ... }
}
match r {
  Ok(v) => { return v },
  Err(e) => { log.warn("bad {e}"); return 0 }
}
```

Вложенные паттерны (`lang/examples/nested.brat`): `Has(Ok(v))`, `Has(_)`.
`if let` / `while let` — тот же механизм (`lang/examples/letflow.brat`,
фрагмент):

```brat
if let Ok(x) = v[0] { return x } else { return 0 }
while let Some(x) = v[i] { total = total + x; i = i + 1 }
```

Векторный деструктор с пропуском слота (`lang/examples/destruct.brat`):

```brat
let [lo, hi] = minmax([3, 1, 9])
let [a, _, c] = [10, 20, 30]
```

6. Структуры, `impl`, трейты, дженерики, замыкания

Структура — тип с заглавной буквы, доступ `p.x`; `move p` инвалидирует и поля
(`lang/examples/struct.brat`):

```brat
let p = Point { x: 3, y: 4 }
print(p.x)
let o = origin()?        // fn origin() -> i64 { return Ok(Point {...}) }
```

Методы — `impl` (`lang/examples/impl.brat`):

```brat
impl Point {
  fn sum(self) -> i64 { return self.x + self.y }
  fn shift(self, dx: i64) -> i64 { return self.x + dx }
}
print(p.sum())     // 7
print(p.shift(10)) // 13
```

Трейт — только сигнатуры, `impl Trait for T` обязан дать все методы, иначе
`impl Shout for Point missing loud` (проверяют и чекер, и рантайм). Из
`lang/examples/trait.brat`:

```brat
trait Shout { fn loud(self) -> i64 }
impl Shout for Point {
  fn loud(self) -> i64 { return self.x * 2 }
}
```

Дженерики — стирание, как в Java: одно тело для всех типов, без
мономорфизации (`lang/examples/generic.brat`, вывод `7`, `a`, `42`, `1`):

```brat
fn first<T>(v) -> i64 { return v[0] }
print(first([7, 8]))
print(first("ab"))       // тот же `first` работает и для str
```

Замыкания захватывают копией; `fn` можно передать в `map`/`filter`
(`lang/examples/closure.brat`, вывод `13`, `610`, затем три списка):

```brat
let add3 = fn(a) { return a + 3 }
let fib = fn fib(n) { ... }         // именованное — для рекурсии
print(v.map(double))
print(v.filter(fn(x) { return x - 2 }))
```

Чего нет: трейт-объектов (`dyn Trait` — v0.2), полной мономорфизации,
макросов, `mod`/`extern`. См. `SPEC.md` §11 и `docs/guide/why.md`.

7. `spawn` и `join_all` — и честный предел

`spawn { }` — настоящий поток ОС в обоих бэкендах (`threading.Thread` в
интерпретаторе, `CreateThread`/`pthread_create` в C-рантайме). `join_all()`
ждёт все задачи поколения; конец программы делает неявный `join_all`.

`lang/examples/spawn.brat` (вывод одной строкой `spawn ok`):

```brat
let mut results = {"a": 0, "b": 0, "c": 0}
spawn { results["a"] = heavy(100) }
spawn { results["b"] = heavy(200) }
spawn { results["c"] = heavy(300) }
join_all()
assert(results["a"] == 4950)
```

Предел, который нельзя скрывать: `spawn` **не защищает от гонок данных**.
Кадр копируется, запись в захваченное связывание внутри кадра отвергается
(`assign to captured ... in spawn`), правило `let mut` действует и внутри
потока — но общая мутабельная память между потоками остаётся ответственностью
программиста. `Send`/`Sync` в v0.1 нет. Побег `ref` через `spawn` — ошибка
чекера (`own_bad.brat:10`). Подробно — `docs/project/LEDGER.md`, раздел «Конкуренция».

8. Телеметрия как синтаксис

`log.info/warn/error`, `trace.span` — не SDK, а
конструкции, разбираемые в AST. Вывод — JSON на stdout. `lang/examples/span.brat`:

```brat
let cfg = Host { name: "up1" }
log.info("routing to {cfg.name}")
trace.span("boot") {
  log.info("warming up")
}
let r = trace.span("sum") { return work(10) }
```

Реальный вывод (порядок — как напечатано):

```
{"ts": <ts>, "lvl": "INFO", "msg": "routing to up1"}
{"ts": <ts>, "span": ["boot"]}
{"ts": <ts>, "lvl": "INFO", "msg": "warming up"}
{"ts": <ts>, "span": ["sum"]}
r is 45
{"ts": <ts>, "span": ["plain"]}
span ok 45 up1
```

Интерполяция `{x}` — любое выражение для того же парсера: `"{len(v)}"`,
`"{p.x}"`, `"{n + 1}"`.

9. Модули и библиотеки

`import` грузит `.brat` рядом с файлом: функции вливаются, имя ищется и в
`vendor/`. `pub` открывает имя наружу, без `pub` — приватно (чекер ловит
нарушения между модулями; `lang/examples/modapi.brat` + `modapp.brat`):

```brat
import modapi
fn main() -> i64 {
  print(ping())     // 42
  return 0
}
```

Пространство имён плоское, как в C. `mod` и `extern "C"` парсер не
поддерживает — FFI нет.

10. Два бэкенда и как это запускать

Один язык, два бэкенда: дерево-обход на Python и C99-эмиттер
(`codegen_c.py` + `crt.h`). Команды:

```
python3 -X utf8 lang/engine/brat.py <file>              # brat run — интерпретатор
python3 -X utf8 lang/engine/cli.py check <file>          # brat check — владение + типы
python3 -X utf8 lang/engine/cli.py emit-c <file>         # C на stdout
python3 -X utf8 lang/engine/cli.py build <file> -o out   # один бинарник через C + zig cc
python3 -X utf8 lang/engine/brat.py <file> -- arg1 arg2  # args() в программе
```

`brat build` печатает `build ok <out>`; готовый бинарник линкует только
`crt.h` и libc. Проверено: `control.brat`, собранный `build`, даёт тот же
stdout, что интерпретатор, побайтово, если нормализовать поле `ts` (см. ниже).

Контракт двух бэкендов: одинаковый **stdout и код возврата**. Его держат два
постоянных сьюта — `lang/tests/test_diff.py` (мелкие программы, обе стороны,
сравнение байт) и `lang/tests/test_native.py` (сборка через `zig cc -O2`; в
`docs/project/LEDGER.md` — 58/58). В сравнении нормализуются `ts` и переводы строк.

Про `ts` честно: два бэкенда считают его по-разному — интерпретатор печатает
Unix-время, натив — секунды от старта процесса. Поэтому поле `ts` в
побайтовом сравнении нормализуется, и в выводе тура оно показано как `<ts>`.

Что не бэкенд: LLVM. `codegen_llvm.py` собирает IR вручную для четырёх
тестовых функций и никогда не открывает `.brat`-файл. Цифры скорости — в
`bench/README.md` и `docs/project/LEDGER.md`, а не здесь.

Куда дальше

- `docs/guide/index.md` — статус, что работает и чего нет.
- `docs/guide/ownership.md` — что именно проверяет чекер и где дыры (читать до
  кода, который трогает `move`).
- `docs/guide/why.md` — сравнение с Rust, Go, Zig, OCaml, Swift, включая раздел
  «Где BRAT проигрывает».
- `lang/spec/SPEC.md`, §11 — что НЕ в v0.1.
- `docs/project/LEDGER.md` — построчный статус «боли Rust» против BRAT.
