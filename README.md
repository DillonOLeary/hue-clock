# Hue Clock

Clock in/out with a [Philips Hue](https://www.philips-hue.com) focus lamp —
every transition is recorded as an immutable domain event, and projected
into the daily note in [Capacities](https://capacities.io). A macOS menu bar
app shows live status.

The lamp is the source of truth ("on" = clocked in). The Hue app maps a Smart
Button to toggle the lamp; this tool never controls anything, it only projects
lamp state into an event store and onward into Capacities. Toggling via
button, app, or Siri logs identically.

This is also a teaching codebase for DDD, event sourcing, and clean
architecture, built on the [`eventsourcing`](https://eventsourcing.readthedocs.io)
library with SQLite event stores — see **[ARCHITECTURE.md](ARCHITECTURE.md)**
for the concept-by-concept map.

## Quickstart

```sh
uv sync                                  # creates .venv, installs deps
cp .env.example .env && chmod 600 .env   # fill in the values (see Setup below)
uv run hue-clock-listener lights         # find your focus lamp's exact name
uv run hue-clock                         # launch the menu bar app
# optional: build the dockable Hue Clock.app — see "Dock app" below
```

## Menu bar app

`uv run hue-clock` puts a 🟢/🔴 icon in the menu bar; click it for
"In for 1h 23m" / today's totals. Quitting (menu, Dock, or logout) clocks
you out and gives queued lines one bounded shot at Capacities. The title is
deliberately emoji-only to stay narrow: on notched MacBooks, macOS silently
hides menu bar items that don't fit left of the notch — if the icon is
missing, free up space (hide/quit another menu bar item). The app embeds the
listener and logs to `~/Library/Logs/hue-clock.log`. A single-instance lock
prevents double-logging if the CLI listener is also started. All config
(Capacities token, bridge IP/key, lamp name) lives in `.env` (chmod 600).

## Design

Event-sourced end to end. Lamp transitions become `WorkDay` events (one
aggregate per calendar day) in `~/.local/state/hue_clock/time_tracking.db`;
a process application follows that stream exactly-once and materializes
`DailyNote` aggregates — rendered note lines plus a durable outbox — in
`capacities_note.db`. Live totals (menu bar, `status`) are read-side queries
over the same events. Nothing in Capacities is ever overwritten; projections
ride on the event line that made them knowable:

    🟢 9:12a                      clock in
    🔴 12:40p · 3h 28m            clock out · session length
    🟢 1:32p · 52m break          clock in · gap since last clock-out
    🔴 5:40p · 4h 08m · Σ 7h 36m  clock out · session · running day total

Day total = the Σ on the last 🔴 line.

**Strikes (tombstones):** menu bar → "Strike work time" lists today's
sessions (`9:12a–12:40p · 3h 28m`); click one to tombstone the whole session.
Already-struck sessions show ⚫ and are disabled. "Custom…" strikes a trailing
window (minutes back from now) for partial cases. A strike appends one line —
`⚫ 1:32p–2:02p · −30m` — and never edits existing lines; the subtraction
shows up in later Σ values and menu totals. Only overlap with clocked-in
sessions is subtracted (striking across a break can't over-subtract), and
overlapping strikes merge.

**Publishing to Capacities** is outbox-based and write-only: each line commits
to the event store atomically with the transition that caused it, and a flusher
thread appends it — at-least-once, retrying every 60s until a 200. The app
never reads or edits the note, so it's yours to annotate freely. Rare duplicate
lines are possible (a crash or concurrent flush between an append's 200 and the
local pop) and are cleaned up by hand. The menu shows ⏳ with queue depth while
lines drain, escalating to ⚠️ after a couple of minutes stuck (check that
Capacities is reachable). The full design story, including why read-back was
removed, is in ARCHITECTURE.md.

The listener subscribes to the bridge's CLIP v2 event stream. The bridge
keeps no event history — if the process is down, missed transitions are
detected on reconnect (last event vs. live lamp state) and recorded with an
`*(approx)*` marker, since their true timestamps are lost. Run it on an
always-on machine.

## CLI

```sh
uv run hue-clock-listener lights           # list lights (find the focus lamp's name)
uv run hue-clock-listener status           # current clock state and today's totals
uv run hue-clock-listener run              # headless listener, no menu bar (foreground)
uv run hue-clock-listener import-history   # one-off: seed the event store from the legacy log
```

## Setup

1. Register a Hue app key (press the bridge's link button, then within ~30s):
   `curl -sk -X POST https://<bridge-ip>/api -d '{"devicetype":"hue_clock#macmini","generateclientkey":true}'`
2. Put `HUE_BRIDGE_IP`, `HUE_APP_KEY`, `FOCUS_LIGHT_NAME` in `.env`
   (`uv run hue-clock-listener lights` lists light names). Add
   `CAPACITIES_API_TOKEN` from Capacities → Settings → Capacities API
   (rotate it there if it ever leaks).
3. In the Hue app, map the Smart Button to toggle the focus lamp.

## Dock app

Nothing auto-starts — you decide when tracking runs. Build the app bundle:

```sh
uv run --python /opt/homebrew/opt/python@3.13/bin/python3.13 --group build scripts/make_app.py
mkdir -p ~/.config/hue_clock && ln -sf "$PWD/.env" ~/.config/hue_clock/.env
```

py2app requires a *framework* build of Python (uv's managed interpreters are
not one; the script checks and says so). Homebrew's is a native arm64
framework build; python.org's universal2 build also works but py2app's stub
picked its Intel slice under Rosetta here, so prefer Homebrew.

This renders the icon and has py2app assemble `~/Applications/Hue Clock.app`
as a real, standalone bundle: interpreter and dependencies embedded, and the
bundle's executable *is* the Python process. (The previous thin launcher
exec'd the venv binary — macOS 26 never renders a menu bar icon for
processes that exec away from their bundle identity, FB21015611.) Drag it to
the Dock once; click it to start tracking, quit from the menu bar (or
right-click the Dock icon → Quit). While running it shows in the Dock; the
single-instance lock makes an accidental double-launch harmless.

The bundle snapshots code and dependencies — rerun the build after changing
either. Config is read from `~/.config/hue_clock/.env` (the symlink above),
falling back to an upward `.env` walk from the working directory, which is
what CLI runs from inside the repo hit.

## Tests

```sh
uv run python -m unittest discover -s tests -t .
uv run pre-commit install   # once per clone: ruff + tach check gate every commit
```

Four rings, mirroring the architecture: pure domain tests (no I/O),
application tests on in-memory persistence, projection/flusher tests with
fakes, and one end-to-end run through real SQLite with a scripted bridge.

## Capacities API notes

- Base URL `https://api.capacities.io`, header `X-Capacities-Api-Version: 1.0.0`,
  bearer token bound to one space
- OpenAPI spec: https://developers.capacities.io/openapi.json
- Rate limit: 30 req/min per endpoint
- `src/hue_clock/adapters/capacities_api.py` is a urllib client (tenacity for
  transient retries): daily-note append, object CRUD via markdown, search,
  block delete
