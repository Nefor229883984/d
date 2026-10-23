Аудит безопасности и багов BRAT

**Агент 1 из 2 — Security / Red Team.** Цель: сломать проект, каждая находка воспроизведена реальным запуском.

**Окружение и метод.**
- Python `3.14.7`; `cc`/`gcc`/`clang` есть, `zig` нет (zig-пути `test_native.py` недоступны).
- Прогон по **текущему рабочему дереву** (в `git status` файлы `lang/engine/brat/runtime.py`, `lang/engine/codegen_c.py`, `lang/engine/crt.h` уже изменены — я их не трогал).
- Интерпретатор: `python3 -X utf8 "<CLI>" run FILE`. Нативный бэкенд: `... build FILE -o OUT --dynamic`.
- Все зонды лежат вне репозитория: `/tmp/opencode/audit/`.
- Код не редактировался; единственный созданный файл — этот отчёт.

Обозначение путей:
```
CLI="/home/ghost/Рабочий стол/BRAT/lang/engine/cli.py"
```

---

Сводная таблица

| # | Severity | Область | Файл:строка | Суть | Воспроизведено |
|---|----------|---------|-------------|------|----------------|
| 1 | **Critical** | Runtime / ФС-песочница | `lang/engine/brat/runtime.py:1216` | Нестроковый путь = «сырой» FD: чтение stdin, запись в fd1, порча интерпретатора (exit 120), паритет-разрыв с C | да |
| 2 | **High** | Defer / паритет бэкендов | `lang/engine/codegen_c.py:1697` | Порядок `defer` при раскрутке исключения из блока различается: интерпретатор `cleanup→caught`, C `caught→cleanup` | да |
| 3 | **Medium** | Spawn / deadlock | `lang/engine/brat/runtime.py:1249` | Вложенный `join_all()` внутри `spawn`-воркера вешает оба бэкенда | да |
| 4 | **Medium** | Regex / паритет | `lang/engine/crt.h:1205` | ≥65 альтернатив: интерпретатор `True`, C — `unhandled: ('err','bad regex pattern')`, exit 1 | да |
| 5 | **Medium** | Regex / паритет | `lang/engine/crt.h:1248` | `.` матчит байты (C) vs кодпоинты (интерпретатор): `re_match("é","..")` → `False` vs `True` | да |
| 6 | **Low** | Память C-рантайма | `lang/engine/crt.h:2439` | `vsortby_q` аллоцирует `Vec` на каждое сравнение и не освобождает | по коду |
| 7 | **Low** | Импорт модулей | `lang/engine/brat/runtime.py:1359` | `load_mod` не проходит `confined_path` (defense-in-depth) | по коду |

`да` — есть команда и реальный вывод. `по коду` — подтверждено чтением, не запуском (см. раздел UNCONFIRMED).

---

1. Critical — Обход ФС-песочницы интерпретатора через нестроковый путь

**Что.** `confined_path()` (интерпретатор) при не-`str` входе возвращает аргумент **без проверки**:

```python
# lang/engine/brat/runtime.py:1216
def confined_path(p):
    if not isinstance(p, str):
        return p                      # <-- строки 1217-1218: проверка не выполняется
```

`read_file`/`write_file` затем отдают это значение прямо в `open()`:

```python
# runtime.py:968
with open(path, encoding='utf-8') as f:      # path == int -> трактуется как OS file descriptor
# runtime.py:1075-1076
with open(path, 'w', encoding='utf-8') as f:
    f.write(vals[1])
```

`open(<int>)` в Python — это «взять уже открытый дескриптор с этим номером». Числовые аргументы становятся сырыми fd (stdin=0, stdout=1, stderr=2 и любые унаследованные), минуя confinement. C-бэкенд такое **запрещает** по тегу:

```c
// lang/engine/crt.h:1363
if (p.tag != V_STR) return verr("read_file wants a string path");
// lang/engine/crt.h:1552
if (p.tag != V_STR || t.tag != V_STR) return verr("write_file wants string path and text");
```

Плюс C-бэкенд **перепроверяет** путь после `open` через `/proc/self/fd` (`crt.h:954 _vfd_confined`, вызовы `crt.h:1369`, `crt.h:1558`), а интерпретатор — нет (см. UNCONFIRMED A).

1a. Чтение stdin (утечка/подмена ввода)

```
$ cat /tmp/opencode/audit/fs4.brat
fn main() -> i64 {
  print(read_file(0))
  0
}
$ echo "FROM_STDIN" | python3 -X utf8 "$CLI" run /tmp/opencode/audit/fs4.brat
('ok', 'FROM_STDIN\n')
```

