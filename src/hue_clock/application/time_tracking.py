from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING

from eventsourcing.application import AggregateNotFoundError, Application

from hue_clock.application.transcodings import DateAsISO, ProvenanceAsName
from hue_clock.domain.work_day import Provenance, WorkDay

if TYPE_CHECKING:
    from eventsourcing.persistence import JSONTranscoder

LOOKBACK_DAYS = 62

ONE_DAY = dt.timedelta(days=1)


class NoSuchSession(Exception):
    pass


@dataclass(frozen=True)
class ClockStatus:
    is_clocked_in: bool
    since: dt.datetime | None


@dataclass(frozen=True)
class DaySummary:
    worked_seconds: float
    away_seconds: float
    struck_seconds: float
    session_count: int


@dataclass(frozen=True)
class SessionOverview:
    started_at: dt.datetime
    ended_at: dt.datetime | None
    seconds: float
    fully_struck: bool


class TimeTracking(Application):
    def register_transcodings(self, transcoder: JSONTranscoder) -> None:
        super().register_transcodings(transcoder)
        transcoder.register(DateAsISO())
        transcoder.register(ProvenanceAsName())

    def record_lamp_state(
        self,
        on: bool,
        at: dt.datetime,
        provenance: Provenance = Provenance.OBSERVED,
    ) -> bool:
        changed = self._rolled_forward(at.date())
        current = changed[-1] if changed else self._latest_work_day(at.date())

        was_clocked_in = current.is_clocked_in if current else False
        if was_clocked_in == on:
            self._save_all(changed)
            return False

        if current is None or current.day != at.date():
            current = WorkDay(at.date())
            changed.append(current)
        elif not changed:
            changed.append(current)
        if on:
            current.clock_in(at, provenance)
        else:
            current.clock_out(at, provenance)
        self._save_all(changed)
        return True

    def strike_span(
        self,
        start: dt.datetime,
        end: dt.datetime,
        now: dt.datetime,
        provenance: Provenance = Provenance.OBSERVED,
    ) -> None:
        changed = self._rolled_forward(now.date())
        current = changed[-1] if changed else self._latest_work_day(now.date())
        if current is None or current.day != now.date():
            current = WorkDay(now.date())
            changed.append(current)
        elif not changed:
            changed.append(current)
        current.strike(start, end, now, provenance)
        self._save_all(changed)

    def strike_session(self, index: int, now: dt.datetime) -> None:
        day = self._work_day(now.date())
        sessions = day.ledger.sessions if day else ()
        if not 0 <= index < len(sessions):
            raise NoSuchSession(index)
        session = sessions[index]
        self.strike_span(session.started_at, session.ended_at or now, now)

    def advance_to(self, now: dt.datetime) -> None:
        self._save_all(self._rolled_forward(now.date()))

    def clock_status(self, now: dt.datetime) -> ClockStatus | None:
        latest = self._latest_work_day(now.date())
        if latest is None:
            return None
        open_session = latest.ledger.open_session
        return ClockStatus(
            is_clocked_in=open_session is not None,
            since=open_session.started_at if open_session else None,
        )

    def day_summary(self, day: dt.date, now: dt.datetime) -> DaySummary | None:
        work_day = self._work_day(day)
        if work_day is None or not work_day.ledger.sessions:
            return None
        ledger = work_day.ledger
        return DaySummary(
            worked_seconds=ledger.worked_seconds(now),
            away_seconds=ledger.away_seconds(),
            struck_seconds=ledger.struck_seconds(now),
            session_count=len(ledger.sessions),
        )

    def sessions(self, day: dt.date, now: dt.datetime) -> tuple[SessionOverview, ...]:
        work_day = self._work_day(day)
        if work_day is None:
            return ()
        ledger = work_day.ledger
        overviews = []
        for session in ledger.sessions:
            seconds = session.realized(now).seconds
            covered = ledger.struck_coverage(session, now)
            overviews.append(
                SessionOverview(
                    started_at=session.started_at,
                    ended_at=session.ended_at,
                    seconds=seconds,
                    fully_struck=covered > 0 and covered >= seconds - 1,
                )
            )
        return tuple(overviews)

    def _rolled_forward(self, to_day: dt.date) -> list[WorkDay]:
        latest = self._latest_work_day(to_day)
        if latest is None or latest.day >= to_day or not latest.is_clocked_in:
            return []
        changed = [latest]
        day = latest
        while day.day < to_day:
            day.clock_out(midnight_after(day.day), Provenance.ROLLOVER)
            day = WorkDay(day.day + ONE_DAY)
            day.clock_in(midnight_of(day.day), Provenance.ROLLOVER)
            changed.append(day)
        return changed

    def _latest_work_day(self, through: dt.date) -> WorkDay | None:
        for offset in range(LOOKBACK_DAYS + 1):
            day = self._work_day(through - offset * ONE_DAY)
            if day is not None:
                return day
        return None

    def day_exists(self, day: dt.date) -> bool:
        return self._work_day(day) is not None

    def _work_day(self, day: dt.date) -> WorkDay | None:
        try:
            return self.repository.get(WorkDay.create_id(day))
        except AggregateNotFoundError:
            return None

    def _save_all(self, changed: list[WorkDay]) -> None:
        if changed:
            self.save(*changed)


def midnight_of(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.min)


def midnight_after(day: dt.date) -> dt.datetime:
    return midnight_of(day + ONE_DAY)
