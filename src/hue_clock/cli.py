"""Observe Hue focus-lamp transitions and project them into Capacities.

Each transition is recorded as a domain event, then projected into the
Capacities daily note.

Usage:
  hue-clock-listener lights            # list lights (find the focus lamp's name)
  hue-clock-listener status            # current clock state and today's totals
  hue-clock-listener run               # headless listener (foreground)
  hue-clock-listener import-history    # seed the event store from the legacy log
"""

import datetime as dt
import sys

from hue_clock.adapters.hue_bridge import HueBridge
from hue_clock.application.time_tracking import ClockedIn, TimeTracking
from hue_clock.formatting import format_clock, format_duration
from hue_clock.projections.capacities_note.projection import CapacitiesNoteProjection
from hue_clock.runtime.config import load_config, require
from hue_clock.runtime.daemon import start_daemon
from hue_clock.runtime.tracker_runtime import persistence_env


def cmd_lights() -> None:
    for light in _bridge().lights():
        on = "on " if light["on"]["on"] else "off"
        print(f"[{on}] {light['metadata']['name']}  ({light['id']})")


def cmd_status() -> None:
    now = dt.datetime.now()
    tracking = TimeTracking(env=persistence_env())
    status = tracking.clock_status(now)
    if status is None:
        print("no history yet")
    elif isinstance(status, ClockedIn):
        elapsed = format_duration((now - status.since).total_seconds())
        print(f"🟢 clocked in since {format_clock(status.since)} ({elapsed})")
    else:
        print("🔴 clocked out")

    summary = tracking.day_summary(now.date(), now)
    if summary:
        struck = (
            f", struck {format_duration(summary.struck_seconds)}"
            if summary.struck_seconds >= 60
            else ""
        )
        print(
            f"today: worked {format_duration(summary.worked_seconds)} over "
            f"{summary.session_count} session(s), "
            f"away {format_duration(summary.away_seconds)}{struck}"
        )

    queue = CapacitiesNoteProjection(env=persistence_env()).queue_status(now.date())
    if queue.has_pending:
        print(f"queued: {queue.pending} line(s)")


def cmd_run() -> None:
    daemon = start_daemon()
    daemon.listener.run()


def cmd_import_history(log_path=None) -> None:
    # Imported lazily: the one-off importer isn't needed on the hot CLI paths.
    from hue_clock.history_import import import_history  # noqa: PLC0415

    import_history(log_path)


def _bridge(config=None) -> HueBridge:
    config = config or load_config()
    return HueBridge(
        require(config.bridge_ip, "HUE_BRIDGE_IP"),
        require(config.bridge_key, "HUE_APP_KEY"),
    )


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "lights":
        cmd_lights()
    elif cmd == "status":
        cmd_status()
    elif cmd == "run":
        cmd_run()
    elif cmd == "import-history":
        cmd_import_history(arg)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
