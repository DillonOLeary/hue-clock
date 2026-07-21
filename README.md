# Hue Clock

Clock in/out with a [Philips Hue](https://www.philips-hue.com) focus lamp —
every transition is appended, event-sourcing style, to the daily note in
[Capacities](https://capacities.io). A macOS menu bar app shows live status.

The lamp is the source of truth ("on" = clocked in). The Hue app maps a Smart
Button to toggle the lamp; this tool never controls anything, it only projects
lamp state into Capacities. Toggling via button, app, or Siri logs identically.

## Quickstart

```sh
uv sync                                  # creates .venv, installs rumps/pyobjc
cp .env.example .env && chmod 600 .env   # fill in the values (see Setup below)
uv run hue-clock-listener lights         # find your focus lamp's exact name
uv run hue-clock                         # launch the menu bar app
uv run scripts/make_app.py               # optional: build the dockable Hue Clock.app
```

## Menu bar app

`uv run hue-clock` puts a 🟢/🔴 icon in the menu bar; click it for
"In for 1h 23m" / today's totals; quit from the menu. The title is
deliberately emoji-only to stay narrow: on notched MacBooks, macOS silently
hides menu bar items that don't fit left of the notch — if the icon is
missing, free up space (hide/quit another menu bar item). The app embeds the
listener and logs to `~/Library/Logs/hue-clock.log`. A single-instance lock
prevents double-logging if the CLI listener is also started. All config
(Capacities token, bridge IP/key, lamp name) lives in `.env` (chmod 600).

## Design

Logging is append-only (event-sourcing style — nothing in Capacities is ever
overwritten). Projections ride on the event line that made them knowable:

    🟢 9:12a                      clock in
    🔴 12:40p · 3h 28m            clock out · session length
    🟢 1:32p · 52m break          clock in · gap since last clock-out
    🔴 5:40p · 4h 08m · Σ 7h 36m  clock out · session · running day total

Day total = the Σ on the last 🔴 line. Live "right now" totals come from the
local state file at `~/.local/state/hue_clock.json` (menu bar icon/menu, or
`uv run hue-clock-listener status`). The permanent second-precision event
log is `~/Library/Logs/hue-clock.log` — lost lines are replayable from it.

**Strikes (tombstones):** menu bar → "Strike work time" lists today's
sessions (`9:12a–12:40p · 3h 28m`); click one to tombstone the whole session.
Already-struck sessions show ⚫ and are disabled. "Custom…" strikes a trailing
window (minutes back from now) for partial cases. A strike appends one line —
`⚫ 1:32p–2:02p · −30m` — and never edits existing lines; the subtraction
shows up in later Σ values and menu totals. Only overlap with clocked-in
sessions is subtracted (striking across a break can't over-subtract), and
overlapping strikes merge. Strikes are per-day, stored in the state file.

Writes are queue-backed: each line is committed to a local pending queue
first, then appended to Capacities and verified by read-back (the desktop app
can silently clobber API appends when its sync wedges — seen after it sits
open on the note across a Mac sleep). Lines that fail or vanish stay queued
and flush automatically on the next press, reconnect, or listener restart —
the menu shows "⚠️ N lines queued — restart Capacities" until they land.
Fix for a wedged app: fully quit and reopen Capacities.

The listener subscribes to the bridge's CLIP v2 event stream. The bridge
keeps no event history — if the process is down, missed transitions are
detected on startup (state file vs. live lamp state) and logged as a
reconciliation entry, but their true timestamps are lost. Run it on an
always-on machine.

## CLI

```sh
uv run hue-clock-listener lights   # list lights (find the focus lamp's name)
uv run hue-clock-listener status   # print current clock state and today's totals
uv run hue-clock-listener run      # headless listener, no menu bar (foreground)
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
uv run scripts/make_app.py
```

This renders the icon (Pillow runs in an ephemeral env via uv's inline
script dependencies — no change to the project venv) and assembles
`~/Applications/Hue Clock.app`, a thin launcher that cd's to this repo and
execs `.venv/bin/hue-clock`. Drag it to the Dock once; from then on click it
to start tracking, and quit from the menu bar (or right-click the Dock
icon → Quit). While running it shows in the Dock; the single-instance lock
makes an accidental double-launch harmless.

The bundle hardcodes this repo's absolute path — after moving or renaming
the repo, rerun `uv sync` and then `uv run scripts/make_app.py`.

## Capacities API notes

- New API (the beta at `api.capacities.io/docs` is deprecated, EOL 2026-09-01)
- Base URL `https://api.capacities.io`, header `X-Capacities-Api-Version: 0.1.0`,
  bearer token bound to one space
- OpenAPI spec: https://developers.capacities.io/openapi.json
- Rate limit: 30 req/min per endpoint
- `src/hue_clock/capacities.py` is a stdlib-only client: daily-note append,
  object CRUD via markdown, search, block delete