Ожидалось `('err','path outside the program directory')` либо ошибка типа. Фактически — процесс читает свой stdin. Нативный аналог (`escint.brat`, тот же код) даёт `('err', 'read_file wants a string path')`.

1b. Запись в stdout и порча интерпретатора (DoS, exit 120) — **самое опасное**

```
$ cat /tmp/opencode/audit/corrupt.brat
fn main() -> i64 {
  print("BEFORE-A")
  print("BEFORE-B")
  print(write_file(1, "RAW-FD1\n"))
  print("AFTER")
  return 0
}
$ python3 -X utf8 "$CLI" run /tmp/opencode/audit/corrupt.brat
RAW-FD1
Exception ignored while flushing sys.stdout:
OSError: [Errno 9] Bad file descriptor
$ echo $?
120
```

`write_file(1, ...)` открывает fd1 в режиме `'w'`, что **закрывает stdout** при выходе из `with`. Строки `BEFORE-A`, `BEFORE-B`, `AFTER` потеряны (буферизованы и сброшены в закрытый fd). Интерпретатор завершается с кодом **120** и трейсбеком на флаше — «errors are values» нарушено, процесс-раннер получает мусорный код возврата. Нативный бэкенд:

```
$ ./corrupt.exe
BEFORE-A
BEFORE-B
('err', 'write_file wants string path and text')
AFTER
$ echo $?
0
```

1c. Неперехваченный `TypeError` при нестроковых аргументах (crash, exit 1)

```
$ cat /tmp/opencode/audit/fs2.brat
fn main() -> i64 {
  print(write_file([1,2], "x"))
  0
}
$ python3 -X utf8 "$CLI" run /tmp/opencode/audit/fs2.brat
TypeError: expected str, bytes or os.PathLike object, not list at line 2
$ echo $?
1
```

```
$ cat /tmp/opencode/audit/wf2.brat
fn main() -> i64 {
  print(write_file("o.txt", 5))
  0
}
$ python3 -X utf8 "$CLI" run /tmp/opencode/audit/wf2.brat
TypeError: write() argument must be str, not int at line 2
$ echo $?
1
```

`read_file` (runtime.py:970-973) ловит только `UnicodeDecodeError`/`OSError`, а `write_file` (1078) — только `OSError`. `TypeError` утекает наружу. Нативный бэкенд в обоих случаях возвращает `('err', ...)`, exit 0.

1d. Паритет-разрыв на числовом fd

```
$ cat /tmp/opencode/audit/fs1.brat
fn main() -> i64 {
  print(read_file(5))
  0
}
$ python3 -X utf8 "$CLI" run /tmp/opencode/audit/fs1.brat
('err', '[Errno 9] Bad file descriptor')        # интерпретатор: дошло до os.open(5)
$ ./par1.exe
('err', 'read_file wants a string path')        # C: отклонено по тегу
```

**Влияние.** (1) Обход ФС-песочницы на чтение/запись дескрипторов процесса; (2) порча stdout и аварийное завершение интерпретатора (DoS) при `write_file(1, …)`; (3) ненадёжные краши вместо value-ошибок на нестроковых аргументах; (4) нарушение контракта двухбэкендного паритета. Запуск недоверенного `.brat` через интерпретатор даёт доступ к дескрипторам раннера.

**Минимальный фикс.** В `confined_path` (runtime.py:1217) заменить `return p` на явный отказ для нестроковых типов:

```python
if not isinstance(p, str):
    return None
```

и/или в `read_file`/`write_file` до `open()` проверять `isinstance(vals[0], str)` (и `isinstance(vals[1], str)`), возвращая `('err', '...wants a string path')`. Ловить `TypeError`/`ValueError` рядом с `OSError`, чтобы сохранить «errors as values».

---

2. High — Разный порядок срабатывания `defer` при исключении из блока (паритет)

**Что.** При `raise` из блока с `defer` интерпретатор выполняет `defer` при выходе из блока (до `catch`), а C-бэкенд — после `catch`. Спецификация однозначна:

```
# lang/spec/SPEC.md:209-211
`defer expr` — LIFO; expr захватывает значения биндингов на регистрации
(как замыкание), сброс на выходе из блока.
```

**Воспроизведение.** Тот же кейс есть в репозиторном тесте `lang/tests/test_diff_semantics.py:101` (`defer_unwind_block`), который сам помечает его как `DIFF`, но не валит процесс:

```
$ python3 -X utf8 lang/tests/test_diff_semantics.py
defer_unwind_block     DIFF rc i=0 n=0
   interp: b'cleanup\ncaught\n'
   native: b'caught\ncleanup\n'
...
    semantic diffs: 1
```

