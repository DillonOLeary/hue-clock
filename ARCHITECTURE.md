# Architecture

Hue Clock is deliberately over-architected for its size. It is a teaching
codebase for **domain-driven design**, **event sourcing**, and **clean
architecture**, built on the [`eventsourcing`](https://eventsourcing.readthedocs.io)
library with SQLite event stores. This document maps each concept to the code
that embodies it.

## The story in one paragraph

A lamp is observed. Every observed transition becomes an immutable domain
event in an append-only store — the single source of truth. Everything else
you can see — the menu bar totals, the lines in the Capacities daily note,
the `status` command — is a *projection* of those events, rebuildable at any
time by replaying them. Nothing is ever updated in place; new facts are
appended, and derived views catch up.

## Bounded contexts

```
┌─────────────────────────┐         ┌──────────────────────────────────┐
│  Time Tracking (core)   │ events  │ Capacities Note Publishing       │
│                         │────────▶│ (supporting)                     │
│  WorkDay aggregate      │  pipe   │  DailyNote aggregate             │
│  sessions, strikes,     │         │  line rendering, outbox,         │
│  rollover, totals       │         │  write-only publish              │
└─────────────────────────┘         └──────────────────────────────────┘
```

The two contexts share nothing but the event stream between them (an
`eventsourcing` `System` pipe). Time Tracking knows nothing about markdown,
HTTP, or Capacities. The projection knows nothing about Hue bridges. Each has
its own SQLite database (`~/.local/state/hue_clock/*.db`).

## Concept → code

| Concept | Where | What to look for |
|---|---|---|
| Value objects | `domain/ledger.py` | `TimeSpan`, `Session`, `DayLedger` — immutable, pure math, no identity |
| Aggregate + invariants | `domain/work_day.py` | `WorkDay` guards *no double clock-in*, *no out-of-order transitions*, *strikes inside the day* |
| Guard vs. apply split | `domain/work_day.py` | public commands (`clock_in`) validate and raise; `@event`-decorated privates (`_clocked_in`) only mutate. Guards must stay out of apply methods — apply re-runs on every replay |
| Deterministic identity | `WorkDay.create_id` | one aggregate per calendar day via `uuid5(date)`; streams stay tiny, no snapshots needed |
| Application service | `application/time_tracking.py` | `TimeTracking` — command handlers (`record_lamp_state`, `strike_span`), the midnight-rollover cascade saved atomically with `save(*aggregates)`, and read-side queries |
| Process manager, exactly-once | `projections/capacities_note/projection.py` | `policy()` materializes `DailyNote` events atomically with a tracking record — a crash between contexts can never double-process |
| Event-sourced projection state | `projections/capacities_note/daily_note.py` | the projection's own state (rendered lines, outbox queue) is itself an event stream |
| Outbox pattern | `daily_note.py` + `flusher.py` | queue a line atomically with the fact that caused it; publish later, at-least-once, behind an idempotent boundary |
| Port | `projections/capacities_note/ports.py` | `NotePublisher` protocol — what the projection needs, not what Capacities offers |
| Adapters | `adapters/` | `CapacitiesNotePublisher`, `CapacitiesClient`, `HueBridge` — all replaceable, none imported by inner layers |
| Composition root | `runtime/composition.py`, `runtime/daemon.py` | the only place concrete adapters meet the application; everything is wired here and nowhere else |
| Anti-corruption at the edge | `runtime/hue_listener.py` | translates Hue's SSE payloads into domain commands; the domain never sees bridge JSON |
| Legacy migration as events | `history_import.py` | the old log replayed as first-class events with `IMPORTED` provenance |

## Bitemporality and provenance

Every domain event carries `at` — the moment the transition *occurred* — as
event data. The library's built-in `timestamp` records when the event was
*stored*. Domain math only ever uses `at`; the two differ for imported
history and reconciled gaps. This occurred-vs-recorded distinction is what
lets an event store absorb late-arriving truth without lying.

`Provenance` says *how we know*:

- `OBSERVED` — the bridge told us live; publish the line.
- `RECONCILED` — inferred at reconnect; the true time is lost, so the line is
  published with an `*(approx)*` marker.
- `ROLLOVER` — bookkeeping at midnight; extends ledgers, publishes nothing.
- `IMPORTED` — legacy history; extends ledgers, publishes nothing (the lines
  are already in Capacities).

The projection makes all publish/archive decisions from provenance alone — no
flags, no side channels.

## The dependency rule

```
domain  ◀─  application  ◀─  projections  ◀─  runtime / cli / menubar
   ▲                              ▲
   └────────── adapters ──────────┘   (adapters implement ports; only the
                                       composition root touches both sides)
```

One accepted impurity: `domain/` imports `eventsourcing.domain.Aggregate`.
Purists would keep the domain framework-free and add a mapping layer; that
layer would re-implement half the library to buy nothing this project needs.
The line drawn: the domain may import `eventsourcing.domain`, never
`.application`, `.system`, or `.persistence`.

## The persistence seam

`runtime/composition.py` selects storage entirely through configuration —
`PERSISTENCE_MODULE=eventsourcing.sqlite` plus two database paths. Swapping
SQLite for Postgres (or a custom Turso/libSQL module) would touch that one
function. No domain, application, or projection code knows SQLite exists.

## Why the outbox looks the way it does (the 2026-07-21 incident)

The naive design — append a line to Capacities inside the event-handling
path, verify by reading it back, retry on failure — melted down in
production. The Capacities read API served hours-stale content while appends
returned 200: every read-back "failure" triggered a re-append, and one break
line was duplicated 23 times while the queue reported itself stuck.

The failure taught a distinction the current design encodes:

- **Send** is decoupled from **confirm**. A 200 is trusted; `LineSent` is
  recorded and the flusher moves on.
- A missing read-back means *unconfirmed*, never *resend now*. Only a line
  still verifiably absent after a grace period (10 minutes, doubling per
  resend, capped at an hour) is re-sent — that covers genuine loss, the
  other observed failure mode, where the Capacities desktop app clobbers
  server-side appends when its sync wedges.
- After a resent line finally confirms, adjacent duplicate clock lines are
  scrubbed. Transitions alternate 🟢/🔴 and the queue is head-blocking, so
  legitimate repeats always have another clock line between them — only
  incident duplicates are ever adjacent. User prose is never touched.
- Publishing is at-least-once + idempotent boundary; the event log is
  exactly-once. Each guarantee lives where it is actually achievable.

### Postscript: read-back removed (2026-07-23)

Read-back confirmation was later retired entirely, and publishing is now
write-only: a 200 pops the queued line, an error keeps it for the next pass.
Two things made read-back untenable:

- **The note is hand-edited.** Confirmation assumed the note mirrors the
  outbox, but the note is the user's own document — any manual edit means a
  queued line's exact text is never found, so it resends forever.
- **Markdown doesn't round-trip.** The API returns `*(approx)*` as rendered
  token text (`(approx)`, no asterisks), so exact-match confirmation of any
  markdown line could never succeed — four lines wedged the queue before this
  was caught.

The lesson: read-back only works when you own both sides of the mirror. Here we
don't, so the design collapses to plain at-least-once. The retired events
(`HeadSent`, `HeadResent`, `DuplicatesScrubbed`) are **kept in the aggregate**,
uncalled — the `eventsourcing` library resolves each stored event back to its
nested class and replays its method, so deleting them would break replay of
existing stores. `LineQueued` also gained an optional `at` field
(backward-compatible: old events carry only `text`). Both are everyday examples
of schema evolution in an event-sourced system: you retire behavior without
retiring history.

## Runtime shape

```
main thread — rumps menu bar
  15s timer: advance_to(now) + queries (clock_status, day_summary,
             sessions, queue_status); strike clicks → commands

listener thread — runtime/hue_listener.py
  SSE loop: reconcile on (re)connect, then each lamp event →
  record_lamp_state() → [policy runs synchronously, same thread]
  → wake flusher

flusher thread — runtime/note_flusher_loop.py
  wakes on event or 60s: NoteFlusher.flush() — all network I/O lives here,
  never on the other threads
```

All writes are serialized by one lock (`TrackerRuntime.commands`). The
flusher performs HTTP outside the lock and re-loads + re-checks the head
inside it before saving, so a lamp event landing mid-flush is never lost to a
stale save. Reads are lock-free (SQLite WAL).

## Event catalog

**Time Tracking — `WorkDay`** (id = uuid5 of the date)

| Event | Data |
|---|---|
| `Opened` | `day` |
| `ClockedIn` | `day, at, provenance` |
| `ClockedOut` | `day, at, provenance` |
| `PeriodStruck` | `day, start, end, at, provenance` |

**Capacities Note Publishing — `DailyNote`** (id = uuid5 of the date)

| Event | Data | Meaning |
|---|---|---|
| `Opened` | `day` | |
| `TransitionNoted` | `kind, at` | extends the rendering ledger |
| `StrikeNoted` | `start, end` | extends the rendering ledger |
| `LineQueued` | `text, at` | enters the outbox (`at` = when queued) |
| `HeadConfirmed` | `at` | published (200 received); queue advances |
| `HeadSent` / `HeadResent` | `at` | historical (read-back era, retired 2026-07-23) |
| `DuplicatesScrubbed` | `at, removed` | historical (read-back era, retired 2026-07-23) |

## What the log file is now

`~/Library/Logs/hue-clock.log` used to be the replayable source of truth. It
is now pure observability — one printed line per published transition plus
diagnostics. The event stores are the record; `history_import.py` was the
one-way bridge from the old world.
