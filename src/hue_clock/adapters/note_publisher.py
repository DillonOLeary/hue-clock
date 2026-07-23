from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime as dt


class CapacitiesNotePublisher:
    """Appends note lines to the Capacities daily note.

    Write-only: the app never reads or edits the note. The flusher trusts a
    200 and pops the queued line; a raised error keeps it queued for retry.
    """

    def __init__(self, client) -> None:
        self.client = client

    def append(self, line: str, day: dt.date) -> None:
        self.client.append_daily_note(line, date=day.isoformat())
