# Почему людям нравятся языки (и нюансы для BRAT)

> Источники: Stack Overflow Developer Survey 2024, публичные постмортемы (Rust, Go, Zig, Elixir), GitHub stars, сообщества.

## 1. Топ-факторы «любви» к языку

1. **Понятные ошибки** — Rust выигрывает за счёт `rustc` с подсказками. Go — за `gofmt` + простоту. BRAT уже делает это (`file:line: error` + сниппет + `own(...)` подсказка).
2. **Быстрая компиляция** — Go (секунды), Rust (минуты, но улучшается). BRAT: интерпретатор мгновенный, натив ~2 с.
3. **Единый toolchain** — `go fmt`, `go test`, `go build`. BRAT: `brat run/check/build/pkg/test` — единый вход.
4. **Документация и DX** — Elixir/Rust выигрывают за счёт docs.rs, rustdoc. BRAT: `docs/` + `guide/`.
5. **Безопасность по умолчанию** — Rust: memory safety. BRAT: нет `unsafe`, ownership-lite.
6. **Простота** — Go: «less is more». BRAT: 4 типа, нет лайфтаймов, нет макросов.
7. **Экосистема** — crates.io, npm. BRAT: `brat pkg` (URL+sha256+vendor) — минимум, roadmap.

## 2. Нюансы (что отталкивает)

- **Сложность типовой системы** (Rust lifetimes, Haskell) — новички уходят. BRAT: ноль аннотаций.
- **Медленная компиляция** (Rust pre-2018) — фрустрация. BRAT: интерпретатор мгновенный.
- **Нет инструментинга** (Nim, Crystal) — «игрушечный» статус. BRAT: LSP/registry — roadmap, честно признано.
- **Обещания без доказательств** (V) — потеря доверия. BRAT: LEDGER честно перечисляет проигрыши.
- **Слом совместимости** (Python 2→3) — раскол сообщества. BRAT: editions нет, совместимость тестами.
- **Зависимость от одного человека** (Elm) — стагнация. BRAT: документация самодостаточна.

## 3. Применимость к BRAT (чек-лист для релиза)

- [ ] Ошибки понятны новичку (сниппет + подсказка + `own(...)`).
- [ ] `brat run` мгновенный, `brat build` < 5 с на малых программах.
- [ ] Единый CLI: `run/check/build/pkg/test/fmt`.
- [ ] Документация: `guide/tour.md`, `guide/ownership.md`, `spec/SPEC.md`.
- [ ] Безопасность: нет `unsafe`, ownership-lite, арены.
- [ ] Простота: ≤ 5 базовых типов, нет лайфтаймов.
- [ ] Честность: LEDGER перечисляет проигрыши.

## 4. Что добавить для «нравится» (v0.2+)

- LSP (автодополнение, go-to-def).
- Отладчик (breakpoints, step).
- Форматтер (`brat fmt` уже есть, улучшить).
- Пакетный менеджер с версиями.
- FFI (`extern`/`mod`).
- Бенчмарки (mono fast-path измерить).

## 5. Данные 2025 (обновление)

- **Python** +7pp (2024→2025): 57.9% — go-to для AI, data science, backend.
- **Rust** 14.8% (+2.2pp), **Go** 16.4%, **TypeScript** 43.6%, **Zig** 2.1%.
- **AI-интеграция**: 42.5% professional developers use AI tools; языки с AI-support растут.
- **IDE**: VS Code 75.9%, Cursor 17.9%, Claude Code 9.7% — AI-редакторы растут.
- **Ключевой тренд**: разработчики ценят языки с хорошим DX, понятными ошибками и быстрым циклом.

## 6. Источники

- Stack Overflow Developer Survey 2025.
- Rust: rustc-dev-guide, Miri, CVE-трекинг.
- Go: `gofmt`, `go test`, `go build`.
- Zig: simplicity, C-interop, comptime.
- Elixir: documentation and DX first.
- BRAT: `docs/guide/why.md`, `docs/project/LEDGER.md`.
