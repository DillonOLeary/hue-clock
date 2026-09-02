from __future__ import annotations

import datetime as dt
import threading

from eventsourcing.system import SingleThreadedRunner, System

from hue_clock.application.time_tracking import (
    ClockStatus,
    DaySummary,
    SessionOverview,
    TimeTracking,
)
from hue_clock.domain.work_day import Provenance
from hue_clock.projections.capacities_note.projection import (
    CapacitiesNoteProjection,
    QueueStatus,
)
from hue_clock.runtime.config import CAPACITIES_NOTE_DB, STATE_DIR, TIME_TRACKING_DB


def persistence_env() -> dict[str, str]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "PERSISTENCE_MODULE": "eventsourcing.sqlite",
        "TIMETRACKING_SQLITE_DBNAME": f"file:{TIME_TRACKING_DB}",
        "CAPACITIESNOTEPROJECTION_SQLITE_DBNAME": f"file:{CAPACITIES_NOTE_DB}",
        "SQLITE_LOCK_TIMEOUT": "10",
    }


class TrackerRuntime:
    """Composition root for the running app.

    Owns the leader→follower system, serializes all writes behind one lock,
    and wakes the flusher after every recorded change.
    """

    def __init__(self, runner: SingleThreadedRunner) -> None:
        self.runner = runner
        self.tracking: TimeTracking = runner.get(TimeTracking)
        self.notes: CapacitiesNoteProjection = runner.get(CapacitiesNoteProjection)
        self.commands = threading.Lock()
        self.flusher_wake = threading.Event()

    @classmethod
    def start(cls, env: dict[str, str] | None = None) -> TrackerRuntime:
        system = System(pipes=[[TimeTracking, CapacitiesNoteProjection]])
        runner = SingleThreadedRunner(system, env=env if env is not None else persistence_env())
        runner.start()
        return cls(runner)

    def stop(self) -> None:
        self.runner.stop()

    def record_clock_state(
        self, clocked_in: bool, at: dt.datetime, provenance: Provenance = Provenance.OBSERVED
    ) -> bool:
        with self.commands:
            recorded = self.tracking.record_clock_state(clocked_in, at, provenance)
        if recorded:
            self.flusher_wake.set()
        return recorded

    def strike_window(self, minutes: int) -> None:
        now = dt.datetime.now()
        with self.commands:
            self.tracking.strike_span(now - dt.timedelta(minutes=minutes), now, now)
        self.flusher_wake.set()

    def strike_session(self, index: int) -> None:
        with self.commands:
            self.tracking.strike_session(index, dt.datetime.now())
        self.flusher_wake.set()

    def advance_to(self, now: dt.datetime) -> None:
        with self.commands:
            self.tracking.advance_to(now)

    def clock_status(self, now: dt.datetime) -> ClockStatus | None:
        return self.tracking.clock_status(now)

    def day_summary(self, now: dt.datetime) -> DaySummary | None:
        return self.tracking.day_summary(now.date(), now)

    def sessions(self, now: dt.datetime) -> tuple[SessionOverview, ...]:
        return self.tracking.sessions(now.date(), now)

    def queue_status(self, now: dt.datetime) -> QueueStatus:
        return self.notes.queue_status(now.date())
