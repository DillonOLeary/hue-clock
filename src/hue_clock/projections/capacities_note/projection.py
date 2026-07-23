from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING

from eventsourcing.application import AggregateNotFoundError
from eventsourcing.system import ProcessApplication

from hue_clock.application.transcodings import DateAsISO, ProvenanceAsName
from hue_clock.domain.work_day import WorkDay
from hue_clock.projections.capacities_note.daily_note import DailyNote

if TYPE_CHECKING:
    from collections.abc import Iterator

    from eventsourcing.persistence import JSONTranscoder

PENDING_WINDOW_DAYS = 7


@dataclass(frozen=True)
class QueueStatus:
    pending: int
    head_queued_at: dt.datetime | None
    last_confirmed_at: dt.datetime | None

    @property
    def has_pending(self) -> bool:
        return self.pending > 0


class CapacitiesNoteProjection(ProcessApplication):
    def register_transcodings(self, transcoder: JSONTranscoder) -> None:
        super().register_transcodings(transcoder)
        transcoder.register(DateAsISO())
        transcoder.register(ProvenanceAsName())

    def policy(self, domain_event, processing_event) -> None:
        # queued_at is wall-clock time, stamped once here at processing time
        # (policy does not re-run on replay), so it reflects when a line entered
        # the outbox — not the transition's logical `at`, which can be hours old
        # for a reconciled line and would make it look stale immediately.
        queued_at = dt.datetime.now()
        if isinstance(domain_event, (WorkDay.ClockedIn, WorkDay.ClockedOut)):
            kind = "in" if isinstance(domain_event, WorkDay.ClockedIn) else "out"
            note = self._note(domain_event.day)
            queued = note.record_transition(
                kind, domain_event.at, domain_event.provenance, queued_at
            )
        elif isinstance(domain_event, WorkDay.PeriodStruck):
            note = self._note(domain_event.day)
            queued = note.record_strike(
                domain_event.start, domain_event.end, domain_event.provenance, queued_at
            )
        else:
            return
        processing_event.collect_events(note)
        if queued is not None:
            print(f"[{domain_event.at.isoformat(timespec='seconds')}] {queued}", flush=True)

    def note(self, day: dt.date) -> DailyNote | None:
        try:
            return self.repository.get(DailyNote.create_id(day))
        except AggregateNotFoundError:
            return None

    def days_with_pending(self, today: dt.date) -> list[dt.date]:
        return [day for day, note in self._recent_notes(today) if note.queue]

    def queue_status(self, today: dt.date) -> QueueStatus:
        pending, head, last_confirmed = 0, None, None
        for _day, note in self._recent_notes(today):
            pending += len(note.queue)
            if head is None and note.head is not None:
                head = note.head
            confirmed = note.last_confirmed_at
            if confirmed is not None and (last_confirmed is None or confirmed > last_confirmed):
                last_confirmed = confirmed
        return QueueStatus(
            pending=pending,
            head_queued_at=head.first_queued_at if head else None,
            last_confirmed_at=last_confirmed,
        )

    def _recent_notes(self, today: dt.date) -> Iterator[tuple[dt.date, DailyNote]]:
        for offset in range(PENDING_WINDOW_DAYS, -1, -1):
            day = today - dt.timedelta(days=offset)
            note = self.note(day)
            if note is not None:
                yield day, note

    def _note(self, day: dt.date) -> DailyNote:
        existing = self.note(day)
        return existing if existing is not None else DailyNote(day)