Отдельный зонд:

```
$ cat /tmp/opencode/audit/defer.brat
fn main() -> i64 {
  try {
    if true {
      defer print("cleanup")
      raise("boom")
    }
  } catch e {
    print("caught")
  }
  return 0
}
$ python3 -X utf8 "$CLI" run /tmp/opencode/audit/defer.brat
cleanup
caught
$ ./defer.exe
caught
cleanup
```

**Причина.** Интерпретатор `run_block` льёт defer'ы в `finally` (runtime.py:1202-1209), поэтому стек разматывается **до** `catch` — это соответствует SPEС. В C-бэкенде `try` (codegen_c.py:1697-1725) генерирует `goto __catch`, не сбрасывая defer-кадр раскручиваемого блока; кадр блока сбрасывается только на нормальном выходе (`vdfreset`, codegen_c.py:1802) либо на выходе из функции (codegen_c.py:2011-2043). Обработчик `catch` успевает выполниться раньше.

**Влияние.** Разное наблюдаемое поведение и разный порядок побочных эффектов (закрытие ресурсов, сброс состояния) между бэкендами при исключениях — ломает контракт паритета; в C-бэкенде `defer` может выполниться после кода, который рассчитывал на уже отработавшую очистку.

**Минимальный фикс.** При раскрутке (путь `raise`/`vrаise` → `goto` в `catch`) сбрасывать defer-кадры, накопленные внутри `try`-тела, до перехода на `catch_lbl`: в `try`-эмиссии запомнить `vdfsp` на входе и вставить `vdfreset(<base>)` в точку ошибки (аналогично `emit_loop_exit`, codegen_c.py:1847). Авторитетный порядок — интерпретатора; тест `defer_unwind_block` должен стать зелёным.

---

3. Medium — Deadlock при вложенном `join_all()` внутри `spawn`

**Что.** `join_all()` внутри `spawn`-воркера, ожидающего ещё не запланированную задачу, исчерпывает пул и вешает программу. Оба бэкенда используют **фиксированный** пул:

```python
# runtime.py:1249  -> 2 * cpu_count, кап 32
n = min(2 * (os.cpu_count() or 1), 32)
# runtime.py:1271-1284  join_all: ждёт done всех задач поколения
```
```c
// crt.h:2548-2554  bpool_ncpu -> ncpu (здесь 2)
long n = sysconf(_SC_NPROCESSORS_ONLN);
// crt.h:2664-2679  vjoin_all -> pthread_cond_wait(g_q_idle)
```

**Воспроизведение** (здесь `nproc` = 2; интерпретатор — пул 4, C — пул 2; оба меньше числа родителей):

```
$ cat /tmp/opencode/audit/nest.brat
fn worker(n: i64) -> i64 {
  spawn {
    print("child")
  }
  join_all()
  print("parent")
  return 0
}
fn main() -> i64 {
  let mut i = 0
  while i < 8 {
    spawn {
      worker(i)
    }
    i = i + 1
  }
  join_all()
  print("done")
  return 0
}
$ timeout 15 python3 -X utf8 "$CLI" run /tmp/opencode/audit/nest.brat ; echo "EXIT=$?"
EXIT=124            # таймаут: ни одного вывода, все воркеры ждут
$ timeout 15 ./nest.exe ; echo "EXIT=$?"
EXIT=124            # та же взаимоблокировка в C-бэкенде
```

Все воркеры заняты родителями, которые блокируются в ожидании детей, а дети не могут получить воркер. Простой `spawn`+`join_all` (без вложенности) работает; проблема именно при `join_all` из воркера.

**Влияние.** Программа на BRAT может детерминированно повесить и интерпретатор, и нативный бинарник (DoS). Спецификация (`SPEC.md:162`) обещает, что `join_all()` ждёт все задачи и пробрасывает первую ошибку — неявного «пул исчерпается» там нет.

**Минимальный фикс.** Либо не блокировать воркер в `join_all`: выполнять ещё не запущенные задачи из очереди в вызывающем потоке (work-stealing / «run pending inline»). Либо в интерпретаторе для `spawn` использовать неограниченный/растущий пул, а в C — поднимать число воркеров при нехватке (или создавать задачу напрямую, если очередь непуста, а пул занят). Документировать запрет вложенного `join_all` тоже вариант, но менее желательный.

---

4. Medium — Regex: ≥65 альтернатив ломают нативный бэкенд (паритет + аварийный выход)

**Что.** Движок C ограничивает число альтернатив массивом `sps[64]`:

