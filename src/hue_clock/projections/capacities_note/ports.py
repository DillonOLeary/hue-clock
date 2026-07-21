import datetime as dt
from typing import Protocol


class NotePublisher(Protocol):
    def append(self, line: str, day: dt.date) -> None: ...

    def line_present(self, line: str, day: dt.date) -> bool: ...

    def scrub_adjacent_duplicates(self, day: dt.date) -> int: ...
