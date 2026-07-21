"""One-off import of the legacy hue-clock.log into the event store.

Lines like "[2026-07-21T11:05:07] 🟢 11:05a" are the replayable record the old
implementation kept; everything else in the log is diagnostics. Imported
transitions carry IMPORTED provenance, so the projection archives them into
its ledger without re-publishing lines that are already in Capacities."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from hue_clock.domain.work_day import Provenance
from hue_clock.runtime.composition import TrackerRuntime
from hue_clock.runtime.config import LOG_FILE
from hue_clock.runtime.instance_lock import acquire_single_instance_lock

TRANSITION = re.compile(
    r"^\[(?P<at>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\] (?P<mark>🟢|🔴|⚫) (?P<rest>.*)$"
)
STRIKE_WINDOW = re.compile(r"^(?P<start>\d{1,2}:\d{2}[ap])–(?P<end>\d{1,2}:\d{2}[ap])")


@dataclass(frozen=True)
class LoggedTransition:
    at: dt.datetime
    mark: str
    strike_span: tuple[dt.datetime, dt.datetime] | None = None


def parse_log_lines(text: str) -> list[LoggedTransition]:
    transitions = []
    for line in text.splitlines():
        match = TRANSITION.match(line.strip())
        if not match:
            continue
        at = dt.datetime.fromisoformat(match["at"])
        mark = match["mark"]
        strike_span = _parse_strike_span(match["rest"], at) if mark == "⚫" else None
        if mark == "⚫" and strike_span is None:
            continue
        transitions.append(LoggedTransition(at, mark, strike_span))
    return transitions


def import_history(log_path: str | None = None, runtime: TrackerRuntime | None = None) -> None:
    path = Path(log_path) if log_path else LOG_FILE
    transitions = parse_log_lines(path.read_text())

    borrowed = runtime is not None
    if not borrowed:
        lock = acquire_single_instance_lock()  # noqa: F841 — held until import ends
        runtime = TrackerRuntime.start()
    try:
        skipped = {
            day
            for day in {t.at.date() for t in transitions}
            if runtime.tracking.day_exists(day)
        }
        imported = 0
        for transition in transitions:
            if transition.at.date() in skipped:
                continue
            if transition.mark == "⚫":
                start, end = transition.strike_span
                runtime.tracking.strike_span(start, end, transition.at, Provenance.IMPORTED)
            else:
                runtime.tracking.record_lamp_state(
                    transition.mark == "🟢", transition.at, Provenance.IMPORTED
                )
            imported += 1
        days = {t.at.date() for t in transitions if t.at.date() not in skipped}
        print(f"imported {imported} transition(s) across {len(days)} day(s) from {path}")
        for day in sorted(skipped):
            print(f"skipped {day}: already in the event store")
    finally:
        if not borrowed:
            runtime.stop()


def _parse_strike_span(rest: str, at: dt.datetime) -> tuple[dt.datetime, dt.datetime] | None:
    match = STRIKE_WINDOW.match(rest)
    if not match:
        return None
    start = _clock_to_datetime(match["start"], at.date())
    end = _clock_to_datetime(match["end"], at.date())
    if start > end:
        start -= dt.timedelta(days=1)
    return start, end


def _clock_to_datetime(clock: str, day: dt.date) -> dt.datetime:
    time_part, meridiem = clock[:-1], clock[-1]
    hour, minute = (int(piece) for piece in time_part.split(":"))
    hour = hour % 12 + (12 if meridiem == "p" else 0)
    return dt.datetime.combine(day, dt.time(hour, minute))