```c
// lang/engine/crt.h:1200-1205
long sps[64];
long jmps[64];
int ns = 0, nj = 0;
while (g->i < g->plen && g->pat[g->i] == '|') {
    g->i++;
    if (ns >= 63) { g->err = 1; return -1; }   // <-- кап: 64 альтернативы максимум
```

**Воспроизведение** (шаблон = N раз `a|`):

```
$ python3 -c "p='|'.join(['a']*65); open('alt65.brat','w').write('fn main() -> i64 {\n  print(re_match(\"a\", \"%s\"))\n  0\n}\n'%p)"
$ python3 -X utf8 "$CLI" run alt65.brat
True
$ python3 -X utf8 "$CLI" build alt65.brat -o alt65.exe --dynamic && ./alt65.exe
unhandled: ('err', 'bad regex pattern')
$ echo $?
1
```

64 альтернативы работают в обоих (`alt64.brat` → `True`/`True`), 65 — только в интерпретаторе. Воспроизводится ровно на границе.

**Влияние.** Шаблон из пользовательского ввода с ≥65 альтернативами проходит в интерпретаторе, но **роняет** нативный бинарник (`unhandled: …`, exit 1). Это и DoS, и нарушение паритета; вместо `err`-значения — необработанный выход.

**Минимальный фикс.** Заменить фиксированные `sps[64]`/`jmps[64]` на динамически растущие буферы (как уже делается для `g->p`), убрав `if (ns >= 63)`. Дополнительно: `vre_match` при `bad regex pattern` должен возвращать `err`-значение (в интерпретаторе `RuntimeError`, ловится), а не `vraise` с аварийным выходом — если такова политика для «плохого паттерна», интерпретатор должен вести себя так же.

---

5. Medium — Regex: `.` по байтам (C) vs по кодпоинтам (интерпретатор)

**Что.** Интерпретатор работает со `str` (кодпоинты Unicode, `runtime.py:399-517`, узел `('dot', None)`), C-движок — с `const char*` (байты):

```c
// crt.h:1250
const char *s = sv.as.str, *pat = pv.as.str;
```

Для не-ASCII `.` в C матчит один **байт**, в интерпретаторе — один **символ**.

**Воспроизведение:**

```
$ cat /tmp/opencode/audit/dot2.brat
fn main() -> i64 {
  print(re_match("é", ".."))
  0
}
$ python3 -X utf8 "$CLI" run dot2.brat
False                     # "é" — 1 кодпоинт, ".." требует 2 -> нет
$ ./dot2.exe
True                      # "é" = 2 байта (C3 A9), ".." -> есть
```

Для сравнения: `re_match("é",".")` даёт `True` в обоих (в C хватает одного байта).

**Влияние.** Результаты regex расходятся для любого UTF-8 не-ASCII входа; логика, построенная на `.`/квантификаторах, на нативном бэкенде даёт иную длину совпадения. Паритет-баг, потенциально влияющий на валидацию/парсинг.

**Минимальный фикс.** Либо явно зафиксировать байтовую семантику `.` в спецификации и в интерпретаторе, либо научить C-движок разбирать UTF-8-последовательности (`. ` = один кодпоинт) так же, как интерпретатор. Пока — задокументировать расхождение и покрыть тестом, если байтовая семантика выбрана намеренно.

---

6. Low — Утечка `Vec` на каждое сравнение в `sort_by` (C-рантайм)

```c
// crt.h:2438-2446
static int vsortby_q(const void *pa, const void *pb) {
    Vec *one = vvec();                       // <-- аллокация на КАЖДОЕ сравнение
    vec_push(one, *(Val *)pa);
    Val ka = vcall_any(vsortkey_fn, one);
    one->len = 0;
    vec_push(one, *(Val *)pb);
    Val kb = vcall_any(vsortkey_fn, one);
    int r = vcmp(ka, kb);
    return r;                                // 'one' не освобождается
}
```

`qsort` делает ~`n·log n` сравнений, и на каждом аллоцируется новый `Vec`, который затем теряется. Для больших списков это десятки-сотни МБ мусора и давление на аллокатор. **Подтверждено чтением; величину не измерял** (не запускал профилирование памяти) — см. UNCONFIRMED C. Стабильность при этом совпала (см. честную проверку в конце).

**Минимальный фикс.** Переиспользовать один буфер: вынести `Vec *one` в `BTLS`-переменную и обнулять `one->len` между сравнениями, либо вовсе не оборачивать `Val` в `Vec`, если `vcall_any` принимает один аргумент.

---

7. Low — `load_mod` не использует `confined_path` (defense-in-depth)

