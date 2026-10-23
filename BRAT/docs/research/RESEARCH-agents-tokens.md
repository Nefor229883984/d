Research: agents, skills, token/accuracy — findings

Status: research only, nothing installed. Numbers verified against the GitHub
API on 2026-10-14; where a figure comes from a paper it is cited, where it
comes from a repo README it is labelled as such.

Two constraints shape every recommendation below, and both are facts about this
machine rather than preferences:

- **2 CPU cores, 3.7 GB RAM.** Anything whose selling point is "fire 5+
  background agents in parallel" is a bad fit before any other consideration.
  The box already froze this session from `zig cc` builds; adding a
  parallel-orchestration harness multiplies that failure mode.
- **Token spend is per-turn and paid by the user.** So is accuracy. Anything
  that trades one for the other needs its measured trade-off stated, not its
  marketing.

---

1. What "sisyphus ultrawoker" actually is

`oh-my-opencode` / `oh-my-openagent` — a plugin that adds a **Sisyphus**
orchestrator agent plus specialist subagents, and a `ultrawork` / `ulw` keyword
that switches it to maximum-intensity mode.

Repo facts, verified:

| Repo | Stars | Last push | Note |
|---|---|---|---|
| `code-yeongyu/oh-my-openagent` | 70 000 | 2026-10-13 | the "OmO" / `mass ulw` version, battery-included |
| `alvinunreal/oh-my-opencode-slim` | 9 505 | 2026-10-13 | **lean fork, explicitly reduced token usage**, MIT |
| `opensoft/oh-my-opencode` | 1 269 | 2026-01-26 | **stale, 9 months** |

The architecture is sound and worth understanding regardless of what gets
installed: Sisyphus keeps the main context lean by firing cheap background
agents — `explore` for internal grep, `librarian` for external docs, `oracle`
for expensive read-only reasoning — and delegating by *category*
(`ultrabrain`, `quick`, `visual-engineering`, `writing`). Delegation prompts are
required to carry seven sections including an explicit tool whitelist, which is
a real anti-tool-sprawl device.

**Verdict for this machine: the slim fork or nothing.** The 70k version is
built for "fire 5+ agents in parallel" on a machine that cannot.

---

2. Agents and skill collections worth knowing

| Repo | Stars | Last push | What it is |
|---|---|---|---|
| `wshobson/agents` | 40 612 | 2026-10-12 | 94 plugins / 202 agents / 184 skills / 105 commands, one Markdown source emitted for Claude Code, Codex, Cursor, **OpenCode**, Copilot, Antigravity, Pi |
| `VoltAgent/awesome-agent-skills` | 35 651 | 2026-10-13 | 1 000+ skills from official dev teams, harness-agnostic |
| `awesome-opencode/awesome-opencode` | 10 572 | 2026-07-03 | the curated index; useful as a map more than as an install |
| `FrancoStino/opencode-skills-collection` | 84 | 2026-10-13 | 2 400+ skills behind a **SkillPointer** layer — see §3 |

Skip: `defuj/opencode-agent-kit` (16 stars), `mikestopcontinues/agent-skills`
(0 stars, 0 forks), `chaturvediabhay24/opencode` (1 star, a research fork, not
a plugin).

`wshobson/agents` is the most credible of these because it is a *single source*
emitted per-harness rather than a pile of copied Markdown, and it has an
OpenCode install path (`make install-opencode`).

---

3. Token reduction — the part that actually moves the needle

This is where the real research is, and the numbers are strong enough to be
worth acting on. Ordered by measured effect.

CliffCompaction — `nguyenvuthientrang/cliffcompaction` (56★, MIT, 2026-09-23)

Let the context grow until a budget is hit, then compact by **truncating and
dropping, never rephrasing**. Never compacts a compaction: each pass works only
on original content and discards the previous compacted block.

- Up to **50 % cost reduction**; cache-read cost down **up to 90 %**
- Cache stays valid between compaction events, which is why the cost curve is a
  series of cliffs rather than a ramp
- Scaffold-agnostic API proxy, works with Claude Code and Codex
- Where the money is, measured: on Terminal-Bench 2.0 tool results are **56 %**
  of context tokens and tool calls **28 %** — together **84 %**

The "never rephrase" rule is the important engineering insight: summarisation
chains compound loss, so CliffCompaction refuses to summarise at all.

SWE-Pruner — arXiv 2601.16746

