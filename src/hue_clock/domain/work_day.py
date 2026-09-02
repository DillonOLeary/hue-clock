from __future__ import annotations

import datetime as dt
from enum import Enum
from uuid import NAMESPACE_URL, UUID, uuid5

from eventsourcing.domain import Aggregate, event

from hue_clock.domain.ledger import DayLedger, TimeSpan


class Provenance(Enum):
    OBSERVED = "observed"
    RECONCILED = "reconciled"
    ROLLOVER = "rollover"
    IMPORTED = "imported"
    QUIT = "quit"  # app quit clocked out; the lamp itself may still be on


class AlreadyClockedIn(Exception):
    pass


class NotClockedIn(Exception):
    pass


class TransitionOutOfOrder(Exception):
    pass


class SpanOutsideDay(Exception):
    pass


class WorkDay(Aggregate):
    @event("Opened")
    def __init__(self, day: dt.date) -> None:
        self.day = day
        self.ledger = DayLedger()

    @classmethod
    def create_id(cls, day: dt.date) -> UUID:
        return uuid5(NAMESPACE_URL, f"/hue_clock/work_day/{day.isoformat()}")

    @property
    def span(self) -> TimeSpan:
        start = dt.datetime.combine(self.day, dt.time.min)
        return TimeSpan(start, start + dt.timedelta(days=1))

    @property
    def is_clocked_in(self) -> bool:
        return self.ledger.is_clocked_in

    def clock_in(self, at: dt.datetime, provenance: Provenance) -> None:
        if self.ledger.is_clocked_in:
            raise AlreadyClockedIn(self.day)
        self._clocked_in(self.day, at, provenance)

    def clock_out(self, at: dt.datetime, provenance: Provenance) -> None:
        session = self.ledger.open_session
        if session is None:
            raise NotClockedIn(self.day)
        if at < session.started_at:
            raise TransitionOutOfOrder(f"{at} precedes {session.started_at}")
        self._clocked_out(self.day, at, provenance)

    def strike(
        self,
        start: dt.datetime,
        end: dt.datetime,
        at: dt.datetime,
        provenance: Provenance,
    ) -> None:
        if end <= start:
            raise TransitionOutOfOrder(f"{end} precedes {start}")
        if not self.span.overlaps(TimeSpan(start, end)):
            raise SpanOutsideDay(f"{start}–{end} outside {self.day}")
        self._period_struck(self.day, start, end, at, provenance)

    @event("ClockedIn")
    def _clocked_in(self, day: dt.date, at: dt.datetime, provenance: Provenance) -> None:
        self.ledger = self.ledger.clock_in(at)

    @event("ClockedOut")
    def _clocked_out(self, day: dt.date, at: dt.datetime, provenance: Provenance) -> None:
        self.ledger = self.ledger.clock_out(at)

    @event("PeriodStruck")
    def _period_struck(
        self,
        day: dt.date,
        start: dt.datetime,
        end: dt.datetime,
        at: dt.datetime,
        provenance: Provenance,
    ) -> None:
        self.ledger = self.ledger.strike(TimeSpan(start, end))