```python
# runtime.py:1359-1367
def load_mod(name, env, funcs):
    base = _MOD_BASE[0]
    path = os.path.join(base, *name.split('.')) + '.brat'
    if not os.path.isfile(path):
        alt = os.path.join(base, 'vendor', *name.split('.')) + '.brat'   # <-- без confined_path
```

Импорт не проходит через `confined_path`, в отличие от `read_file`/`write_file`. Имя модуля строится из токенов `import`. **Попытка эксплуатации не удалась** (см. UNCONFIRMED B), поэтому это Low: защита в глубину отсутствует, но пробить её имеющимися токенами не получилось. Фикс: прогонять `path` через `confined_path` (или эквивалентную проверку) и отказывать при `None`.

---

Проверено — багов нет (честные отрицательные результаты)

Чтобы не выдавать теорию за факты, отдельно указаны проверенные и **не подтвердившиеся** гипотезы:

- **Инъекция в C-эмиссию (`setfield`/`let`/`set`)** — `test_inject.py` зелёный: `INJECT ALL GREEN (35 asserts)`. Парсер ограничивает имена полей до ID/KW, эмиссия не ломается.
- **`sort_by`: стабильность/устойчивость** — интерпретатор и C (glibc `qsort` = mergesort) дали идентичный вывод на списках с равными ключами (зонд `sortp.brat`). Паритет-бага нет (остаётся только утечка из §6).
- **Обход через строковый литерал в `import`** — не достижим (см. UNCONFIRMED B).

---

UNCONFIRMED / нужно глубже тестировать

Теоретические или недовоспроизведённые проблемы. **Не включать как факты** без отдельного подтверждения.

**A. TOCTOU в `confined_path` интерпретатора (реальный, но гонка).**
Интерпретатор делает `realpath` → проверку → `open` без перепроверки после открытия (runtime.py:1221-1227 → 968). C-бэкенд **специально** перепроверяет дескриптор постфактум (`crt.h:954 _vfd_confined`, вызовы `crt.h:1369`, `crt.h:1558`), что показывает: угроза считается реальной. Статическая симлинк-ссылка ловится (`realpath` резолвит её), но подмена между `realpath` и `open` в гонке даёт побег. Детерминированно не воспроизвёл. Рекомендация: и в интерпретаторе открывать и затем сверять `/proc/self/fd` (или `os.O_NOFOLLOW` + post-check), как в C.

**B. Побег `load_mod` через имя модуля.**
Проверял строковый литерал и `..`-последовательности. Токенизатор отдаёт строку **вместе с кавычками** (`lexer.py:71-79`), а `name.split('.')`/`os.path.join` не дают собрать произвольный абсолютный путь: части после точки приклеиваются с `"`. Вывод: имеющимися токенами не эксплуатируется; остаётся §7 (нет `confined_path`). Нужен более изощрённый разбор (например, интерполяция в STR) для окончательного вывода.

**C. Impact утечки `vsortby_q` (§6).**
Утечка видна по коду, но размер не измерен. Нужно `sort_by` на 10⁵–10⁶ элементов с замером RSS/времени, чтобы отнести к `Medium` (DoS) вместо `Low`.

**D. `_RE_STEPS` — модуль-глобальный (не thread-local) счётчик шагов регекса.**
`re_match` (runtime.py:582) сбрасывает общий счётчик. При конкурентном `re_match` в `spawn`-потоках возможны ложные срабатывания лимита шагов (ложные результаты/DoS). Не воспроизведено надёжно (нужен детерминированный триггер лимита в двух потоках). Кандидат на `Medium`, если подтвердится.

**E. Weak-DoS/неявные падения C при прочих «плохих» паттернах.**
Аналогично §4: любые ветки `g->err` в C-движке regex приводят к `vraise("bad regex pattern")` → `unhandled:` + exit 1. Проверил только границу альтернатив; стоит просканировать вложенность/длину, чтобы оценить охват.

---

Итог


**Не подтверждено запуском (только по коду/теория):** §6 (утечка — размер не мерил), §7, UNCONFIRMED A–E.

**Одна самая опасная находка:** **§1 — обход ФС-песочницы интерпретатора через нестроковый путь** (`runtime.py:1216-1218`). Конкретно `write_file(1, …)` пишет в stdout, теряет буферизованный вывод и **роняет интерпретатор с exit 120** (`corrupt.brat`), а `read_file(0)` читает stdin процесса — при том что C-бэкенд корректно отклоняет оба случая. Это одновременно побег из песочницы, DoS-порча раннера и нарушение двухбэкендного паритета, и лечится минимальной проверкой типа.

Файлы балансировщика и WAF удалены из репозитория; находки по ним сняты с этого отчёта.
