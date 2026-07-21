"""Clock in/out by lamp: observe Hue focus-lamp transitions, record them as
domain events, and project them into the Capacities daily note.

Usage:
  hue-clock-listener lights            # list lights (find the focus lamp's name)
  hue-clock-listener status            # current clock state and today's totals
  hue-clock-listener run               # headless listener (foreground)
  hue-clock-listener dedupe [DATE]     # delete duplicated clock lines (default: today)
  hue-clock-listener import-history    # seed the event store from the legacy log
"""
import datetime as dt
import sys

from hue_clock.adapters.capacities_api import CapacitiesClient
from hue_clock.adapters.hue_bridge import HueBridge
from hue_clock.adapters.note_publisher import CapacitiesNotePublisher
from hue_clock.application.time_tracking import TimeTracking
from hue_clock.formatting import format_clock, format_duration
from hue_clock.projections.capacities_note.projection import CapacitiesNoteProjection
from hue_clock.runtime.composition import persistence_env
from hue_clock.runtime.config import load_config, require
from hue_clock.runtime.daemon import start_daemon


def cmd_lights():
    for light in _bridge().lights():
        on = "on " if light["on"]["on"] else "off"
        print(f"[{on}] {light['metadata']['name']}  ({light['id']})")


def cmd_status():
    now = dt.datetime.now()
    tracking = TimeTracking(env=persistence_env())
    status = tracking.clock_status(now)
    if status is None:
        print("no history yet")
    elif status.is_clocked_in:
        elapsed = format_duration((now - status.since).total_seconds())
        print(f"🟢 clocked in since {format_clock(status.since)} ({elapsed})")
    else:
        print("🔴 clocked out")

    summary = tracking.day_summary(now.date(), now)
    if summary:
        struck = (f", struck {format_duration(summary.struck_seconds)}"
                  if summary.struck_seconds >= 60 else "")
        print(f"today: worked {format_duration(summary.worked_seconds)} over "
              f"{summary.session_count} session(s), "
              f"away {format_duration(summary.away_seconds)}{struck}")

    queue = CapacitiesNoteProjection(env=persistence_env()).queue_status(now.date())
    if queue.has_pending:
        state = "sent, awaiting confirmation" if queue.head_sent else "not yet sent"
        print(f"queued: {queue.pending} line(s); head {state}")


def cmd_run():
    lock, runtime, listener = start_daemon()  # noqa: F841 — lock held for process lifetime
    listener.run()


def cmd_dedupe(date_str=None):
    day = dt.date.fromisoformat(date_str) if date_str else dt.date.today()
    removed = _publisher().scrub_adjacent_duplicates(day)
    print(f"deleted {removed} duplicate clock line(s) from {day}")


def cmd_import_history(log_path=None):
    from hue_clock.history_import import import_history

    import_history(log_path)


def _bridge(config=None):
    config = config or load_config()
    return HueBridge(
        require(config.bridge_ip, "HUE_BRIDGE_IP"),
        require(config.bridge_key, "HUE_APP_KEY"),
    )


def _publisher(config=None):
    config = config or load_config()
    token = require(config.capacities_token, "CAPACITIES_API_TOKEN")
    return CapacitiesNotePublisher(CapacitiesClient(token))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "lights":
        cmd_lights()
    elif cmd == "status":
        cmd_status()
    elif cmd == "run":
        cmd_run()
    elif cmd == "dedupe":
        cmd_dedupe(arg)
    elif cmd == "import-history":
        cmd_import_history(arg)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
