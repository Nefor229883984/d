BRAT — язык без аннотаций времён

Что это

BRAT — статически проверенный на владение императивный язык. Три
конструкции в коде: заимствования без `'a`-лайфтаймов, ошибки как значения,
телеметрия как синтаксис. Один рантайм, два бэкенда: дерево-обход на Python и
C99-эмиттер.

Одно предложение: **владение без аннотаций, ошибки без паник, телеметрия без
SDK**.

Apache-2.0, открыт с первого дня.

Hello за 60 секунд

Сохраните как `hello.brat`:

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

Проверка:

```
python -X utf8 lang/engine/cli.py check hello.brat
```

Вывод:

```
hello.brat: 0 error(s), 0 warning(s)
hello.brat: 0 type error(s)
```

Запуск:

```
python -X utf8 lang/engine/brat.py hello.brat
```

Вывод (`ts` — метка времени, меняется от запуска к запуску):

```
127.0.0.1:9001
{"ts": 1791478808.6515036, "lvl": "INFO", "msg": "up 127.0.0.1:9001 ok"}
42
8080
{"ts": 1791478808.6516664, "lvl": "INFO", "msg": "hit 200"}
```

Что здесь видно сразу: `?` на вызове функции (строка 6), `or` как дефолт
(строка 14), `mut` обязателен для мутации (строка 15), телеметрия — обычные
вызовы (строки 13, 20). Ни одного `ref`, ни одного lifetime.

Честный статус

v0.1. До продакшена не готово. Ниже — что работает и как это проверено,
и чего нет вообще.

Работает сегодня

| Что | Чем подтверждено |
|---|---|
| C-бэкенд эмитит компилируемый C99 | `codegen_c.py` + `crt.h`; 58 программ из 58 дают тот же stdout и тот же exit code, что интерпретатор (поле `ts` нормализуется, см. docs/guide/tour.md §10) |
| `spawn` — настоящие потоки ОС | `threading.Thread` в интерпретаторе, `CreateThread`/`pthread_create` в C-рантайме |
| Ошибки как значения | `?`, `or`, `unwrap`, `try/catch`; `null` в языке нет |
| Трейты, дженерики стиранием, замыкания | `lang/examples/trait.brat`, `generic.brat`, `closure.brat` |
| `brat fmt` идемпотентен | два прогона подряд дают байт-в-байт одинаковый файл |
| `brat pkg` | `vendor/` + `brat.lock` с sha256, `pkg install` сверяет хеш |
| Чекер владения ловит базовые вещи | use-after-move, double-move, move-под-займом, double-mut-borrow, запись в иммутабельное, побег `ref` |
| Лексер и парсер на самом BRAT | `bratc.brat` и `bratp.brat`, дифф-тест против Python |

Чего нет

- **LLVM-бэкенда нет.** `codegen_llvm.py` вручную пишет llvmlite-IR для
  четырёх тестовых функций (`add`, `fib`, `route`, `sum_to`) и никогда не
  открывает `.brat`-файл. Это проверка того, что MCJIT на хосте работает, не
  бэкенд. Прежняя цифра «x6268» была неверной (время JIT считалось до
  компиляции) и удалена.
- **Проверки владения внутрифункциональные и неполные.** Гарантии даются
  внутри одной функции. `test_ownership.py` зелёный (177 ассертов); что
  именно НЕ проверяется — в [docs/guide/ownership.md](ownership.md).
- **Числовых замеров mono fast-path нет.** Механизм есть, измерений нет.
- **`move` в рантайме — НЕ ПРОВЕРЕНО.** Что именно гарантирует `move` на
  уровне рантайма C-бэкенда, сейчас выясняется. Не полагайтесь на это без
  чтения исходников `codegen_c.py` и `brat/runtime.py`.
- **Фазер покрывает только lex+parse.** Ни исполнения, ни кодогенерации.
  2000 входов меньше чем за 2 секунды — это доказывает, что парсер не виснет,
  и ничего больше.

Быстрый старт

Клонируйте репозиторий, дальше — из корня:

```
python -X utf8 lang/engine/cli.py check lang/examples/hello.brat
python -X utf8 lang/engine/brat.py lang/examples/hello.brat
```

Подкоманды `cli.py`:

| Команда | Что делает |
|---|---|
| `brat file.brat` | запуск |
| `brat run file.brat -- args` | запуск с аргументами |
| `brat check file.brat` | владение + типовые острова |
| `brat fmt file.brat` | форматирование |
| `brat emit-c file.brat` | C на stdout |
| `brat test file.brat` | прогон по exit code |
| `brat build file.brat -o out.exe` | один бинарь через C и zig cc |
| `brat pkg add <url>` | зависимость в `vendor/` + sha256 в `brat.lock` |

Требования: Python 3.12+, только стандартная библиотека. `brat build`
дополнительно требует `ziglang` ради `cc`.

Дальше: [docs/guide/tour.md](tour.md) — язык целиком за час.

Куда читать спеку

`lang/spec/SPEC.md`, раздел 11 — «что НЕ в v0.1». Раздел этот честный;
если доктрина и код расходятся, прав код, а не доктрина.