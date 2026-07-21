# capacities_scripts

Personal ingestion pipelines into [Capacities](https://capacities.io) ("Dillon's Mind Map" space).

## Hue clock in/out → daily note

**Run it:** double-click `~/Desktop/Hue Clock.app` — a 🟢/🔴 icon appears in the
menu bar; click it for "In for 1h 23m" / today's totals; quit from the menu.
The title is deliberately emoji-only to stay narrow: on notched MacBooks,
macOS silently hides menu bar items that don't fit left of the notch — if the
icon is missing, free up space (hide/quit another menu bar item).
The app embeds the listener below and logs to `~/Library/Logs/hue-clock.log`.
To auto-start at login: System Settings → General → Login Items → add the app.
A single-instance lock prevents double-logging if the CLI listener is also started.
All config (Capacities token, bridge IP/key, lamp name) lives in `.env` (chmod 600).

Logging is append-only (event-sourcing style — nothing in Capacities is ever
overwritten). Projections ride on the event line that made them knowable:

    🟢 9:12a                      clock in
    🔴 12:40p · 3h 28m            clock out · session length
    🟢 1:32p · 52m break          clock in · gap since last clock-out
    🔴 5:40p · 4h 08m · Σ 7h 36m  clock out · session · running day total

Day total = the Σ on the last 🔴 line. Live "right now" totals come from the
local state file (menu bar icon/menu, or `hue_clock_listener.py status`).

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

`hue_clock_listener.py` — the focus lamp is the source of truth: Hue Smart
Button toggles the lamp (configured natively in the Hue app), and this listener
subscribes to the bridge's CLIP v2 event stream and appends `🟢 Clocked in` /
`🔴 Clocked out — <duration>` lines to the daily note on lamp transitions.
The bridge keeps no event history, so the listener runs on the always-on Mac
mini via launchd (`launchd/com.dillonoleary.hue-clock.plist`, KeepAlive).
Missed transitions are logged with an "approx" marker on reconnect.

Setup:
1. Register an app key (press the bridge's link button, then within ~30s):
   `curl -sk -X POST https://<bridge-ip>/api -d '{"devicetype":"capacities_scripts#macmini","generateclientkey":true}'`
2. Put `HUE_BRIDGE_IP`, `HUE_APP_KEY`, `FOCUS_LIGHT_NAME` in `.env`
   (`python3 hue_clock_listener.py lights` lists light names).
3. In the Hue app, map the Smart Button to toggle the focus lamp.
4. Deploy: copy this directory to the mini, install the plist to
   `~/Library/LaunchAgents/`, `launchctl load` it, and keep the mini awake
   (`sudo pmset -a sleep 0`).

## Screen Time → daily note

`screentime_report.py` reads Apple Screen Time's synced store on this Mac
(`$(getconf DARWIN_USER_DIR)/com.apple.ScreenTimeAgent/Store/RMAdminStore-Cloud.sqlite`)
and appends a per-device, per-app summary to the matching Capacities daily note.
With Screen Time's **Share Across Devices** enabled, the store includes iPhone/iPad
usage too, so one Mac-side job covers every device. Apple only keeps ~30 days of
Screen Time history — the daily notes become the permanent archive.

### One-time setup

1. **Full Disk Access** (System Settings → Privacy & Security → Full Disk Access):
   - add **iTerm** (for running the script by hand) — restart iTerm afterwards
   - add **/usr/bin/python3** (for the scheduled launchd run): press
     ⌘⇧G in the file picker and type `/usr/bin/python3`
2. Token lives in `.env` (git-ignored, chmod 600). Rotate it in
   Capacities → Settings → Capacities API if it ever leaks.

### Usage

```sh
python3 screentime_report.py inspect            # sanity-check DB tables
python3 screentime_report.py report             # print today's summary
python3 screentime_report.py push               # append to today's daily note
python3 screentime_report.py push --date yesterday
```

`push` is idempotent per day — it skips if the daily note already has a
"Screen Time —" section for that date.

### Schedule (nightly 23:45)

```sh
cp launchd/com.dillonoleary.screentime-capacities.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.dillonoleary.screentime-capacities.plist
launchctl start com.dillonoleary.screentime-capacities   # test-fire once
tail -f ~/Library/Logs/screentime-capacities.log
```

Note: iPhone→Mac Screen Time sync (CloudKit) can lag by minutes-to-hours. If the
23:45 numbers look short on phone-heavy days, switch the plist to a morning hour
and change the program argument to `--date yesterday`.

## Capacities API notes

- New API (the beta at `api.capacities.io/docs` is deprecated, EOL 2026-09-01)
- Base URL `https://api.capacities.io`, header `X-Capacities-Api-Version: 0.1.0`,
  bearer token bound to one space
- OpenAPI spec: https://developers.capacities.io/openapi.json
- Rate limit: 30 req/min per endpoint
- `capacities_client.py` is a stdlib-only client: daily-note append, object
  CRUD via markdown, search, block delete
