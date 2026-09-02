from __future__ import annotations

import datetime as dt
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from hue_clock.projections.capacities_note.daily_note import DailyNote
    from hue_clock.projections.capacities_note.ports import NotePublisher
    from hue_clock.projections.capacities_note.projection import CapacitiesNoteProjection


class NoteFlusher:
    """Drains queued note lines to Capacities, write-only and at-least-once.

    Each line is appended, then popped on a 200 (a raised error keeps it queued
    for the next pass). Publisher calls happen outside the lock; every note
    mutation re-loads the aggregate inside the lock and re-checks the head, so a
    lamp event landing mid-flush can never be lost to a stale save.
    """

    def __init__(
        self,
        notes: CapacitiesNoteProjection,
        publisher: NotePublisher,
        now: Callable[[], dt.datetime] = dt.datetime.now,
        lock: threading.Lock | None = None,
    ) -> None:
        self.notes = notes
        self.publisher = publisher
        self.now = now
        self.lock = lock or threading.Lock()

    def flush(self) -> None:
        for day in self.notes.days_with_pending(self.now().date()):
            try:
                self._flush_day(day)
            except Exception as error:
                note = self.notes.note(day)
                pending = len(note.queue) if note else 0
                print(
                    f"capacities write failed — {pending} line(s) queued for {day}: {error}",
                    flush=True,
                )

    def _flush_day(self, day: dt.date) -> None:
        while self._publish_head(day):
            pass

    def _publish_head(self, day: dt.date) -> bool:
        with self.lock:
            note = self.notes.note(day)
            head = note.head if note else None
            if head is None:
                return False
            text, already_sent = head.text, head.is_sent

        # Migration guard: a line with sent_at set already got a 200 in the
        # read-back era (this drains the lines wedged by the retired confirm
        # loop). Pop without re-appending so we don't add another duplicate.
        # Inert afterward — sent_at is never set on new lines.
        if not already_sent:
            self.publisher.append(text, day)
            print(f"[{self.now().isoformat(timespec='seconds')}] published: {text}", flush=True)
        self._apply(day, text, lambda n: n.head_confirmed(self.now()))
        return True

    def _apply(
        self, day: dt.date, expected_head: str, mutate: Callable[[DailyNote], None]
    ) -> DailyNote | None:
        with self.lock:
            note = self.notes.note(day)
            if note is None or note.head is None or note.head.text != expected_head:
                return None
            mutate(note)
            self.notes.save(note)
            return note
