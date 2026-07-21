from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Callable

from hue_clock.formatting import format_duration
from hue_clock.projections.capacities_note.daily_note import DailyNote
from hue_clock.projections.capacities_note.ports import NotePublisher
from hue_clock.projections.capacities_note.projection import CapacitiesNoteProjection

SETTLE_SECONDS = 4
RESEND_GRACE_SECONDS = 600
RESEND_GRACE_MAX_SECONDS = 3600


class NoteFlusher:
    """Drains queued note lines to Capacities, at-least-once.

    Publisher calls happen outside the lock; every note mutation re-loads the
    aggregate inside the lock and re-checks the head, so a lamp event landing
    mid-flush can never be lost to a stale save."""

    def __init__(
        self,
        notes: CapacitiesNoteProjection,
        publisher: NotePublisher,
        now: Callable[[], dt.datetime] = dt.datetime.now,
        settle: Callable[[float], None] = time.sleep,
        lock: threading.Lock | None = None,
    ):
        self.notes = notes
        self.publisher = publisher
        self.now = now
        self.settle = settle
        self.lock = lock or threading.Lock()

    def flush(self) -> None:
        for day in self.notes.days_with_pending(self.now().date()):
            try:
                self._flush_day(day)
            except Exception as error:
                note = self.notes.note(day)
                print(
                    f"capacities write failed — {len(note.queue)} line(s) "
                    f"queued for {day}: {error}",
                    flush=True,
                )

    def _flush_day(self, day: dt.date) -> None:
        while self._settle_head(day):
            pass

    def _settle_head(self, day: dt.date) -> bool:
        with self.lock:
            note = self.notes.note(day)
            head = note.head if note else None
            if head is None:
                return False
            text, sent_at, resends = head.text, head.sent_at, head.resends

        now = self.now()
        if sent_at is None:
            self.publisher.append(text, day)
            self._apply(day, text, lambda n: n.head_sent(now))
            sent_at = now
            self.settle(SETTLE_SECONDS)
        if self.publisher.line_present(text, day):
            return self._confirm(day, text, was_resent=resends > 0)

        age = (now - sent_at).total_seconds()
        grace = min(RESEND_GRACE_SECONDS * 2**resends, RESEND_GRACE_MAX_SECONDS)
        if age < grace:
            print(f"unconfirmed (read-back lag?) — will re-check: {text}", flush=True)
            return False

        self.publisher.append(text, day)
        self._apply(day, text, lambda n: n.head_resent(self.now()))
        print(f"absent {format_duration(age)} after send — re-sent: {text}", flush=True)
        self.settle(SETTLE_SECONDS)
        if self.publisher.line_present(text, day):
            return self._confirm(day, text, was_resent=True)
        return False

    def _confirm(self, day: dt.date, text: str, was_resent: bool) -> bool:
        confirmed = self._apply(day, text, lambda n: n.head_confirmed(self.now()))
        if confirmed is None:
            return False
        if was_resent:
            self._scrub(day)
        return True

    def _apply(
        self, day: dt.date, expected_head: str, mutate: Callable[[DailyNote], None]
    ) -> DailyNote | None:
        with self.lock:
            note = self.notes.note(day)
            head = note.head if note else None
            if head is None or head.text != expected_head:
                return None
            mutate(note)
            self.notes.save(note)
            return note

    def _scrub(self, day: dt.date) -> None:
        try:
            removed = self.publisher.scrub_adjacent_duplicates(day)
        except Exception as error:
            print(f"duplicate scrub failed (`dedupe` runs it by hand): {error}", flush=True)
            return
        if removed:
            with self.lock:
                note = self.notes.note(day)
                note.duplicates_scrubbed(self.now(), removed)
                self.notes.save(note)
            print(f"deleted {removed} duplicate line(s) from {day}", flush=True)
