import datetime as dt

from hue_clock.domain.ledger import DayLedger
from hue_clock.formatting import format_clock, format_duration

MINIMUM_BREAK_SECONDS = 60


def clock_in_line(ledger: DayLedger, at: dt.datetime, approx: bool = False) -> str:
    line = f"🟢 {format_clock(at)}"
    gap = ledger.gap_before(at)
    if gap is not None and gap.total_seconds() >= MINIMUM_BREAK_SECONDS:
        line += f" · {format_duration(gap.total_seconds())} break"
    return _suffixed(line, approx)


def clock_out_line(ledger: DayLedger, at: dt.datetime, approx: bool = False) -> str:
    line = f"🔴 {format_clock(at)}"
    session = ledger.open_session
    if session is not None:
        line += f" · {format_duration((at - session.started_at).total_seconds())}"
    after = ledger.clock_out(at) if session is not None else ledger
    if len(after.closed_sessions) > 1:
        line += f" · Σ {format_duration(after.closed_total_seconds())}"
    return _suffixed(line, approx)


def strike_line(start: dt.datetime, end: dt.datetime) -> str:
    window = f"{format_clock(start)}–{format_clock(end)}"
    return f"⚫ {window} · −{format_duration((end - start).total_seconds())}"


def _suffixed(line: str, approx: bool) -> str:
    return f"{line} *(approx)*" if approx else line
