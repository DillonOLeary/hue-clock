from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable

CLOCK_MARKS = ("🟢", "🔴", "⚫")
DELETE_PACING_SECONDS = 2.1


class CapacitiesNotePublisher:
    """Publishes note lines through the Capacities HTTP API.

    line_present treats an unreadable note as present: a 200 append that
    cannot be read back must never push the flusher into duplicating.
    """

    def __init__(self, client, pace: Callable[[float], None] = time.sleep) -> None:
        self.client = client
        self.pace = pace

    def append(self, line: str, day: dt.date) -> None:
        self.client.append_daily_note(line, date=day.isoformat())

    def line_present(self, line: str, day: dt.date) -> bool:
        try:
            note = self._day_blocks(day)
        except Exception:
            return True
        if note is None:
            return False
        return any(text.strip() == line.strip() for _, text in note[1])

    def scrub_adjacent_duplicates(self, day: dt.date) -> int:
        note = self._day_blocks(day)
        if note is None:
            return 0
        object_id, blocks = note
        previous, removed = None, 0
        for block_id, text in blocks:
            text = text.strip()
            if not text.startswith(CLOCK_MARKS):
                continue
            if text == previous and block_id:
                self.client.delete_block(object_id, block_id)
                removed += 1
                self.pace(DELETE_PACING_SECONDS)
            else:
                previous = text
        return removed

    def _day_blocks(self, day: dt.date) -> tuple[str, list[tuple[str | None, str]]] | None:
        date_str = day.isoformat()
        for result in self.client.search(date_str, structure_ids=["RootDailyNote"], limit=5):
            if date_str not in result.get("title", ""):
                continue
            obj = self.client.get_object(result["id"])
            found: list[tuple[str | None, str]] = []
            for blocks in (obj.get("blocks") or {}).values():
                _collect_blocks(blocks, found)
            return result["id"], found
        return None


def _collect_blocks(blocks, out) -> None:
    for block in blocks or []:
        text = "".join(t.get("text", "") for t in block.get("tokens") or [])
        out.append((block.get("id"), text))
        _collect_blocks(block.get("blocks"), out)
