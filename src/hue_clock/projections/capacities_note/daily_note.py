from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

from eventsourcing.domain import Aggregate, event

from hue_clock.domain.ledger import DayLedger, TimeSpan
from hue_clock.domain.work_day import Provenance
from hue_clock.projections.capacities_note.note_lines import (
    clock_in_line,
    clock_out_line,
    strike_line,
)

if TYPE_CHECKING:
    import datetime as dt

PUBLISHED_PROVENANCES = (Provenance.OBSERVED, Provenance.RECONCILED)


@dataclass
class QueuedLine:
    text: str
    first_queued_at: dt.datetime | None = None
    # Historical: read-back era, retired 2026-07-23. `sent_at`/`resends` are no
    # longer written by new appends; they survive so old HeadSent/HeadResent
    # events still replay, and the flusher's one-time migration guard reads
    # `is_sent` to drain lines that already got a 200 without re-appending them.
    sent_at: dt.datetime | None = None
    resends: int = 0

    @property
    def is_sent(self) -> bool:
        return self.sent_at is not None


class DailyNote(Aggregate):
    @event("Opened")
    def __init__(self, day: dt.date) -> None:
        self.day = day
        self.ledger = DayLedger()
        self.queue: list[QueuedLine] = []
        self.last_confirmed_at: dt.datetime | None = None

    @classmethod
    def create_id(cls, day: dt.date) -> UUID:
        return uuid5(NAMESPACE_URL, f"/hue_clock/daily_note/{day.isoformat()}")

    @property
    def head(self) -> QueuedLine | None:
        return self.queue[0] if self.queue else None

    def record_transition(
        self, kind: str, at: dt.datetime, provenance: Provenance, queued_at: dt.datetime
    ) -> str | None:
        queued = None
        if provenance in PUBLISHED_PROVENANCES:
            approx = provenance is Provenance.RECONCILED
            render = clock_in_line if kind == "in" else clock_out_line
            queued = render(self.ledger, at, approx)
            self._line_queued(queued, queued_at)
        self._transition_noted(kind, at)
        return queued

    def record_strike(
        self,
        start: dt.datetime,
        end: dt.datetime,
        provenance: Provenance,
        queued_at: dt.datetime,
    ) -> str | None:
        queued = None
        if provenance in PUBLISHED_PROVENANCES:
            queued = strike_line(start, end)
            self._line_queued(queued, queued_at)
        self._strike_noted(start, end)
        return queued

    @event("HeadConfirmed")
    def head_confirmed(self, at: dt.datetime) -> None:
        self.queue.pop(0)
        self.last_confirmed_at = at

    # Historical: read-back era, retired 2026-07-23. No longer triggered by new
    # code — kept so events already in the store replay onto their queued line.
    @event("HeadSent")
    def head_sent(self, at: dt.datetime) -> None:
        self.queue[0].sent_at = at

    @event("HeadResent")
    def head_resent(self, at: dt.datetime) -> None:
        head = self.queue[0]
        head.sent_at = at
        head.resends += 1

    @event("DuplicatesScrubbed")
    def duplicates_scrubbed(self, at: dt.datetime, removed: int) -> None:
        pass

    @event("LineQueued")
    def _line_queued(self, text: str, at: dt.datetime | None = None) -> None:
        # `at` is optional so LineQueued events from before it was added still
        # replay (they carry only `text`) — backward-compatible schema evolution.
        self.queue.append(QueuedLine(text, first_queued_at=at))

    @event("TransitionNoted")
    def _transition_noted(self, kind: str, at: dt.datetime) -> None:
        if kind == "in":
            self.ledger = self.ledger.clock_in(at)
        else:
            self.ledger = self.ledger.clock_out(at)

    @event("StrikeNoted")
    def _strike_noted(self, start: dt.datetime, end: dt.datetime) -> None:
        self.ledger = self.ledger.strike(TimeSpan(start, end))
