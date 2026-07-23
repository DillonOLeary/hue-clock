import datetime as dt
from typing import Protocol


class NotePublisher(Protocol):
    def append(self, line: str, day: dt.date) -> None: ...
