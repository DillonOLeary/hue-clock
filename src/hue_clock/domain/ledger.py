from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Iterable


@dataclass(frozen=True)
class TimeSpan:
    start: dt.datetime
    end: dt.datetime

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def overlap_seconds(self, other: TimeSpan) -> float:
        latest_start = max(self.start, other.start)
        earliest_end = min(self.end, other.end)
        return max(0.0, (earliest_end - latest_start).total_seconds())

    def overlaps(self, other: TimeSpan) -> bool:
        return self.overlap_seconds(other) > 0


def merge_spans(spans: Iterable[TimeSpan]) -> tuple[TimeSpan, ...]:
    merged: list[TimeSpan] = []
    for span in sorted(spans, key=lambda s: (s.start, s.end)):
        if merged and span.start <= merged[-1].end:
            merged[-1] = TimeSpan(merged[-1].start, max(merged[-1].end, span.end))
        else:
            merged.append(span)
    return tuple(merged)


def overlap_seconds(spans: Iterable[TimeSpan], others: Iterable[TimeSpan]) -> float:
    others = tuple(others)
    return sum(span.overlap_seconds(other) for span in spans for other in others)


@dataclass(frozen=True)
class Session:
    started_at: dt.datetime
    ended_at: dt.datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    def realized(self, now: dt.datetime) -> TimeSpan:
        return TimeSpan(self.started_at, self.ended_at or now)


@dataclass(frozen=True)
class DayLedger:
    sessions: tuple[Session, ...] = ()
    strikes: tuple[TimeSpan, ...] = ()

    def clock_in(self, at: dt.datetime) -> DayLedger:
        return replace(self, sessions=(*self.sessions, Session(at)))

    def clock_out(self, at: dt.datetime) -> DayLedger:
        *earlier, last = self.sessions
        return replace(self, sessions=(*earlier, Session(last.started_at, at)))

    def strike(self, span: TimeSpan) -> DayLedger:
        return replace(self, strikes=(*self.strikes, span))

    @property
    def open_session(self) -> Session | None:
        last = self.sessions[-1] if self.sessions else None
        return last if last is not None and last.is_open else None

    @property
    def is_clocked_in(self) -> bool:
        return self.open_session is not None

    @property
    def closed_sessions(self) -> tuple[Session, ...]:
        return tuple(session for session in self.sessions if not session.is_open)

    @property
    def merged_strikes(self) -> tuple[TimeSpan, ...]:
        return merge_spans(self.strikes)

    @property
    def first_started_at(self) -> dt.datetime | None:
        return self.sessions[0].started_at if self.sessions else None

    @property
    def last_ended_at(self) -> dt.datetime | None:
        return self.sessions[-1].ended_at if self.sessions else None

    def gap_before(self, at: dt.datetime) -> dt.timedelta | None:
        last = self.sessions[-1] if self.sessions else None
        if last is None or last.ended_at is None:
            return None
        return at - last.ended_at

    def worked_seconds(self, now: dt.datetime) -> float:
        realized = [session.realized(now) for session in self.sessions]
        return sum(span.seconds for span in realized) - overlap_seconds(
            realized, self.merged_strikes
        )

    def struck_seconds(self, now: dt.datetime) -> float:
        realized = [session.realized(now) for session in self.sessions]
        return overlap_seconds(realized, self.merged_strikes)

    def away_seconds(self) -> float:
        return sum(
            (later.started_at - earlier.ended_at).total_seconds()
            for earlier, later in zip(self.sessions, self.sessions[1:])
            if earlier.ended_at is not None
        )

    def closed_total_seconds(self) -> float:
        spans = [TimeSpan(s.started_at, s.ended_at) for s in self.closed_sessions]
        return sum(span.seconds for span in spans) - overlap_seconds(
            spans, self.merged_strikes
        )

    def struck_coverage(self, session: Session, now: dt.datetime) -> float:
        return overlap_seconds([session.realized(now)], self.merged_strikes)
