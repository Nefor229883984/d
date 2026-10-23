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

## 5. Данные 2026 (обновление)

- **Python** 58.0% (стабильно), **Rust** не в топ-10, но admired; **Go** ~16%, **TypeScript** 43.8%, **Zig** 2.1%.
- **AI-интеграция**: 71.8% devs use LLMs; AI-редакторы растут (Cursor, Claude Code).
- **Security**: 24% команд теряют время из-за security/privacy issues.
- **Ключевой тренд**: AI-ассистированная разработка, безопасность, понятные ошибки.

## 6. AI и типизированные языки (2026)

- **GitHub Octoverse 2025**: AI делает типизированные языки более популярными — TypeScript стал #1.
- **AI feedback loop**: типы становятся safety net для кода, написанного AI.
- **BRAT**: уже статически типизирован, нет лайфтаймов — проще для AI-генерации.
- **Вывод**: BRAT должен поддерживать AI-инструменты (LSP, четкие ошибки, предсказуемый код).

## 7. Источники

- Stack Overflow Developer Survey 2026.
- GitHub Octoverse 2025/2026.
- Rust: rustc-dev-guide, Miri, CVE-трекинг.
- Go: `gofmt`, `go test`, `go build`.
- Zig: simplicity, C-interop, comptime.
- Elixir: documentation and DX first.
- BRAT: `docs/guide/why.md`, `docs/project/LEDGER.md`.