Task-aware line-level pruning driven by an explicit *goal hint* ("focus on error
handling"), implemented with a 0.6 B skimmer.

- **23–54 % token reduction**, and success rate *up* 1.2–1.4 pp
- Reads are **76.1 %** of agent tokens (execute 12.1 %, edit the rest)
- Cost per task falls because retrieval rounds shrink, not just because prompts
  shrink

This is the one result that reduces tokens **and** improves accuracy, because it
removes noise rather than information.

AgentDiet — ACM PACMSE 2026

Removes useless/redundant/expired spans from trajectories at inference time.

- Input tokens **−39.9 % to −59.7 %**, total cost **−21.1 % to −35.9 %**,
  same performance

CORVUS — arXiv 2607.22711

Decouples file-read actions from their observations by keeping a synchronised
registry and injecting only *current* file contents, so stale snapshots never
accumulate.

- **9–50 %** fewer input tokens, **15–32 %** shorter final prompts, up to
  **37 %** fewer reasoning rounds

This one is directly relevant to how I work: I re-read files and each read is a
full fresh copy in the trajectory. A synchronised registry makes that a
reference instead.

DTOC — arXiv 2609.26121, built on OpenCode

Reversible tool-output compression: full outputs live in external memory, the
active context holds a placeholder, the agent re-enables on demand.

- **67–72 % lower cost per solved task** on compression-responsive models
  (Sonnet 4.6, GPT-5.4); solve rates up too
- **The ablation is the finding**: tracking alone *hurt* (87 K tokens, degraded
  success); disable-only worked better still; **full reversible compression was
  best** (83.6 % at 61 K tokens, re-reads eliminated). Reversibility is not
  optional.

TokenPilot — arXiv 2606.17016

Dual-granularity, cache-aware. **61–87 % cost reduction.**

CAT / "Context as a Tool" — ACL Findings 2026

Makes context management a *callable tool* the agent invokes when a subtask
completes, rather than a background heuristic. Context stays ~35 K tokens where
ReAct's saturates and then degrades after ~60 rounds.

The negative result that must not be skipped — minification

arXiv 2609.26121's sibling study (ICPC 2026): stripping comments, docstrings and
whitespace from retrieved source cut input tokens **42 %** and cost **93 %** of
which is input — and dropped resolution **50.0 % → 38.0 %, i.e. −12 pp
absolute**.

**So "reduce tokens" done crudely makes the agent measurably dumber.** Every
result above that also gained accuracy did it by removing *stale and redundant*
content, not by removing information. That distinction is the whole game, and it
is the reason to prefer SWE-Pruner/DTOC-style pruning over minification.

---

4. Skills

Already installed and load-bearing: `karpathy-guidelines`,
`software-design-philosophy`, `no-slop-code`, `paranoid-audit`, `brat-lang`.

The SkillPointer idea (`FrancoStino/opencode-skills-collection`) is worth
understanding even at 84 stars: OpenCode advertises *every* discovered skill's
ID + name + description at each step, so 2 400 skills means 2 400 advertised
entries. The plugin keeps skills in a hidden vault and registers ~100 pointers
that are loaded on demand. That is the correct shape for a large library and it
is the only token-safe way to scale skills past a few dozen.

The user's own 103-skill pile in `~/.config/opencode/skills/` is the same
problem the other way round: it is not loaded by this harness, so it costs
nothing at runtime, but it is a maintenance surface. Left as-is per instruction.

---

5. Honest assessment for this project

**Highest value, lowest risk:**
1. **Adopt CliffCompaction's rule manually, no install.** "Never rephrase, only
   truncate/drop; never compact a compaction" is a discipline I can apply to how
   I keep notes across a long session, and it needs no plugin and no CPU. This
   is the single best value-per-risk item on the page.
2. **Keep a synchronised file registry (CORVUS) instead of re-reading.** I
   re-read `crt.h` several times this session, each a full fresh copy. A short
   "what I have already read and its revision" note removes those reads.
3. **`alvinunreal/oh-my-opencode-slim` if orchestration is wanted at all** —
   MIT, 9.5k★, actively maintained, explicitly token-reduced. Not the 70k
   battery-included one.

**Do not do:**
- Install the 70k `oh-my-openagent` on this box. Parallel agents on 2 cores /
  3.7 GB is the freeze scenario, and the whole session's evidence says so.
- Any minification-style context reduction. Measured −12 pp accuracy.
- `opencode-dynamic-context-pruning` (4.3k★) — **AGPL-3.0**, a licensing
  question for this project before it is a technical one.

**The one-line answer:** the accuracy gains are real and come from *not*
keeping stale copies; the token savings are real and come from the same act.
Both are available with discipline rather than software, and on this hardware
that is the better trade.
